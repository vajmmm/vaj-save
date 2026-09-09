from pathlib import Path
from typing import Iterable, List, Optional, Protocol, Union

from .volume import MountedVolumeProvider, VolumeProvider


class StorageBackend(Protocol):
    """Protocol for iterating root paths to be scanned."""

    def iter_roots(self) -> Iterable[Path]:
        ...


class MountedVolumeBackend:
    """Backend discovering roots via a VolumeProvider."""

    def __init__(self, provider: Optional[VolumeProvider] = None) -> None:
        self.provider = provider or MountedVolumeProvider()

    def iter_roots(self) -> Iterable[Path]:
        for vol in self.provider.list_volumes():
            yield Path(vol.mount_point)


class FakeStorageBackend:
    """Fake storage backend providing a fixed list of roots."""

    def __init__(self, roots: Optional[List[Union[Path, str]]] = None) -> None:
        self.roots = [Path(r) for r in (roots or [])]

    def iter_roots(self) -> Iterable[Path]:
        for r in self.roots:
            yield r
