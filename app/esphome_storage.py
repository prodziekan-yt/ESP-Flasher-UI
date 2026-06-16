"""Read ESPHome's per-device manifests from `.esphome/storage/*.yaml.json`."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class KnownDevice:
    name: str
    friendly_name: str
    address: str
    esp_platform: str
    framework: str
    firmware_bin_path: str
    yaml_path: str
    storage_path: str
    integrations: list[str] = field(default_factory=list)

    def display(self) -> str:
        """Compact one-line label for the QComboBox."""
        parts = [self.friendly_name or self.name]
        if self.friendly_name and self.name and self.name != self.friendly_name:
            parts.append(f"({self.name})")
        if self.address:
            parts.append(f"@ {self.address}")
        if self.esp_platform:
            parts.append(f"[{self.esp_platform}]")
        return " ".join(parts)


def discover_known_devices(project_dir: str | Path) -> list[KnownDevice]:
    """`KnownDevice`s under `<project_dir>/.esphome/storage/`; never raises."""
    project = Path(project_dir)
    storage = project / ".esphome" / "storage"
    if not storage.is_dir():
        return []

    devices: list[KnownDevice] = []
    for json_path in sorted(storage.glob("*.yaml.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        name = (data.get("name") or json_path.stem.removesuffix(".yaml")).strip()
        if not name:
            continue

        yaml_candidate = project / json_path.name.removesuffix(".json")
        bin_path = data.get("firmware_bin_path") or ""

        devices.append(
            KnownDevice(
                name=name,
                friendly_name=(data.get("friendly_name") or "").strip(),
                address=(data.get("address") or "").strip(),
                esp_platform=(data.get("esp_platform") or "").strip(),
                framework=(data.get("framework") or "").strip(),
                firmware_bin_path=bin_path if Path(bin_path).is_file() else "",
                yaml_path=str(yaml_candidate) if yaml_candidate.is_file() else "",
                storage_path=str(json_path),
                integrations=list(data.get("loaded_integrations") or []),
            )
        )

    devices.sort(key=lambda d: d.name.lower())
    return devices
