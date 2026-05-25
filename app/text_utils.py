"""Text helpers for console output: ANSI stripping + log level classification."""
from __future__ import annotations

import re

# Matches CSI (`ESC [ ...`), OSC (`ESC ] ... BEL/ST`) and Fe (`ESC <char>`) escapes.
_ANSI_RE = re.compile(
    r"\x1B(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1B]*(?:\x07|\x1B\\)|[@-Z\\-_])"
)


def strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences."""
    return _ANSI_RE.sub("", text)


# Log-level tags. Mapped to QColors by main_window.LEVEL_COLORS.
LEVEL_DEFAULT = "default"
LEVEL_DIM = "dim"
LEVEL_CMD = "cmd"
LEVEL_WARN = "warn"
LEVEL_ERROR = "error"
LEVEL_SUCCESS = "success"
LEVEL_CONFIG = "config"
LEVEL_MARKER = "marker"

_LEVEL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (LEVEL_ERROR, re.compile(
        r"\[(E|F)\]\[|\bERROR\b|\bFAILED\b|\bTraceback\b|\bExit code [1-9]|"
        r"\*\*\* CRASH|\bBacktrace:\s+0x|\btask_wdt:|\bGuru Meditation|"
        r"\babort\(\) was called|\bRebooting\.\.\."
    )),
    (LEVEL_WARN, re.compile(
        r"\[W\]\[|\bWARNING\b|\bWarning:|took a long time for an operation"
    )),
    (LEVEL_SUCCESS, re.compile(
        r"\bSuccessfully\b|\[SUCCESS\]|\bOTA successful\b|"
        r"\bexit code 0\b|finished_ok"
    )),
    (LEVEL_CONFIG, re.compile(r"\[C\]\[")),
    (LEVEL_DIM, re.compile(r"\[D\]\[|\[V\]\[")),
    (LEVEL_MARKER, re.compile(r"^=+ .* =+$")),
    (LEVEL_CMD, re.compile(r"^\$ ")),
)


def classify_line(line: str) -> str:
    """Return a log-level tag for a single console line."""
    if not line.strip():
        return LEVEL_DEFAULT
    for level, pattern in _LEVEL_PATTERNS:
        if pattern.search(line):
            return level
    return LEVEL_DEFAULT
