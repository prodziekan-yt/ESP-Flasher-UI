"""Background workers.

* `DetectWorker` - non-blocking DNS resolve + TCP probe on ESPHome ports.
* `FlashWorker` - QProcess wrapper that streams stdout/stderr line by line.
"""
from __future__ import annotations

import ipaddress
import shutil
import socket
import time
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QProcess, QThread, pyqtSignal

from .text_utils import strip_ansi


OTA_PROBE_PORTS: tuple[tuple[int, str], ...] = (
    (6053, "api"),
    (3232, "ota"),
    (8266, "arduino_ota"),
)
OTA_PROBE_TIMEOUT_S: float = 1.5
HOST_RESOLVE_TIMEOUT_S: float = 2.0


@dataclass(frozen=True)
class DetectResult:
    ok: bool
    host: str
    port: int | None
    latency_ms: int | None
    error: str | None
    resolved_ip: str | None = None
    is_public: bool = False
    port_role: str | None = None


def is_private_address(ip: str) -> bool:
    """True for RFC1918, link-local, loopback or ULA addresses."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return bool(addr.is_private or addr.is_loopback or addr.is_link_local)


def resolve_host(host: str) -> tuple[str | None, str | None]:
    """Resolve `host` to its first IPv4 address. Returns `(ip, None)` or `(None, error)`."""
    if not host:
        return None, "empty host"
    prev = socket.getdefaulttimeout()
    socket.setdefaulttimeout(HOST_RESOLVE_TIMEOUT_S)
    try:
        infos = socket.getaddrinfo(host, None, family=socket.AF_INET)
    except OSError as exc:
        return None, str(exc)
    finally:
        socket.setdefaulttimeout(prev)
    if not infos:
        return None, "no address"
    return infos[0][4][0], None


class DetectWorker(QThread):
    """Resolve host, then probe ESPHome-specific TCP ports."""

    finished_with = pyqtSignal(object)

    def __init__(self, host: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._host = host.strip()

    def run(self) -> None:
        if not self._host:
            self.finished_with.emit(
                DetectResult(False, self._host, None, None, "empty host")
            )
            return

        ip, err = resolve_host(self._host)
        if ip is None:
            self.finished_with.emit(
                DetectResult(False, self._host, None, None, err)
            )
            return

        public = not is_private_address(ip)
        last_err: str | None = None
        for port, role in OTA_PROBE_PORTS:
            t0 = time.monotonic()
            try:
                with socket.create_connection(
                    (ip, port), timeout=OTA_PROBE_TIMEOUT_S
                ):
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    self.finished_with.emit(
                        DetectResult(
                            True,
                            self._host,
                            port,
                            latency_ms,
                            None,
                            resolved_ip=ip,
                            is_public=public,
                            port_role=role,
                        )
                    )
                    return
            except (OSError, socket.timeout) as exc:
                last_err = str(exc)
                continue

        self.finished_with.emit(
            DetectResult(
                False,
                self._host,
                None,
                None,
                last_err,
                resolved_ip=ip,
                is_public=public,
            )
        )


class FlashWorker(QObject):
    """Subprocess wrapper that streams stdout/stderr line-by-line via signals."""

    line = pyqtSignal(str)
    finished_with_code = pyqtSignal(int)
    live_logs_started = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None
        self._stdout_buf = ""
        self._stderr_buf = ""
        self._operation: str = ""
        self._logs_announced: bool = False

    @property
    def operation(self) -> str:
        """Operation tag: `flash`, `ota`, `esphome.<sub>` or empty."""
        return self._operation

    def is_running(self) -> bool:
        return (
            self._process is not None
            and self._process.state() != QProcess.ProcessState.NotRunning
        )

    def start_usb(
        self,
        device: str,
        firmware: str,
        chip: str = "auto",
        baud: int | None = None,
    ) -> bool:
        """Launch `esptool write-flash` on `device`.

        `baud=None` uses esptool's default (no `--baud` flag).
        """
        tool = shutil.which("esptool") or shutil.which("esptool.py")
        if not tool:
            return False
        args = ["--chip", chip, "--port", device]
        if baud is not None:
            args.extend(["--baud", str(baud)])
        args.extend(["write-flash", "0x0", firmware])
        return self._spawn(tool, args, operation="flash")

    def start_read_flash(
        self,
        device: str,
        output_path: str,
        offset: int = 0,
        length: int | None = None,
        chip: str = "auto",
        baud: int | None = None,
    ) -> bool:
        """Launch `esptool read-flash` to dump device flash to `output_path`.

        `length=None` lets esptool detect the chip flash size and reads all of it.
        """
        tool = shutil.which("esptool") or shutil.which("esptool.py")
        if not tool:
            return False
        args = ["--chip", chip, "--port", device]
        if baud is not None:
            args.extend(["--baud", str(baud)])
        args.extend(["read-flash", hex(offset)])
        args.append(hex(length) if length is not None else "ALL")
        args.append(output_path)
        return self._spawn(tool, args, operation="read_flash")

    def start_flash_id(
        self,
        device: str,
        chip: str = "auto",
        baud: int | None = None,
    ) -> bool:
        """Launch `esptool flash-id` to detect chip + flash size."""
        tool = shutil.which("esptool") or shutil.which("esptool.py")
        if not tool:
            return False
        args = ["--chip", chip, "--port", device]
        if baud is not None:
            args.extend(["--baud", str(baud)])
        args.append("flash-id")
        return self._spawn(tool, args, operation="flash_id")

    def start_erase_flash(self, device: str, baud: int | None = None) -> bool:
        """Erase entire SPI flash via `esptool erase-flash` (USB only)."""
        tool = shutil.which("esptool") or shutil.which("esptool.py")
        if not tool:
            return False
        args = ["--port", device]
        if baud is not None:
            args.extend(["--baud", str(baud)])
        args.append("erase-flash")
        return self._spawn(tool, args, operation="erase_flash")

    def start_reset(self, device: str, baud: int | None = None) -> bool:
        """Hard-reset the ESP on `device` via `esptool --no-stub chip-id`."""
        tool = shutil.which("esptool") or shutil.which("esptool.py")
        if not tool:
            return False
        args = ["--port", device, "--no-stub"]
        if baud is not None:
            args.extend(["--baud", str(baud)])
        args.append("chip-id")
        return self._spawn(tool, args, operation="reset")

    def start_ota(self, host: str, firmware: str, password: str | None = None) -> bool:
        """Launch `espota.py` for OTA upload."""
        tool = shutil.which("espota.py")
        if not tool:
            return False
        args = ["-i", host, "-f", firmware]
        if password:
            args.extend(["-a", password])
        return self._spawn(tool, args, operation="ota")

    def start_esphome(
        self,
        subcommand: str,
        yaml_path: str,
        device: str | None = None,
        cwd: str | None = None,
    ) -> bool:
        """Launch `esphome <subcommand> <yaml>` with optional `--device`."""
        tool = shutil.which("esphome")
        if not tool:
            return False
        args = [subcommand, yaml_path]
        if device:
            args.extend(["--device", device])
        return self._spawn(tool, args, cwd=cwd, operation=f"esphome.{subcommand}")

    def stop(self) -> None:
        if self._process is None:
            return
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.terminate()
            if not self._process.waitForFinished(3000):
                self._process.kill()
                self._process.waitForFinished(2000)

    def _spawn(
        self,
        program: str,
        args: list[str],
        cwd: str | None = None,
        operation: str = "",
    ) -> bool:
        if self.is_running():
            return False
        self._operation = operation
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        if cwd:
            proc.setWorkingDirectory(cwd)
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_finished)
        self._process = proc
        self._stdout_buf = ""
        self._stderr_buf = ""
        self._logs_announced = False
        cwd_hint = f"  (cwd: {cwd})" if cwd else ""
        self.line.emit(f"$ {program} {' '.join(args)}{cwd_hint}")
        proc.start(program, args)
        return True

    def _drain(self, buf: str, chunk: str) -> str:
        buf += chunk
        while True:
            idx = buf.find("\n")
            if idx < 0:
                return buf
            raw, buf = buf[:idx], buf[idx + 1 :]
            line = strip_ansi(raw.rstrip("\r"))
            self.line.emit(line)
            if (
                not self._logs_announced
                and "Starting log output" in line
                and self._operation in ("esphome.run", "esphome.logs")
            ):
                self._logs_announced = True
                self.live_logs_started.emit()

    def _on_stdout(self) -> None:
        self._read_channel("stdout")

    def _on_stderr(self) -> None:
        self._read_channel("stderr")

    def _read_channel(self, channel: str) -> None:
        if self._process is None:
            return
        reader = (
            self._process.readAllStandardOutput
            if channel == "stdout"
            else self._process.readAllStandardError
        )
        data = bytes(reader()).decode("utf-8", errors="replace")
        buf = self._drain(getattr(self, f"_{channel}_buf"), data)
        setattr(self, f"_{channel}_buf", buf)

    def _on_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        for tail in (self._stdout_buf, self._stderr_buf):
            if tail.strip():
                self.line.emit(strip_ansi(tail.rstrip()))
        self._stdout_buf = ""
        self._stderr_buf = ""
        self.finished_with_code.emit(int(exit_code))
        self._process = None
