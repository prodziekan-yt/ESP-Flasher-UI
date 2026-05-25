"""Flash .BIN tab: firmware picker + Flash button.

The device target is owned by the shared `DeviceSelector` widget at the
`MainWindow` level; this tab only reads the current mode and host/port.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6 import QtCore, QtWidgets

from .device_selector import DeviceSelector, MODE_USB
from .i18n import I18n
from .workers import FlashWorker


class FlashBinTab(QtWidgets.QWidget):
    log = QtCore.pyqtSignal(str)
    flash_state_changed = QtCore.pyqtSignal(bool)

    def __init__(
        self,
        i18n: I18n,
        flash_worker: FlashWorker,
        device_selector: DeviceSelector,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.flash_worker = flash_worker
        self.device_selector = device_selector
        self._firmware_path: str = ""

        self._build_ui()
        self._wire_signals()
        self.retranslate_ui()

    # --------------------------------------------------------------- build
    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)

        self.firmware_box = QtWidgets.QGroupBox()
        row = QtWidgets.QHBoxLayout(self.firmware_box)
        self.firmware_edit = QtWidgets.QLineEdit()
        self.firmware_edit.setClearButtonEnabled(True)
        self.firmware_browse_button = QtWidgets.QPushButton()
        row.addWidget(self.firmware_edit, 1)
        row.addWidget(self.firmware_browse_button)
        layout.addWidget(self.firmware_box)

        actions = QtWidgets.QHBoxLayout()
        self.flash_button = QtWidgets.QPushButton()
        self.flash_button.setMinimumHeight(36)
        actions.addWidget(self.flash_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addStretch(1)

    # -------------------------------------------------------------- wiring
    def _wire_signals(self) -> None:
        self.firmware_browse_button.clicked.connect(self._on_browse_clicked)
        self.firmware_edit.editingFinished.connect(self._on_firmware_text_committed)
        self.flash_button.clicked.connect(self._on_flash_clicked)
        self.i18n.language_changed.connect(lambda _code: self.retranslate_ui())

    # --------------------------------------------------------- translation
    def retranslate_ui(self) -> None:
        tr = self.i18n.tr
        self.firmware_box.setTitle(tr("firmware.label"))
        self.firmware_edit.setPlaceholderText(tr("firmware.placeholder"))
        self.firmware_browse_button.setText(tr("firmware.browse"))
        self.flash_button.setText(tr("actions.flash"))

    # -------------------------------------------------------- public hooks
    def set_firmware_path(self, path: str) -> None:
        """Pre-fill the firmware field (used by the ESPHome -> .BIN bridge)."""
        self._set_firmware(path)

    def firmware_path(self) -> str:
        return self._firmware_path

    # ----------------------------------------------------------- callbacks
    def _on_browse_clicked(self) -> None:
        start_dir = self._firmware_path or str(Path.home())
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            self.i18n.tr("firmware.dialog_title"),
            start_dir,
            self.i18n.tr("firmware.filter"),
        )
        if path:
            self._set_firmware(path)

    def _on_firmware_text_committed(self) -> None:
        path = self.firmware_edit.text().strip()
        if path and path != self._firmware_path:
            self._set_firmware(path)

    def _set_firmware(self, path: str) -> None:
        self._firmware_path = path
        self.firmware_edit.setText(path)
        self.log.emit(self.i18n.tr("firmware.selected", path=path))

    def _on_flash_clicked(self) -> None:
        if self.flash_worker.is_running():
            self.log.emit(self.i18n.tr("flash.in_progress"))
            return

        firmware = self._firmware_path or self.firmware_edit.text().strip()
        if not firmware:
            self.log.emit(self.i18n.tr("firmware.missing"))
            return
        if not Path(firmware).is_file():
            self.log.emit(self.i18n.tr("firmware.not_found", path=firmware))
            return

        mode = self.device_selector.current_mode()
        if mode == MODE_USB:
            device = self.device_selector.current_device()
            if not device:
                self.log.emit(self.i18n.tr("flash.no_device"))
                return
            baud = self.device_selector.current_baud()
            self.log.emit(
                self.i18n.tr("flash.starting_usb", device=device, firmware=firmware)
            )
            ok = self.flash_worker.start_usb(device, firmware, baud=baud)
            if not ok:
                self.log.emit(
                    self.i18n.tr(
                        "flash.tool_missing", tool="esptool", package="esptool"
                    )
                )
                return
        else:
            host = self.device_selector.current_host()
            if not host:
                self.log.emit(self.i18n.tr("flash.no_ip"))
                return
            self.device_selector.preflight_ota(host)
            self.log.emit(
                self.i18n.tr("flash.starting_ota", host=host, firmware=firmware)
            )
            ok = self.flash_worker.start_ota(
                host, firmware, self.device_selector.current_password() or None
            )
            if not ok:
                self.log.emit(self.i18n.tr("flash.ota_stub"))
                self.log.emit(
                    self.i18n.tr(
                        "flash.tool_missing", tool="espota.py", package="esphome"
                    )
                )
                return

        self.flash_state_changed.emit(True)
