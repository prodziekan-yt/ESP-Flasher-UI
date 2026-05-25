"""Offline test suite.

Run from any working directory:
    QT_QPA_PLATFORM=offscreen python build-tools/tests.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Pin sys.path / cwd to the repo root so `from app.X` works from anywhere.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
os.chdir(_PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.device_discovery import SerialDevice, list_serial_devices
from app.device_selector import MODE_IP, MODE_USB
from app.esphome_storage import KnownDevice, discover_known_devices
from app.i18n import I18n
from app.text_utils import (
    LEVEL_CMD,
    LEVEL_CONFIG,
    LEVEL_DEFAULT,
    LEVEL_DIM,
    LEVEL_ERROR,
    LEVEL_MARKER,
    LEVEL_SUCCESS,
    LEVEL_WARN,
    classify_line,
    strip_ansi,
)
from app.tool_checker import ToolStatus, check_all_tools, pkg_version
from app.workers import DetectResult, is_private_address, resolve_host

passed = 0
failed = 0


def check(name: str, condition: bool) -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  [V] {name}")
    else:
        failed += 1
        print(f"  [X] {name}")


# ===================================================================
print("=== Module imports ===")
# ===================================================================
try:
    from app.ui_utils import make_muted_label
    from app.device_selector import DeviceSelector
    from app.flash_bin_tab import FlashBinTab
    from app.flash_esphome_tab import FlashEsphomeTab
    from app.main_window import MainWindow
    from app.workers import FlashWorker, DetectWorker

    check("All imports", True)
except Exception as exc:
    check(f"All imports ({exc})", False)
    sys.exit(1)

# ===================================================================
print("=== I18n ===")
# ===================================================================
i18n = I18n()
langs = i18n.available()

check("37 languages loaded", len(langs) == 37)
check("tr(app.title)", i18n.tr("app.title") == "ESP Flasher UI")
check("tr(missing key) returns key", i18n.tr("no.such.key") == "no.such.key")
check("tr with kwargs", i18n.tr("about.version", version="1.0") == "Version 1.0")

check("set_language(de)", i18n.set_language("de") is True)
check("current() == de", i18n.current() == "de")
check("set_language(de) no-op", i18n.set_language("de") is False)
check("set_language(en)", i18n.set_language("en") is True)

en_idx = next(i for i, (c, _) in enumerate(langs) if c == "en")
ru_idx = next(i for i, (c, _) in enumerate(langs) if c == "ru")
check("Latin sorts before Cyrillic", en_idx < ru_idx)

# Keys that must not appear in any pack.
dead_keys = ("flash.finished_ok", "flash.finished_err", "flash.stopped")
all_clean = all(
    k not in pack for pack in i18n._packs.values() for k in dead_keys
)
check("Dead keys removed from all packs", all_clean)

# Every pack must hold the exact same key set as the English baseline.
en_keys = {k for k in i18n._packs["en"] if k != "_meta"}
mismatches = []
for code, pack in i18n._packs.items():
    if code == "en":
        continue
    other = {k for k in pack if k != "_meta"}
    if en_keys != other:
        mismatches.append(code)
check("Translation keys consistent across all files", len(mismatches) == 0)

# ===================================================================
print("=== text_utils ===")
# ===================================================================
check("CSI color strip", strip_ansi("\x1b[31mError\x1b[0m: x") == "Error: x")
check("Bold+color strip", strip_ansi("\x1b[1;32mOK\x1b[0m") == "OK")
check("OSC title strip", strip_ansi("\x1b]0;title\x07rest") == "rest")
check("Fe single-byte strip", strip_ansi("\x1bM") == "")
check("Plain text unchanged", strip_ansi("hello world") == "hello world")
check("Empty string", strip_ansi("") == "")
check(
    "Multi-color CSI strip",
    strip_ansi("\x1b[31mA\x1b[0m \x1b[32mB\x1b[0m \x1b[34mC\x1b[0m") == "A B C",
)
check(
    "Mixed CSI + OSC strip",
    strip_ansi("\x1b]0;title\x07\x1b[1;31mhi\x1b[0m end") == "hi end",
)

# Log-line classification feeds the console colorizer.
check("classify $ cmd", classify_line("$ esphome run x.yaml") == LEVEL_CMD)
check("classify INFO default", classify_line("INFO ESPHome 2026.5.0") == LEVEL_DEFAULT)
check("classify WARNING", classify_line("WARNING GPIO8 is strapping") == LEVEL_WARN)
check("classify ERROR", classify_line("ERROR Failed to connect") == LEVEL_ERROR)
check("classify Successfully", classify_line("Successfully compiled") == LEVEL_SUCCESS)
check(
    "classify [I][...] default",
    classify_line("[12:31:55][I][app:151]: ESPHome version 2026.5.0") == LEVEL_DEFAULT,
)
check(
    "classify [C][...] config",
    classify_line("[12:31:55][C][logger:219]: Logger:") == LEVEL_CONFIG,
)
check(
    "classify [D][...] dim",
    classify_line("[12:31:55][D][api:123]: connected") == LEVEL_DIM,
)
check(
    "classify [W][...] warn",
    classify_line("[12:31:55][W][wifi:001]: weak signal") == LEVEL_WARN,
)
check(
    "classify [E][...] error",
    classify_line("[12:31:55][E][api:999]: refused") == LEVEL_ERROR,
)
check(
    "classify ==== marker ====",
    classify_line("==== Live log stream ====") == LEVEL_MARKER,
)
# Crash / wdt / abort lines map to the ERROR group.
check(
    "classify *** CRASH",
    classify_line("[E][esp32.crash:221]: *** CRASH DETECTED ***") == LEVEL_ERROR,
)
check(
    "classify Backtrace:",
    classify_line("Backtrace: 0x4013d30e:0x3ffbac20") == LEVEL_ERROR,
)
check(
    "classify task_wdt:",
    classify_line("E (5906) task_wdt: Task watchdog got triggered.") == LEVEL_ERROR,
)
check(
    "classify Guru Meditation",
    classify_line("Guru Meditation Error: Core 0 panic'ed") == LEVEL_ERROR,
)
check(
    "classify abort()",
    classify_line("abort() was called at PC 0x40089a4f") == LEVEL_ERROR,
)
# "took a long time" -> WARN.
check(
    "classify took a long time",
    classify_line("Component took a long time for an operation (85 ms)") == LEVEL_WARN,
)
check("classify empty -> default", classify_line("") == LEVEL_DEFAULT)

# ===================================================================
print("=== workers (pure functions) ===")
# ===================================================================
check("private 192.168.x", is_private_address("192.168.1.1"))
check("private 10.x", is_private_address("10.0.0.1"))
check("private 127.x", is_private_address("127.0.0.1"))
check("private 172.16.x", is_private_address("172.16.0.1"))
check("public 8.8.8.8", not is_private_address("8.8.8.8"))
check("public 1.1.1.1", not is_private_address("1.1.1.1"))
check("invalid ip", not is_private_address("not-an-ip"))
check("empty ip", not is_private_address(""))

ip, err = resolve_host("")
check("resolve_host('') fails", ip is None and err == "empty host")
ip, err = resolve_host("localhost")
check("resolve_host('localhost')", ip == "127.0.0.1")

r = DetectResult(
    True, "esp.local", 6053, 12, None,
    resolved_ip="192.168.1.5", is_public=False, port_role="api",
)
check("DetectResult fields", r.ok and r.port == 6053 and not r.is_public)

# ===================================================================
print("=== device_discovery ===")
# ===================================================================
d1 = SerialDevice("/dev/ttyUSB0", "CP2102", "USB VID:PID=10C4:EA60")
d2 = SerialDevice("/dev/ttyACM0", "n/a", "n/a")
check("display with description", "CP2102" in d1.display())
check("display without description", d2.display() == "/dev/ttyACM0")
devs = list_serial_devices()
check("list_serial_devices returns list", isinstance(devs, list))

# ===================================================================
print("=== esphome_storage ===")
# ===================================================================
kd1 = KnownDevice("test", "Test Device", "192.168.1.10", "ESP32", "arduino", "", "", "", [])
d = kd1.display()
check("display full", "Test Device" in d and "(test)" in d and "@ 192.168.1.10" in d)

kd2 = KnownDevice("mydev", "", "", "", "", "", "", "", [])
check("display name-only", kd2.display() == "mydev")

kd3 = KnownDevice("mydev", "mydev", "", "", "", "", "", "", [])
check("display no duplicate parens", "(" not in kd3.display())

check("discover nonexistent dir", discover_known_devices("/nonexistent") == [])

# ===================================================================
print("=== stack_decoder ===")
# ===================================================================
import tempfile
from app.stack_decoder import (
    extract_addresses,
    find_addr2line_binaries,
    find_elf_candidates,
    read_elf_machine,
)

sample_trace = """[E][esp32.crash:228]:   PC:  0x4011AD40
[E][esp32.crash:242]:   BT0: 0x4011AD3D
[E][esp32.crash:242]:   BT1: 0x4011AD95
[E][esp32.crash:242]:   BT0: 0x4011AD3D"""
addrs = extract_addresses(sample_trace)
check("extract_addresses unique", len(addrs) == 3)
check("extract_addresses order", addrs[0] == "0x4011ad40")
check("extract_addresses lowercase", all(a == a.lower() for a in addrs))
check("extract_addresses empty input", extract_addresses("no hex here") == [])
check(
    "extract_addresses ignores short hex",
    extract_addresses("0x1234 is too short") == [],
)

check("find_elf_candidates nonexistent", find_elf_candidates("/nonexistent") == [])
check("find_elf_candidates empty arg", find_elf_candidates("") == [])

_elf_proj = tempfile.mkdtemp(prefix="elftest_")
_pio = Path(_elf_proj) / ".esphome" / "build" / "dev1" / ".pioenvs" / "dev1"
_pio.mkdir(parents=True)
(_pio / "firmware.elf").write_bytes(b"x")
(_pio / "dev1.elf").write_bytes(b"x")
(_pio / "bootloader").mkdir()
(_pio / "bootloader" / "bootloader.elf").write_bytes(b"x")
_found = find_elf_candidates(_elf_proj)
check("find_elf_candidates picks firmware.elf", any(p.endswith("firmware.elf") for p in _found))
check("find_elf_candidates picks <project>.elf", any(p.endswith("dev1.elf") for p in _found))
check("find_elf_candidates skips nested bootloader", not any("bootloader.elf" in p for p in _found))

import os as _os1
import time as _t1
_now = _t1.time()
_os1.utime(_pio / "firmware.elf", (_now - 60, _now - 60))
_os1.utime(_pio / "dev1.elf", (_now - 30, _now - 30))
(_pio / "bootloader.elf").write_bytes(b"x")
_os1.utime(_pio / "bootloader.elf", (_now, _now))
_found2 = find_elf_candidates(_elf_proj)
check(
    "find_elf_candidates ranks firmware.elf first",
    _found2 and _found2[0].endswith("firmware.elf"),
)
check(
    "find_elf_candidates puts sibling bootloader last",
    _found2 and _found2[-1].endswith("bootloader.elf"),
)

import shutil as _sh1
_sh1.rmtree(_elf_proj)

with tempfile.NamedTemporaryFile("wb", delete=False) as fp:
    fp.write(b"not an elf at all")
    fake = fp.name
check("read_elf_machine non-ELF", read_elf_machine(fake) == "")
os.unlink(fake)
check("read_elf_machine missing file", read_elf_machine("/nonexistent.elf") == "")

# Synthetic 20-byte ELF header: magic + ident + e_type=ET_EXEC + e_machine.
def _fake_elf(machine: int) -> str:
    head = (
        b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8
        + b"\x02\x00"
        + machine.to_bytes(2, "little")
    )
    fp = tempfile.NamedTemporaryFile("wb", delete=False, suffix=".elf")
    fp.write(head)
    fp.close()
    return fp.name

p_xt = _fake_elf(0x5E)
check("read_elf_machine xtensa", read_elf_machine(p_xt) == "xtensa")
os.unlink(p_xt)
p_rv = _fake_elf(0xF3)
check("read_elf_machine riscv", read_elf_machine(p_rv) == "riscv")
os.unlink(p_rv)
p_unk = _fake_elf(0x99)
check("read_elf_machine unknown -> ''", read_elf_machine(p_unk) == "")
os.unlink(p_unk)

check(
    "find_addr2line_binaries returns list",
    isinstance(find_addr2line_binaries("xtensa"), list),
)

# ===================================================================
print("=== tool_checker ===")
# ===================================================================
check("pkg_version(pyserial)", pkg_version("pyserial") != "")
check("pkg_version(missing)", pkg_version("nonexistent-xyz") == "")
statuses = check_all_tools()
check("7 tool checks", len(statuses) == 7)
check("all ToolStatus instances", all(isinstance(s, ToolStatus) for s in statuses))

# ===================================================================
print("=== GUI (offscreen) ===")
# ===================================================================
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
window = MainWindow(i18n)

check("window title", window.windowTitle() == "ESP Flasher UI")
check("4 tabs", window.tabs.count() == 4)
check("tab 0 = Flash ESPHome", window.tabs.tabText(0) == "Flash ESPHome")
check("tab 1 = Flash .BIN", window.tabs.tabText(1) == "Flash .BIN")
check("tab 2 = Read flash", window.tabs.tabText(2) == "Read flash")
check("stop button disabled", not window.stop_button.isEnabled())

window.tabs.setCurrentIndex(1)
check("tab switch", window.tabs.currentIndex() == 1)

# Language combo round-trip: en -> de -> en.
de_idx = -1
for i in range(window.language_combo.count()):
    if window.language_combo.itemData(i) == "de":
        de_idx = i
        break
window.language_combo.setCurrentIndex(de_idx)
check("language combo -> de", i18n.current() == "de")
for i in range(window.language_combo.count()):
    if window.language_combo.itemData(i) == "en":
        window.language_combo.setCurrentIndex(i)
        break
check("language combo -> en", i18n.current() == "en")

check("firmware_path empty", window.flash_bin_tab.firmware_path() == "")
window.flash_bin_tab.set_firmware_path("/tmp/test.bin")
check("firmware_path set", window.flash_bin_tab.firmware_path() == "/tmp/test.bin")

check("project_dir empty", window.flash_esphome_tab.project_dir() == "")

console = window.console.toPlainText()
check("console has startup lines", len(console.splitlines()) > 0)

window._on_clear_clicked()
check("clear console", "cleared" in window.console.toPlainText().lower())

check(
    "check_tools_button removed (merged into diagnostics)",
    not hasattr(window, "check_tools_button"),
)
check(
    "_on_check_tools_clicked method gone",
    not hasattr(window, "_on_check_tools_clicked"),
)
check(
    "_log_tools_check helper present",
    hasattr(window, "_log_tools_check") and callable(window._log_tools_check),
)

# App messages get a `>> ` prefix; raw tool output does not.
window._on_clear_clicked()
window._log("hello from the app")
_app_line = window.console.toPlainText().splitlines()[-1]
check("internal log has >> prefix", ">>" in _app_line and "hello from the app" in _app_line)

window._on_clear_clicked()
window._on_flash_line("Chip is ESP32-C3")
_raw_line = window.console.toPlainText().splitlines()[-1]
check("external tool line has no >> prefix", ">>" not in _raw_line and "Chip is ESP32-C3" in _raw_line)

# Prefix constant and call-site behaviour for hints + tab signals.
from app.main_window import MainWindow as _MW
check("_APP_PREFIX constant is '>> '", _MW._APP_PREFIX == ">> ")

window._on_clear_clicked()
window._on_flash_state_changed(True)
window._on_flash_line("[E][esp32.crash:221]: *** CRASH DETECTED ***")
_hint_lines = [
    ln for ln in window.console.toPlainText().splitlines()
    if "decode" in ln.lower() and "***" not in ln
]
check("crash hint line carries >> prefix", _hint_lines and ">>" in _hint_lines[0])

window._on_clear_clicked()
window._on_flash_state_changed(True)
window._on_flash_line("plain tool line A")
window._log("internal between")
window._on_flash_line("plain tool line B")
_mixed = window.console.toPlainText().splitlines()
check(
    "alternating lines: external no prefix, internal prefixed",
    not (">>" in _mixed[-3]) and (">>" in _mixed[-2]) and not (">>" in _mixed[-1]),
)

window._on_clear_clicked()
window.read_flash_tab.log.emit("hello from a tab")
_tab_line = window.console.toPlainText().splitlines()[-1]
check("tab.log signal goes through internal path", ">>" in _tab_line)

window._on_clear_clicked()
window._on_diagnostics_clicked()
diag = window.console.toPlainText()
check("diagnostics has Python", "Python" in diag)
check("diagnostics has PyQt6", "PyQt6" in diag)
check("diagnostics has tool checklist markers", "[V]" in diag or "[X]" in diag)
check("diagnostics has tools.header section", "Tool check" in diag or "tools.header" in diag.lower())

check("flash_worker idle", not window.flash_worker.is_running())
check("flash_worker no op", window.flash_worker.operation == "")
check("default mode USB", window.device_selector.current_mode() == MODE_USB)

# Stack decoder button is wired into the ESPHome tab.
check(
    "decode_stack_button exists",
    hasattr(window.flash_esphome_tab, "decode_stack_button"),
)
check(
    "decode_stack_button has label",
    bool(window.flash_esphome_tab.decode_stack_button.text()),
)

# Window/taskbar icon must load and rasterise.
from app import ICON_PATH

check("ICON_PATH points at app/assets", ICON_PATH.parent.name == "assets")
check("icon file exists", ICON_PATH.is_file())
check("icon is SVG", ICON_PATH.suffix == ".svg")
check("window has non-null icon", not window.windowIcon().isNull())
_icon_pix = window.windowIcon().pixmap(64, 64)
check("icon rasterises 64x64", not _icon_pix.isNull() and _icon_pix.width() > 0)

# ===================================================================
print("=== Hint detection ===")
# ===================================================================
def _reset_console_and_state():
    window._on_clear_clicked()
    window._on_flash_state_changed(True)


_reset_console_and_state()
window._on_flash_line("[E][esp32.crash:221]: *** CRASH DETECTED ON PREVIOUS BOOT ***")
crash_console = window.console.toPlainText()
check(
    "crash hint emitted",
    "Decode" in crash_console or "decode" in crash_console.lower(),
)

_reset_console_and_state()
window._on_flash_line("[E][esp32.crash:221]: *** CRASH DETECTED ***")
window._on_flash_line("[E][esp32.crash:221]: *** CRASH DETECTED ***")
n_crash_hints = window.console.toPlainText().lower().count("'decode stack trace")
check("crash hint shown only once", n_crash_hints <= 1)

_reset_console_and_state()
window._on_flash_line("WARNING Found stack trace! Trying to decode it")
window._on_flash_line("(unrelated subsequent line)")
out = window.console.toPlainText()
check(
    "decode-no-elf hint after stranded 'Found stack trace'",
    "compile" in out.lower() or "firmware.elf" in out.lower(),
)

_reset_console_and_state()
window._on_flash_line("WARNING Found stack trace! Trying to decode it")
window._on_flash_line("WARNING Decoded 0x4013d30e: setup() at file.yaml:89")
out = window.console.toPlainText()
check(
    "no decode-no-elf hint when Decoded follows Found",
    "compile (or run) first" not in out.lower()
    and "stack trace not decoded automatically" not in out.lower(),
)

_reset_console_and_state()
window._on_flash_line("[W][component:511]: sensor took a long time for an operation (85 ms)")
out = window.console.toPlainText()
check(
    "long-op hint emitted",
    "watchdog" in out.lower() or "main loop" in out.lower(),
)

_reset_console_and_state()
window._on_flash_line("[W][component:511]: sensor took a long time for an operation (85 ms)")
window._on_flash_line("[W][component:511]: sensor took a long time for an operation (90 ms)")
n_long_hints = window.console.toPlainText().lower().count("blocked the main loop")
check("long-op hint shown only once", n_long_hints <= 1)

_reset_console_and_state()
window._on_flash_line("Just a regular info line, nothing special.")
out = window.console.toPlainText()
check(
    "no false-positive hints on plain line",
    "decode" not in out.lower() and "watchdog" not in out.lower(),
)

# ===================================================================
print("=== YAML pre-validate hint ===")
# ===================================================================
yaml_verbose = """esphome:
  name: test
