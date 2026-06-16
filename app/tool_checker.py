"""Dependency checks rendered as a `[V]`/`[X]` checklist in the console."""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version as _pkg_version_raw


def pkg_version(pkg: str) -> str:
    """Installed version of a Python package, or empty string if missing."""
    try:
        return _pkg_version_raw(pkg)
    except PackageNotFoundError:
        return ""


@dataclass(frozen=True)
class ToolStatus:
    name_key: str
    ok: bool
    required: bool
    detail_key: str
    detail_args: dict[str, object] = field(default_factory=dict)


def check_all_tools() -> list[ToolStatus]:
    """Run every check; required entries appear first in the result."""
    return [
        _check_python(),
        _check_pyqt(),
        _check_esptool(),
        _check_pyserial(),
        _check_espota(),
        _check_esphome(),
        _check_dialout(),
    ]


def _check_python() -> ToolStatus:
    version = ".".join(str(p) for p in sys.version_info[:3])
    ok = sys.version_info >= (3, 10)
    return ToolStatus(
        name_key="tools.name.python",
        ok=ok,
        required=True,
        detail_key="tools.detail.python_ok" if ok else "tools.detail.python_old",
        detail_args={"version": version},
    )


def _check_pyqt() -> ToolStatus:
    try:
        from PyQt6 import QtCore
    except ImportError:
        return ToolStatus(
            name_key="tools.name.pyqt",
            ok=False,
            required=True,
            detail_key="tools.detail.missing_install",
            detail_args={"cmd": "pip install PyQt6"},
        )
    return ToolStatus(
        name_key="tools.name.pyqt",
        ok=True,
        required=True,
        detail_key="tools.detail.python_ok",
        detail_args={
            "version": f"{QtCore.PYQT_VERSION_STR} (Qt {QtCore.QT_VERSION_STR})"
        },
    )


def _check_pyserial() -> ToolStatus:
    ver = pkg_version("pyserial")
    if not ver:
        return ToolStatus(
            name_key="tools.name.pyserial",
            ok=False,
            required=True,
            detail_key="tools.detail.missing_install",
            detail_args={"cmd": "pip install pyserial"},
        )
    return ToolStatus(
        name_key="tools.name.pyserial",
        ok=True,
        required=True,
        detail_key="tools.detail.python_ok",
        detail_args={"version": ver},
    )


def _check_cli_tool(
    name_key: str,
    pkg: str,
    executables: tuple[str, ...],
    install_cmd: str,
    required: bool = True,
) -> ToolStatus:
    """Look up a CLI tool by PATH and report the matching pip package version."""
    path: str | None = None
    for exe in executables:
        path = shutil.which(exe)
        if path:
            break
    if not path:
        if required:
            return ToolStatus(
                name_key=name_key, ok=False, required=True,
                detail_key="tools.detail.missing_install",
                detail_args={"cmd": install_cmd},
            )
        return ToolStatus(
            name_key=name_key, ok=False, required=False,
            detail_key="tools.detail.missing_optional",
        )
    ver = pkg_version(pkg)
    if ver:
        return ToolStatus(
            name_key=name_key, ok=True, required=required,
            detail_key="tools.detail.found_with_version",
            detail_args={"version": ver, "path": path},
        )
    return ToolStatus(
        name_key=name_key, ok=True, required=required,
        detail_key="tools.detail.found_at",
        detail_args={"path": path},
    )


def _check_esptool() -> ToolStatus:
    return _check_cli_tool(
        "tools.name.esptool", "esptool",
        ("esptool", "esptool.py"), "pip install esptool",
    )


def _check_espota() -> ToolStatus:
    return _check_cli_tool(
        "tools.name.espota", "esphome",
        ("espota.py",), "pip install esphome", required=False,
    )


def _check_esphome() -> ToolStatus:
    return _check_cli_tool(
        "tools.name.esphome", "esphome",
        ("esphome",), "pip install esphome",
    )


def _check_dialout() -> ToolStatus:
    """`dialout` membership in `/etc/group` and the live process (catches stale sessions)."""
    import getpass

    user = getpass.getuser()
    try:
        import grp
    except ImportError:
        return ToolStatus(
            name_key="tools.name.dialout",
            ok=True,
            required=False,
            detail_key="tools.detail.missing_optional",
        )
    try:
        entry = grp.getgrnam("dialout")
    except KeyError:
        return ToolStatus(
            name_key="tools.name.dialout",
            ok=False,
            required=False,
            detail_key="tools.detail.dialout_missing",
            detail_args={"user": user, "cmd": "sudo usermod -aG dialout $USER"},
        )
    static_ok = user in entry.gr_mem
    try:
        effective_groups = os.getgroups()
    except OSError:
        effective_groups = []
    effective_ok = entry.gr_gid in effective_groups

    if static_ok and effective_ok:
        return ToolStatus(
            name_key="tools.name.dialout",
            ok=True,
            required=False,
            detail_key="tools.detail.dialout_ok",
            detail_args={"user": user},
        )
    if static_ok and not effective_ok:
        return ToolStatus(
            name_key="tools.name.dialout",
            ok=False,
            required=False,
            detail_key="tools.detail.dialout_stale",
            detail_args={"user": user},
        )
    return ToolStatus(
        name_key="tools.name.dialout",
        ok=False,
        required=False,
        detail_key="tools.detail.dialout_missing",
        detail_args={"user": user, "cmd": "sudo usermod -aG dialout $USER"},
    )
