"""Inspect .BIN tab: plain text report + interactive coloured hex dump."""
from __future__ import annotations

from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets

from .bin_inspector import (
    BYTES_PER_HEX_LINE,
    BinReport,
    RegionSpan,
    format_plain_report,
    hex_dump_lines,
    parse_bin,
    region_spans,
)
from .i18n import I18n
from .ui_utils import make_muted_label


VIEW_PLAIN = "plain"
VIEW_INTERACTIVE = "interactive"

HEX_PREVIEW_BYTES = 1024

HEX_BYTE_COLUMN_START = 11
HEX_BYTE_COLUMN_STRIDE = 3


def _color(r: int, g: int, b: int, a: int) -> QtGui.QColor:
    c = QtGui.QColor(r, g, b)
    c.setAlpha(a)
    return c


_REGION_COLORS: dict[str, QtGui.QColor] = {
    "header":     _color(220, 80, 80, 70),
    "ext_header": _color(220, 140, 80, 70),
    "seg_header": _color(220, 200, 80, 70),
    "segment":    _color(80, 160, 220, 50),
    "app_desc":   _color(140, 80, 220, 80),
    "footer":     _color(80, 200, 140, 70),
}
_DEFAULT_REGION_COLOR = _color(150, 150, 150, 50)


class InspectBinTab(QtWidgets.QWidget):
    log = QtCore.pyqtSignal(str)

    def __init__(
        self,
        i18n: I18n,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self._bin_path: str = ""
        self._report: BinReport | None = None
        self._raw_data: bytes = b""
        self._line_offsets: list[int] = []

        self._build_ui()
        self._wire_signals()
        self.retranslate_ui()
        self._on_view_changed()

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(10)

        mono = QtGui.QFont("Monospace")
        mono.setStyleHint(QtGui.QFont.StyleHint.TypeWriter)

        self.input_box = QtWidgets.QGroupBox()
        grid = QtWidgets.QGridLayout(self.input_box)
        self.input_label = QtWidgets.QLabel()
        self.input_edit = QtWidgets.QLineEdit()
        self.input_edit.setReadOnly(True)
        self.browse_button = QtWidgets.QPushButton()
        self.parse_button = QtWidgets.QPushButton()
        grid.addWidget(self.input_label, 0, 0)
        grid.addWidget(self.input_edit, 0, 1)
        grid.addWidget(self.browse_button, 0, 2)
        grid.addWidget(self.parse_button, 0, 3)
        root.addWidget(self.input_box)

        self.view_box = QtWidgets.QGroupBox()
        vrow = QtWidgets.QHBoxLayout(self.view_box)
        self.view_label = QtWidgets.QLabel()
        self.view_plain_radio = QtWidgets.QRadioButton()
        self.view_plain_radio.setChecked(True)
        self.view_interactive_radio = QtWidgets.QRadioButton()
        vrow.addWidget(self.view_label)
        vrow.addWidget(self.view_plain_radio)
        vrow.addWidget(self.view_interactive_radio)
        vrow.addStretch(1)
        self.legend_label = make_muted_label()
        vrow.addWidget(self.legend_label)
        root.addWidget(self.view_box)

        self.stack = QtWidgets.QStackedWidget()
        self.plain_view = QtWidgets.QPlainTextEdit()
        self.plain_view.setReadOnly(True)
        self.plain_view.setFont(mono)
        self.stack.addWidget(self.plain_view)

        interactive = QtWidgets.QWidget()
        ilay = QtWidgets.QVBoxLayout(interactive)
        ilay.setContentsMargins(0, 0, 0, 0)
        self.interactive_view = QtWidgets.QPlainTextEdit()
        self.interactive_view.setReadOnly(True)
        self.interactive_view.setFont(mono)
        self.interactive_view.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        ilay.addWidget(self.interactive_view, 1)
        self.detail_label = QtWidgets.QLabel()
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet("padding: 6px; background: palette(alternate-base);")
        ilay.addWidget(self.detail_label)
        self.stack.addWidget(interactive)

        root.addWidget(self.stack, 1)

        self.status_label = make_muted_label()
        root.addWidget(self.status_label)

    def _wire_signals(self) -> None:
        self.browse_button.clicked.connect(self._on_browse)
        self.parse_button.clicked.connect(self._on_parse)
        self.view_plain_radio.toggled.connect(self._on_view_changed)
        self.view_interactive_radio.toggled.connect(self._on_view_changed)
        self.interactive_view.cursorPositionChanged.connect(self._on_hex_cursor_moved)

    def retranslate_ui(self) -> None:
        tr = self.i18n.tr
        self.input_box.setTitle(tr("inspect.input_section"))
        self.input_label.setText(tr("inspect.input_label") + ":")
        self.input_edit.setPlaceholderText(tr("inspect.input_placeholder"))
        self.browse_button.setText(tr("inspect.browse"))
        self.parse_button.setText(tr("inspect.parse"))
        self.view_box.setTitle(tr("inspect.view_section"))
        self.view_label.setText(tr("inspect.view_label") + ":")
        self.view_plain_radio.setText(tr("inspect.view.plain"))
        self.view_interactive_radio.setText(tr("inspect.view.interactive"))
        self.legend_label.setText(tr("inspect.legend"))
        self.status_label.setText(tr("inspect.status_idle"))
        self._refresh_views()

    def set_bin_path(self, path: str) -> None:
        """Load and parse `path` immediately."""
        if not path or not Path(path).is_file():
            return
        self._bin_path = path
        self.input_edit.setText(path)
        self._on_parse()

    def current_view(self) -> str:
        return VIEW_PLAIN if self.view_plain_radio.isChecked() else VIEW_INTERACTIVE

    # ------------------------------------------------------------- handlers
    def _on_browse(self) -> None:
        tr = self.i18n.tr
        start_dir = str(Path(self._bin_path).parent) if self._bin_path else str(Path.home())
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            tr("inspect.dialog_title"),
            start_dir,
            tr("inspect.dialog_filter"),
        )
        if not path:
            return
        self._bin_path = path
        self.input_edit.setText(path)

    def _on_parse(self) -> None:
        tr = self.i18n.tr
        if not self._bin_path or not Path(self._bin_path).is_file():
            self._emit_status(tr("inspect.no_file"))
            return
        try:
            self._raw_data = Path(self._bin_path).read_bytes()
            self._report = parse_bin(self._bin_path)
        except OSError as e:
            self._emit_status(tr("inspect.read_error", error=str(e)))
            self._report = None
            return
        size_kb = f"{self._report.file_size / 1024:.1f}"
        self._emit_status(tr("inspect.parsed", path=self._bin_path, kb=size_kb))
        self._refresh_views()

    def _emit_status(self, msg: str) -> None:
        self.status_label.setText(msg)
        self.log.emit(msg)

    def _on_view_changed(self) -> None:
        idx = 0 if self.view_plain_radio.isChecked() else 1
        self.stack.setCurrentIndex(idx)
        self.detail_label.setVisible(idx == 1)

    # ------------------------------------------------------------- rendering
    def _refresh_views(self) -> None:
        if self._report is None:
            self.plain_view.setPlainText(self.i18n.tr("inspect.no_report"))
            self.interactive_view.setPlainText("")
            self._line_offsets = []
            self.detail_label.setText(self.i18n.tr("inspect.detail_idle"))
            return
        self.plain_view.setPlainText(format_plain_report(self._report))
        self._render_interactive()

    def _render_interactive(self) -> None:
        if self._report is None or not self._raw_data:
            self.interactive_view.setPlainText("")
            self._line_offsets = []
            return
        lines = hex_dump_lines(self._raw_data, base_offset=0, max_bytes=HEX_PREVIEW_BYTES)
        self.interactive_view.setPlainText("\n".join(text for _, text in lines))
        self._line_offsets = [off for off, _ in lines]
        self._apply_region_tints(region_spans(self._report))
        self.detail_label.setText(self.i18n.tr("inspect.detail_idle"))

    def _apply_region_tints(self, spans: list[RegionSpan]) -> None:
        """Tint each hex line overlapping a region with its palette colour."""
        doc = self.interactive_view.document()
        selections: list[QtWidgets.QTextEdit.ExtraSelection] = []
        for span in spans:
            color = _REGION_COLORS.get(span.kind, _DEFAULT_REGION_COLOR)
            for idx, line_offset in enumerate(self._line_offsets):
                if span.end <= line_offset or span.start >= line_offset + BYTES_PER_HEX_LINE:
                    continue
                block = doc.findBlockByLineNumber(idx)
                if not block.isValid():
                    continue
                selections.append(_line_selection(block, color))
        self.interactive_view.setExtraSelections(selections)

    # ------------------------------------------------------------- cursor info
    def _on_hex_cursor_moved(self) -> None:
        if self._report is None or not self._raw_data:
            return
        cursor = self.interactive_view.textCursor()
        line_no = cursor.blockNumber()
        if line_no >= len(self._line_offsets):
            return
        line_offset = self._line_offsets[line_no]
        col = max(0, cursor.columnNumber() - HEX_BYTE_COLUMN_START)
        byte_in_line = min(col // HEX_BYTE_COLUMN_STRIDE, BYTES_PER_HEX_LINE - 1)
        self.detail_label.setText(self._describe_offset(line_offset + byte_in_line))

    def _describe_offset(self, offset: int) -> str:
        tr = self.i18n.tr
        if self._report is None or not self._raw_data or offset >= len(self._raw_data):
            return tr("inspect.detail_idle")
        region_name = self._region_name_for(offset)
        seg_info = self._segment_info_for(offset)
        base = tr(
            "inspect.detail_offset",
            offset=f"0x{offset:06X}",
            byte=f"0x{self._raw_data[offset]:02X}",
            region=region_name,
        )
        return base + (" - " + seg_info if seg_info else "")

    def _region_name_for(self, offset: int) -> str:
        if self._report is None:
            return "?"
        for span in region_spans(self._report):
            if span.start <= offset < span.end:
                return span.name
        return "?"

    def _segment_info_for(self, offset: int) -> str:
        if self._report is None:
            return ""
        for s in self._report.segments:
            if s.file_offset <= offset < s.file_offset + s.length:
                return self.i18n.tr(
                    "inspect.detail_segment",
                    index=s.index,
                    load=f"0x{s.load_addr + (offset - s.file_offset):08X}",
                    region=s.region,
                )
        return ""


def _line_selection(block: QtGui.QTextBlock, color: QtGui.QColor) -> QtWidgets.QTextEdit.ExtraSelection:
    """Full-line `ExtraSelection` painted with `color`."""
    sel = QtWidgets.QTextEdit.ExtraSelection()
    cursor = QtGui.QTextCursor(block)
    cursor.movePosition(
        QtGui.QTextCursor.MoveOperation.EndOfBlock,
        QtGui.QTextCursor.MoveMode.KeepAnchor,
    )
    sel.cursor = cursor
    fmt = QtGui.QTextCharFormat()
    fmt.setBackground(QtGui.QBrush(color))
    sel.format = fmt
    return sel
