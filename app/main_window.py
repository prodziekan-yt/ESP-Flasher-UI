"""Top-level QMainWindow.

Composes the language switcher, shared device selector, tab widget
(`FlashEsphomeTab` + `FlashBinTab`), global action row and console.
Tabs emit `log` and `flash_state_changed`; this class forwards them
into the console and toggles the Stop button.
"""
from __future__ import annotations

import os
import platform
from datetime import datetime

from PyQt6 import QtCore, QtGui, QtWidgets

from . import ICON_PATH, __version__
from .device_discovery import list_serial_devices
from .device_selector import DeviceSelector, MODE_USB
from .flash_bin_tab import FlashBinTab
from .flash_esphome_tab import FlashEsphomeTab
from .i18n import I18n
from .erase_flash_tab import EraseFlashTab
from .read_flash_tab import ReadFlashTab
from .text_utils import (
    LEVEL_CMD,
    LEVEL_CONFIG,
    LEVEL_DIM,
    LEVEL_ERROR,
    LEVEL_MARKER,
    LEVEL_SUCCESS,
    LEVEL_WARN,
    classify_line,
)
from .tool_checker import ToolStatus, check_all_tools
from .workers import FlashWorker
from .yaml_editor import YamlEditor


# Color palette for console line classification (readable on light + dark themes).
LEVEL_COLORS: dict[str, QtGui.QColor] = {
    LEVEL_DIM: QtGui.QColor(140, 140, 140),
    LEVEL_CMD: QtGui.QColor(86, 156, 214),
    LEVEL_WARN: QtGui.QColor(214, 153, 0),
    LEVEL_ERROR: QtGui.QColor(220, 50, 47),
    LEVEL_SUCCESS: QtGui.QColor(63, 150, 63),
    LEVEL_CONFIG: QtGui.QColor(70, 130, 130),
    LEVEL_MARKER: QtGui.QColor(38, 139, 210),
}
TIMESTAMP_COLOR = QtGui.QColor(140, 140, 140)


_DECODE_FOUND_TRIGGER = "Found stack trace! Trying to decode it"
_DECODE_OK_TRIGGER = "WARNING Decoded "

