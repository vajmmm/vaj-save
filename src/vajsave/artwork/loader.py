"""A tiny background worker that marshals results back to the Tk main thread.

Doing network I/O on the UI thread would freeze the window, and touching Tk
widgets from a worker thread is unsafe.  This helper bridges the two:

* :meth:`ArtworkLoader.submit` runs ``task`` on a worker (thread pool).  While a
  task for a given key is still running, further submissions for the *same key*
  do not start second work item -- they attach another callback instead, so
  repeated selection changes cannot multiply the I/O;
* every callback is delivered by calling ``schedule(deliver)`` -- in the app that
  is ``root.after(0, deliver)``, so ``deliver`` runs on the main thread;
* the *caller's* callback is responsible for dropping stale results (e.g. "the
  selection changed since I asked"), because only it knows what is current.

The class never imports tkinter, which keeps it unit-testable with a synchronous
executor and a recording ``schedule``.
"""

from __future__ import annotations

import threading
from concurrent.futures import Executor, ThreadPoolExecutor
from typing import Any, Callable, Hashable, List, Optional


class ArtworkLoader:
    def __init__(
        self,
        schedule: Callable[[Callable[[], None]], Any],
        *,
        executor: Optional[Executor] = None,
        max_workers: int = 2,
    ) -> None:
        self._schedule = schedule
        self._owns_executor = executor is None
        self._executor: Executor = executor or ThreadPoolExecutor(max_workers=max_workers)
        self._waiters: dict = {}
        self._lock = threading.Lock()
        self._closed = False

    def is_inflight(self, key: Hashable) -> bool:
        with self._lock:
            return key in self._waiters

    def submit(
        self,
        key: Hashable,
        task: Callable[[], Any],
        callback: Callable[[Any], None],
    ) -> bool:
        """Schedule ``task`` unless ``key`` is already running.

        Returns ``True`` when new work was started, ``False`` when ``callback``
        was attached to the in-flight task (or the loader is shut down).
        """
        with self._lock:
            if self._closed:
                return False
            waiters = self._waiters.get(key)
            if waiters is not None:
                waiters.append(callback)
                return False
            self._waiters[key] = [callback]
        try:
            self._executor.submit(self._run, key, task)
        except RuntimeError:
            # Executor already shut down: drop the task without delivering.
            with self._lock:
                self._waiters.pop(key, None)
            return False
        return True

    def _run(self, key: Hashable, task: Callable[[], Any]) -> None:
        try:
            result = task()
        except Exception:  # noqa: BLE001 - a failed task is just "no result"
            result = None

        def deliver() -> None:
            with self._lock:
                callbacks: List[Callable[[Any], None]] = self._waiters.pop(key, [])
            for callback in callbacks:
                try:
                    callback(result)
                except Exception:  # noqa: BLE001 - a broken callback must not escape
                    pass

        try:
            self._schedule(deliver)
        except Exception:  # noqa: BLE001 - scheduling failure means no delivery
            with self._lock:
                self._waiters.pop(key, None)

    def shutdown(self, wait: bool = False) -> None:
        with self._lock:
            self._closed = True
        if self._owns_executor:
            try:
                self._executor.shutdown(wait=wait, cancel_futures=True)
            except TypeError:  # pragma: no cover - Python < 3.9 fallback
                self._executor.shutdown(wait=wait)


__all__ = ["ArtworkLoader"]
