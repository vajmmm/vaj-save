"""vajsave: Read-only handheld console save manager and scanner."""

from .models import SaveEntry, SaveSource, ScanResult, VolumeInfo
from .sfo import parse_sfo
from .scanner import scan
from .volume import (
    VolumeProvider,
    MountedVolumeProvider,
    FakeVolumeProvider,
    watch_volumes,
)
from .backend import (
    StorageBackend,
    MountedVolumeBackend,
    FakeStorageBackend,
)

__version__ = "0.1.0"
__all__ = [
    "SaveEntry",
    "SaveSource",
    "ScanResult",
    "VolumeInfo",
    "parse_sfo",
    "scan",
    "VolumeProvider",
    "MountedVolumeProvider",
    "FakeVolumeProvider",
    "watch_volumes",
    "StorageBackend",
    "MountedVolumeBackend",
    "FakeStorageBackend",
]