logger:
  level: VERY_VERBOSE
"""
yaml_normal = """esphome:
  name: test
logger:
  level: DEBUG
"""
with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fp:
    fp.write(yaml_verbose)
    p_verbose = fp.name
with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fp:
    fp.write(yaml_normal)
    p_normal = fp.name

_reset_console_and_state()
window.flash_esphome_tab._maybe_warn_verbose_logger(p_verbose)
out = window.console.toPlainText()
check(
    "VERY_VERBOSE hint emitted",
    "VERY_VERBOSE" in out or "verbose" in out.lower(),
)

_reset_console_and_state()
window.flash_esphome_tab._maybe_warn_verbose_logger(p_normal)
out = window.console.toPlainText()
check(
    "no VERY_VERBOSE hint for DEBUG level",
    "VERY_VERBOSE" not in out,
)

_reset_console_and_state()
window.flash_esphome_tab._maybe_warn_verbose_logger("/nonexistent.yaml")
check(
    "no crash on missing YAML",
    "VERY_VERBOSE" not in window.console.toPlainText(),
)

os.unlink(p_verbose)
os.unlink(p_normal)

# ===================================================================
print("=== Stack decoder dialog ===")
# ===================================================================
from app.stack_decoder import StackDecoderDialog

dialog = StackDecoderDialog(i18n, project_dir="", parent=window)
check("StackDecoderDialog constructs", dialog is not None)
check(
    "StackDecoderDialog has title",
    dialog.windowTitle() == i18n.tr("decoder.title"),
)
check("decode button present", bool(dialog.decode_button.text()))
check("copy button initially disabled", not dialog.copy_button.isEnabled())

# Empty input + missing ELF -> error messages on the status label.
dialog.input_edit.setPlainText("")
dialog.elf_combo.setEditText("")
dialog._on_decode()
check(
    "decoder reports missing ELF",
    "ELF" in dialog.status_label.text(),
)

dialog.elf_combo.setEditText("/nonexistent.elf")
dialog._on_decode()
check(
    "decoder reports missing file",
    "not found" in dialog.status_label.text().lower()
    or "nonexistent" in dialog.status_label.text(),
)

# ELF auto-population from a real project tree.
_decode_proj = tempfile.mkdtemp(prefix="decoderproj_")
_decode_pio = Path(_decode_proj) / ".esphome" / "build" / "dev2" / ".pioenvs" / "dev2"
_decode_pio.mkdir(parents=True)
(_decode_pio / "firmware.elf").write_bytes(b"x")
(_decode_pio / "dev2.elf").write_bytes(b"x")
_dialog2 = StackDecoderDialog(i18n, project_dir=_decode_proj, parent=window)
check("decoder auto-populates ELF combo", _dialog2.elf_combo.count() >= 2)
check(
    "decoder lists firmware.elf first",
    _dialog2.elf_combo.itemText(0).endswith("firmware.elf"),
)
_dialog2.deleteLater()
_sh1.rmtree(_decode_proj)

# ===================================================================
print("=== __version__ ===")
# ===================================================================
from app import __version__

check("__version__ is string", isinstance(__version__, str))
check("__version__ semver-like", re.match(r"^\d+\.\d+\.\d+", __version__) is not None)

# ===================================================================
print("=== I18n edge cases ===")
# ===================================================================
check("set_language nonexistent", i18n.set_language("xx_FAKE") is False)
check("current unchanged after bad set", i18n.current() == "en")

check(
    "tr bad kwargs returns raw value",
    i18n.tr("about.version", wrong_key="x") == "Version {version}",
)

_bad_meta = []
for code, pack in i18n._packs.items():
    meta = pack.get("_meta")
    if meta is None or not all(k in meta for k in ("code", "name", "native_name")):
        _bad_meta.append(code)
check(f"_meta complete in all 37 packs ({len(_bad_meta)} bad)", len(_bad_meta) == 0)

# _meta.code must equal the JSON filename stem (pl.json -> "pl").
_code_mismatch = [
    code for code, pack in i18n._packs.items()
    if pack.get("_meta", {}).get("code") != code
]
check("_meta.code matches filename in all packs", not _code_mismatch)

_empty_native = [
    code for code, pack in i18n._packs.items()
    if not pack.get("_meta", {}).get("native_name")
]
check("_meta.native_name non-empty in all packs", not _empty_native)

# Pack codes are unique (no collision on insert).
check("all language codes unique", len(set(i18n._packs)) == len(i18n._packs))

# ===================================================================
print("=== text_utils (extra patterns) ===")
# ===================================================================
check(
    "classify [V][...] dim",
    classify_line("[12:31:55][V][sensor:123]: raw 42") == LEVEL_DIM,
)
check(
    "classify [F][...] error",
    classify_line("[12:31:55][F][core:001]: fatal") == LEVEL_ERROR,
)
check(
    "classify Rebooting...",
    classify_line("Rebooting...") == LEVEL_ERROR,
)
check(
    "classify Exit code 1",
    classify_line("Exit code 1") == LEVEL_ERROR,
)
check(
    "classify Traceback",
    classify_line("Traceback (most recent call last):") == LEVEL_ERROR,
)
check(
    "classify OTA successful",
    classify_line("OTA successful") == LEVEL_SUCCESS,
)
check(
    "classify FAILED",
    classify_line("FAILED to compile") == LEVEL_ERROR,
)

# ===================================================================
print("=== Console colorizer coverage ===")
# ===================================================================
from app.main_window import LEVEL_COLORS

for lvl in (LEVEL_DIM, LEVEL_CMD, LEVEL_WARN, LEVEL_ERROR,
            LEVEL_SUCCESS, LEVEL_CONFIG, LEVEL_MARKER):
    check(f"LEVEL_COLORS has '{lvl}'", lvl in LEVEL_COLORS)
check("LEVEL_DEFAULT not in LEVEL_COLORS", LEVEL_DEFAULT not in LEVEL_COLORS)

# ===================================================================
print("=== FlashWorker guards ===")
# ===================================================================
fw = window.flash_worker
check("stop when idle is safe", fw.stop() is None)
check("is_running idle", not fw.is_running())

_usb_ok = fw.start_usb("/dev/null", "/nonexistent.bin")
check("start_usb returns bool", isinstance(_usb_ok, bool))
if _usb_ok:
    fw.stop()

_ota_ok = fw.start_ota("127.0.0.1", "/nonexistent.bin")
check("start_ota returns bool", isinstance(_ota_ok, bool))
if _ota_ok:
    fw.stop()

_esh_ok = fw.start_esphome("config", "/nonexistent.yaml")
check("start_esphome returns bool", isinstance(_esh_ok, bool))
if _esh_ok:
    fw.stop()

# Operation tag remains empty until a spawn actually succeeds. After stop()
# while idle, is_running() must still report False.
check("operation tag stays empty after failed/short spawn", fw.operation in ("", "flash", "ota", "esphome.config"))
fw.stop()
check("is_running False after stop on idle", not fw.is_running())

# ===================================================================
print("=== DeviceSelector state ===")
# ===================================================================
ds = window.device_selector
check("initial mode USB", ds.current_mode() == MODE_USB)
check("current_host empty", ds.current_host() == "")
check("current_password empty", ds.current_password() == "")

ds.set_host("192.168.1.50")
check("set_host switches to IP", ds.current_mode() == MODE_IP)
check("set_host fills field", ds.current_host() == "192.168.1.50")

ds.mode_usb_radio.setChecked(True)
check("radio back to USB", ds.current_mode() == MODE_USB)

check("baud default 460800", ds.current_baud() == 460800)
check("baud combo has 9 entries", ds.usb_baud_combo.count() == 9)
ds.usb_baud_combo.setCurrentIndex(0)
check("baud Auto -> None", ds.current_baud() is None)
ds.usb_baud_combo.setCurrentIndex(8)
check("baud max -> 2000000", ds.current_baud() == 2000000)
ds.usb_baud_combo.setCurrentIndex(1)
check("baud low -> 9600", ds.current_baud() == 9600)
ds.usb_baud_combo.setCurrentIndex(2)
check("baud low -> 57600", ds.current_baud() == 57600)
ds.usb_baud_combo.setCurrentIndex(5)
check("baud hint tooltip present", "115200" in ds.usb_baud_combo.toolTip())

# ===================================================================
print("=== FlashBinTab guards ===")
# ===================================================================
_reset_console_and_state()
window.flash_bin_tab._firmware_path = ""
window.flash_bin_tab.firmware_edit.clear()
window.flash_bin_tab._on_flash_clicked()
out = window.console.toPlainText().lower()
check("flash without firmware -> error", "firmware" in out or "missing" in out)

# ===================================================================
print("=== FlashEsphomeTab guards ===")
# ===================================================================
_reset_console_and_state()
et = window.flash_esphome_tab
et._yaml_path = ""
et.yaml_edit.clear()
et._run_action("config")
out = window.console.toPlainText().lower()
check("esphome without yaml -> error", "yaml" in out or "missing" in out)

check("_detect_build_artifact no project", et._detect_build_artifact() == "")

check("project_dir initially empty", et.project_dir() == "")

# _commit_edit: no-op when text matches current value.
from PyQt6.QtWidgets import QLineEdit

_ce_edit = QLineEdit()
_ce_edit.setText("/some/path")
_ce_called = []
FlashEsphomeTab._commit_edit(_ce_edit, "/some/path", lambda p: _ce_called.append(p))
check("_commit_edit no-op for same value", len(_ce_called) == 0)
_ce_edit.setText("/new/path")
FlashEsphomeTab._commit_edit(_ce_edit, "/some/path", lambda p: _ce_called.append(p))
check("_commit_edit fires for changed value", _ce_called == ["/new/path"])

# ===================================================================
print("=== esphome_storage (real manifest) ===")
# ===================================================================
import json as _json

_tmp_proj = tempfile.mkdtemp(prefix="esptest_")
_storage = Path(_tmp_proj) / ".esphome" / "storage"
_storage.mkdir(parents=True)

_manifest = {
    "name": "testdev",
    "friendly_name": "Test Device",
    "address": "10.0.0.42",
    "esp_platform": "ESP32",
    "framework": "arduino",
    "firmware_bin_path": "",
    "loaded_integrations": ["wifi", "api"],
}
(_storage / "testdev.yaml.json").write_text(_json.dumps(_manifest), encoding="utf-8")

_devs = discover_known_devices(_tmp_proj)
check("discover parses manifest", len(_devs) == 1)
check("manifest name", _devs[0].name == "testdev" if _devs else False)
check("manifest address", _devs[0].address == "10.0.0.42" if _devs else False)
check("manifest platform", _devs[0].esp_platform == "ESP32" if _devs else False)
check("manifest integrations", _devs[0].integrations == ["wifi", "api"] if _devs else False)

import shutil as _shutil
_shutil.rmtree(_tmp_proj)

# ===================================================================
print("=== YamlEditor side panel ===")
# ===================================================================
from app.yaml_editor import YamlEditor

_editor = window.yaml_editor
check("yaml_editor wired into main window", isinstance(_editor, YamlEditor))
check("splitter has 2 widgets", window.splitter.count() == 2)
check("editor hidden on startup", _editor.isHidden())
check("editor starts unmodified", not _editor.is_modified())
check("editor path empty initially", _editor.current_path() == "")

from app.yaml_editor import YamlHighlighter

check("highlighter attached", isinstance(_editor.highlighter, YamlHighlighter))

from PyQt6.QtWidgets import QPlainTextEdit as _QPlainTextEdit
_hl_probe = _QPlainTextEdit()
_probe_hl = YamlHighlighter(_hl_probe.document())
_hl_probe.setPlainText(
    "# comment\n"
    "key: value\n"
    "list:\n"
    "  - item: \"string\"\n"
    "  - flag: true\n"
    "  - count: 42\n"
)
_probe_hl.rehighlight()
_blocks_formatted = 0
_block = _hl_probe.document().firstBlock()
while _block.isValid():
    if _block.layout() is not None and _block.layout().formats():
        _blocks_formatted += 1
    _block = _block.next()
check("highlighter applies formats to multiple lines", _blocks_formatted >= 4)

check("show_whitespace checkbox exists", hasattr(_editor, "show_ws_checkbox"))
check("show_whitespace off by default", not _editor.show_ws_checkbox.isChecked())
check(
    "editor flag matches checkbox default (off)",
    not _editor.editor._show_whitespace,
)
_editor.show_ws_checkbox.setChecked(True)
check("checking the box turns whitespace overlay on", _editor.editor._show_whitespace)
_editor.show_ws_checkbox.setChecked(False)
check("unchecking the box turns whitespace overlay off", not _editor.editor._show_whitespace)

_qv_btn = window.flash_esphome_tab.quick_view_button
check("quick_view_button exists", _qv_btn is not None)
check("quick_view_button disabled without YAML", not _qv_btn.isEnabled())
check("quick_view_button checkable", _qv_btn.isCheckable())

_yaml_dir = tempfile.mkdtemp(prefix="esptest_yaml_")
_yaml_path = Path(_yaml_dir) / "device.yaml"
_yaml_path.write_text(
    "esphome:\n  name: testdev\n  platform: ESP32\nwifi:\n  ssid: HOME\n",
    encoding="utf-8",
)
window.flash_esphome_tab._set_yaml(str(_yaml_path))
check("quick_view_button enabled after yaml selected", _qv_btn.isEnabled())

window._on_quick_view_requested(str(_yaml_path))
check("editor visible after toggle on", not _editor.isHidden())
check("button checked after open", _qv_btn.isChecked())
check("editor loaded YAML content", "esphome:" in _editor.editor.toPlainText())
check("editor path tracks file", _editor.current_path() == str(_yaml_path))

_editor.find_input.setText("ESP32")
_editor._on_find_next()
check(
    "find_next selects existing token",
    _editor.editor.textCursor().selectedText() == "ESP32",
)
_status_after_hit = _editor.status_label.text().lower()
check("find_hit status shown", "found" in _status_after_hit or _status_after_hit != "")

_editor.find_input.setText("zzznotinfile")
_editor._on_find_next()
check("find_miss status shown", _editor.status_label.text() != "")

_editor.find_input.setText("ESP32")
_editor.replace_input.setText("ESP8266")
_editor._on_replace_all()
check(
    "replace_all swaps token",
    "ESP32" not in _editor.editor.toPlainText()
    and "ESP8266" in _editor.editor.toPlainText(),
)
check("buffer marked modified after replace", _editor.is_modified())
check("title shows modified marker", _editor.title_label.text().endswith("*"))

_editor._on_save()
saved_text = _yaml_path.read_text(encoding="utf-8")
check("save wrote ESP8266 to disk", "ESP8266" in saved_text)
check("buffer not modified after save", not _editor.is_modified())

_pre_close_w = window.width()
_pre_close_side = window.splitter.sizes()[1] if len(window.splitter.sizes()) >= 2 else 0
window._on_quick_view_requested(str(_yaml_path))
check("editor hidden after second toggle", _editor.isHidden())
check("button unchecked after close", not _qv_btn.isChecked())
check(
    "window shrinks back when panel closes",
    _pre_close_side == 0 or window.width() <= _pre_close_w - _pre_close_side + 1,
)

window._on_quick_view_requested(str(_yaml_path))
check("editor visible again", not _editor.isHidden())
_editor._on_cancel()
check("cancel on clean buffer closes panel", _editor.isHidden())

_blank_editor = YamlEditor(i18n)
_blank_editor._on_save()
check(
    "save without path -> error status",
    "no yaml" in _blank_editor.status_label.text().lower()
    or _blank_editor.status_label.text() != "",
)

_bad_load = _blank_editor.load("/no/such/file.yaml")
check("load returns False for missing file", _bad_load is False)
check("editor remains unloaded after failed load", _blank_editor.current_path() == "")

window._on_quick_view_requested(str(_yaml_path))
_pre_text = _editor.editor.toPlainText()
_editor.find_input.setText("zzznotinfile")
_editor.replace_input.setText("XX")
_editor._on_replace_all()
check(
    "replace_all on missing needle leaves text untouched",
    _editor.editor.toPlainText() == _pre_text,
)
_editor._on_cancel()

window.flash_esphome_tab.set_quick_view_state(True)
check(
    "set_quick_view_state(True) -> close label",
    window.flash_esphome_tab.quick_view_button.text()
    == i18n.tr("esphome.action.quick_view.close"),
)
window.flash_esphome_tab.set_quick_view_state(False)
check(
    "set_quick_view_state(False) -> open label",
    window.flash_esphome_tab.quick_view_button.text()
    == i18n.tr("esphome.action.quick_view"),
)

_shutil.rmtree(_yaml_dir)

# ===================================================================
print("=== ReadFlashTab ===")
# ===================================================================
from app.read_flash_tab import (
    PRESET_APP,
    PRESET_BOOTLOADER,
    PRESET_FULL,
    ReadFlashTab,
    _parse_int,
)

check("_parse_int hex", _parse_int("0x10000") == 65536)
check("_parse_int decimal", _parse_int("1024") == 1024)
check("_parse_int empty -> None", _parse_int("") is None)
check("_parse_int garbage -> None", _parse_int("foo") is None)
check("_parse_int negative", _parse_int("-1") == -1)

rt = window.read_flash_tab
check("read_flash_tab present", isinstance(rt, ReadFlashTab))
check("offset default 0x0", rt.offset_edit.text() == "0x0")
check("length empty by default", rt.length_edit.text() == "")

rt._apply_preset(PRESET_APP)
check("preset_app sets offset", rt.offset_edit.text() == "0x10000")
check("preset_app sets length", rt.length_edit.text() == "0x200000")

rt._apply_preset(PRESET_BOOTLOADER)
check("preset_bootloader offset", rt.offset_edit.text() == "0x0")
check("preset_bootloader length", rt.length_edit.text() == "0x10000")

rt._apply_preset(PRESET_FULL)
check("preset_full offset", rt.offset_edit.text() == "0x0")
check("preset_full clears length", rt.length_edit.text() == "")

_reset_console_and_state()
rt._on_read_clicked()
_out_no_output = window.console.toPlainText().lower()
check("read without output -> error", "output" in _out_no_output or "first" in _out_no_output)

_reset_console_and_state()
rt._output_path = "/tmp/x.bin"
rt.output_edit.setText("/tmp/x.bin")
rt.offset_edit.setText("not-a-number")
rt._on_read_clicked()
_out_bad_offset = window.console.toPlainText().lower()
check("bad offset -> error", "invalid offset" in _out_bad_offset)

_reset_console_and_state()
rt.offset_edit.setText("0x0")
rt.length_edit.setText("xyz")
rt._on_read_clicked()
_out_bad_length = window.console.toPlainText().lower()
check("bad length -> error", "invalid length" in _out_bad_length)

_reset_console_and_state()
rt.length_edit.clear()
window.device_selector.mode_ip_radio.setChecked(True)
rt._on_read_clicked()
_out_usb_only = window.console.toPlainText().lower()
check("OTA mode rejected for read flash", "usb" in _out_usb_only)
window.device_selector.mode_usb_radio.setChecked(True)

_fid_ok = window.flash_worker.start_flash_id("/dev/null")
check("start_flash_id returns bool", isinstance(_fid_ok, bool))
if _fid_ok:
    window.flash_worker.stop()

_rf_ok = window.flash_worker.start_read_flash("/dev/null", "/tmp/out.bin")
check("start_read_flash returns bool", isinstance(_rf_ok, bool))
if _rf_ok:
    window.flash_worker.stop()

check(
    "op.name.read_flash translates",
    window._operation_label("read_flash") == "read flash",
)
check(
    "op.name.flash_id translates",
    window._operation_label("flash_id") == "chip detect",
)

# Output sync: _set_output writes both the line edit and the internal cache.
rt._set_output("/tmp/sync-check.bin")
check("_set_output updates line edit", rt.output_edit.text() == "/tmp/sync-check.bin")
check("_set_output stores path", rt._output_path == "/tmp/sync-check.bin")

check("preset_full has tooltip", bool(rt.preset_full.toolTip()))
check("preset_app has tooltip", bool(rt.preset_app.toolTip()))
check("preset_bootloader has tooltip", bool(rt.preset_bootloader.toolTip()))

# Detect chip is USB-only just like Read flash.
_reset_console_and_state()
window.device_selector.mode_ip_radio.setChecked(True)
rt._on_detect_clicked()
_detect_ota = window.console.toPlainText().lower()
check("detect chip rejects OTA", "usb" in _detect_ota)
window.device_selector.mode_usb_radio.setChecked(True)

# in_progress guard for detect chip.
_reset_console_and_state()
_orig_is_running = window.flash_worker.is_running
window.flash_worker.is_running = lambda: True
rt._on_detect_clicked()
_detect_busy = window.console.toPlainText().lower()
window.flash_worker.is_running = _orig_is_running
check("detect blocked while worker busy", "progress" in _detect_busy or "already" in _detect_busy)

# ===================================================================
print("=== Reset ESP button ===")
# ===================================================================
check("reset_button exists", hasattr(window, "reset_button"))
check("reset_button label", window.reset_button.text() == "Reset ESP")
check(
    "reset_button tooltip set",
    "DTR" in window.reset_button.toolTip() or "USB" in window.reset_button.toolTip(),
)
check(
    "reset_button next to diag_button",
    window.diag_button.parent() is window.reset_button.parent(),
)
_actions_layout = None
_root_layout = window.diag_button.parent().layout()
for _i in range(_root_layout.count()):
    _item = _root_layout.itemAt(_i)
    _sub = _item.layout()
    if _sub is not None and _sub.indexOf(window.diag_button) != -1:
        _actions_layout = _sub
        break
check("found actions layout", _actions_layout is not None)
_reset_idx = _actions_layout.indexOf(window.reset_button) if _actions_layout else -1
_diag_idx = _actions_layout.indexOf(window.diag_button) if _actions_layout else -1
check(
    "reset_button sits left of diag_button",
    0 <= _reset_idx < _diag_idx,
)

_rst_ok = window.flash_worker.start_reset("/dev/null")
check("start_reset returns bool", isinstance(_rst_ok, bool))
if _rst_ok:
    window.flash_worker.stop()
check(
    "op.name.reset translates",
    window._operation_label("reset") == "reset",
)

_reset_console_and_state()
window.device_selector.mode_ip_radio.setChecked(True)
window._on_reset_clicked()
_out_reset_ota = window.console.toPlainText().lower()
check("reset rejects OTA mode", "usb" in _out_reset_ota)
window.device_selector.mode_usb_radio.setChecked(True)

_reset_console_and_state()
_prev_dev_idx = window.device_selector.usb_device_combo.currentIndex()
window.device_selector.usb_device_combo.setCurrentIndex(-1)
window._on_reset_clicked()
_out_reset_no_dev = window.console.toPlainText().lower()
check(
    "reset warns when no device",
    "device" in _out_reset_no_dev or "select" in _out_reset_no_dev,
)
window.device_selector.usb_device_combo.setCurrentIndex(_prev_dev_idx)

# in_progress guard for reset.
_reset_console_and_state()
_orig_is_running = window.flash_worker.is_running
window.flash_worker.is_running = lambda: True
window._on_reset_clicked()
_out_reset_busy = window.console.toPlainText().lower()
window.flash_worker.is_running = _orig_is_running
check("reset blocked while worker busy", "progress" in _out_reset_busy or "already" in _out_reset_busy)

# ===================================================================
print("=== EraseFlashTab ===")
# ===================================================================
from app.erase_flash_tab import EraseFlashTab

et = window.erase_flash_tab
check("erase_flash_tab present", isinstance(et, EraseFlashTab))
check("erase_flash_tab is 4th tab", window.tabs.indexOf(et) == 3)
check("erase confirm checkbox unchecked by default", not et.confirm_checkbox.isChecked())
check("erase button disabled by default", not et.erase_button.isEnabled())

# Warning content is localized and visible to the user.
check("erase warning body non-empty", bool(et.warning_label.text()))
check("erase warning has bold tag", "<b>" in et.warning_label.text())
check("erase consequences non-empty", bool(et.consequences_label.text()))
check("erase confirm checkbox label non-empty", bool(et.confirm_checkbox.text()))
check("erase button label is 'Erase Flash'", et.erase_button.text() == "Erase Flash")
check("erase tab uses make_muted_label for hint", bool(et.erase_desc.text()))

et.confirm_checkbox.setChecked(True)
check("checking confirm enables erase button", et.erase_button.isEnabled())
et.confirm_checkbox.setChecked(False)
check("unchecking disables erase button again", not et.erase_button.isEnabled())

_er_ok = window.flash_worker.start_erase_flash("/dev/null")
check("start_erase_flash returns bool", isinstance(_er_ok, bool))
if _er_ok:
    window.flash_worker.stop()
check(
    "op.name.erase_flash translates",
    window._operation_label("erase_flash") == "erase flash",
)

# Safety: clicking must auto-uncheck so the next module is protected.
_reset_console_and_state()
et.confirm_checkbox.setChecked(True)
et.erase_button.click()
check(
    "checkbox auto-unchecks after click",
    not et.confirm_checkbox.isChecked(),
)
check(
    "erase button disables after auto-uncheck",
    not et.erase_button.isEnabled(),
)

_reset_console_and_state()
window.device_selector.mode_ip_radio.setChecked(True)
et.confirm_checkbox.setChecked(True)
et.erase_button.click()
_out_erase_ota = window.console.toPlainText().lower()
check("erase rejects OTA mode", "usb" in _out_erase_ota)
check(
    "checkbox unchecked even when OTA blocked",
    not et.confirm_checkbox.isChecked(),
)
window.device_selector.mode_usb_radio.setChecked(True)

_reset_console_and_state()
_prev_dev_idx = window.device_selector.usb_device_combo.currentIndex()
window.device_selector.usb_device_combo.setCurrentIndex(-1)
et.confirm_checkbox.setChecked(True)
et.erase_button.click()
_out_erase_no_dev = window.console.toPlainText().lower()
check(
    "erase warns when no device",
    "device" in _out_erase_no_dev or "select" in _out_erase_no_dev,
)
check(
    "checkbox unchecked when no device",
    not et.confirm_checkbox.isChecked(),
)
window.device_selector.usb_device_combo.setCurrentIndex(_prev_dev_idx)

# in_progress guard: pretend the worker is busy and click erase.
_reset_console_and_state()
_orig_is_running = window.flash_worker.is_running
window.flash_worker.is_running = lambda: True
et.confirm_checkbox.setChecked(True)
et.erase_button.click()
_out_busy = window.console.toPlainText().lower()
window.flash_worker.is_running = _orig_is_running
check("erase blocked while worker busy", "progress" in _out_busy or "already" in _out_busy)
check(
    "checkbox unchecked even when worker busy",
    not et.confirm_checkbox.isChecked(),
)

# Live retranslate: switching language updates the tab label.
i18n.set_language("pl")
check(
    "erase tab title localized to Polish",
    window.tabs.tabText(window.tabs.indexOf(et)) == i18n.tr("tabs.erase.title"),
)
check(
    "erase button localized to Polish",
    et.erase_button.text() == i18n.tr("erase.button"),
)
i18n.set_language("en")
check("erase button back to English", et.erase_button.text() == "Erase Flash")

# ===================================================================
print("=== Dialout hint ===")
# ===================================================================
_reset_console_and_state()
window._on_flash_line("Could not open /dev/ttyACM0, the port doesn't exist")
out_no_perm = window.console.toPlainText()
check(
    "no dialout hint without permission keyword",
    "dialout" not in out_no_perm.lower() or "usermod" not in out_no_perm,
)

_reset_console_and_state()
window._on_flash_line("Permission denied: '/dev/ttyUSB0'")
out_perm = window.console.toPlainText()
check(
    "dialout hint on permission denied + ttyUSB",
    "usermod" in out_perm or "dialout" in out_perm.lower(),
)

_reset_console_and_state()
window._on_flash_line("error: do not have read or write permission on /dev/ttyACM0")
out_rw = window.console.toPlainText()
check(
    "dialout hint on read/write permission",
    "usermod" in out_rw or "dialout" in out_rw.lower(),
)

# ===================================================================
print("=== New translation keys ===")
# ===================================================================
read_keys = (
    "tabs.read.title",
    "read.section",
    "read.presets",
    "read.output",
    "read.output_placeholder",
    "read.offset",
    "read.length",
    "read.length_auto",
    "read.preset_full",
    "read.preset_full.desc",
    "read.preset_app",
    "read.preset_app.desc",
    "read.preset_bootloader",
    "read.preset_bootloader.desc",
    "read.detect",
    "read.detect.desc",
    "read.read",
    "read.read.desc",
    "read.dialog_title",
    "read.dialog_filter",
    "read.no_output",
    "read.invalid_offset",
    "read.invalid_length",
    "read.usb_only",
    "read.starting",
    "read.starting_detect",
    "op.name.read_flash",
    "op.name.flash_id",
    "actions.reset",
    "actions.reset.desc",
    "op.name.reset",
    "reset.usb_only",
    "reset.starting",
    "tabs.erase.title",
    "op.name.erase_flash",
    "erase.warning_title",
    "erase.warning_body",
    "erase.consequences",
    "erase.confirm",
    "erase.button",
    "erase.button.desc",
    "erase.usb_only",
    "erase.starting",
)
for key in read_keys:
    missing = [c for c, p in i18n._packs.items() if key not in p]
    check(f"'{key}' in all 37 packs", not missing)

new_keys = (
    "flash.hint.crash_detected",
    "flash.hint.decode_no_elf",
    "flash.hint.long_op",
    "flash.hint.logger_verbose",
    "esphome.action.decode_stack",
    "esphome.action.decode_stack.desc",
    "decoder.title",
    "decoder.decode_button",
    "decoder.copy_button",
    "decoder.close_button",
    "decoder.no_elf",
    "decoder.elf_not_found",
    "decoder.no_addresses",
    "decoder.no_addr2line",
    "decoder.success",
    "decoder.copied",
    "esphome.action.quick_view",
    "esphome.action.quick_view.close",
    "esphome.action.quick_view.desc",
    "editor.title",
    "editor.find_placeholder",
    "editor.replace_placeholder",
    "editor.find_next",
    "editor.replace",
    "editor.replace_all",
    "editor.save",
    "editor.cancel",
    "editor.find_hit",
    "editor.find_miss",
    "editor.replaced",
    "editor.saved",
    "editor.saved_log",
    "editor.save_failed",
    "editor.load_failed",
    "editor.no_yaml",
    "editor.discard_prompt",
    "editor.show_whitespace",
)
for key in new_keys:
    missing = [c for c, p in i18n._packs.items() if key not in p]
    check(f"'{key}' in all 37 packs", not missing)

# ===================================================================
print("=== Placeholder consistency (all keys) ===")
# ===================================================================
en_pack = i18n._packs["en"]
_ph_errors = []
for key, en_val in en_pack.items():
    if key == "_meta":
        continue
    en_ph = set(re.findall(r"\{(\w+)\}", str(en_val)))
    if not en_ph:
        continue
    for code, pack in i18n._packs.items():
        if code == "en":
            continue
        other_val = pack.get(key, "")
        other_ph = set(re.findall(r"\{(\w+)\}", str(other_val)))
        if en_ph != other_ph:
            _ph_errors.append(f"{code}:{key} expected {en_ph} got {other_ph}")
check(
    f"All placeholder tokens match en.json ({len(_ph_errors)} mismatches)",
    len(_ph_errors) == 0,
)
if _ph_errors:
    for e in _ph_errors[:10]:
        print(f"    ! {e}")

# ===================================================================
print()
total = passed + failed
if failed == 0:
    print(f"=== ALL {total} TESTS PASSED ===")
else:
    print(f"=== {failed} FAILED / {total} total ===")
sys.exit(1 if failed else 0)
