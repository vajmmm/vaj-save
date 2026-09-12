"""Shared atomic file-write helpers for the small JSON-backed stores.

Several modules keep a compact JSON document on disk and expect the same
robustness properties (``identity/bindings.py``, ``identity/cache.py``,
``metadata/cache.py`` and ``artwork/cache.py``):

* a write is **atomic** -- the payload is written to a sibling ``*.tmp`` file
  and renamed into place, so a crash mid-write can never leave a truncated
  document that the next load would reject;
* a write is **best-effort** -- any OS or serialisation failure degrades to
  ``False`` instead of raising, and the half-written temp file is removed so it
  cannot linger next to the real document;
* parent directories are created on demand.

Centralising the pattern keeps every store's failure semantics identical and
gives the cleanup path a single implementation to test.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional, Union


def _atomic_write(
    path: Optional[Union[Path, str]],
    write: Callable[[Path], None],
) -> bool:
    """Run ``write`` against a temp sibling, then rename it over ``path``.

    Returns ``True`` only when the rename succeeded; on any failure the temp
    file is dropped and ``False`` is returned.
    """
    if path is None:
        return False
    target = Path(path)
    tmp: Optional[Path] = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        write(tmp)
        tmp.replace(target)
    except (OSError, TypeError, ValueError):
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return False
    return True


def atomic_write_bytes(path: Optional[Union[Path, str]], data: bytes) -> bool:
    """Atomically write raw ``data`` to ``path``; ``False`` on any failure."""
    return _atomic_write(path, lambda tmp: tmp.write_bytes(data))


def atomic_write_json(
    path: Optional[Union[Path, str]],
    payload: Any,
    *,
    indent: int = 2,
) -> bool:
    """Atomically write ``payload`` as UTF-8 JSON to ``path``.

    The document is serialised inside the guarded block, so a non-serialisable
    payload degrades to ``False`` (with temp cleanup) exactly like an OS error
    would, rather than raising at the caller.
    """
    return _atomic_write(
        path,
        lambda tmp: tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=indent),
            encoding="utf-8",
        ),
    )


__all__ = ["atomic_write_bytes", "atomic_write_json"]
