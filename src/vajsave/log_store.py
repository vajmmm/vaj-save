"""In-memory logging store and standard logging handler for VajSave.

Provides structured, thread-safe logging memory buffer for scanning and artwork processes,
with categorisation (scan, cover, app) and pub/sub listener support for the Qt UI.
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional


@dataclass(frozen=True)
class LogRecordEntry:
    timestamp: float
    formatted_time: str
    level: str
    category: str  # "scan", "cover", "app"
    message: str
    logger_name: str


class LogStore:
    def __init__(self, max_records: int = 2000) -> None:
        self._max_records = max_records
        self._records: collections.deque[LogRecordEntry] = collections.deque(maxlen=max_records)
        self._listeners: List[Callable[[LogRecordEntry], None]] = []
        self._lock = threading.Lock()

    def add_record(self, entry: LogRecordEntry) -> None:
        with self._lock:
            self._records.append(entry)
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(entry)
            except Exception:
                pass

    def get_records(
        self,
        category: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[LogRecordEntry]:
        with self._lock:
            records = list(self._records)
        if category and category != "all":
            records = [r for r in records if r.category == category]
        if search:
            query = search.strip().casefold()
            records = [
                r for r in records
                if query in r.message.casefold() or query in r.logger_name.casefold()
            ]
        return records

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def subscribe(self, callback: Callable[[LogRecordEntry], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._listeners:
                    self._listeners.remove(callback)

        return unsubscribe


_GLOBAL_LOG_STORE: Optional[LogStore] = None
_STORE_LOCK = threading.Lock()


def get_log_store() -> LogStore:
    global _GLOBAL_LOG_STORE
    with _STORE_LOCK:
        if _GLOBAL_LOG_STORE is None:
            _GLOBAL_LOG_STORE = LogStore()
        return _GLOBAL_LOG_STORE


class LogStoreHandler(logging.Handler):
    """Logging handler that captures python logging events into LogStore."""

    def __init__(self, store: Optional[LogStore] = None) -> None:
        super().__init__()
        self.store = store or get_log_store()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            cat = getattr(record, "category", None)
            if not cat:
                name = record.name.lower()
                if any(k in name for k in ("scan", "platform", "volume", "device", "identity")):
                    cat = "scan"
                elif any(k in name for k in ("artwork", "cover", "gametdb", "nlib", "libretro", "thumbnail")):
                    cat = "cover"
                else:
                    cat = "app"

            formatted_time = time.strftime("%H:%M:%S", time.localtime(record.created))
            entry = LogRecordEntry(
                timestamp=record.created,
                formatted_time=formatted_time,
                level=record.levelname,
                category=cat,
                message=msg,
                logger_name=record.name,
            )
            self.store.add_record(entry)
        except Exception:
            self.handleError(record)


_LOGGING_INITIALIZED = False


def setup_logging(level: int = logging.INFO, store: Optional[LogStore] = None) -> LogStoreHandler:
    """Initialize root vajsave logger and attach LogStoreHandler."""
    global _LOGGING_INITIALIZED
    logger = logging.getLogger("vajsave")
    logger.setLevel(level)

    target_store = store or get_log_store()
    for handler in logger.handlers:
        if isinstance(handler, LogStoreHandler) and handler.store is target_store:
            return handler

    handler = LogStoreHandler(store=target_store)
    handler.setLevel(level)
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    _LOGGING_INITIALIZED = True
    return handler
