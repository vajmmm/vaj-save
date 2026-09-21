"""Persist filesystem-volume ids to relative save-root bindings."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from .models import ScanResult
from .persistence import atomic_write_json
from .settings_store import SettingsStore
from .volume_id import volume_id_for

DEVICES_NAME = "devices.json"
DEVICES_FORMAT = "vaj-save-devices"
DEVICES_FORMAT_VERSION = 1


@dataclass(frozen=True)
class BoundSource:
    platform: str
    source_id: str
    relative_root: str


def relative_source_root(mount: Path, source_root: str) -> str | None:
    """Return a POSIX path of ``source_root`` relative to ``mount``.

    Rejects paths that escape the mount (including any ``..`` segment).
    """
    try:
        mount_res = Path(mount).resolve()
        raw = Path(source_root)
        if ".." in raw.parts:
            return None
        src_res = raw.resolve() if raw.is_absolute() else (mount_res / raw).resolve()
        rel = src_res.relative_to(mount_res)
    except (OSError, RuntimeError, ValueError):
        return None
    posix = rel.as_posix()
    if posix.startswith("..") or "/../" in f"/{posix}/":
        return None
    return posix


def device_key_for(mount: Path, extra: dict | None = None) -> str | None:
    """Registry key for a mount: volume serial, ``path:{resolved}``, or ``ftp:{preset}``."""
    payload = extra or {}
    if payload.get("ftp"):
        preset = payload.get("ftp_preset")
        if isinstance(preset, str) and preset.strip():
            return f"ftp:{preset.strip()}"
        return None
    if payload.get("custom"):
        try:
            return f"path:{Path(mount).resolve()}"
        except OSError:
            return None
    return volume_id_for(mount, extra)


def bound_sources_from_result(mount: Path, result: ScanResult) -> List[BoundSource]:
    """Convert ``ScanResult.sources`` to relative bindings under ``mount``."""
    out: List[BoundSource] = []
    seen: set[str] = set()
    for source in result.sources:
        rel = relative_source_root(mount, source.root_path)
        if rel is None or rel in seen:
            continue
        seen.add(rel)
        out.append(
            BoundSource(
                platform=source.platform,
                source_id=source.source_id,
                relative_root=rel,
            )
        )
    return out


def _valid_relative_root(relative_root: str) -> bool:
    text = str(relative_root or "").strip().replace("\\", "/")
    if not text:
        return False
    parts = Path(text).parts
    if not parts or ".." in parts:
        return False
    if parts[0] == "/":
        return False
    return True


class DeviceRegistry:
    """``devices.json``: volume id → relative save roots next to ``config.json``."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else SettingsStore().config_dir() / DEVICES_NAME

    def get(self, volume_id: str) -> list[BoundSource]:
        device = self._devices().get(volume_id)
        if not isinstance(device, dict):
            return []
        return self._parse_sources(device.get("sources"))

    def record(
        self,
        volume_id: str,
        *,
        label: str,
        sources: list[BoundSource],
        merge: bool,
    ) -> None:
        data = self._load()
        devices = data.setdefault("devices", {})
        if not isinstance(devices, dict):
            devices = {}
            data["devices"] = devices
        existing = devices.get(volume_id) if isinstance(devices.get(volume_id), dict) else {}
        incoming = [item for item in sources if _valid_relative_root(item.relative_root)]
        if merge:
            by_root = {item.relative_root: item for item in self._parse_sources(existing.get("sources"))}
            for item in incoming:
                by_root[item.relative_root] = item
            combined = list(by_root.values())
        else:
            combined = incoming
        devices[volume_id] = {
            "label": label,
            "updated_at": datetime.now().replace(microsecond=0).isoformat(),
            "sources": [
                {
                    "platform": item.platform,
                    "source_id": item.source_id,
                    "relative_root": item.relative_root,
                }
                for item in combined
            ],
        }
        data["format"] = DEVICES_FORMAT
        data["format_version"] = DEVICES_FORMAT_VERSION
        atomic_write_json(self.path, data)

    def usable_sources(self, volume_id: str, mount: Path) -> list[BoundSource]:
        usable: List[BoundSource] = []
        for item in self.get(volume_id):
            candidate = Path(mount) / item.relative_root
            try:
                if candidate.is_dir():
                    usable.append(item)
            except OSError:
                continue
        return usable

    def _load(self) -> Dict[str, Any]:
        empty: Dict[str, Any] = {
            "format": DEVICES_FORMAT,
            "format_version": DEVICES_FORMAT_VERSION,
            "devices": {},
        }
        try:
            if not self.path.is_file():
                return empty
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return empty
        if not isinstance(payload, dict):
            return empty
        devices = payload.get("devices")
        if not isinstance(devices, dict):
            payload["devices"] = {}
        return payload

    def _devices(self) -> Dict[str, Any]:
        devices = self._load().get("devices")
        return devices if isinstance(devices, dict) else {}

    @staticmethod
    def _parse_sources(raw: object) -> List[BoundSource]:
        if not isinstance(raw, list):
            return []
        out: List[BoundSource] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            platform = str(item.get("platform") or "").strip()
            source_id = str(item.get("source_id") or "").strip()
            relative_root = str(item.get("relative_root") or "").strip().replace("\\", "/")
            if not platform or not source_id or not _valid_relative_root(relative_root):
                continue
            out.append(
                BoundSource(
                    platform=platform,
                    source_id=source_id,
                    relative_root=relative_root,
                )
            )
        return out


__all__ = [
    "BoundSource",
    "DeviceRegistry",
    "bound_sources_from_result",
    "device_key_for",
    "relative_source_root",
]
