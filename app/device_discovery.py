"""USB serial port discovery.

Uses `pyserial` when available (rich metadata). Falls back to globbing
`/dev/ttyACM*` / `/dev/ttyUSB*` on Linux when pyserial is missing.
"""
from __future__ import annotations

import glob
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from serial.tools.list_ports_common import ListPortInfo


@dataclass(frozen=True)
class SerialDevice:
    device: str
    description: str
    hwid: str

    def display(self) -> str:
        if self.description and self.description != "n/a":
            return f"{self.device}  -  {self.description}"
        return self.device


def list_serial_devices(usb_only: bool = True) -> list[SerialDevice]:
    """List detected serial devices.

    With `usb_only=True` (default) the platform UARTs (e.g. `/dev/ttyS*`) are
    filtered out so only USB-attached adapters are returned.
    """
    try:
        import serial.tools.list_ports  # type: ignore[import-untyped]
    except ImportError:
        return _fallback_glob()

    devices: list[SerialDevice] = []
    for port in serial.tools.list_ports.comports():
        if usb_only and not _is_usb_port(port):
            continue
        devices.append(
            SerialDevice(
                device=port.device,
                description=port.description or "n/a",
                hwid=port.hwid or "n/a",
            )
        )
    devices.sort(key=lambda d: d.device)
    return devices


def _is_usb_port(port: ListPortInfo) -> bool:
    """True if `port` looks like a USB serial adapter (not a built-in UART)."""
    device = getattr(port, "device", "") or ""
    if device.startswith("/dev/ttyACM") or device.startswith("/dev/ttyUSB"):
        return True
    hwid = (getattr(port, "hwid", "") or "").upper()
    if "USB" in hwid or "VID:PID" in hwid:
        return True
    if getattr(port, "vid", None) is not None:
        return True
    return False


def _fallback_glob() -> list[SerialDevice]:
    candidates = sorted(set(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")))
    return [SerialDevice(device=d, description="n/a", hwid="n/a") for d in candidates]
