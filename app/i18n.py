"""i18n manager backed by `app/translations/<code>.json` files."""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal


TRANSLATIONS_DIR = Path(__file__).resolve().parent / "translations"
DEFAULT_LANGUAGE = "en"


def _sort_key(name: str) -> tuple[int, str, str]:
    """Sort key: Latin (diacritics-folded) first, then non-Latin scripts."""
    folded = "".join(
        ch for ch in unicodedata.normalize("NFKD", name)
        if not unicodedata.combining(ch)
    ).casefold()
    first = folded[:1]
    script = 0 if ("a" <= first <= "z") else 1
    return (script, folded, name.casefold())


class I18n(QObject):
    """Loaded translation packs; emits `language_changed` on switch."""

    language_changed = pyqtSignal(str)

    def __init__(self, translations_dir: Path = TRANSLATIONS_DIR) -> None:
        super().__init__()
        self._translations_dir = translations_dir
        self._packs: dict[str, dict[str, Any]] = {}
        self._current: str = DEFAULT_LANGUAGE
        self._load_all()
        if DEFAULT_LANGUAGE not in self._packs and self._packs:
            self._current = next(iter(self._packs))

    def _load_all(self) -> None:
        if not self._translations_dir.is_dir():
            return
        for path in sorted(self._translations_dir.glob("*.json")):
            try:
                with path.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            code = (data.get("_meta") or {}).get("code") or path.stem
            self._packs[code] = data

    def available(self) -> list[tuple[str, str]]:
        """`[(code, native_name), ...]` sorted by display name."""
        items: list[tuple[str, str]] = []
        for code, pack in self._packs.items():
            meta = pack.get("_meta") or {}
            name = meta.get("native_name") or meta.get("name") or code
            items.append((code, name))
        items.sort(key=lambda x: _sort_key(x[1]))
        return items

    def current(self) -> str:
        return self._current

    def set_language(self, code: str) -> bool:
        if code not in self._packs or code == self._current:
            return False
        self._current = code
        self.language_changed.emit(code)
        return True

    def tr(self, key: str, **kwargs: Any) -> str:
        """Translate `key`; falls back to English, then the raw key. Accepts `.format()` kwargs."""
        pack = self._packs.get(self._current) or {}
        value = pack.get(key)
        if value is None:
            fallback = self._packs.get(DEFAULT_LANGUAGE) or {}
            value = fallback.get(key, key)
        if kwargs:
            try:
                return str(value).format(**kwargs)
            except (KeyError, IndexError, ValueError):
                return str(value)
        return str(value)
