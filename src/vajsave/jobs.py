"""Cooperative job progress and cancellation."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class JobProgress:
    message: str = ""
    current: int = 0
    total: int = 0
    cancellable: bool = False


class JobCancelled(Exception):
    """Raised when a cooperative job should stop between units of work."""


class CancelToken:
    def __init__(self) -> None:
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled():
            raise JobCancelled()


class JobSlot:
    """At most one cooperative job. Thread-safe for UI cancel vs worker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: CancelToken | None = None
        self._progress = JobProgress()

    def try_begin(self, token: CancelToken | None = None) -> CancelToken | None:
        with self._lock:
            if self._token is not None:
                return None
            started = token if token is not None else CancelToken()
            self._token = started
            self._progress = JobProgress(cancellable=True)
            return started

    def end(self, token: CancelToken | None = None) -> None:
        with self._lock:
            if token is not None and self._token is not token:
                return
            self._token = None
            self._progress = JobProgress()

    def cancel(self) -> None:
        with self._lock:
            token = self._token
        if token is not None:
            token.cancel()

    def progress(self) -> JobProgress:
        with self._lock:
            return replace(self._progress)

    def report(self, progress: JobProgress) -> None:
        with self._lock:
            if self._token is None:
                return
            self._progress = progress

    def is_running(self) -> bool:
        with self._lock:
            return self._token is not None
