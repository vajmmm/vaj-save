"""Artwork resolution and download orchestration.

The public fallback order is fixed and tested:

    user local  >  downloaded  >  embedded  >  placeholder

* **user local** -- ``<library_root>/covers/<platform>/<name>.<ext>`` (the
  existing :func:`vajsave.covers.user_cover_path`);
* **downloaded** -- an image fetched from a provider and committed to the
  identity-hash cover cache (``covers/<platform>/<identity-hash>.png``);
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
from typing import Callable, Iterable, List, Optional, Sequence, Tuple, Union

from ..covers import find_embedded_cover, user_cover_path
from .boxart_index import (
    ambiguous_boxart_matches,
    fetch_boxart_listing,
    loose_boxart_candidates,
    resolve_boxart_system,
    unique_boxart_match,
)
from .cache import CoverCache
from .downloader import ArtworkDownloader
from .providers import (
    Artwork,
    ArtworkProvider,
    LibretroThumbnailProvider,
    libretro_title_candidates,
)
from .title_ids import fetch_3dsdb_catalog, load_catalog, store_catalog, title_candidates_for_id

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


def _downloaded_path(
    cache: Optional[CoverCache],
    platform: Optional[str],
    identity_key: Optional[str],
) -> Optional[Path]:
    if cache is None or not identity_key:
        return None
    try:
        return cache.lookup(platform or "", identity_key)
    except Exception:  # noqa: BLE001
        return None


def resolve_artwork(
    entry,
    library_root,
    *,
    cache: Optional[CoverCache] = None,
    identity_key: Optional[str] = None,
    platform: Optional[str] = None,
) -> ArtworkResolution:
    """Network-free best cover: user local > downloaded cache > embedded."""
    if entry is None:
        return PLACEHOLDER
    plat = platform if platform is not None else getattr(entry, "platform", None)
    user = _user_path(entry, library_root)
    if user is not None:
        return ArtworkResolution(str(user), SOURCE_USER)
    downloaded = _downloaded_path(cache, plat, identity_key)
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
        llm_chooser: Optional[Callable[[Sequence[str], str], Optional[str]]] = None,
    ) -> None:
        self.cache = cache
        self.downloader = downloader or ArtworkDownloader()
        # Optional ``(candidates, query) -> filename|None`` resolver for a
        # genuinely ambiguous listing (different titles sharing a query).
        # ``None`` keeps the app fully deterministic and offline-by-default.
        self.llm_chooser = llm_chooser
        if providers is None:
            self.providers: List[ArtworkProvider] = [LibretroThumbnailProvider()]
        else:
            self.providers = list(providers)

    # -- provider selection --------------------------------------------------

    def cover_for(self, metadata) -> Optional[Artwork]:
        """First provider that supports the platform and yields artwork."""
        if metadata is None:
            return None
        for provider in self.providers:
            try:
                artwork = provider.find_cover(metadata)
            except Exception:  # noqa: BLE001 - one bad provider must not stop the rest
                continue
            if artwork is not None:
                return artwork
        return None

    def ref_for(self, platform: str, title: str) -> Optional[Artwork]:
        """Build artwork from a platform+title via the first provider (compat)."""
        if not title:
            return None
        for provider in self.providers:
            try:
                artwork = provider.cover_for(platform, title)
            except Exception:  # noqa: BLE001
                continue
            if artwork is not None:
                return artwork
        return None

    # -- resolution ----------------------------------------------------------

    def resolve(
        self,
        entry,
        library_root,
        *,
        identity_key: Optional[str] = None,
        platform: Optional[str] = None,
    ) -> ArtworkResolution:
        """Synchronous, no-network resolution (first paint)."""
        return resolve_artwork(
            entry,
            library_root,
            cache=self.cache,
            identity_key=identity_key,
            platform=platform,
        )

    def ensure_downloaded(
        self,
        metadata,
        *,
        identity_key: Optional[str],
        platform: Optional[str] = None,
    ) -> Optional[Path]:
        """Fetch + cache one cover. A valid cache hit performs zero network I/O."""
        if self.cache is None or not identity_key:
            return None
        plat = platform if platform is not None else getattr(metadata, "platform", None)
        hit = _downloaded_path(self.cache, plat, identity_key)
        if hit is not None:
            return hit
        artwork = self.cover_for(metadata)
        if artwork is None:
            return None
        return self._store_artwork(artwork, identity_key=identity_key, platform=plat)

    def _store_artwork(
        self,
        artwork: Artwork,
        *,
        identity_key: Optional[str],
        platform: Optional[str] = None,
    ) -> Optional[Path]:
        """Fetch ``artwork`` and commit it to the cache; ``None`` on any failure."""
        if self.cache is None or not identity_key:
            return None
        data = self.downloader.fetch(artwork.url)
        if not data:
            return None
        return self.cache.store(
            platform or artwork.platform,
            identity_key,
            data,
            provider=artwork.provider,
            canonical_title=artwork.canonical_title,
            remote_url=artwork.url,
        )

    def ensure_cover_for_title(
        self,
        entry,
        *,
        platform: str,
        title: str,
        identity_key: Optional[str],
        library_root: Union[Path, str, None],
        title_id: Optional[str] = None,
    ) -> ArtworkResolution:
        """Cover for a platform whose provider key is the save's own title.

        PSP/Vita have no ROM index, so the caller passes the PARAM.SFO / display
        title explicitly.  Unlike :meth:`ensure_cover`, an embedded icon
        (``ICON0.PNG`` / ``sce_sys/icon0.png``) is preferred over a download: a
        save that already ships artwork is never looked up online.

        Checkpoint / SFO titles often miss the No-Intro filename on the first
        try, so :func:`libretro_title_candidates` walks a short list of
        whitespace, case and region variants until one download succeeds.
        3DS Checkpoint short IDs (``0x00306``) are expanded to Title IDs and
        looked up in the cached 3dsdb eShop list first.  When every candidate
        404s the provider's ``Named_Boxarts`` directory listing is consulted and
        accepted only for a unique match.  A 3DS entry without a usable title id
        whose name is a DS cartridge (``AZEJ Kirby Super Star Ultra``) is looked
        up under the NDS system instead.
        """
        if entry is None:
            return PLACEHOLDER
        plat = (platform or getattr(entry, "platform", "") or "").strip().lower()
        plat, title = resolve_boxart_system(plat, title, title_id)
        user = _user_path(entry, library_root)
        if user is not None:
            return ArtworkResolution(str(user), SOURCE_USER)
        cached = _downloaded_path(self.cache, plat, identity_key)
        if cached is not None:
            return ArtworkResolution(str(cached), SOURCE_DOWNLOADED)
        embedded = _embedded_path(entry)
        if embedded is not None:
            return ArtworkResolution(str(embedded), SOURCE_EMBEDDED)
        names = []
        if plat == "3ds" and title_id:
            names.extend(self._3ds_names_for_title_id(title_id))
        names.extend(libretro_title_candidates(title))
        seen = set()
        for candidate in names:
            if candidate in seen:
                continue
            seen.add(candidate)
            artwork = self.ref_for(plat, candidate)
            if artwork is None:
                continue
            stored = self._store_artwork(
                artwork, identity_key=identity_key, platform=plat
            )
            if stored is not None:
                return ArtworkResolution(str(stored), SOURCE_DOWNLOADED)
        return self._download_from_listing(
            plat, names, identity_key=identity_key
        ) or PLACEHOLDER

    def _download_from_listing(
        self,
        platform: str,
        names: List[str],
        *,
        identity_key: Optional[str],
    ) -> Optional[ArtworkResolution]:
        """Last-resort fallback: match ``names`` against the provider's own
        ``Named_Boxarts`` directory listing and download the unique hit.

        Every generated candidate 404s often enough (renames, region tags) that
        the real file name is only discoverable from the listing.  An ambiguous
        match is refused by :func:`unique_boxart_match`.
        """
        provider = None
        for candidate_provider in self.providers:
            try:
                if candidate_provider.supports(platform) and candidate_provider.boxart_listing_url(
                    platform
                ):
                    provider = candidate_provider
                    break
            except Exception:  # noqa: BLE001 - one bad provider must not stop the rest
                continue
        if provider is None:
            return None
        listing_url = provider.boxart_listing_url(platform)
        timeout = max(float(getattr(self.downloader, "timeout", 5) or 5), 20.0)
        filenames = fetch_boxart_listing(
            self.downloader._urlopen, listing_url, timeout=timeout
        )
        if not filenames:
            return None
        matched = None
        for query in names:
            matched = unique_boxart_match(filenames, query)
            if matched:
                break
        if not matched:
            # Deterministic matching is exhausted. The optional LLM is offered
            # the strict ambiguous pools first, then the looser word-overlap
            # pools; region variants were already resolved above.
            matched = self._choose_listing_with_llm(filenames, names)
        if not matched:
            return None
        filename = matched[:-4] if matched.lower().endswith(".png") else matched
        try:
            url = provider.url_for(platform, filename)
        except Exception:  # noqa: BLE001
            url = None
        if not url:
            return None
        artwork = Artwork(
            url=url,
            filename=filename,
            provider=provider.name,
            platform=platform,
            canonical_title=names[0] if names else "",
        )
        stored = self._store_artwork(
            artwork, identity_key=identity_key, platform=platform
        )
        if stored is None:
            return None
        return ArtworkResolution(str(stored), SOURCE_DOWNLOADED)

    def _choose_listing_with_llm(
        self, filenames: Tuple[str, ...], names: List[str]
    ) -> Optional[str]:
        """Offer the optional chooser a candidate pool from the listing.

        The strict pool (full token containment, conflicting titles) is tried
        first. When it yields no valid answer -- either because no query has
        strict candidates or because the chooser returned nothing for every
        strict pool -- the looser word-overlap pool is offered so a real file
        name the strict matcher cannot line up can still resolve. A pool already
        offered is never repeated. No chooser, a chooser error, or a name
        outside the offered list all resolve to ``None`` so the caller keeps the
        placeholder and never writes the cache.
        """
        if self.llm_chooser is None:
            return None
        seen_pools = set()
        for query in names:
            candidates = ambiguous_boxart_matches(filenames, query)
            if not candidates or candidates in seen_pools:
                continue
            seen_pools.add(candidates)
            choice = self._ask_listing_chooser(candidates, query)
            if choice is not None:
                return choice
        for query in names:
            candidates = loose_boxart_candidates(filenames, query)
            if not candidates or candidates in seen_pools:
                continue
            seen_pools.add(candidates)
            choice = self._ask_listing_chooser(candidates, query)
            if choice is not None:
                return choice
        return None

    def _ask_listing_chooser(
        self, candidates: Sequence[str], query: str
    ) -> Optional[str]:
        """Offer ``candidates`` to the chooser, accepting only an offered name."""
        try:
            choice = self.llm_chooser(candidates, query)
        except Exception:  # noqa: BLE001 - a bad chooser must not break fallback
            return None
        if isinstance(choice, str) and choice in candidates:
            return choice
        return None

    def _3ds_names_for_title_id(self, title_id: object) -> Tuple[str, ...]:
        root = self.cache.root if self.cache is not None else None
        catalog = load_catalog(root)
        if not catalog:
            timeout = max(float(getattr(self.downloader, "timeout", 5) or 5), 20.0)
            catalog = fetch_3dsdb_catalog(self.downloader._urlopen, timeout=timeout)
            if catalog:
                store_catalog(root, catalog)
        return title_candidates_for_id(catalog, title_id)

    def ensure_cover(
        self,
        entry,
        *,
        metadata,
        identity_key: Optional[str],
        library_root: Union[Path, str, None],
        platform: Optional[str] = None,
    ) -> ArtworkResolution:
        """Full fallback order including a download attempt.

        Intended to run on a worker thread: it may block on the network, but any
        failure simply falls through to the embedded icon or the placeholder.
        """
        if entry is None:
            return PLACEHOLDER
        plat = platform if platform is not None else getattr(entry, "platform", None)
        user = _user_path(entry, library_root)
        if user is not None:
            return ArtworkResolution(str(user), SOURCE_USER)
        cached = _downloaded_path(self.cache, plat, identity_key)
        if cached is not None:
            return ArtworkResolution(str(cached), SOURCE_DOWNLOADED)
        downloaded = self.ensure_downloaded(
            metadata, identity_key=identity_key, platform=plat
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
