from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class SaveEntry:
    platform: str
    source_id: str
    display_name: str
    path: str
    title_id: Optional[str] = None
    slot: Optional[str] = None
    user: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "platform": self.platform,
            "source_id": self.source_id,
            "display_name": self.display_name,
            "path": self.path,
        }
        if self.title_id is not None:
            d["title_id"] = self.title_id
        if self.slot is not None:
            d["slot"] = self.slot
        if self.user is not None:
            d["user"] = self.user
        if self.extra:
            d["extra"] = self.extra
        return d


@dataclass
class SaveSource:
    source_id: str
    platform: str
    description: str
    root_path: str
    extra: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "source_id": self.source_id,
            "platform": self.platform,
            "description": self.description,
            "root_path": self.root_path,
        }
        if self.extra:
            d["extra"] = self.extra
        return d


@dataclass
class ScanResult:
    root_path: str
    platform: str
    sources: List[SaveSource] = field(default_factory=list)
    saves: List[SaveEntry] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root_path": self.root_path,
            "platform": self.platform,
            "sources": [s.to_dict() for s in self.sources],
            "saves": [s.to_dict() for s in self.saves],
            "warnings": self.warnings,
        }


@dataclass
class VolumeInfo:
    name: str
    mount_point: Path
    is_removable: bool = True
    extra: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name": self.name,
            "mount_point": str(self.mount_point),
            "is_removable": self.is_removable,
        }
        if self.extra:
            d["extra"] = self.extra
        return d
