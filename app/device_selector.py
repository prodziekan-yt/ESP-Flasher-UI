"""Shared USB / Network (OTA) device selector widget."""
from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from .device_discovery import SerialDevice, list_serial_devices
from .i18n import I18n
from .ui_utils import make_muted_label
from .workers import DetectResult, DetectWorker, is_private_address, resolve_host


MODE_USB = "usb"
MODE_IP = "ip"


class DeviceSelector(QtWidgets.QGroupBox):
    """Mode radio (USB / IP) + USB port combo + OTA host/password fields."""

    mode_changed = QtCore.pyqtSignal(str)
    log = QtCore.pyqtSignal(str)

    def __init__(self, i18n: I18n, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self._detect_worker: DetectWorker | None = None
        self._mode: str = MODE_USB

        self._build_ui()
        self._wire_signals()
        self.refresh_usb_devices(silent=True)
        self.retranslate_ui()

    # --------------------------------------------------------------- build
    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(8)

        mode_row = QtWidgets.QHBoxLayout()
        self.mode_usb_radio = QtWidgets.QRadioButton()
        self.mode_ip_radio = QtWidgets.QRadioButton()
        self.mode_usb_radio.setChecked(True)
        mode_row.addWidget(self.mode_usb_radio)
        mode_row.addWidget(self.mode_ip_radio)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self._build_usb_panel())
        self.stack.addWidget(self._build_ip_panel())
        layout.addWidget(self.stack)

    def _build_usb_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(panel)
        grid.setContentsMargins(0, 0, 0, 0)
        self.usb_device_label = QtWidgets.QLabel()
        self.usb_device_combo = QtWidgets.QComboBox()
        self.usb_device_combo.setMinimumWidth(320)
        self.usb_refresh_button = QtWidgets.QPushButton()
        grid.addWidget(self.usb_device_label, 0, 0)
        grid.addWidget(self.usb_device_combo, 0, 1)
        grid.addWidget(self.usb_refresh_button, 0, 2)

        self.usb_baud_label = QtWidgets.QLabel()
        self.usb_baud_combo = QtWidgets.QComboBox()
        for baud in (
            None,
            9600,
            57600,
            115200,
            230400,
            460800,
            921600,
            1500000,
            2000000,
        ):
            self.usb_baud_combo.addItem("", userData=baud)
        self.usb_baud_combo.setCurrentIndex(5)
        grid.addWidget(self.usb_baud_label, 1, 0)
        grid.addWidget(self.usb_baud_combo, 1, 1)

        grid.setColumnStretch(1, 1)
        return panel

    def _build_ip_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(panel)
        grid.setContentsMargins(0, 0, 0, 0)

        self.ip_address_label = QtWidgets.QLabel()
        self.ip_address_edit = QtWidgets.QLineEdit()
        self.ip_resolved_label = make_muted_label()
        self.ip_resolved_label.setVisible(False)

        self.ip_password_label = QtWidgets.QLabel()
        self.ip_password_edit = QtWidgets.QLineEdit()
        self.ip_password_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.ip_password_hint = make_muted_label()

        self.ip_detect_button = QtWidgets.QPushButton()

        grid.addWidget(self.ip_address_label, 0, 0)
        grid.addWidget(self.ip_address_edit, 0, 1)
        grid.addWidget(self.ip_resolved_label, 1, 1)
        grid.addWidget(self.ip_password_label, 2, 0)
        grid.addWidget(self.ip_password_edit, 2, 1)
        grid.addWidget(self.ip_password_hint, 3, 1)
        grid.addWidget(
            self.ip_detect_button,
            4,
            1,
            alignment=QtCore.Qt.AlignmentFlag.AlignRight,
        )
        grid.setColumnStretch(1, 1)
        return panel

    # -------------------------------------------------------------- wiring
    def _wire_signals(self) -> None:
        self.mode_usb_radio.toggled.connect(self._on_mode_toggled)
        self.mode_ip_radio.toggled.connect(self._on_mode_toggled)
        self.usb_refresh_button.clicked.connect(self.refresh_usb_devices)
        self.ip_detect_button.clicked.connect(self._on_detect_clicked)
        self.ip_address_edit.textChanged.connect(
            lambda _t: self._set_resolved_caption(None, False)
        )
        self.i18n.language_changed.connect(lambda _code: self.retranslate_ui())

    # --------------------------------------------------------- translation
    def retranslate_ui(self) -> None:
        tr = self.i18n.tr
        self.setTitle(tr("mode.group"))
        self.mode_usb_radio.setText(tr("mode.usb"))
        self.mode_ip_radio.setText(tr("mode.ip"))

        self.usb_device_label.setText(tr("usb.device") + ":")
        self.usb_refresh_button.setText(tr("usb.refresh"))
        placeholder_idx = self.usb_device_combo.findData(None)
        if placeholder_idx >= 0:
            self.usb_device_combo.setItemText(placeholder_idx, tr("usb.no_devices"))

        self.usb_baud_label.setText(tr("usb.baud") + ":")
        for i in range(self.usb_baud_combo.count()):
            data = self.usb_baud_combo.itemData(i)
            label = tr("usb.baud.auto") if data is None else f"{data} bps"
            self.usb_baud_combo.setItemText(i, label)
        hint = tr("usb.baud.hint")
        self.usb_baud_combo.setToolTip(hint)
        self.usb_baud_label.setToolTip(hint)

        self.ip_address_label.setText(tr("ip.address") + ":")
        self.ip_password_label.setText(tr("ip.password") + ":")
        self.ip_address_edit.setPlaceholderText(tr("ip.placeholder.ip"))
        self.ip_password_edit.setPlaceholderText(tr("ip.placeholder.password"))
        self.ip_password_hint.setText(tr("ip.password.hint"))
        self.ip_password_edit.setToolTip(tr("ip.password.hint"))
        self.ip_detect_button.setText(tr("ip.detect"))

    # ----------------------------------------------------------- callbacks
    def _on_mode_toggled(self, _checked: bool) -> None:
        mode = MODE_USB if self.mode_usb_radio.isChecked() else MODE_IP
        if mode == self._mode:
            return
        self._mode = mode
        self.stack.setCurrentIndex(0 if mode == MODE_USB else 1)
        self.mode_changed.emit(mode)

    def refresh_usb_devices(self, silent: bool = False) -> None:
        if not silent:
            self.log.emit(self.i18n.tr("usb.scanning"))
        devices: list[SerialDevice] = list_serial_devices()
        self.usb_device_combo.clear()
        if not devices:
            self.usb_device_combo.addItem(self.i18n.tr("usb.no_devices"), userData=None)
            self.usb_device_combo.setEnabled(False)
        else:
            self.usb_device_combo.setEnabled(True)
            for dev in devices:
                self.usb_device_combo.addItem(dev.display(), userData=dev.device)
        if not silent:
            self.log.emit(self.i18n.tr("usb.found", count=len(devices)))

    def _on_detect_clicked(self) -> None:
        host = self.ip_address_edit.text().strip()
        if not host:
            self.log.emit(self.i18n.tr("ip.invalid"))
            return
        if self._detect_worker is not None and self._detect_worker.isRunning():
            return
        self.log.emit(self.i18n.tr("ip.detecting", host=host))
        self.ip_detect_button.setEnabled(False)
        worker = DetectWorker(host, self)
        worker.finished_with.connect(self._on_detect_finished)
        worker.finished.connect(lambda: self.ip_detect_button.setEnabled(True))
        self._detect_worker = worker
        worker.start()

    def _on_detect_finished(self, result: DetectResult) -> None:
        if result.resolved_ip:
            self._set_resolved_caption(result.resolved_ip, result.is_public)
        else:
            self._set_resolved_caption(None, False)

        if result.ok:
            role_label = self._port_role_label(result.port_role)
            self.log.emit(
                self.i18n.tr(
                    "ip.detected",
                    host=result.host,
                    ip=result.resolved_ip or result.host,
                    port=result.port,
                    role=role_label,
                    ms=result.latency_ms,
                )
            )
            if result.is_public:
                self._emit_public_warning(result.host, result.resolved_ip or "?")
        else:
            if result.resolved_ip is None:
                self.log.emit(
                    self.i18n.tr("ip.resolve_failed", host=result.host)
                )
            else:
                self.log.emit(
                    self.i18n.tr(
                        "ip.detect_failed",
                        host=result.host,
                        ip=result.resolved_ip,
                    )
                )
                if result.is_public:
                    self._emit_public_warning(result.host, result.resolved_ip)

    def _port_role_label(self, role: str | None) -> str:
        if not role:
            return ""
        key = f"ip.port_role.{role}"
        label = self.i18n.tr(key)
        return "" if label == key else label

    def _set_resolved_caption(self, ip: str | None, is_public: bool) -> None:
        if not ip:
            self.ip_resolved_label.setVisible(False)
            self.ip_resolved_label.clear()
            return
        text_key = "ip.resolved_public" if is_public else "ip.resolved_private"
        self.ip_resolved_label.setText(self.i18n.tr(text_key, ip=ip))
        if is_public:
            self.ip_resolved_label.setStyleSheet(
                "color: palette(highlight); font-weight: bold;"
            )
        else:
            self.ip_resolved_label.setStyleSheet("color: palette(mid);")
        self.ip_resolved_label.setVisible(True)

    def _emit_public_warning(self, host: str, ip: str) -> None:
        self.log.emit(self.i18n.tr("ip.warn_public", host=host, ip=ip))

    # -------------------------------------------------------------- public
    def current_mode(self) -> str:
        return self._mode

    def current_device(self) -> str:
        """Selected device path (USB) or host (OTA); empty when nothing is picked."""
        if self._mode == MODE_USB:
            return self.usb_device_combo.currentData() or ""
        return self.ip_address_edit.text().strip()

    def current_host(self) -> str:
        return self.ip_address_edit.text().strip()

    def current_password(self) -> str:
        return self.ip_password_edit.text()

    def current_baud(self) -> int | None:
        """Selected USB baud rate, or None for esptool default (Auto)."""
        return self.usb_baud_combo.currentData()

    def set_host(self, host: str) -> None:
        """Pre-fill the OTA host and switch to IP mode."""
        self.ip_address_edit.setText(host)
        if host and self._mode != MODE_IP:
            self.mode_ip_radio.setChecked(True)

    def preflight_ota(self, host: str) -> None:
        """Resolve `host` and warn the user if it points at a public IP."""
        if not host:
            return
        ip, err = resolve_host(host)
        if ip is None:
            self._set_resolved_caption(None, False)
            self.log.emit(
                self.i18n.tr("ip.resolve_failed", host=host)
                + (f" ({err})" if err else "")
            )
            return
        is_public = not is_private_address(ip)
        self._set_resolved_caption(ip, is_public)
        if is_public:
            self._emit_public_warning(host, ip)
