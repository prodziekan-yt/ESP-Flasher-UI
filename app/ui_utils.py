"""Shared UI helper widgets."""
from __future__ import annotations

from PyQt6 import QtCore, QtWidgets


def make_muted_label(word_wrap: bool = True) -> QtWidgets.QLabel:
    """Centered caption label, palette(mid) color, 85% of the default font size."""
    lbl = QtWidgets.QLabel()
    lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet("color: palette(mid);")
    font = lbl.font()
    font.setPointSizeF(max(font.pointSizeF() * 0.85, 8.0))
    lbl.setFont(font)
    lbl.setWordWrap(word_wrap)
    return lbl
