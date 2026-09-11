"""Artwork resolution and download orchestration.

The public fallback order is fixed and tested:

    user local  >  downloaded  >  embedded  >  placeholder

* **user local** -- ``<library_root>/covers/<platform>/<name>.<ext>`` (the
  existing :func:`vajsave.covers.user_cover_path`);
* **downloaded** -- an image fetched from a provider and committed to the
  identity-hash cover cache;
* **embedded** -- an icon found inside the save folder during the scan;
* **placeholder** -- no path; the UI paints its light-grey square.

:func:`resolve_artwork` is the synchronous, network-free resolver used for the
first paint.  :meth:`ArtworkService.ensure_cover` additionally attempts one
bounded download before falling back to the embedded icon, and is meant to run
off the UI thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Union

from ..covers import find_embedded_cover, user_cover_path
from .cache import CoverCache
from .downloader import ArtworkDownloader
from .providers import ArtworkProvider, ArtworkRef, LibretroArtworkProvider

SOURCE_USER = "user"
SOURCE_DOWNLOADED = "downloaded"
SOURCE_EMBEDDED = "embedded"
SOURCE_PLACEHOLDER = "placeholder"


@dataclass(frozen=True)
class ArtworkResolution:
    """Where a cover came from, and the local file to render (if any)."""

    path: Optional[str]
    source: str

    @property
    def is_placeholder(self) -> bool:
        return self.path is None


PLACEHOLDER = ArtworkResolution(None, SOURCE_PLACEHOLDER)


def _embedded_path(entry) -> Optional[Path]:
    raw = getattr(entry, "cover_path", None)
    if raw:
        candidate = Path(raw)
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            pass
    try:
        return find_embedded_cover(getattr(entry, "path", None))
    except Exception:  # noqa: BLE001 - cover discovery must never raise
        return None


def _user_path(entry, library_root) -> Optional[Path]:
    try:
        return user_cover_path(entry, library_root)
    except Exception:  # noqa: BLE001
        return None


def _downloaded_path(cache: Optional[CoverCache], identity_key: Optional[str]) -> Optional[Path]:
    if cache is None or not identity_key:
        return None
    try:
        return cache.lookup(identity_key)
    except Exception:  # noqa: BLE001
        return None


def resolve_artwork(
    entry,
    library_root,
    *,
    cache: Optional[CoverCache] = None,
    identity_key: Optional[str] = None,
) -> ArtworkResolution:
    """Network-free best cover: user local > downloaded cache > embedded."""
    if entry is None:
        return PLACEHOLDER
    user = _user_path(entry, library_root)
    if user is not None:
        return ArtworkResolution(str(user), SOURCE_USER)
    downloaded = _downloaded_path(cache, identity_key)
    if downloaded is not None:
        return ArtworkResolution(str(downloaded), SOURCE_DOWNLOADED)
    embedded = _embedded_path(entry)
    if embedded is not None:
        return ArtworkResolution(str(embedded), SOURCE_EMBEDDED)
    return PLACEHOLDER


class ArtworkService:
    """Ties the provider list, downloader and cover cache together."""

    def __init__(
        self,
        *,
        cache: Optional[CoverCache] = None,
        downloader: Optional[ArtworkDownloader] = None,
        providers: Optional[Iterable[ArtworkProvider]] = None,
    ) -> None:
        self.cache = cache
        self.downloader = downloader or ArtworkDownloader()
        if providers is None:
            self.providers: List[ArtworkProvider] = [LibretroArtworkProvider()]
        else:
            self.providers = list(providers)

    # -- provider selection --------------------------------------------------

    def ref_for(self, platform: str, title: Optional[str]) -> Optional[ArtworkRef]:
        """First provider that supports ``platform`` and yields a URL."""
        if not title:
            return None
        for provider in self.providers:
            try:
                ref = provider.ref_for(platform, title)
            except Exception:  # noqa: BLE001 - one bad provider must not stop the rest
                continue
            if ref is not None:
                return ref
        return None

    # -- resolution ----------------------------------------------------------

    def resolve(
        self,
        entry,
        library_root,
        *,
        identity_key: Optional[str] = None,
    ) -> ArtworkResolution:
        """Synchronous, no-network resolution (first paint)."""
        return resolve_artwork(
            entry, library_root, cache=self.cache, identity_key=identity_key
        )

    def ensure_downloaded(
        self,
        *,
        platform: str,
        title: Optional[str],
        identity_key: Optional[str],
    ) -> Optional[Path]:
        """Fetch + cache one cover. A valid cache hit performs zero network I/O."""
        if self.cache is None or not identity_key:
            return None
        hit = _downloaded_path(self.cache, identity_key)
        if hit is not None:
            return hit
        ref = self.ref_for(platform, title)
        if ref is None:
            return None
        data = self.downloader.fetch(ref.url)
        if not data:
            return None
        return self.cache.store(identity_key, data, url=ref.url, source=ref.provider)

    def ensure_cover(
        self,
        entry,
        *,
        platform: str,
        title: Optional[str],
        identity_key: Optional[str],
        library_root: Union[Path, str, None],
    ) -> ArtworkResolution:
        """Full fallback order including a download attempt.

        Intended to run on a worker thread: it may block on the network, but any
        failure simply falls through to the embedded icon or the placeholder.
        """
        if entry is None:
            return PLACEHOLDER
        user = _user_path(entry, library_root)
        if user is not None:
            return ArtworkResolution(str(user), SOURCE_USER)
        cached = _downloaded_path(self.cache, identity_key)
        if cached is not None:
            return ArtworkResolution(str(cached), SOURCE_DOWNLOADED)
        downloaded = self.ensure_downloaded(
            platform=platform, title=title, identity_key=identity_key
        )
        if downloaded is not None:
            return ArtworkResolution(str(downloaded), SOURCE_DOWNLOADED)
        embedded = _embedded_path(entry)
        if embedded is not None:
            return ArtworkResolution(str(embedded), SOURCE_EMBEDDED)
        return PLACEHOLDER


__all__ = [
    "ArtworkResolution",
    "ArtworkService",
    "PLACEHOLDER",
    "resolve_artwork",
    "SOURCE_USER",
    "SOURCE_DOWNLOADED",
    "SOURCE_EMBEDDED",
    "SOURCE_PLACEHOLDER",
]
