"""Flash ESPHome tab.

Drives `esphome <subcommand>` on a YAML config (validate / compile / upload /
run / logs / clean). The "Known devices" dropdown lists ESPHome's per-device
manifests under `<project_dir>/.esphome/storage/*.yaml.json`. A successful
compile / run enables the "Send to Flash .BIN" bridge.
"""
from __future__ import annotations

import re
from pathlib import Path

from PyQt6 import QtCore, QtWidgets

from .device_selector import DeviceSelector, MODE_IP, MODE_USB
from .esphome_storage import KnownDevice, discover_known_devices
from .i18n import I18n
from .stack_decoder import StackDecoderDialog
from .ui_utils import make_muted_label
from .workers import FlashWorker


# Subcommands that need a connected device (USB path or OTA host).
DEVICE_BOUND_ACTIONS = frozenset({"upload", "run", "logs"})

# Subcommands that implicitly run YAML validation; trigger pre-flight hints.
VALIDATING_ACTIONS = frozenset({"config", "compile", "upload", "run"})

# Plain-text scan for `level: VERY_VERBOSE` or `log_level: VERY_VERBOSE`.
# Avoids YAML parsing because configs commonly use !include / !secret tags.
_VERY_VERBOSE_RE = re.compile(
    r"^\s*(log_level|level)\s*:\s*VERY_VERBOSE\b", re.MULTILINE
)


