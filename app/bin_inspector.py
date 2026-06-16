"""Parser for ESP firmware images (esp_image_header + esp_app_desc + segments)."""
from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path


# --- ESP image layout constants ---------------------------------------------
ESP_IMAGE_MAGIC = 0xE9
ESP_APP_DESC_MAGIC = 0xABCD5432

HEADER_SIZE = 8
EXT_HEADER_SIZE = 16
FULL_HEADER_SIZE = HEADER_SIZE + EXT_HEADER_SIZE  # 24
SEGMENT_HEADER_SIZE = 8

APP_DESC_OFFSET = 0x20
APP_DESC_SIZE = 256

CHECKSUM_BOUNDARY = 16
CHECKSUM_INIT = 0xEF

SHA256_SIZE = 32

STRING_SCAN_LIMIT = 256 * 1024
STRING_MIN_LEN = 6
PLAIN_REPORT_STRING_CAP = 80


# --- chip-id + flash flags --------------------------------------------------
CHIP_ID_MAP: dict[int, str] = {
    0x0000: "ESP32",
    0x0002: "ESP32-S2",
    0x0005: "ESP32-C3",
    0x0006: "ESP32-H4",
    0x0009: "ESP32-S3",
    0x000C: "ESP32-C2",
    0x000D: "ESP32-C6",
    0x0010: "ESP32-H2",
    0x0012: "ESP32-P4",
    0x0014: "ESP32-C5",
    0x0017: "ESP32-C61",
    0xFFFF: "ESP8266 (or chip_id unset)",
}

SPI_MODE_MAP: dict[int, str] = {
    0: "QIO",
    1: "QOUT",
    2: "DIO",
    3: "DOUT",
    4: "FAST_READ",
    5: "SLOW_READ",
}

SPI_SIZE_MAP: dict[int, str] = {
    0: "1 MB",
    1: "2 MB",
    2: "4 MB",
    3: "8 MB",
    4: "16 MB",
    5: "32 MB",
    6: "64 MB",
    7: "128 MB",
}

SPI_FREQ_MAP: dict[int, str] = {
    0x0: "40 MHz",
    0x1: "26 MHz",
    0x2: "20 MHz",
    0xF: "80 MHz",
}


def chip_name(chip_id: int) -> str:
    return CHIP_ID_MAP.get(chip_id, f"unknown chip_id 0x{chip_id:04X}")


# --- memory region resolution -----------------------------------------------
_CHIP_REGIONS: dict[int, list[tuple[int, int, str]]] = {
    0x0000: [
        (0x3F400000, 0x3F800000, "DROM (flash-mapped)"),
        (0x400D0000, 0x40400000, "IROM (flash-mapped)"),
        (0x40080000, 0x400A0000, "IRAM"),
        (0x3FFB0000, 0x40000000, "DRAM"),
        (0x400C0000, 0x400C2000, "RTC_IRAM"),
        (0x50000000, 0x50002000, "RTC_DATA"),
    ],
    0x0002: [
        (0x3F000000, 0x3FF80000, "DROM (flash-mapped)"),
        (0x40080000, 0x407FFFFF, "IROM (flash-mapped)"),
        (0x40020000, 0x4007FFFF, "IRAM"),
        (0x3FFB0000, 0x4002FFFF, "DRAM"),
    ],
    0x0005: [
        (0x3C000000, 0x3D000000, "DROM (flash-mapped)"),
        (0x42000000, 0x43000000, "IROM (flash-mapped)"),
        (0x40380000, 0x403E0000, "IRAM"),
        (0x3FC80000, 0x3FCE0000, "DRAM"),
    ],
    0x0009: [
        (0x3C000000, 0x3D000000, "DROM (flash-mapped)"),
        (0x42000000, 0x43000000, "IROM (flash-mapped)"),
        (0x40378000, 0x403E0000, "IRAM"),
        (0x3FC88000, 0x3FD00000, "DRAM"),
        (0x50000000, 0x50002000, "RTC_DATA"),
    ],
    0x000D: [
        (0x42000000, 0x43000000, "IROM (flash-mapped)"),
        (0x40800000, 0x40880000, "IRAM"),
    ],
    0x0010: [
        (0x42000000, 0x43000000, "IROM (flash-mapped)"),
        (0x40800000, 0x40850000, "IRAM"),
    ],
    0x0012: [
        (0x40000000, 0x41000000, "IROM (flash-mapped)"),
        (0x4FF00000, 0x4FFC0000, "IRAM"),
    ],
}

