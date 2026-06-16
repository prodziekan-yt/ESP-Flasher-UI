"""Erase flash tab: full flash wipe via `esptool erase-flash` with a confirm gate."""
from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets

from .device_selector import DeviceSelector, MODE_USB
from .i18n import I18n
from .ui_utils import make_muted_label
from .workers import FlashWorker


class EraseFlashTab(QtWidgets.QWidget):
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

        self._build_ui()
        self._wire_signals()
        self.retranslate_ui()

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(10)

        self.warning_box = QtWidgets.QGroupBox()
        warn_layout = QtWidgets.QVBoxLayout(self.warning_box)

        self.warning_label = QtWidgets.QLabel()
        self.warning_label.setWordWrap(True)
        self.warning_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        warn_layout.addWidget(self.warning_label)

        self.consequences_label = QtWidgets.QLabel()
        self.consequences_label.setWordWrap(True)
        warn_layout.addWidget(self.consequences_label)

        root.addWidget(self.warning_box)

        self.confirm_checkbox = QtWidgets.QCheckBox()
        font = self.confirm_checkbox.font()
        font.setBold(True)
        self.confirm_checkbox.setFont(font)
        root.addWidget(self.confirm_checkbox)

        actions = QtWidgets.QGridLayout()
        actions.setHorizontalSpacing(8)
        actions.setVerticalSpacing(2)

        self.erase_button = QtWidgets.QPushButton()
        self.erase_button.setMinimumHeight(40)
        self.erase_button.setEnabled(False)

        palette = self.erase_button.palette()
        palette.setColor(
            QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor("#b00020")
        )
        self.erase_button.setPalette(palette)

        self.erase_desc = make_muted_label()

        actions.addWidget(self.erase_button, 0, 0)
        actions.addWidget(self.erase_desc, 1, 0)
        actions.setColumnStretch(0, 1)
        root.addLayout(actions)

        root.addStretch(1)

    def _wire_signals(self) -> None:
        self.confirm_checkbox.toggled.connect(self.erase_button.setEnabled)
        self.erase_button.clicked.connect(self._on_erase_clicked)
        self.i18n.language_changed.connect(lambda _code: self.retranslate_ui())

    def retranslate_ui(self) -> None:
        tr = self.i18n.tr
        self.warning_box.setTitle(tr("erase.warning_title"))
        self.warning_label.setText(tr("erase.warning_body"))
        self.consequences_label.setText(tr("erase.consequences"))
        self.confirm_checkbox.setText(tr("erase.confirm"))
        self.erase_button.setText(tr("erase.button"))
        self.erase_button.setToolTip(tr("erase.button.desc"))
        self.erase_desc.setText(tr("erase.button.desc"))

    def _on_erase_clicked(self) -> None:
        self.confirm_checkbox.setChecked(False)

        if self.flash_worker.is_running():
            self.log.emit(self.i18n.tr("flash.in_progress"))
            return
        if self.device_selector.current_mode() != MODE_USB:
            self.log.emit(self.i18n.tr("erase.usb_only"))
            return
        device = self.device_selector.current_device()
        if not device:
            self.log.emit(self.i18n.tr("flash.no_device"))
            return

        baud = self.device_selector.current_baud()
        self.log.emit(self.i18n.tr("erase.starting", device=device))
        ok = self.flash_worker.start_erase_flash(device, baud=baud)
        if not ok:
            self.log.emit(
                self.i18n.tr("flash.tool_missing", tool="esptool", package="esptool")
            )
            return
        self.flash_state_changed.emit(True)
