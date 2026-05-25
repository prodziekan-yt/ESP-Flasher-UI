"""Offline ESPHome stack trace decoder.

Pairs a crash backtrace with the matching `firmware.elf` from the last
local compile and resolves every hex address via PlatformIO's `addr2line`
binary (falling back to system `addr2line`).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from PyQt6 import QtGui, QtWidgets

from .i18n import I18n


# 6-8 digit hex addresses (ESPHome traces use 8, ESP8266 RAM sometimes 6).
_HEX_RE = re.compile(r"0x[0-9a-fA-F]{6,8}")

# ELF e_machine -> candidate addr2line binaries in priority order.
# Each toolchain matches a specific ESP chip family.
_MACHINE_TO_ADDR2LINE: dict[str, tuple[str, ...]] = {
    "xtensa": (
        "xtensa-esp32s3-elf-addr2line",
        "xtensa-esp32s2-elf-addr2line",
        "xtensa-esp32-elf-addr2line",
        "xtensa-lx106-elf-addr2line",
    ),
    "riscv": (
        "riscv32-esp-elf-addr2line",
    ),
}


_ELF_SKIP_STEMS = frozenset({
    "bootloader",
    "partitions",
    "partition-table",
    "ota_data_initial",
})


def _elf_priority(elf: Path) -> int:
    """Lower is better. Picks firmware.elf or `<env>.elf` over bootloader-like ELFs."""
    stem = elf.stem
    if stem in _ELF_SKIP_STEMS:
        return 9
    if stem == "firmware":
        return 0
    if stem == elf.parent.name:
        return 1
    return 5


def find_elf_candidates(project_dir: str) -> list[str]:
    """Project ELF files in `.esphome/build/`, application ELFs first, newest first."""
    if not project_dir:
        return []
    build_root = Path(project_dir) / ".esphome" / "build"
    if not build_root.is_dir():
        return []
    rows: list[tuple[int, float, str]] = []
    seen: set[str] = set()
    for pattern in ("*/.pioenvs/*/*.elf", "*/.pio/build/*/*.elf"):
        for elf in build_root.glob(pattern):
            path = str(elf)
            if path in seen:
                continue
            try:
                mt = elf.stat().st_mtime
            except OSError:
                continue
            seen.add(path)
            rows.append((_elf_priority(elf), mt, path))
    rows.sort(key=lambda t: (t[0], -t[1]))
    return [path for _, _, path in rows]


def read_elf_machine(elf_path: str) -> str:
    """Return `'xtensa'`, `'riscv'` or `''` based on the ELF header e_machine field."""
    try:
        with open(elf_path, "rb") as fh:
            head = fh.read(20)
    except OSError:
        return ""
    if len(head) < 20 or head[:4] != b"\x7fELF":
        return ""
    e_machine = int.from_bytes(head[18:20], byteorder="little")
    if e_machine == 0x5E or e_machine == 0x5E00:
        return "xtensa"
    if e_machine == 0xF3:
        return "riscv"
    return ""


def find_addr2line_binaries(machine: str) -> list[str]:
    """`addr2line` binaries under `~/.platformio/packages` matching `machine`.

    Falls back to system `addr2line` when no PlatformIO toolchain is present.
    """
    names = _MACHINE_TO_ADDR2LINE.get(machine, ())
    pio_root = Path.home() / ".platformio" / "packages"
    found: list[str] = []
    if pio_root.is_dir():
        for name in names:
            for path in pio_root.glob(f"toolchain-*/bin/{name}"):
                if path.is_file():
                    found.append(str(path))
    if not found:
        from shutil import which
        sys_addr2line = which("addr2line")
        if sys_addr2line:
            found.append(sys_addr2line)
    return found


def extract_addresses(text: str) -> list[str]:
    """Unique hex addresses from `text`, lower-cased, first-seen order preserved."""
    seen: set[str] = set()
    out: list[str] = []
    for match in _HEX_RE.finditer(text):
        addr = match.group(0).lower()
        if addr in seen:
            continue
        seen.add(addr)
        out.append(addr)
    return out


def decode_trace(elf_path: str, text: str) -> tuple[str, int]:
    """Decode hex addresses from `text` against `elf_path`.

    Returns `(decoded_text, n_resolved)`. `decoded_text` is ready for display;
    `n_resolved` counts addresses that resolved to a real symbol.
    """
    if not Path(elf_path).is_file():
        raise FileNotFoundError(elf_path)
    addrs = extract_addresses(text)
    if not addrs:
        return "", 0

    machine = read_elf_machine(elf_path)
    binaries = find_addr2line_binaries(machine)
    if not binaries:
        raise RuntimeError("no addr2line found")

    decoded_lines: list[str] = []
    n_resolved = 0
    for binary in binaries:
        decoded_lines.clear()
        n_resolved = 0
        try:
            proc = subprocess.run(
                [binary, "-e", elf_path, "-f", "-p", "-i", "-C", *addrs],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            decoded_lines.append(f"{binary}: {exc}")
            continue
        if proc.returncode != 0:
            continue

        output_lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        if not output_lines:
            continue

        # `-i` may yield multiple lines per address (inlined frames); group
        # them by walking the output once.
        addr_iter = iter(addrs)
        current_addr: str | None = next(addr_iter, None)
        for ln in output_lines:
            stripped = ln.strip()
            if not stripped:
                continue
            if current_addr is not None and not stripped.startswith("(inlined"):
                decoded_lines.append(f"{current_addr}: {stripped}")
                if stripped != "?? ??:0" and "?? at ??:?" not in stripped:
                    n_resolved += 1
                current_addr = next(addr_iter, None)
            else:
                decoded_lines.append(f"          {stripped}")
        if n_resolved > 0:
            break

    return "\n".join(decoded_lines), n_resolved


class StackDecoderDialog(QtWidgets.QDialog):
    """Modal dialog: paste a backtrace, pick the matching ELF, click Decode."""

    def __init__(
        self,
        i18n: I18n,
        project_dir: str = "",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self._project_dir = project_dir
        self._build_ui()
        self._wire_signals()
        self._populate_elf_candidates()
        self.retranslate_ui()
        self.i18n.language_changed.connect(lambda _c: self.retranslate_ui())

    def _build_ui(self) -> None:
        self.setMinimumSize(720, 560)
        root = QtWidgets.QVBoxLayout(self)

        elf_row = QtWidgets.QHBoxLayout()
        self.elf_label = QtWidgets.QLabel()
        elf_row.addWidget(self.elf_label)
        self.elf_combo = QtWidgets.QComboBox()
        self.elf_combo.setEditable(True)
        self.elf_combo.setMinimumWidth(400)
        elf_row.addWidget(self.elf_combo, 1)
        self.elf_browse = QtWidgets.QPushButton()
        elf_row.addWidget(self.elf_browse)
        root.addLayout(elf_row)

        mono = QtGui.QFont("Monospace")
        mono.setStyleHint(QtGui.QFont.StyleHint.TypeWriter)

        self.input_label = QtWidgets.QLabel()
        root.addWidget(self.input_label)
        self.input_edit = QtWidgets.QPlainTextEdit()
        self.input_edit.setFont(mono)
        root.addWidget(self.input_edit, 1)

        button_row = QtWidgets.QHBoxLayout()
        self.decode_button = QtWidgets.QPushButton()
        self.decode_button.setDefault(True)
        button_row.addWidget(self.decode_button)
        button_row.addStretch(1)
        self.copy_button = QtWidgets.QPushButton()
        self.copy_button.setEnabled(False)
        button_row.addWidget(self.copy_button)
        self.close_button = QtWidgets.QPushButton()
        button_row.addWidget(self.close_button)
        root.addLayout(button_row)

        self.output_label = QtWidgets.QLabel()
        root.addWidget(self.output_label)
        self.output_edit = QtWidgets.QPlainTextEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setFont(mono)
        root.addWidget(self.output_edit, 1)

        self.status_label = QtWidgets.QLabel()
        self.status_label.setStyleSheet("color: palette(mid);")
        root.addWidget(self.status_label)

    def _wire_signals(self) -> None:
        self.elf_browse.clicked.connect(self._on_browse_elf)
        self.decode_button.clicked.connect(self._on_decode)
        self.copy_button.clicked.connect(self._on_copy)
        self.close_button.clicked.connect(self.accept)

    def _populate_elf_candidates(self) -> None:
        self.elf_combo.clear()
        for path in find_elf_candidates(self._project_dir):
            self.elf_combo.addItem(path)

    def retranslate_ui(self) -> None:
        tr = self.i18n.tr
        self.setWindowTitle(tr("decoder.title"))
        self.elf_label.setText(tr("decoder.elf_label") + ":")
        self.elf_browse.setText(tr("decoder.elf_browse"))
        self.input_label.setText(tr("decoder.input_label") + ":")
        self.input_edit.setPlaceholderText(tr("decoder.input_placeholder"))
        self.output_label.setText(tr("decoder.output_label"))
        self.decode_button.setText(tr("decoder.decode_button"))
        self.copy_button.setText(tr("decoder.copy_button"))
        self.close_button.setText(tr("decoder.close_button"))

    def _on_browse_elf(self) -> None:
        start_dir = (
            str(Path(self._project_dir) / ".esphome" / "build")
            if self._project_dir
            else str(Path.home())
        )
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            self.i18n.tr("decoder.elf_dialog_title"),
            start_dir,
            self.i18n.tr("decoder.elf_filter"),
        )
        if path:
            self.elf_combo.setEditText(path)

    def _on_decode(self) -> None:
        elf = self.elf_combo.currentText().strip()
        text = self.input_edit.toPlainText()
        if not elf:
            self._set_status(self.i18n.tr("decoder.no_elf"), error=True)
            return
        if not Path(elf).is_file():
            self._set_status(
                self.i18n.tr("decoder.elf_not_found", path=elf), error=True
            )
            return
        if not extract_addresses(text):
            self._set_status(self.i18n.tr("decoder.no_addresses"), error=True)
            return

        try:
            decoded, n_resolved = decode_trace(elf, text)
        except RuntimeError:
            self._set_status(self.i18n.tr("decoder.no_addr2line"), error=True)
            return
        except FileNotFoundError:
            self._set_status(
                self.i18n.tr("decoder.elf_not_found", path=elf), error=True
            )
            return

        self.output_edit.setPlainText(decoded)
        self.copy_button.setEnabled(bool(decoded))
        if n_resolved > 0:
            self._set_status(
                self.i18n.tr("decoder.success", n=n_resolved), error=False
            )
        else:
            self._set_status(self.i18n.tr("decoder.no_symbols"), error=True)

    def _on_copy(self) -> None:
        QtWidgets.QApplication.clipboard().setText(self.output_edit.toPlainText())
        self._set_status(self.i18n.tr("decoder.copied"), error=False)

    def _set_status(self, text: str, *, error: bool) -> None:
        if error:
            self.status_label.setStyleSheet("color: #d62a2a;")
        else:
            self.status_label.setStyleSheet("color: palette(mid);")
        self.status_label.setText(text)
