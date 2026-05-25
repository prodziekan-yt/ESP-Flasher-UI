"""Read flash tab: USB-only dump of the device's SPI flash to a local .bin file."""
from __future__ import annotations

from pathlib import Path

from PyQt6 import QtCore, QtWidgets

from .device_selector import DeviceSelector, MODE_USB
from .i18n import I18n
from .ui_utils import make_muted_label
from .workers import FlashWorker


PRESET_FULL = "full"
PRESET_APP = "app"
PRESET_BOOTLOADER = "bootloader"

_APP_OFFSET = 0x10000
_APP_LENGTH = 0x200000
_BOOTLOADER_LENGTH = 0x10000


def _parse_int(text: str) -> int | None:
    """Accept decimal or 0x-prefixed hex; return None on parse failure."""
    text = text.strip()
    if not text:
        return None
    try:
        return int(text, 0)
    except ValueError:
        return None


class ReadFlashTab(QtWidgets.QWidget):
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
        self._output_path: str = ""

        self._build_ui()
        self._wire_signals()
        self.retranslate_ui()

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(10)

        self.output_box = QtWidgets.QGroupBox()
        grid = QtWidgets.QGridLayout(self.output_box)

        self.output_label = QtWidgets.QLabel()
        self.output_edit = QtWidgets.QLineEdit()
        self.output_edit.setClearButtonEnabled(True)
        self.output_browse = QtWidgets.QPushButton()

        self.offset_label = QtWidgets.QLabel()
        self.offset_edit = QtWidgets.QLineEdit()
        self.offset_edit.setText("0x0")
        self.offset_edit.setMaximumWidth(140)

        self.length_label = QtWidgets.QLabel()
        self.length_edit = QtWidgets.QLineEdit()
        self.length_edit.setMaximumWidth(140)

        grid.addWidget(self.output_label, 0, 0)
        grid.addWidget(self.output_edit, 0, 1, 1, 3)
        grid.addWidget(self.output_browse, 0, 4)

        grid.addWidget(self.offset_label, 1, 0)
        grid.addWidget(self.offset_edit, 1, 1)
        grid.addWidget(self.length_label, 1, 2)
        grid.addWidget(self.length_edit, 1, 3)

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        root.addWidget(self.output_box)

        self.presets_box = QtWidgets.QGroupBox()
        presets = QtWidgets.QHBoxLayout(self.presets_box)
        self.preset_full = QtWidgets.QPushButton()
        self.preset_app = QtWidgets.QPushButton()
        self.preset_bootloader = QtWidgets.QPushButton()
        for btn in (self.preset_full, self.preset_app, self.preset_bootloader):
            presets.addWidget(btn)
        presets.addStretch(1)
        root.addWidget(self.presets_box)

        actions = QtWidgets.QGridLayout()
        actions.setHorizontalSpacing(8)
        actions.setVerticalSpacing(2)

        self.detect_button = QtWidgets.QPushButton()
        self.read_button = QtWidgets.QPushButton()
        self.read_button.setMinimumHeight(36)

        self.detect_desc = make_muted_label()
        self.read_desc = make_muted_label()

        actions.addWidget(self.detect_button, 0, 0)
        actions.addWidget(self.read_button, 0, 1)
        actions.addWidget(self.detect_desc, 1, 0)
        actions.addWidget(self.read_desc, 1, 1)
        actions.setColumnStretch(0, 1)
        actions.setColumnStretch(1, 1)
        root.addLayout(actions)

        root.addStretch(1)

    def _wire_signals(self) -> None:
        self.output_browse.clicked.connect(self._on_browse_clicked)
        self.output_edit.editingFinished.connect(self._on_output_text_committed)

        self.preset_full.clicked.connect(lambda: self._apply_preset(PRESET_FULL))
        self.preset_app.clicked.connect(lambda: self._apply_preset(PRESET_APP))
        self.preset_bootloader.clicked.connect(
            lambda: self._apply_preset(PRESET_BOOTLOADER)
        )

        self.detect_button.clicked.connect(self._on_detect_clicked)
        self.read_button.clicked.connect(self._on_read_clicked)

        self.i18n.language_changed.connect(lambda _code: self.retranslate_ui())

    def retranslate_ui(self) -> None:
        tr = self.i18n.tr
        self.output_box.setTitle(tr("read.section"))
        self.presets_box.setTitle(tr("read.presets"))

        self.output_label.setText(tr("read.output") + ":")
        self.output_edit.setPlaceholderText(tr("read.output_placeholder"))
        self.output_browse.setText(tr("firmware.browse"))

        self.offset_label.setText(tr("read.offset") + ":")
        self.length_label.setText(tr("read.length") + ":")
        self.length_edit.setPlaceholderText(tr("read.length_auto"))

        self.preset_full.setText(tr("read.preset_full"))
        self.preset_full.setToolTip(tr("read.preset_full.desc"))
        self.preset_app.setText(tr("read.preset_app"))
        self.preset_app.setToolTip(tr("read.preset_app.desc"))
        self.preset_bootloader.setText(tr("read.preset_bootloader"))
        self.preset_bootloader.setToolTip(tr("read.preset_bootloader.desc"))

        self.detect_button.setText(tr("read.detect"))
        self.read_button.setText(tr("read.read"))

        for btn, lbl, key in (
            (self.detect_button, self.detect_desc, "read.detect.desc"),
            (self.read_button, self.read_desc, "read.read.desc"),
        ):
            lbl.setText(tr(key))
            btn.setToolTip(tr(key))

    def _on_browse_clicked(self) -> None:
        start_dir = self._output_path or str(Path.home() / "flash_dump.bin")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            self.i18n.tr("read.dialog_title"),
            start_dir,
            self.i18n.tr("read.dialog_filter"),
        )
        if path:
            self._set_output(path)

    def _on_output_text_committed(self) -> None:
        path = self.output_edit.text().strip()
        if path and path != self._output_path:
            self._set_output(path)

    def _set_output(self, path: str) -> None:
        self._output_path = path
        self.output_edit.setText(path)

    def _apply_preset(self, preset: str) -> None:
        if preset == PRESET_FULL:
            self.offset_edit.setText("0x0")
            self.length_edit.clear()
        elif preset == PRESET_APP:
            self.offset_edit.setText(hex(_APP_OFFSET))
            self.length_edit.setText(hex(_APP_LENGTH))
        elif preset == PRESET_BOOTLOADER:
            self.offset_edit.setText("0x0")
            self.length_edit.setText(hex(_BOOTLOADER_LENGTH))

    def _device_or_warn(self) -> str:
        if self.device_selector.current_mode() != MODE_USB:
            self.log.emit(self.i18n.tr("read.usb_only"))
            return ""
        device = self.device_selector.current_device()
        if not device:
            self.log.emit(self.i18n.tr("flash.no_device"))
        return device

    def _on_detect_clicked(self) -> None:
        if self.flash_worker.is_running():
            self.log.emit(self.i18n.tr("flash.in_progress"))
            return
        device = self._device_or_warn()
        if not device:
            return
        baud = self.device_selector.current_baud()
        self.log.emit(self.i18n.tr("read.starting_detect", device=device))
        ok = self.flash_worker.start_flash_id(device, baud=baud)
        if not ok:
            self.log.emit(
                self.i18n.tr("flash.tool_missing", tool="esptool", package="esptool")
            )
            return
        self.flash_state_changed.emit(True)

    def _on_read_clicked(self) -> None:
        if self.flash_worker.is_running():
            self.log.emit(self.i18n.tr("flash.in_progress"))
            return

        output = self._output_path or self.output_edit.text().strip()
        if not output:
            self.log.emit(self.i18n.tr("read.no_output"))
            return

        offset = _parse_int(self.offset_edit.text())
        if offset is None or offset < 0:
            self.log.emit(self.i18n.tr("read.invalid_offset", value=self.offset_edit.text()))
            return

        length: int | None = None
        length_text = self.length_edit.text().strip()
        if length_text:
            length = _parse_int(length_text)
            if length is None or length <= 0:
                self.log.emit(
                    self.i18n.tr("read.invalid_length", value=length_text)
                )
                return

        device = self._device_or_warn()
        if not device:
            return

        baud = self.device_selector.current_baud()
        size_hint = hex(length) if length is not None else self.i18n.tr("read.length_auto")
        self.log.emit(
            self.i18n.tr(
                "read.starting",
                device=device,
                offset=hex(offset),
                length=size_hint,
                output=output,
            )
        )
        ok = self.flash_worker.start_read_flash(
            device, output, offset=offset, length=length, baud=baud
        )
        if not ok:
            self.log.emit(
                self.i18n.tr("flash.tool_missing", tool="esptool", package="esptool")
            )
            return
        self.flash_state_changed.emit(True)