_HEURISTIC_HIGH_BYTE: dict[int, str] = {
    0x40: "IRAM (heuristic)",
    0x3F: "DROM (heuristic)",
    0x3C: "DROM (heuristic)",
    0x42: "IROM (heuristic)",
}


def region_for_addr(addr: int, chip_id: int) -> str:
    """Memory region name for `addr` on the given chip."""
    for start, end, name in _CHIP_REGIONS.get(chip_id, ()):
        if start <= addr < end:
            return name
    return _HEURISTIC_HIGH_BYTE.get((addr >> 24) & 0xFF, "unknown")


# --- data classes -----------------------------------------------------------
@dataclass
class Segment:
    index: int
    file_offset: int
    load_addr: int
    length: int
    region: str


@dataclass
class AppDesc:
    project_name: str
    version: str
    build_time: str
    build_date: str
    idf_ver: str
    secure_version: int
    elf_sha256_hex: str


@dataclass
class BinReport:
    file_path: str
    file_size: int
    image_kind: str
    magic_ok: bool
    chip_id: int
    chip_name: str
    segment_count: int
    spi_mode: str
    spi_size: str
    spi_freq: str
    entry_point: int
    min_chip_rev: int
    hash_appended: bool
    segments: list[Segment] = field(default_factory=list)
    app_desc: AppDesc | None = None
    checksum_ok: bool | None = None
    sha256_ok: bool | None = None
    sha256_value: str | None = None
    strings_of_interest: list[tuple[int, str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class RegionSpan:
    """File-offset range with a name and palette `kind`."""
    start: int
    end: int
    name: str
    kind: str


# --- string extraction ------------------------------------------------------
_PRINTABLE = bytes(range(0x20, 0x7F)) + b"\t"

_STRING_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("hostname", re.compile(r"\b[a-z0-9][a-z0-9-]{1,40}\.(local|lan|home|internal)\b", re.I)),
    ("ip_address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("mqtt_uri", re.compile(r"\bmqtts?://[\w\.\-:@/]+", re.I)),
    ("http_uri", re.compile(r"\bhttps?://[\w\.\-:@/]+", re.I)),
    ("esphome_ver", re.compile(r"esphome[/\-_ ]\d{4}\.\d+\.\d+", re.I)),
    ("idf_ver", re.compile(r"v\d+\.\d+(?:\.\d+)?(?:-\w+)?")),
    ("wifi_ssid_hint", re.compile(r"SSID[: ]+\S+", re.I)),
]


def _extract_strings(data: bytes, min_len: int = STRING_MIN_LEN) -> list[tuple[int, str]]:
    """Printable ASCII runs of `min_len`+ chars; returns (offset, text)."""
    out: list[tuple[int, str]] = []
    start = -1
    buf = bytearray()
    for i, b in enumerate(data):
        if b in _PRINTABLE:
            if start < 0:
                start = i
            buf.append(b)
            continue
        if len(buf) >= min_len:
            out.append((start, buf.decode("ascii", "replace")))
        start = -1
        buf = bytearray()
    if len(buf) >= min_len:
        out.append((start, buf.decode("ascii", "replace")))
    return out


def _match_interesting(strings: list[tuple[int, str]]) -> list[tuple[int, str, str]]:
    """Strings matching `_STRING_PATTERNS`; returns (offset, category, value)."""
    out: list[tuple[int, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for offset, s in strings:
        for category, pattern in _STRING_PATTERNS:
            for m in pattern.finditer(s):
                key = (category, m.group(0))
                if key in seen:
                    continue
                seen.add(key)
                out.append((offset + m.start(), category, m.group(0)))
    return out


def _decode_ascii(buf: bytes) -> str:
    """NUL-terminated ASCII string from a fixed-size buffer."""
    end = buf.find(b"\x00")
    if end >= 0:
        buf = buf[:end]
    return buf.decode("ascii", "replace").strip()


# --- structured parsing helpers ---------------------------------------------
def _empty_report(path: str, size: int) -> BinReport:
    return BinReport(
        file_path=path,
        file_size=size,
        image_kind="unknown",
        magic_ok=False,
        chip_id=0xFFFF,
        chip_name="unknown",
        segment_count=0,
        spi_mode="?",
        spi_size="?",
        spi_freq="?",
        entry_point=0,
        min_chip_rev=0,
        hash_appended=False,
    )


def _apply_header(report: BinReport, data: bytes) -> None:
    """Image header: segment count, SPI flags, entry point."""
    seg_count = data[1]
    spi_mode_byte = data[2]
    spi_size_freq = data[3]
    entry = struct.unpack_from("<I", data, 4)[0]
    report.segment_count = seg_count
    report.spi_mode = SPI_MODE_MAP.get(spi_mode_byte, f"unknown ({spi_mode_byte})")
    report.spi_size = SPI_SIZE_MAP.get(spi_size_freq >> 4, f"unknown ({spi_size_freq >> 4})")
    report.spi_freq = SPI_FREQ_MAP.get(spi_size_freq & 0x0F, f"unknown ({spi_size_freq & 0x0F})")
    report.entry_point = entry


def _apply_ext_header(report: BinReport, data: bytes) -> None:
    """Extended header (bytes 8-23): chip_id, min_chip_rev, hash_appended."""
    chip_id = struct.unpack_from("<H", data, 12)[0]
    report.chip_id = chip_id
    report.chip_name = chip_name(chip_id)
    report.min_chip_rev = data[14]
    report.hash_appended = bool(data[23])
    report.image_kind = "esp_app_image"


def _try_parse_app_desc(report: BinReport, data: bytes) -> None:
    """`esp_app_desc_t` at offset 0x20, if present."""
    if len(data) < APP_DESC_OFFSET + 4:
        return
    magic_word = struct.unpack_from("<I", data, APP_DESC_OFFSET)[0]
    if magic_word != ESP_APP_DESC_MAGIC:
        return
    try:
        report.app_desc = _parse_app_desc(data, APP_DESC_OFFSET)
    except Exception as e:
        report.warnings.append(f"esp_app_desc parse failed: {e}")


def _parse_app_desc(data: bytes, offset: int) -> AppDesc:
    """Parse an `esp_app_desc_t` struct at `offset`."""
    secure_version = struct.unpack_from("<I", data, offset + 0x04)[0]
    version = _decode_ascii(data[offset + 0x10:offset + 0x30])
    project_name = _decode_ascii(data[offset + 0x30:offset + 0x50])
    build_time = _decode_ascii(data[offset + 0x50:offset + 0x60])
    build_date = _decode_ascii(data[offset + 0x60:offset + 0x70])
    idf_ver = _decode_ascii(data[offset + 0x70:offset + 0x90])
    elf_sha = data[offset + 0x90:offset + 0xB0].hex()
    return AppDesc(
        project_name=project_name,
        version=version,
        build_time=build_time,
        build_date=build_date,
        idf_ver=idf_ver,
        secure_version=secure_version,
        elf_sha256_hex=elf_sha,
    )


def _parse_segments(report: BinReport, data: bytes) -> int:
    """Segment table; returns the file offset past the last segment."""
    size = len(data)
    cursor = FULL_HEADER_SIZE
    for i in range(report.segment_count):
        if cursor + SEGMENT_HEADER_SIZE > size:
            report.warnings.append(f"segment {i}: header at 0x{cursor:X} runs past EOF")
            break
        load_addr, seg_len = struct.unpack_from("<II", data, cursor)
        data_offset = cursor + SEGMENT_HEADER_SIZE
        if data_offset + seg_len > size:
            report.warnings.append(f"segment {i}: declared length 0x{seg_len:X} runs past EOF")
            seg_len = max(0, size - data_offset)
        report.segments.append(
            Segment(
                index=i,
                file_offset=data_offset,
                load_addr=load_addr,
                length=seg_len,
                region=region_for_addr(load_addr, report.chip_id),
            )
        )
        cursor = data_offset + seg_len
    return cursor


def _verify_checksum(report: BinReport, data: bytes, cursor: int) -> None:
    """XOR checksum byte at the next 16-byte boundary."""
    pad = (CHECKSUM_BOUNDARY - 1) - (cursor % CHECKSUM_BOUNDARY)
    checksum_pos = cursor + pad
    if checksum_pos >= len(data):
        return
    expected = CHECKSUM_INIT
    for seg in report.segments:
        for b in data[seg.file_offset:seg.file_offset + seg.length]:
            expected ^= b
    report.checksum_ok = expected == data[checksum_pos]


def _verify_sha256(report: BinReport, data: bytes) -> None:
    """Appended SHA256 footer (last 32 bytes) vs. a fresh hash of the body."""
    if not report.hash_appended or len(data) < SHA256_SIZE:
        return
    tail_sha = data[-SHA256_SIZE:]
    computed = hashlib.sha256(data[:-SHA256_SIZE]).digest()
    report.sha256_ok = tail_sha == computed
    report.sha256_value = tail_sha.hex()


def _collect_strings(report: BinReport, data: bytes) -> None:
    scan = data[: min(len(data), STRING_SCAN_LIMIT)]
    report.strings_of_interest = _match_interesting(_extract_strings(scan))


# --- public entry point -----------------------------------------------------
def parse_bin(path: str) -> BinReport:
    """Parse an ESP firmware .bin; bad input populates `BinReport.warnings`."""
    p = Path(path)
    data = p.read_bytes()
    size = len(data)
    report = _empty_report(str(p), size)

    if size < HEADER_SIZE:
        report.warnings.append(f"file too small (<{HEADER_SIZE} bytes)")
        return report

    magic = data[0]
    report.magic_ok = magic == ESP_IMAGE_MAGIC
    if not report.magic_ok:
        if size >= 0x8000 + SHA256_SIZE:
            report.image_kind = "raw_flash_dump_or_unknown"
            report.warnings.append(
                f"first byte 0x{magic:02X} is not 0xE9 (ESP image magic); "
                "could be a raw flash dump, partition table or non-ESP binary"
            )
        else:
            report.warnings.append(f"first byte 0x{magic:02X} is not 0xE9 - not an ESP image")
        return report

    _apply_header(report, data)

    if size < FULL_HEADER_SIZE:
        report.image_kind = "esp8266_image"
        report.warnings.append("image too short for an ESP32 extended header")
        return report

    _apply_ext_header(report, data)
    _try_parse_app_desc(report, data)
    cursor = _parse_segments(report, data)
    _verify_checksum(report, data, cursor)
    _verify_sha256(report, data)
    _collect_strings(report, data)

    return report


# --- text rendering ---------------------------------------------------------
def format_plain_report(report: BinReport) -> str:
    """`BinReport` as a human-readable text dump."""
    lines: list[str] = []
    push = lines.append

    push(f"file:     {report.file_path}")
    push(f"size:     {report.file_size:,} bytes ({report.file_size / 1024:.1f} KB)")
    push(f"kind:     {report.image_kind}")
    push("")

    if not report.magic_ok:
        push("Image magic byte 0xE9 not found at offset 0.")
        for w in report.warnings:
            push(f"WARNING: {w}")
        return "\n".join(lines)

    push("=== Image header ===")
    push(f"chip:           {report.chip_name} (chip_id=0x{report.chip_id:04X})")
    push(f"segments:       {report.segment_count}")
    push(f"flash:          {report.spi_mode} @ {report.spi_freq}, {report.spi_size}")
    push(f"entry point:    0x{report.entry_point:08X}")
    push(f"min chip rev:   {report.min_chip_rev}")
    push(f"hash appended:  {'yes' if report.hash_appended else 'no'}")
    push("")

    if report.app_desc:
        ad = report.app_desc
        push("=== ESPHome / ESP-IDF application descriptor ===")
        push(f"project:        {ad.project_name or '(empty)'}")
        push(f"version:        {ad.version or '(empty)'}")
        push(f"built:          {ad.build_date} {ad.build_time}")
        push(f"ESP-IDF:        {ad.idf_ver}")
        push(f"secure ver:     {ad.secure_version}")
        push(f"ELF SHA256:     {ad.elf_sha256_hex}")
        push("")
    else:
        push("(no esp_app_desc_t magic at offset 0x20 - bootloader, partition table or non-app image)")
        push("")

    if report.segments:
        push(f"=== Segments ({len(report.segments)}) ===")
        for s in report.segments:
            push(
                f"  [{s.index}] file@0x{s.file_offset:06X}  load=0x{s.load_addr:08X}  "
                f"size={s.length:>7,} B  {s.region}"
            )
        push("")

    push("=== Integrity ===")
    if report.checksum_ok is None:
        push("checksum:       (not located)")
    else:
        push(f"checksum:       {'VALID' if report.checksum_ok else 'INVALID'}")
    if report.hash_appended:
        if report.sha256_ok is None:
            push("SHA256:         (not validated)")
        else:
            push(f"SHA256:         {'VALID' if report.sha256_ok else 'INVALID'} ({report.sha256_value})")
    push("")

    if report.strings_of_interest:
        push(f"=== Strings of interest ({len(report.strings_of_interest)}) ===")
        for off, cat, val in report.strings_of_interest[:PLAIN_REPORT_STRING_CAP]:
            push(f"  +0x{off:06X}  {cat:14s}  {val}")
        if len(report.strings_of_interest) > PLAIN_REPORT_STRING_CAP:
            extra = len(report.strings_of_interest) - PLAIN_REPORT_STRING_CAP
            push(f"  ... +{extra} more (truncated)")
        push("")

    if report.warnings:
        push("=== Warnings ===")
        for w in report.warnings:
            push(f"  ! {w}")

    return "\n".join(lines)


BYTES_PER_HEX_LINE = 16


def hex_dump_lines(data: bytes, base_offset: int = 0, max_bytes: int = 1024) -> list[tuple[int, str]]:
    """Hex+ASCII dump; returns (file_offset, formatted_line) pairs."""
    out: list[tuple[int, str]] = []
    end = min(len(data), max_bytes)
    for line_start in range(0, end, BYTES_PER_HEX_LINE):
        chunk = data[line_start:line_start + BYTES_PER_HEX_LINE]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        if len(chunk) < BYTES_PER_HEX_LINE:
            hex_part = hex_part + "   " * (BYTES_PER_HEX_LINE - len(chunk))
        ascii_part = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        absolute = base_offset + line_start
        out.append((absolute, f"0x{absolute:06X}   {hex_part}   {ascii_part}"))
    return out


def region_spans(report: BinReport) -> list[RegionSpan]:
    """Structural regions of the image as file-offset spans."""
    spans: list[RegionSpan] = []
    if not report.magic_ok:
        return spans
    spans.append(RegionSpan(0, HEADER_SIZE, "image header", "header"))
    spans.append(RegionSpan(HEADER_SIZE, FULL_HEADER_SIZE, "ext header", "ext_header"))

    cursor = FULL_HEADER_SIZE
    for i, s in enumerate(report.segments):
        spans.append(RegionSpan(cursor, s.file_offset, f"seg {i} header", "seg_header"))
        spans.append(RegionSpan(s.file_offset, s.file_offset + s.length, f"seg {i} data", "segment"))
        cursor = s.file_offset + s.length

    if report.app_desc:
        spans.append(
            RegionSpan(APP_DESC_OFFSET, APP_DESC_OFFSET + APP_DESC_SIZE, "esp_app_desc_t", "app_desc")
        )

    if report.hash_appended and report.file_size >= SHA256_SIZE:
        spans.append(
            RegionSpan(report.file_size - SHA256_SIZE, report.file_size, "SHA256 footer", "footer")
        )

    return spans
