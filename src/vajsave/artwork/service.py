"""Artwork resolution and download orchestration.

The public fallback order is fixed and tested:

    user local  >  downloaded  >  embedded  >  placeholder

PSP and Vita are deliberate exceptions: their ``ICON0.PNG`` / ``sce_sys/icon0.png``
files are a wide banner or a 128×128 LiveArea icon, never portrait box art, so
those resolvers skip the embedded layer and show the placeholder when neither a
user nor a downloaded cover exists.  Cartridge platforms keep the embedded
fallback.

* **user local** -- a portrait or near-square image under
  ``<library_root>/covers/<platform>/<name>.<ext>`` (the existing
  :func:`vajsave.covers.user_cover_path`);
* **downloaded** -- a portrait or near-square image fetched from a provider and
  committed to the identity-hash cover cache
  (``covers/<platform>/<identity-hash>.png``);
* **embedded** -- a portrait or near-square icon found inside the save folder during
  the scan;
* **placeholder** -- no path; the UI paints its light-grey square.

:func:`resolve_artwork` is the synchronous, network-free resolver used for the
first paint.  :meth:`ArtworkService.ensure_cover` additionally attempts one
bounded portrait download before falling back to the embedded icon, and is meant
to run off the UI thread. Landscape downloads are rejected before they enter
the cache.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple, Union

logger = logging.getLogger("vajsave.artwork.service")

from ..covers import find_embedded_cover, user_cover_path
from .boxart_index import (
    ambiguous_boxart_matches,
    concatenation_boxart_candidates,
    fetch_boxart_listing,
    loose_boxart_candidates,
    resolve_boxart_system,
    unique_boxart_match,
)
from .cache import CoverCache, is_portrait_image_file
from .downloader import ArtworkDownloader
from .providers import (
    Artwork,
    ArtworkProvider,
    LibretroThumbnailProvider,
    libretro_title_candidates,
    psp_title_candidates,
)
from .switch_covers import (
    GameTDBSwitchProvider,
    NlibSwitchProvider,
    _clean_switch_title_id,
    get_switch_id_for_title,
    switch_title_candidates,
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


def _is_psp_save(entry, platform=None) -> bool:
    """Whether ``entry``/``platform`` identifies a PSP save.

    The embedded ``ICON0.PNG`` of a PSP save is never box art, so this decides
    the embedded layer for both native PSP scans and PSP saves living inside a
    Vita memory card (Adrenaline / ``pspemu``).  The platform string alone is
    not enough: a display/identity layer may relabel a ``pspemu`` save as
    ``vita``, so the entry's ``source_id`` and path are consulted too.
    """
    plat = (
        platform if platform is not None else getattr(entry, "platform", None)
    )
    if str(plat or "").strip().lower() == "psp":
        return True
    source_id = getattr(entry, "source_id", None)
    if str(source_id or "").strip().lower() == "psp":
        return True
    raw_path = getattr(entry, "path", None)
    if not raw_path:
        return False
    normalised = str(raw_path).replace("\\", "/").casefold()
    return (
        "pspemu/" in normalised
        or "/psp/savedata/" in normalised
        or normalised.endswith("/psp/savedata")
    )


def _is_vita_save(entry, platform=None) -> bool:
    """Whether ``entry``/``platform`` identifies a native Vita save.

    Vita ``sce_sys/icon0.png`` is a 128×128 LiveArea icon, not box art.  PSP
    saves living on a Vita card are handled by :func:`_is_psp_save` instead.
    """
    plat = (
        platform if platform is not None else getattr(entry, "platform", None)
    )
    if str(plat or "").strip().lower() == "vita":
        return True
    source_id = str(getattr(entry, "source_id", None) or "").strip().lower()
    return source_id in ("vita", "vita_exported")


def _embedded_allowed(entry, platform=None) -> bool:
    """Whether a save's embedded icon may be shown as a gallery cover.

    PSP and Vita skip the embedded layer (banner / square LiveArea icon) and
    show a placeholder when no user or downloaded box art is available.  This
    includes PSP saves found inside a Vita memory card (Adrenaline / ``pspemu``).
    Cartridge platforms keep their embedded fallback.
    """
    if _is_psp_save(entry, platform):
        return False
    return not _is_vita_save(entry, platform)


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


def _portrait_path(path: Optional[Path]) -> Optional[Path]:
    """Return ``path`` only when it is a decodable cover, not a wide banner.

    Save-folder icons are useful provenance, but PSP ``ICON0.PNG`` files are
    commonly 144×80 banners rather than box art. Keeping the orientation gate
    here lets the scanner preserve the original file while the gallery uses a
    consistent cover-only policy.
    """
    if path is None:
        return None
    return path if is_portrait_image_file(path) else None


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
    """Network-free best portrait cover: user > downloaded > embedded (non-PSP)."""
    if entry is None:
        return PLACEHOLDER
    plat = platform if platform is not None else getattr(entry, "platform", None)
    user = _portrait_path(_user_path(entry, library_root))
    if user is not None:
        return ArtworkResolution(str(user), SOURCE_USER)
    downloaded = _downloaded_path(cache, plat, identity_key)
    if downloaded is not None:
        return ArtworkResolution(str(downloaded), SOURCE_DOWNLOADED)
    if _embedded_allowed(entry, plat):
        embedded = _portrait_path(_embedded_path(entry))
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
            self.providers: List[ArtworkProvider] = [
                LibretroThumbnailProvider(),
                GameTDBSwitchProvider(),
                NlibSwitchProvider(),
            ]
        else:
            self.providers = list(providers)
        # A provider's directory listing is immutable for the lifetime of one
        # service run. Reusing it avoids one multi-entry PSP scan issuing the
        # same multi-megabyte request for every unresolved save.
        self._listing_cache: dict[Tuple[str, str], Tuple[str, ...]] = {}
        self._listing_cache_lock = threading.Lock()

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
        plat = platform or artwork.platform
        logger.info("[%s] 正在请求封面资源: %s (提供方: %s)", plat, artwork.url, artwork.provider)
        data = self.downloader.fetch(artwork.url)
        if not data:
            logger.debug("[%s] 封面下载失败或数据为空: %s", plat, artwork.url)
            return None
        stored = self.cache.store(
            plat,
            identity_key,
            data,
            provider=artwork.provider,
            canonical_title=artwork.canonical_title,
            remote_url=artwork.url,
        )
        if stored is not None:
            logger.info("[%s] 封面验证通过并写入缓存: %s -> %s", plat, artwork.url, stored)
        else:
            logger.warning("[%s] 封面校验未通过 (非竖版或图像损坏): %s", plat, artwork.url)
        return stored

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
        """Cover for a platform whose provider key is the save's own title."""
        if entry is None:
            return PLACEHOLDER
        plat = (platform or getattr(entry, "platform", "") or "").strip().lower()
        plat, title = resolve_boxart_system(plat, title, title_id)
        logger.info("[%s] 开始获取封面: '%s' (Title ID: %s, Key: %s)", plat, title, title_id or "无", identity_key or "无")
        user = _portrait_path(_user_path(entry, library_root))
        if user is not None:
            logger.info("[%s] 命中本地自定义封面: %s", plat, user)
            return ArtworkResolution(str(user), SOURCE_USER)
        cached = _downloaded_path(self.cache, plat, identity_key)
        if plat == "switch":
            # 1. Resolve 16-hex Title ID from any available source
            switch_tid = None
            for cand_id in (
                title_id,
                getattr(entry, "title_id", None),
                title if (title and (_clean_switch_title_id(str(title)) or "0100" in str(title))) else None,
                identity_key.split("switch:", 1)[1] if (identity_key and "switch:" in identity_key) else None,
            ):
                cleaned = _clean_switch_title_id(cand_id)
                if cleaned:
                    switch_tid = cleaned
                    break

            if not switch_tid and title:
                switch_tid = get_switch_id_for_title(title)

            if switch_tid:
                logger.info("[Switch] 解析得到 Title ID: %s (游戏名: '%s')", switch_tid, title)

            # If already cached as a square nlib icon, attempt to upgrade to GameTDB physical box art
            if cached is not None and self.cache is not None:
                record = getattr(self.cache, "get_entry", lambda k: None)(identity_key)
                if record and record.get("provider") == "nlib":
                    logger.info("[Switch] 发现已缓存的 Nlib 方标，尝试升级为 GameTDB 实体盒装封面...")
                    if switch_tid:
                        for provider in self.providers:
                            if isinstance(provider, GameTDBSwitchProvider):
                                for art in provider.cover_candidates_for_title_id(switch_tid):
                                    stored = self._store_artwork(
                                        art, identity_key=identity_key, platform=plat
                                    )
                                    if stored is not None:
                                        return ArtworkResolution(str(stored), SOURCE_DOWNLOADED)
                    names = switch_title_candidates(title, switch_tid or title_id)
                    for provider in self.providers:
                        if isinstance(provider, GameTDBSwitchProvider):
                            for name in names:
                                for art in provider.cover_candidates(name):
                                    stored = self._store_artwork(
                                        art, identity_key=identity_key, platform=plat
                                    )
                                    if stored is not None:
                                        return ArtworkResolution(str(stored), SOURCE_DOWNLOADED)
                logger.info("[%s] 命中本地下载缓存封面: %s", plat, cached)
                return ArtworkResolution(str(cached), SOURCE_DOWNLOADED)

            if cached is not None:
                logger.info("[%s] 命中本地下载缓存封面: %s", plat, cached)
                return ArtworkResolution(str(cached), SOURCE_DOWNLOADED)
        elif cached is not None:
            logger.info("[%s] 命中本地下载缓存封面: %s", plat, cached)
            return ArtworkResolution(str(cached), SOURCE_DOWNLOADED)

        embedded = (
            _portrait_path(_embedded_path(entry)) if _embedded_allowed(entry, plat) else None
        )
        if plat == "switch":
            # 2. If Title ID is known, perform Title ID-first direct lookups!
            if switch_tid:
                # 2a. Direct GameTDB physical retail box art (primary + regional fallbacks)
                logger.info("[Switch] 正在尝试 GameTDB 实体盒装封面 (Title ID: %s)...", switch_tid)
                for provider in self.providers:
                    if isinstance(provider, GameTDBSwitchProvider):
                        for art in provider.cover_candidates_for_title_id(switch_tid):
                            stored = self._store_artwork(
                                art, identity_key=identity_key, platform=plat
                            )
                            if stored is not None:
                                return ArtworkResolution(str(stored), SOURCE_DOWNLOADED)

                # 2b. Direct Nlib official square icon (digital-only eShop titles / uncataloged)
                logger.info("[Switch] 正在尝试 Nintendo eShop 官方图标 (Title ID: %s)...", switch_tid)
                for provider in self.providers:
                    if isinstance(provider, NlibSwitchProvider):
                        nlib_art = provider.cover_for_title_id(switch_tid)
                        if nlib_art is not None:
                            stored = self._store_artwork(
                                nlib_art, identity_key=identity_key, platform=plat
                            )
                            if stored is not None:
                                return ArtworkResolution(str(stored), SOURCE_DOWNLOADED)

            # 3. String candidate matching fallback if Title ID lookups did not find a cover
            names = []
            names.extend(switch_title_candidates(title, switch_tid or title_id))
            if not names and switch_tid:
                resolved_name = NlibSwitchProvider.resolve_title_name(
                    self.downloader._urlopen, str(switch_tid)
                )
                if resolved_name:
                    names.extend(switch_title_candidates(resolved_name, switch_tid))

            if names:
                logger.info("[Switch] 尝试游戏名称候选列表匹配封面: %s", names[:5])

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

            for provider in self.providers:
                if isinstance(provider, GameTDBSwitchProvider):
                    for cand_title in names:
                        for art in provider.cover_candidates(cand_title):
                            stored = self._store_artwork(
                                art, identity_key=identity_key, platform=plat
                            )
                            if stored is not None:
                                return ArtworkResolution(str(stored), SOURCE_DOWNLOADED)
        else:
            names = []
            if plat == "3ds" and title_id:
                names.extend(self._3ds_names_for_title_id(title_id))
            if plat == "psp":
                names.extend(psp_title_candidates(title, title_id))
            else:
                names.extend(libretro_title_candidates(title))
            if names:
                logger.info("[%s] 尝试游戏名称候选匹配封面: %s", plat, names[:5])
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
            downloaded = self._download_from_listing(
                plat, names, identity_key=identity_key
            )
            if downloaded is not None:
                return downloaded
        if embedded is not None:
            logger.info("[%s] 未获取到网络封面，回退使用存档内置图标: %s", plat, embedded)
            return ArtworkResolution(str(embedded), SOURCE_EMBEDDED)
        logger.info("[%s] 未获取到可用封面，使用默认占位图: %s", plat, title)
        return PLACEHOLDER

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
        timeout = max(float(getattr(self.downloader, "timeout", 5) or 5), 20.0)
        filenames = self._listing_for(provider, platform, timeout=timeout)
        if not filenames:
            return None
        matched = None
        # A more specific query may identify a concatenated retail token (for
        # example "3 (Try) G" -> "3G"), while a later, shorter candidate can
        # now resolve a neighbouring title deterministically. Give the precise
        # concatenation pool a chance first when the optional chooser is
        # enabled; without a chooser the normal strict match remains the
        # deterministic fallback.
        has_concatenation = any(
            concatenation_boxart_candidates(filenames, query) for query in names
        )
        if has_concatenation:
            matched = self._choose_listing_with_llm(filenames, names)
        if not matched:
            for query in names:
                matched = unique_boxart_match(filenames, query)
                if matched:
                    break
        if not matched and not has_concatenation:
            # Deterministic matching is exhausted. The optional LLM is offered
            # concatenation hits first, then the strict ambiguous pools, then
            # the looser word-overlap pools; region variants were already
            # resolved above.
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

    def _listing_for(
        self,
        provider: ArtworkProvider,
        platform: str,
        *,
        timeout: float,
    ) -> Tuple[str, ...]:
        """Fetch and cache one provider/platform directory listing.

        Only a non-empty listing is retained. A transient offline response can
        therefore be retried later in the same service lifetime, while all
        successful scans share the first parsed tuple.
        """
        provider_name = str(getattr(provider, "name", "") or provider.__class__.__name__)
        plat = str(platform or "").strip().lower()
        key = (provider_name, plat)
        with self._listing_cache_lock:
            cached = self._listing_cache.get(key)
            if cached is not None:
                return cached
            try:
                listing_url = provider.boxart_listing_url(plat)
            except Exception:  # noqa: BLE001 - one provider must not break fallback
                return ()
            if not listing_url:
                return ()
            filenames = fetch_boxart_listing(
                self.downloader._urlopen, listing_url, timeout=timeout
            )
            if filenames:
                self._listing_cache[key] = filenames
            return filenames

    def _choose_listing_with_llm(
        self, filenames: Tuple[str, ...], names: List[str]
    ) -> Optional[str]:
        """Offer the optional chooser a candidate pool from the listing.

        A query whose words join a real listing token (``3 G`` -> ``3G``) is
        offered first, with the joined name merged ahead of its word-overlap
        pool, so a strict token-containment pool that merely repeats the query
        words (``Monster Hunter 3`` -> ``Monster Hunter 3 Ultimate``) can never
        preempt it.  The strict pool is tried next, then the remaining loose
        word-overlap pools, so a real file name the strict matcher cannot line
        up can still resolve. A pool already offered is never repeated. No
        chooser, a chooser error, or a name outside the offered list all resolve
        to ``None`` so the caller keeps the placeholder and never writes the
        cache.
        """
        if self.llm_chooser is None:
            return None
        seen_pools = set()
        # Concatenation hits take priority over the strict ambiguous pools. The
        # leading candidate is merged ahead of the word-overlap pool so a
        # genuine joined name that shares no whole word (``3 G`` -> ``3G``) is
        # still offered, and a joined name is ranked before a plain neighbour.
        for query in names:
            concatenated = concatenation_boxart_candidates(filenames, query)
            if not concatenated:
                continue
            loose = loose_boxart_candidates(filenames, query)
            candidates = concatenated + tuple(
                name for name in loose if name not in concatenated
            )
            if not candidates or candidates in seen_pools:
                continue
            seen_pools.add(candidates)
            choice = self._ask_listing_chooser(candidates, query)
            if choice is not None:
                return choice
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
        failure simply falls through to a portrait embedded icon (non-PSP) or the
        placeholder.
        """
        if entry is None:
            return PLACEHOLDER
        plat = platform if platform is not None else getattr(entry, "platform", None)
        user = _portrait_path(_user_path(entry, library_root))
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
        if _embedded_allowed(entry, plat):
            embedded = _portrait_path(_embedded_path(entry))
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
