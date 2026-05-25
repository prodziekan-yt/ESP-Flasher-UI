"""Inline YAML viewer/editor attached to the main window."""
from __future__ import annotations

import re
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets

from .i18n import I18n


def _fmt(color: str, *, bold: bool = False, italic: bool = False) -> QtGui.QTextCharFormat:
    f = QtGui.QTextCharFormat()
    f.setForeground(QtGui.QColor(color))
    if bold:
        f.setFontWeight(QtGui.QFont.Weight.Bold)
    if italic:
        f.setFontItalic(True)
    return f


class YamlHighlighter(QtGui.QSyntaxHighlighter):
    """Lightweight YAML highlighter - readable on light + dark themes."""

    _KEY = _fmt("#569cd6", bold=True)
    _STR = _fmt("#ce9178")
    _NUM = _fmt("#b5cea8")
    _CONST = _fmt("#c586c0")
    _ANCHOR = _fmt("#4ec9b0")
    _TAG = _fmt("#dcdcaa")
    _COMMENT = _fmt("#6a9955", italic=True)
    _MARKER = _fmt("#d16969", bold=True)

    _KEY_RE = re.compile(r"^(\s*(?:-\s+)?)([\w\-.][\w\-./]*)(\s*:)")
    _STR_RE = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\']|\'\')*\'')
    _CONST_RE = re.compile(r"\b(true|false|null|yes|no|on|off|~)\b", re.IGNORECASE)
    _NUM_RE = re.compile(
        r"(?<![\w.])-?(?:0x[0-9a-fA-F]+|\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\b"
    )
    _ANCHOR_RE = re.compile(r"[&*][\w\-]+")
    _TAG_RE = re.compile(r"!(?:!|<[^>]+>|[\w/.-]*)")
    _MARKER_RE = re.compile(r"^\s*-(?=\s|$)")
    _COMMENT_RE = re.compile(r"(^|\s)#.*$")

    def highlightBlock(self, text: str) -> None:
        for rx, fmt in (
            (self._STR_RE, self._STR),
            (self._CONST_RE, self._CONST),
            (self._NUM_RE, self._NUM),
            (self._ANCHOR_RE, self._ANCHOR),
            (self._TAG_RE, self._TAG),
            (self._MARKER_RE, self._MARKER),
        ):
            for m in rx.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)

        m = self._KEY_RE.match(text)
        if m:
            self.setFormat(m.start(2), m.end(2) - m.start(2), self._KEY)

        m = self._COMMENT_RE.search(text)
        if m:
            offset = 1 if m.group(1) else 0
            self.setFormat(m.start() + offset, m.end() - m.start() - offset, self._COMMENT)


class _YamlPlainTextEdit(QtWidgets.QPlainTextEdit):
    """Plain text edit that can overlay a U+2591 (░) glyph on every space."""

    _WS_GLYPH = "\u2591"
    _WS_COLOR = QtGui.QColor(128, 128, 128, 140)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._show_whitespace = False

    def set_show_whitespace(self, on: bool) -> None:
        if self._show_whitespace == on:
            return
        self._show_whitespace = on
        self.viewport().update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        if not self._show_whitespace:
            return
        painter = QtGui.QPainter(self.viewport())
        painter.setPen(self._WS_COLOR)
        painter.setFont(self.font())
        offset = self.contentOffset()
        bottom = event.rect().bottom()
        block = self.firstVisibleBlock()
        while block.isValid():
            geom = self.blockBoundingGeometry(block).translated(offset)
            if geom.top() > bottom:
                break
            if block.isVisible():
                layout = block.layout()
                text = block.text()
                if layout is not None:
                    for i, ch in enumerate(text):
                        if ch != " ":
                            continue
                        line = layout.lineForTextPosition(i)
                        if not line.isValid():
                            continue
                        x = line.cursorToX(i)[0] + offset.x()
                        y = geom.top() + line.y() + line.ascent()
                        painter.drawText(QtCore.QPointF(x, y), self._WS_GLYPH)
            block = block.next()
        painter.end()