_SIMPLE_HINTS: tuple[tuple[str, tuple[str, ...], str, bool], ...] = (
    # (flag_attr, triggers, i18n_key, case_insensitive)
    (
        "_crash_hint_shown",
        ("*** CRASH DETECTED", "Guru Meditation", "abort() was called",
         "task_wdt:", "Backtrace:"),
        "flash.hint.crash_detected",
        False,
    ),
    (
        "_long_op_hint_shown",
        ("took a long time for an operation",),
        "flash.hint.long_op",
        False,
    ),
)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, i18n: I18n) -> None:
        super().__init__()
        self.i18n = i18n
        self.flash_worker = FlashWorker(self)
        self._dialout_hint_shown: bool = False
        self._crash_hint_shown: bool = False
        self._long_op_hint_shown: bool = False
        self._decode_armed: bool = False
        self._decode_hint_shown: bool = False
        self._user_stopped: bool = False

        if ICON_PATH.is_file():
            self.setWindowIcon(QtGui.QIcon(str(ICON_PATH)))

        self._build_ui()
        self._wire_signals()
        self._populate_languages()
        self.retranslate_ui()

        self._log(self.i18n.tr("console.started"))
        self.device_selector.refresh_usb_devices()
        self._log(self.i18n.tr("console.ready"))

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        outer = QtWidgets.QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal, central)
        self.splitter.setChildrenCollapsible(False)
        outer.addWidget(self.splitter)

        main_panel = QtWidgets.QWidget(self.splitter)
        root = QtWidgets.QVBoxLayout(main_panel)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)
        self.splitter.addWidget(main_panel)

        # ---- header
        header = QtWidgets.QHBoxLayout()
        self.title_label = QtWidgets.QLabel()
        font = self.title_label.font()
        font.setPointSizeF(font.pointSizeF() + 3)
        font.setBold(True)
        self.title_label.setFont(font)
        header.addWidget(self.title_label)
        header.addStretch(1)
        self.language_label = QtWidgets.QLabel()
        self.language_combo = QtWidgets.QComboBox()
        self.language_combo.setMinimumWidth(140)
        self.about_button = QtWidgets.QPushButton()
        header.addWidget(self.language_label)
        header.addWidget(self.language_combo)
        header.addWidget(self.about_button)
        root.addLayout(header)

        self.subtitle_label = QtWidgets.QLabel()
        self.subtitle_label.setStyleSheet("color: palette(mid);")
        root.addWidget(self.subtitle_label)

        # ---- shared device selector
        self.device_selector = DeviceSelector(self.i18n)
        root.addWidget(self.device_selector)

        # ---- tabs
        self.tabs = QtWidgets.QTabWidget()
        self.flash_bin_tab = FlashBinTab(
            self.i18n, self.flash_worker, self.device_selector
        )
        self.flash_esphome_tab = FlashEsphomeTab(
            self.i18n, self.flash_worker, self.device_selector
        )
        self.read_flash_tab = ReadFlashTab(
            self.i18n, self.flash_worker, self.device_selector
        )
        self.erase_flash_tab = EraseFlashTab(
            self.i18n, self.flash_worker, self.device_selector
        )
        self.tabs.addTab(self.flash_esphome_tab, "")
        self.tabs.addTab(self.flash_bin_tab, "")
        self.tabs.addTab(self.read_flash_tab, "")
        self.tabs.addTab(self.erase_flash_tab, "")
        root.addWidget(self.tabs)

        # ---- global actions
        actions = QtWidgets.QHBoxLayout()
        self.stop_button = QtWidgets.QPushButton()
        self.stop_button.setEnabled(False)
        self.diag_button = QtWidgets.QPushButton()
        self.reset_button = QtWidgets.QPushButton()
        self.clear_button = QtWidgets.QPushButton()
        actions.addWidget(self.stop_button)
        actions.addStretch(1)
        actions.addWidget(self.reset_button)
        actions.addWidget(self.diag_button)
        actions.addWidget(self.clear_button)
        root.addLayout(actions)

        # ---- console
        self.console_label = QtWidgets.QLabel()
        root.addWidget(self.console_label)
        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        mono = QtGui.QFont("Monospace")
        mono.setStyleHint(QtGui.QFont.StyleHint.TypeWriter)
        self.console.setFont(mono)
        self.console.setMinimumHeight(220)
        root.addWidget(self.console, 1)

        self.yaml_editor = YamlEditor(self.i18n, parent=self.splitter)
        self.splitter.addWidget(self.yaml_editor)
        self.yaml_editor.hide()
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)

        self.resize(820, 880)

    # ------------------------------------------------------------- signals
    def _wire_signals(self) -> None:
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)

        self.device_selector.mode_changed.connect(self._on_mode_changed)
        self.device_selector.log.connect(self._log)

        self.flash_bin_tab.log.connect(self._log)
        self.flash_bin_tab.flash_state_changed.connect(self._on_flash_state_changed)

        self.flash_esphome_tab.log.connect(self._log)
        self.flash_esphome_tab.flash_state_changed.connect(self._on_flash_state_changed)
        self.flash_esphome_tab.send_bin_to_bin_tab.connect(self._on_send_bin_to_bin_tab)
        self.flash_esphome_tab.quick_view_requested.connect(self._on_quick_view_requested)

        self.read_flash_tab.log.connect(self._log)
        self.read_flash_tab.flash_state_changed.connect(self._on_flash_state_changed)
        self.erase_flash_tab.log.connect(self._log)
        self.erase_flash_tab.flash_state_changed.connect(self._on_flash_state_changed)

        self.yaml_editor.log.connect(self._log)
        self.yaml_editor.close_requested.connect(self._on_editor_close)

        self.flash_worker.line.connect(self._on_flash_line)
        self.flash_worker.finished_with_code.connect(self._on_flash_finished)
        self.flash_worker.live_logs_started.connect(self._on_live_logs_started)

        self.stop_button.clicked.connect(self._on_stop_clicked)
        self.diag_button.clicked.connect(self._on_diagnostics_clicked)
        self.reset_button.clicked.connect(self._on_reset_clicked)
        self.clear_button.clicked.connect(self._on_clear_clicked)
        self.about_button.clicked.connect(self._on_about_clicked)

        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.i18n.language_changed.connect(lambda _code: self.retranslate_ui())

    # --------------------------------------------------------- translation
    def _populate_languages(self) -> None:
        self.language_combo.blockSignals(True)
        self.language_combo.clear()
        current_index = 0
        for idx, (code, name) in enumerate(self.i18n.available()):
            self.language_combo.addItem(name, userData=code)
            if code == self.i18n.current():
                current_index = idx
        self.language_combo.setCurrentIndex(current_index)
        self.language_combo.blockSignals(False)

    def retranslate_ui(self) -> None:
        tr = self.i18n.tr
        self.setWindowTitle(tr("app.title"))
        self.title_label.setText(tr("app.title"))
        self.subtitle_label.setText(tr("app.subtitle"))
        self.language_label.setText(tr("language.label") + ":")
        self.about_button.setText(tr("actions.about"))

        self.tabs.setTabText(0, tr("tabs.esphome.title"))
        self.tabs.setTabText(1, tr("tabs.bin.title"))
        self.tabs.setTabText(2, tr("tabs.read.title"))
        self.tabs.setTabText(3, tr("tabs.erase.title"))

        self.stop_button.setText(tr("actions.stop"))
        self.diag_button.setText(tr("actions.diagnostics"))
        self.reset_button.setText(tr("actions.reset"))
        self.reset_button.setToolTip(tr("actions.reset.desc"))
        self.clear_button.setText(tr("actions.clear_console"))
        self.console_label.setText(tr("console.label") + ":")

    # ----------------------------------------------------- log + callbacks
    _APP_PREFIX = ">> "

    def _log(self, message: str, internal: bool = True) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        level = classify_line(message)
        cursor = self.console.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        if not self.console.document().isEmpty():
            cursor.insertText("\n")
        ts_fmt = QtGui.QTextCharFormat()
        ts_fmt.setForeground(TIMESTAMP_COLOR)
        cursor.insertText(f"[{stamp}] ", ts_fmt)
        if internal:
            cursor.insertText(self._APP_PREFIX, ts_fmt)
        msg_fmt = QtGui.QTextCharFormat()
        color = LEVEL_COLORS.get(level)
        if color is not None:
            msg_fmt.setForeground(color)
        cursor.insertText(message, msg_fmt)
        sb = self.console.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_language_changed(self, _idx: int) -> None:
        code = self.language_combo.currentData()
        if not code:
            return
        if self.i18n.set_language(code):
            name = self.language_combo.currentText()
            self._log(self.i18n.tr("console.language_changed", language=name))

    def _on_mode_changed(self, mode: str) -> None:
        display = self.i18n.tr("mode.usb") if mode == MODE_USB else self.i18n.tr("mode.ip")
        self._log(self.i18n.tr("console.mode_changed", mode=display))

    def _on_tab_changed(self, idx: int) -> None:
        title = self.tabs.tabText(idx)
        self._log(self.i18n.tr("console.tab_changed", tab=title))

    def _on_flash_state_changed(self, running: bool) -> None:
        self.stop_button.setEnabled(running)
        if running:
            self._dialout_hint_shown = False
            self._crash_hint_shown = False
            self._long_op_hint_shown = False
            self._decode_armed = False
            self._decode_hint_shown = False
            self._user_stopped = False

    def _on_flash_line(self, line: str) -> None:
        self._log(line, internal=False)
        self._maybe_emit_hints(line)

    def _maybe_emit_hints(self, line: str) -> None:
        self._check_dialout_hint(line)
        self._check_simple_hints(line)
        self._check_decode_hint(line)

    def _check_dialout_hint(self, line: str) -> None:
        if self._dialout_hint_shown:
            return
        lowered = line.lower()
        if not (
            any(t in lowered for t in ("dialout", "do not have read or write permission"))
            or ("permission denied" in lowered
                and any(f in lowered for f in ("/dev/tty", "ttyacm", "ttyusb", "uucp")))
        ):
            return
        self._dialout_hint_shown = True
        self._log(self.i18n.tr("flash.hint.dialout"))

    def _check_simple_hints(self, line: str) -> None:
        for flag_attr, triggers, i18n_key, case_insensitive in _SIMPLE_HINTS:
            if getattr(self, flag_attr):
                continue
            haystack = line.lower() if case_insensitive else line
            if any(t in haystack for t in triggers):
                setattr(self, flag_attr, True)
                self._log(self.i18n.tr(i18n_key))

    def _check_decode_hint(self, line: str) -> None:
        # "Found stack trace! Trying to decode it" arms the watch; a follow-up
        # "WARNING Decoded ..." means success, anything else means missing ELF.
        if self._decode_hint_shown:
            return
        if _DECODE_FOUND_TRIGGER in line:
            self._decode_armed = True
            return
        if self._decode_armed and _DECODE_OK_TRIGGER in line:
            self._decode_armed = False
            return
        if self._decode_armed and line.strip() and _DECODE_OK_TRIGGER not in line:
            self._decode_armed = False
            self._decode_hint_shown = True
            self._log(self.i18n.tr("flash.hint.decode_no_elf"))

    def _on_live_logs_started(self) -> None:
        self._log(self.i18n.tr("console.live_logs_hint"))

    def _on_stop_clicked(self) -> None:
        if not self.flash_worker.is_running():
            return
        op_label = self._operation_label(self.flash_worker.operation)
        self._log(self.i18n.tr("op.stopped", op=op_label))
        # Flag must be set before stop(): waitForFinished() pumps events and
        # can fire _on_flash_finished synchronously.
        self._user_stopped = True
        self.flash_worker.stop()

    def _on_flash_finished(self, code: int) -> None:
        self.stop_button.setEnabled(False)
        if self._user_stopped:
            self._user_stopped = False
            return
        op_label = self._operation_label(self.flash_worker.operation)
        if code == 0:
            self._log(self.i18n.tr("op.finished_ok", op=op_label))
        else:
            self._log(self.i18n.tr("op.finished_err", op=op_label, code=code))

    def _operation_label(self, op: str) -> str:
        """Translate a FlashWorker operation tag to a localized label."""
        tr = self.i18n.tr
        if op == "flash":
            return tr("op.name.flash")
        if op == "ota":
            return tr("op.name.ota")
        if op == "read_flash":
            return tr("op.name.read_flash")
        if op == "flash_id":
            return tr("op.name.flash_id")
        if op == "reset":
            return tr("op.name.reset")
        if op == "erase_flash":
            return tr("op.name.erase_flash")
        if op.startswith("esphome."):
            sub = op.split(".", 1)[1]
            key = f"op.name.esphome.{sub}"
            label = tr(key)
            if label != key:
                return label
            return tr("op.name.esphome.generic", sub=sub)
        return tr("op.name.unknown")

    def _on_clear_clicked(self) -> None:
        self.console.clear()
        self._log(self.i18n.tr("console.cleared"))

    def _on_about_clicked(self) -> None:
        tr = self.i18n.tr
        body = (
            f"<h3>{tr('app.title')}</h3>"
            f"<p>{tr('about.version', version=__version__)}</p>"
            f"<p>{tr('about.description')}</p>"
            f"<p><b>{tr('about.author')}:</b> Prodziekan<br>"
            f"<b>{tr('about.email')}:</b> "
            f"<a href='mailto:prodziekan.yt@gmail.com'>prodziekan.yt@gmail.com</a><br>"
            f"<b>{tr('about.website')}:</b> "
            f"<a href='https://prodziekan-yt.github.io'>prodziekan-yt.github.io</a><br>"
            f"<b>{tr('about.youtube')}:</b> "
            f"<a href='https://www.youtube.com/@Prodziekan'>youtube.com/@Prodziekan</a></p>"
            f"<p><small>{tr('about.license')}</small></p>"
        )
        QtWidgets.QMessageBox.about(self, tr("actions.about"), body)

    def _on_send_bin_to_bin_tab(self, path: str) -> None:
        self.flash_bin_tab.set_firmware_path(path)
        self.tabs.setCurrentWidget(self.flash_bin_tab)
        self._log(self.i18n.tr("esphome.bin_sent_to_bin_tab"))

    # ---------------------------------------------------- side editor toggle
    def _on_quick_view_requested(self, path: str) -> None:
        if not self.yaml_editor.isHidden():
            if path and path != self.yaml_editor.current_path():
                self.yaml_editor.load(path)
                self.flash_esphome_tab.set_quick_view_state(True)
                return
            self._on_editor_close()
            return
        if not self.yaml_editor.load(path):
            self.flash_esphome_tab.set_quick_view_state(False)
            return
        self.yaml_editor.show()
        self.flash_esphome_tab.set_quick_view_state(True)
        self._ensure_editor_width()

    def _on_editor_close(self) -> None:
        sizes = self.splitter.sizes()
        side_width = sizes[1] if len(sizes) >= 2 else 0
        self.yaml_editor.hide()
        self.flash_esphome_tab.set_quick_view_state(False)
        if side_width > 0 and not self.isMaximized() and not self.isFullScreen():
            self.resize(max(self.width() - side_width, 600), self.height())

    def _ensure_editor_width(self) -> None:
        """Allocate width to the side panel on first open."""
        sizes = self.splitter.sizes()
        if len(sizes) < 2 or sizes[1] > 0:
            return
        total = max(sum(sizes), self.width())
        side = min(560, max(420, total // 3))
        self.splitter.setSizes([max(total - side, 320), side])
        if self.width() < total + 80 and not self.isMaximized() and not self.isFullScreen():
            self.resize(min(self.width() + side + 40, 1600), self.height())

    def _format_tool_line(self, status: ToolStatus) -> str:
        tr = self.i18n.tr
        name = tr(status.name_key)
        detail = tr(status.detail_key, **status.detail_args)
        key = "tools.line_ok" if status.ok else "tools.line_missing"
        return tr(key, name=name, detail=detail)

    def _log_tools_check(self) -> None:
        """Tool-availability checklist with `[V]`/`[X]` markers and install hints."""
        tr = self.i18n.tr
        statuses = check_all_tools()
        self._log(tr("tools.header"))

        required = [s for s in statuses if s.required]
        optional = [s for s in statuses if not s.required]

        if required:
            self._log(tr("tools.section.required"))
            for status in required:
                self._log(self._format_tool_line(status))
        if optional:
            self._log(tr("tools.section.optional"))
            for status in optional:
                self._log(self._format_tool_line(status))

        missing = sum(1 for s in required if not s.ok)
        if missing == 0:
            self._log(tr("tools.summary_ok"))
        else:
            self._log(tr("tools.summary_missing", count=missing))
        self._log(tr("tools.footer"))

    def _on_diagnostics_clicked(self) -> None:
        tr = self.i18n.tr
        self._log(tr("diag.header"))
        self._log(tr("diag.platform", platform=platform.platform()))
        self._log(tr("diag.cwd", cwd=os.getcwd()))

        devices = list_serial_devices()
        self._log(tr("diag.serial_ports", count=len(devices)))
        for dev in devices:
            self._log(
                tr(
                    "diag.port_line",
                    device=dev.device,
                    description=dev.description,
                    hwid=dev.hwid,
                )
            )

        languages = [f"{code}({name})" for code, name in self.i18n.available()]
        self._log(tr("diag.language_loaded", list=", ".join(languages)))
        self._log(tr("diag.current_language", code=self.i18n.current()))
        mode = self.device_selector.current_mode()
        mode_disp = tr("mode.usb") if mode == MODE_USB else tr("mode.ip")
        self._log(tr("diag.current_mode", mode=mode_disp))

        bin_path = self.flash_bin_tab.firmware_path()
        if bin_path:
            self._log(tr("diag.firmware_path", path=bin_path))
        else:
            self._log(tr("diag.firmware_none"))

        proj = self.flash_esphome_tab.project_dir()
        if proj:
            self._log(tr("diag.esphome_project", path=proj))
        else:
            self._log(tr("diag.esphome_project_none"))

        self._log_tools_check()
        self._log(tr("diag.footer"))

    def _on_reset_clicked(self) -> None:
        """Hard-reset the ESP via DTR/RTS (USB only)."""
        tr = self.i18n.tr
        if self.flash_worker.is_running():
            self._log(tr("flash.in_progress"))
            return
        if self.device_selector.current_mode() != MODE_USB:
            self._log(tr("reset.usb_only"))
            return
        device = self.device_selector.current_device()
        if not device:
            self._log(tr("flash.no_device"))
            return
        baud = self.device_selector.current_baud()
        self._log(tr("reset.starting", device=device))
        ok = self.flash_worker.start_reset(device, baud=baud)
        if not ok:
            self._log(tr("flash.tool_missing", tool="esptool", package="esptool"))
            return
        self._on_flash_state_changed(True)