class FlashEsphomeTab(QtWidgets.QWidget):
    log = QtCore.pyqtSignal(str)
    flash_state_changed = QtCore.pyqtSignal(bool)
    send_bin_to_bin_tab = QtCore.pyqtSignal(str)
    quick_view_requested = QtCore.pyqtSignal(str)

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

        self._project_dir: str = ""
        self._yaml_path: str = ""
        self._known_devices: list[KnownDevice] = []
        self._last_built_bin: str = ""
        self._last_subcommand: str = ""

        self._build_ui()
        self._wire_signals()
        self.retranslate_ui()

    # --------------------------------------------------------------- build
    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(10)

        self.project_box = QtWidgets.QGroupBox()
        proj = QtWidgets.QGridLayout(self.project_box)

        self.project_dir_label = QtWidgets.QLabel()
        self.project_dir_edit = QtWidgets.QLineEdit()
        self.project_dir_edit.setClearButtonEnabled(True)
        self.project_dir_browse = QtWidgets.QPushButton()

        self.known_devices_label = QtWidgets.QLabel()
        self.known_devices_combo = QtWidgets.QComboBox()
        self.known_devices_combo.setMinimumWidth(420)
        self.known_devices_refresh = QtWidgets.QPushButton()

        self.yaml_label = QtWidgets.QLabel()
        self.yaml_edit = QtWidgets.QLineEdit()
        self.yaml_edit.setClearButtonEnabled(True)
        self.yaml_browse = QtWidgets.QPushButton()

        self.quick_view_button = QtWidgets.QPushButton()
        self.quick_view_button.setCheckable(True)
        self.quick_view_button.setEnabled(False)

        proj.addWidget(self.project_dir_label, 0, 0)
        proj.addWidget(self.project_dir_edit, 0, 1)
        proj.addWidget(self.project_dir_browse, 0, 2)

        proj.addWidget(self.known_devices_label, 1, 0)
        proj.addWidget(self.known_devices_combo, 1, 1)
        proj.addWidget(self.known_devices_refresh, 1, 2)

        proj.addWidget(self.yaml_label, 2, 0)
        proj.addWidget(self.yaml_edit, 2, 1)
        proj.addWidget(self.yaml_browse, 2, 2)

        proj.addWidget(self.quick_view_button, 3, 2)

        proj.setColumnStretch(1, 1)
        root.addWidget(self.project_box)

        self.actions_box = QtWidgets.QGroupBox()
        actions = QtWidgets.QGridLayout(self.actions_box)
        actions.setHorizontalSpacing(8)
        actions.setVerticalSpacing(2)

        self.btn_validate = QtWidgets.QPushButton()
        self.btn_compile = QtWidgets.QPushButton()
        self.btn_upload = QtWidgets.QPushButton()
        self.btn_run = QtWidgets.QPushButton()
        self.btn_run.setMinimumHeight(36)
        self.btn_logs = QtWidgets.QPushButton()

        self.desc_validate = make_muted_label()
        self.desc_compile = make_muted_label()
        self.desc_upload = make_muted_label()
        self.desc_run = make_muted_label()
        self.desc_logs = make_muted_label()

        columns = [
            (self.btn_validate, self.desc_validate),
            (self.btn_compile, self.desc_compile),
            (self.btn_upload, self.desc_upload),
            (self.btn_run, self.desc_run),
            (self.btn_logs, self.desc_logs),
        ]
        for col, (btn, lbl) in enumerate(columns):
            actions.addWidget(btn, 0, col)
            actions.addWidget(lbl, 1, col)
            actions.setColumnStretch(col, 1)

        root.addWidget(self.actions_box)

        # Secondary tools row, laid out as button + caption to match the grid above.
        extras = QtWidgets.QGridLayout()
        extras.setHorizontalSpacing(8)
        extras.setVerticalSpacing(2)

        self.send_bin_button = QtWidgets.QPushButton()
        self.send_bin_button.setEnabled(False)
        self.decode_stack_button = QtWidgets.QPushButton()
        self.btn_clean = QtWidgets.QPushButton()

        self.send_bin_desc = make_muted_label()
        self.decode_stack_desc = make_muted_label()
        self.desc_clean = make_muted_label()

        extra_columns = [
            (self.send_bin_button, self.send_bin_desc),
            (self.decode_stack_button, self.decode_stack_desc),
            (self.btn_clean, self.desc_clean),
        ]
        for col, (btn, lbl) in enumerate(extra_columns):
            extras.addWidget(btn, 0, col)
            extras.addWidget(lbl, 1, col)
            extras.setColumnStretch(col, 1)

        root.addLayout(extras)

        root.addStretch(1)

    # -------------------------------------------------------------- wiring
    def _wire_signals(self) -> None:
        self.project_dir_browse.clicked.connect(self._on_project_browse)
        self.project_dir_edit.editingFinished.connect(self._on_project_text_committed)
        self.known_devices_refresh.clicked.connect(self._refresh_known_devices)
        self.known_devices_combo.currentIndexChanged.connect(self._on_known_device_pick)
        self.yaml_browse.clicked.connect(self._on_yaml_browse)
        self.yaml_edit.editingFinished.connect(self._on_yaml_text_committed)

        self.btn_validate.clicked.connect(lambda: self._run_action("config"))
        self.btn_compile.clicked.connect(lambda: self._run_action("compile"))
        self.btn_upload.clicked.connect(lambda: self._run_action("upload"))
        self.btn_run.clicked.connect(lambda: self._run_action("run"))
        self.btn_logs.clicked.connect(lambda: self._run_action("logs"))
        self.btn_clean.clicked.connect(lambda: self._run_action("clean"))

        self.send_bin_button.clicked.connect(self._on_send_bin_clicked)
        self.decode_stack_button.clicked.connect(self._on_decode_stack_clicked)
        self.quick_view_button.clicked.connect(self._on_quick_view_clicked)

        self.flash_worker.finished_with_code.connect(self._on_worker_finished)

        self.i18n.language_changed.connect(lambda _code: self.retranslate_ui())

    # --------------------------------------------------------- translation
    def retranslate_ui(self) -> None:
        tr = self.i18n.tr
        self.project_box.setTitle(tr("esphome.project_section"))
        self.actions_box.setTitle(tr("esphome.actions_label"))

        self.project_dir_label.setText(tr("esphome.project_dir") + ":")
        self.project_dir_edit.setPlaceholderText(tr("esphome.project_dir_placeholder"))
        self.project_dir_browse.setText(tr("firmware.browse"))

        self.known_devices_label.setText(tr("esphome.known_devices") + ":")
        self.known_devices_refresh.setText(tr("usb.refresh"))

        self.yaml_label.setText(tr("esphome.yaml") + ":")
        self.yaml_edit.setPlaceholderText(tr("esphome.yaml_placeholder"))
        self.yaml_browse.setText(tr("firmware.browse"))

        self.btn_validate.setText(tr("esphome.action.validate"))
        self.btn_compile.setText(tr("esphome.action.compile"))
        self.btn_upload.setText(tr("esphome.action.upload"))
        self.btn_run.setText(tr("esphome.action.run"))
        self.btn_logs.setText(tr("esphome.action.logs"))
        self.btn_clean.setText(tr("esphome.action.clean"))
        self.send_bin_button.setText(tr("esphome.action.send_to_bin"))
        self.decode_stack_button.setText(tr("esphome.action.decode_stack"))
        self._refresh_quick_view_label()

        button_caption_pairs = [
            (self.btn_validate, self.desc_validate, "esphome.action.validate.desc"),
            (self.btn_compile, self.desc_compile, "esphome.action.compile.desc"),
            (self.btn_upload, self.desc_upload, "esphome.action.upload.desc"),
            (self.btn_run, self.desc_run, "esphome.action.run.desc"),
            (self.btn_logs, self.desc_logs, "esphome.action.logs.desc"),
            (self.btn_clean, self.desc_clean, "esphome.action.clean.desc"),
            (self.send_bin_button, self.send_bin_desc, "esphome.action.send_to_bin.desc"),
            (self.decode_stack_button, self.decode_stack_desc, "esphome.action.decode_stack.desc"),
        ]
        for btn, lbl, key in button_caption_pairs:
            text = tr(key)
            lbl.setText(text)
            btn.setToolTip(text)

        self.quick_view_button.setToolTip(tr("esphome.action.quick_view.desc"))

        combo = self.known_devices_combo
        if combo.count() == 0:
            combo.addItem(tr("esphome.known_devices_none"), userData=None)
        elif combo.itemData(0) is None:
            placeholder_key = (
                "esphome.pick_known_device"
                if combo.count() > 1
                else "esphome.known_devices_none"
            )
            combo.setItemText(0, tr(placeholder_key))

    # ----------------------------------------------------------- callbacks
    @staticmethod
    def _commit_edit(
        edit: QtWidgets.QLineEdit, current: str, setter: object
    ) -> None:
        path = edit.text().strip()
        if path and path != current:
            setter(path)  # type: ignore[operator]

    def _on_project_browse(self) -> None:
        start_dir = self._project_dir or str(Path.home())
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, self.i18n.tr("esphome.project_dir_dialog"), start_dir
        )
        if path:
            self._set_project_dir(path)

    def _on_project_text_committed(self) -> None:
        self._commit_edit(self.project_dir_edit, self._project_dir, self._set_project_dir)

    def _set_project_dir(self, path: str) -> None:
        self._project_dir = path
        self.project_dir_edit.setText(path)
        self.log.emit(self.i18n.tr("esphome.project_dir_set", path=path))
        self._refresh_known_devices()

    def _refresh_known_devices(self) -> None:
        if not self._project_dir:
            self.log.emit(self.i18n.tr("esphome.no_project_dir"))
            return
        self._known_devices = discover_known_devices(self._project_dir)
        self.known_devices_combo.blockSignals(True)
        self.known_devices_combo.clear()
        if not self._known_devices:
            self.known_devices_combo.addItem(
                self.i18n.tr("esphome.known_devices_none"), userData=None
            )
            self.log.emit(self.i18n.tr("esphome.no_devices_found"))
        else:
            self.known_devices_combo.addItem(
                self.i18n.tr("esphome.pick_known_device"), userData=None
            )
            for dev in self._known_devices:
                self.known_devices_combo.addItem(dev.display(), userData=dev.name)
            self.log.emit(
                self.i18n.tr("esphome.found_devices", count=len(self._known_devices))
            )
        self.known_devices_combo.blockSignals(False)

    def _on_known_device_pick(self, idx: int) -> None:
        if idx <= 0:
            return
        name = self.known_devices_combo.itemData(idx)
        if not name:
            return
        device = next((d for d in self._known_devices if d.name == name), None)
        if device is None:
            return
        if device.yaml_path:
            self._set_yaml(device.yaml_path)
        else:
            self.log.emit(
                self.i18n.tr("esphome.no_yaml_for_device", name=device.name)
            )
        if device.address:
            self.device_selector.set_host(device.address)
            self.log.emit(
                self.i18n.tr(
                    "esphome.device_picked", name=device.name, address=device.address
                )
            )
        if device.firmware_bin_path:
            self._last_built_bin = device.firmware_bin_path
            self.send_bin_button.setEnabled(True)
            self.log.emit(
                self.i18n.tr("esphome.bin_detected", path=device.firmware_bin_path)
            )

    def _on_yaml_browse(self) -> None:
        start_dir = (
            str(Path(self._yaml_path).parent)
            if self._yaml_path
            else self._project_dir or str(Path.home())
        )
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            self.i18n.tr("esphome.yaml_dialog_title"),
            start_dir,
            self.i18n.tr("esphome.yaml_filter"),
        )
        if path:
            self._set_yaml(path)

    def _on_yaml_text_committed(self) -> None:
        self._commit_edit(self.yaml_edit, self._yaml_path, self._set_yaml)

    def _set_yaml(self, path: str) -> None:
        self._yaml_path = path
        self.yaml_edit.setText(path)
        if not self._project_dir:
            parent = str(Path(path).parent)
            self.project_dir_edit.setText(parent)
            self._project_dir = parent
        self.quick_view_button.setEnabled(bool(path) and Path(path).is_file())
        self.log.emit(self.i18n.tr("esphome.yaml_selected", path=path))

    # ---------------------------------------------------------- run action
    def _run_action(self, subcommand: str) -> None:
        if self.flash_worker.is_running():
            self.log.emit(self.i18n.tr("flash.in_progress"))
            return

        yaml = self._yaml_path or self.yaml_edit.text().strip()
        if not yaml:
            self.log.emit(self.i18n.tr("esphome.yaml_missing"))
            return
        if not Path(yaml).is_file():
            self.log.emit(self.i18n.tr("firmware.not_found", path=yaml))
            return

        device: str | None = None
        if subcommand in DEVICE_BOUND_ACTIONS:
            device = self._device_argument()
            if not device:
                self.log.emit(
                    self.i18n.tr("esphome.no_device_for_action", action=subcommand)
                )
                return
            if self.device_selector.current_mode() == MODE_IP:
                self.device_selector.preflight_ota(device)

        if subcommand in VALIDATING_ACTIONS:
            self._maybe_warn_verbose_logger(yaml)

        cwd = self._project_dir or str(Path(yaml).parent)
        self.log.emit(
            self.i18n.tr("esphome.starting", subcommand=subcommand, yaml=yaml)
        )
        ok = self.flash_worker.start_esphome(subcommand, yaml, device=device, cwd=cwd)
        if not ok:
            self.log.emit(
                self.i18n.tr(
                    "flash.tool_missing", tool="esphome", package="esphome"
                )
            )
            return

        self._last_subcommand = subcommand
        self.flash_state_changed.emit(True)

    def _maybe_warn_verbose_logger(self, yaml_path: str) -> None:
        """Warn once if the YAML sets logger level to VERY_VERBOSE.

        That level can slow the device and break connectivity; recommended
        only for short debugging sessions.
        """
        try:
            text = Path(yaml_path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return
        if _VERY_VERBOSE_RE.search(text):
            self.log.emit(self.i18n.tr("flash.hint.logger_verbose"))

    def _device_argument(self) -> str:
        """Value passed to `esphome --device`: serial port or OTA host."""
        if self.device_selector.current_mode() == MODE_USB:
            return self.device_selector.current_device()
        return self.device_selector.current_host()

    # ---------------------------------------------------------- on-finished
    def _on_worker_finished(self, code: int) -> None:
        if code != 0 or not self._last_subcommand:
            self._last_subcommand = ""
            return

        if self._last_subcommand in ("compile", "run"):
            built = self._detect_build_artifact()
            if built:
                self._last_built_bin = built
                self.send_bin_button.setEnabled(True)
                self.log.emit(self.i18n.tr("esphome.bin_detected", path=built))
        self._last_subcommand = ""

    def _detect_build_artifact(self) -> str:
        """Newest `firmware.bin` from the selected device manifest or the build tree."""
        idx = self.known_devices_combo.currentIndex()
        name = self.known_devices_combo.itemData(idx) if idx > 0 else None
        if name:
            device = next((d for d in self._known_devices if d.name == name), None)
            if device and device.firmware_bin_path and Path(device.firmware_bin_path).is_file():
                return device.firmware_bin_path

        if not self._project_dir:
            return ""
        build_root = Path(self._project_dir) / ".esphome" / "build"
        if not build_root.is_dir():
            return ""
        candidates = sorted(build_root.glob("*/.pioenvs/*/firmware.bin"))
        if not candidates:
            return ""
        best: Path | None = None
        best_mtime: float = -1
        for p in candidates:
            try:
                mt = p.stat().st_mtime
            except OSError:
                continue
            if mt > best_mtime:
                best_mtime = mt
                best = p
        return str(best) if best else ""

    def _on_send_bin_clicked(self) -> None:
        if not self._last_built_bin:
            self.log.emit(self.i18n.tr("esphome.no_bin_available"))
            return
        self.send_bin_to_bin_tab.emit(self._last_built_bin)

    def _on_decode_stack_clicked(self) -> None:
        dialog = StackDecoderDialog(self.i18n, self._project_dir, parent=self)
        dialog.exec()

    def _on_quick_view_clicked(self) -> None:
        if not self._yaml_path or not Path(self._yaml_path).is_file():
            self.log.emit(self.i18n.tr("esphome.yaml_missing"))
            self.quick_view_button.setChecked(False)
            return
        self.quick_view_requested.emit(self._yaml_path)

    def _refresh_quick_view_label(self) -> None:
        tr = self.i18n.tr
        key = "esphome.action.quick_view.close" if self.quick_view_button.isChecked() \
            else "esphome.action.quick_view"
        self.quick_view_button.setText(tr(key))

    def set_quick_view_state(self, open_: bool) -> None:
        """Sync toggle button to match the side panel visibility."""
        if self.quick_view_button.isChecked() != open_:
            self.quick_view_button.blockSignals(True)
            self.quick_view_button.setChecked(open_)
            self.quick_view_button.blockSignals(False)
        self._refresh_quick_view_label()

    # -------------------------------------------------------- public hooks
    def set_project_dir(self, path: str) -> None:
        if path:
            self._set_project_dir(path)

    def project_dir(self) -> str:
        return self._project_dir

    def current_yaml(self) -> str:
        return self._yaml_path
