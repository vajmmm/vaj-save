"""Bounded, exception-safe HTTP downloader for artwork.

Every failure mode a real network exposes -- ``404``, DNS/offline errors,
timeouts, a response that declares more bytes than it delivers ("partial"), or
an oversized payload -- collapses to ``None``.  Callers therefore never need to
handle an exception or distinguish "missing" from "offline"; they just fall back
to the next artwork layer.

The downloader only ever reads; validation and caching happen one layer up, so a
bad body can be rejected before it reaches the cache.
"""

from __future__ import annotations

import logging
import urllib.request
from typing import Any, Callable, Optional

logger = logging.getLogger("vajsave.artwork.downloader")

DEFAULT_TIMEOUT = 5.0

# Same ceiling as the cover cache: never read more than this from the network.
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 vaj-save/1.0"
)


def _default_urlopen(target: Any, timeout: float = DEFAULT_TIMEOUT) -> Any:
    if isinstance(target, str):
        target = urllib.request.Request(target, headers={"User-Agent": DEFAULT_USER_AGENT})
    return urllib.request.urlopen(target, timeout=timeout)


class ArtworkDownloader:
    """A minimal GET-only client with a hard size cap."""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_bytes: int = MAX_DOWNLOAD_BYTES,
        urlopen: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.timeout = float(timeout)
        self.max_bytes = int(max_bytes)
        self._urlopen = urlopen or _default_urlopen

    def fetch(self, url: Optional[str]) -> Optional[bytes]:
        """Return the body of ``url`` or ``None`` on any failure or oversize."""
        if not url:
            return None
        logger.debug("发起封面 HTTP 请求: %s", url)
        try:
            response = self._urlopen(url, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001 - 404/offline/timeout all mean "no cover"
            logger.debug("封面 HTTP 请求未成功 [%s]: %s", exc, url)
            return None
        try:
            return self._read_response(response)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass

    def _read_response(self, response: Any) -> Optional[bytes]:
        status = getattr(response, "status", None)
        if status is None:
            status = getattr(response, "code", None)
        if status is not None and int(status) != 200:
            logger.debug("HTTP 状态码异常 (%s): %s", status, getattr(response, "url", ""))
            return None
        try:
            # Read one byte past the cap so an oversize body is detectable
            # without buffering the whole thing.
            data = response.read(self.max_bytes + 1)
        except Exception as exc:  # noqa: BLE001 - a broken connection is just "no cover"
            logger.debug("读取响应流失败 [%s]: %s", exc, getattr(response, "url", ""))
            return None
        if not data or len(data) > self.max_bytes:
            logger.debug("下载数据为空或超出字节上限 (%d 字节)", len(data) if data else 0)
            return None
        if self._declared_length(response) not in (None, len(data)):
            # Content-Length promised more than arrived -> truncated download.
            logger.debug("响应数据长度与 Content-Length 不符，丢弃截断数据")
            return None
        logger.info("封面图片下载完成 (%d 字节): %s", len(data), getattr(response, "url", ""))
        return data

    @staticmethod
    def _declared_length(response: Any) -> Optional[int]:
        headers = getattr(response, "headers", None)
        if headers is None:
            return None
        try:
            raw = headers.get("Content-Length")
        except Exception:  # noqa: BLE001
            return None
        if raw is None:
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None


__all__ = ["ArtworkDownloader", "DEFAULT_TIMEOUT", "MAX_DOWNLOAD_BYTES"]