class YamlEditor(QtWidgets.QWidget):
    """Side panel with a monospace text editor + find / replace / save."""

    close_requested = QtCore.pyqtSignal()
    log = QtCore.pyqtSignal(str)

    def __init__(
        self,
        i18n: I18n,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self._path: str = ""
        self._original_text: str = ""

        self._build_ui()
        self._wire_signals()
        self.retranslate_ui()
        self.i18n.language_changed.connect(lambda _c: self.retranslate_ui())

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        self.title_label = QtWidgets.QLabel()
        font = self.title_label.font()
        font.setBold(True)
        self.title_label.setFont(font)
        root.addWidget(self.title_label)

        search_row = QtWidgets.QHBoxLayout()
        self.find_input = QtWidgets.QLineEdit()
        self.find_input.setClearButtonEnabled(True)
        self.find_next_button = QtWidgets.QPushButton()
        search_row.addWidget(self.find_input, 2)
        search_row.addWidget(self.find_next_button)
        root.addLayout(search_row)

        replace_row = QtWidgets.QHBoxLayout()
        self.replace_input = QtWidgets.QLineEdit()
        self.replace_input.setClearButtonEnabled(True)
        self.replace_button = QtWidgets.QPushButton()
        self.replace_all_button = QtWidgets.QPushButton()
        replace_row.addWidget(self.replace_input, 2)
        replace_row.addWidget(self.replace_button)
        replace_row.addWidget(self.replace_all_button)
        root.addLayout(replace_row)

        mono = QtGui.QFont("Monospace")
        mono.setStyleHint(QtGui.QFont.StyleHint.TypeWriter)
        self.editor = _YamlPlainTextEdit()
        self.editor.setFont(mono)
        self.editor.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setTabStopDistance(
            QtGui.QFontMetricsF(mono).horizontalAdvance(" ") * 2
        )

        self.highlighter = YamlHighlighter(self.editor.document())

        root.addWidget(self.editor, 1)

        self.status_label = QtWidgets.QLabel()
        self.status_label.setStyleSheet("color: palette(mid);")
        root.addWidget(self.status_label)

        button_row = QtWidgets.QHBoxLayout()
        self.show_ws_checkbox = QtWidgets.QCheckBox()
        self.show_ws_checkbox.setChecked(False)
        button_row.addWidget(self.show_ws_checkbox)
        button_row.addStretch(1)
        self.save_button = QtWidgets.QPushButton()
        self.cancel_button = QtWidgets.QPushButton()
        button_row.addWidget(self.save_button)
        button_row.addWidget(self.cancel_button)
        root.addLayout(button_row)

    def _wire_signals(self) -> None:
        self.find_next_button.clicked.connect(self._on_find_next)
        self.find_input.returnPressed.connect(self._on_find_next)
        self.replace_button.clicked.connect(self._on_replace)
        self.replace_input.returnPressed.connect(self._on_replace)
        self.replace_all_button.clicked.connect(self._on_replace_all)
        self.save_button.clicked.connect(self._on_save)
        self.cancel_button.clicked.connect(self._on_cancel)
        self.show_ws_checkbox.toggled.connect(self.editor.set_show_whitespace)

    def retranslate_ui(self) -> None:
        tr = self.i18n.tr
        self.find_input.setPlaceholderText(tr("editor.find_placeholder"))
        self.replace_input.setPlaceholderText(tr("editor.replace_placeholder"))
        self.find_next_button.setText(tr("editor.find_next"))
        self.replace_button.setText(tr("editor.replace"))
        self.replace_all_button.setText(tr("editor.replace_all"))
        self.save_button.setText(tr("editor.save"))
        self.cancel_button.setText(tr("editor.cancel"))
        self.show_ws_checkbox.setText(tr("editor.show_whitespace"))
        self._refresh_title()

    def load(self, path: str) -> bool:
        """Load YAML file content; returns True on success."""
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            self.log.emit(self.i18n.tr("editor.load_failed", path=path, error=str(exc)))
            return False
        self._path = path
        self._original_text = text
        self.editor.setPlainText(text)
        self.editor.moveCursor(QtGui.QTextCursor.MoveOperation.Start)
        self._refresh_title()
        self._set_status("")
        return True

    def is_modified(self) -> bool:
        return self.editor.toPlainText() != self._original_text

    def current_path(self) -> str:
        return self._path

    def _refresh_title(self) -> None:
        tr = self.i18n.tr
        if not self._path:
            self.title_label.setText(tr("editor.title"))
            return
        name = Path(self._path).name
        marker = " *" if self.is_modified() else ""
        self.title_label.setText(f"{tr('editor.title')}: {name}{marker}")

    def _on_find_next(self) -> None:
        needle = self.find_input.text()
        if not needle:
            return
        found = self.editor.find(needle)
        if not found:
            cursor = self.editor.textCursor()
            cursor.movePosition(QtGui.QTextCursor.MoveOperation.Start)
            self.editor.setTextCursor(cursor)
            found = self.editor.find(needle)
        self._set_status(
            self.i18n.tr("editor.find_hit") if found else self.i18n.tr("editor.find_miss"),
            error=not found,
        )

    def _on_replace(self) -> None:
        needle = self.find_input.text()
        if not needle:
            return
        cursor = self.editor.textCursor()
        if cursor.hasSelection() and cursor.selectedText() == needle:
            cursor.insertText(self.replace_input.text())
        self._on_find_next()
        self._refresh_title()

    def _on_replace_all(self) -> None:
        needle = self.find_input.text()
        if not needle:
            return
        replacement = self.replace_input.text()
        text = self.editor.toPlainText()
        count = text.count(needle)
        if count == 0:
            self._set_status(self.i18n.tr("editor.find_miss"), error=True)
            return
        self.editor.setPlainText(text.replace(needle, replacement))
        self._set_status(self.i18n.tr("editor.replaced", n=count))
        self._refresh_title()

    def _on_save(self) -> None:
        if not self._path:
            self._set_status(self.i18n.tr("editor.no_yaml"), error=True)
            return
        text = self.editor.toPlainText()
        try:
            Path(self._path).write_text(text, encoding="utf-8")
        except OSError as exc:
            self._set_status(
                self.i18n.tr("editor.save_failed", error=str(exc)), error=True
            )
            return
        self._original_text = text
        self._refresh_title()
        self._set_status(self.i18n.tr("editor.saved"))
        self.log.emit(self.i18n.tr("editor.saved_log", path=self._path))

    def _on_cancel(self) -> None:
        if self.is_modified():
            tr = self.i18n.tr
            reply = QtWidgets.QMessageBox.question(
                self,
                tr("editor.title"),
                tr("editor.discard_prompt"),
                QtWidgets.QMessageBox.StandardButton.Discard
                | QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            if reply != QtWidgets.QMessageBox.StandardButton.Discard:
                return
            self.editor.setPlainText(self._original_text)
        self.close_requested.emit()

    def _set_status(self, text: str, *, error: bool = False) -> None:
        color = "#d62a2a" if error else "palette(mid)"
        self.status_label.setStyleSheet(f"color: {color};")
        self.status_label.setText(text)
