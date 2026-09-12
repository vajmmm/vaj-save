"""Tests for the shared atomic file-write helpers in ``vajsave.persistence``.

These helpers back every JSON store in the app (identity bindings, identity
cache, metadata cache, cover manifest) plus the cover image commit.  The
contract they must uphold:

* a successful write is all-or-nothing (temp file renamed into place);
* every failure mode degrades to ``False`` and never raises;
* a failed write never leaves the half-written ``.tmp`` sibling behind.
"""

from __future__ import annotations

import json
from pathlib import Path

from vajsave.persistence import atomic_write_bytes, atomic_write_json


def test_atomic_write_json_creates_parents_and_persists(tmp_path: Path):
    target = tmp_path / "deep" / "nested" / "store.json"
    assert atomic_write_json(target, {"version": 1, "entries": {"a": {"b": 1}}}) is True
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "version": 1,
        "entries": {"a": {"b": 1}},
    }
    # No temporary sibling survives a successful write.
    assert not (target.with_name(target.name + ".tmp")).exists()


def test_atomic_write_json_none_path_is_noop(tmp_path: Path):
    assert atomic_write_json(None, {"version": 1}) is False


def test_atomic_write_json_unserialisable_payload_cleans_temp(tmp_path: Path):
    target = tmp_path / "store.json"

    class NotSerialisable:
        pass

    assert atomic_write_json(target, {"bad": NotSerialisable()}) is False
    assert not target.exists()
    assert not (tmp_path / "store.json.tmp").exists()


def test_atomic_write_json_unmappable_parent_degrades(tmp_path: Path):
    # The parent path is a file, so ``mkdir`` can never succeed.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    target = blocker / "store.json"
    assert atomic_write_json(target, {"version": 1}) is False
    assert not (tmp_path / "blocker.tmp").exists()


def test_atomic_write_json_replace_failure_cleans_temp(tmp_path: Path):
    # The target is a directory, so the final rename cannot replace it.
    target = tmp_path / "store.json"
    target.mkdir()
    assert atomic_write_json(target, {"version": 1}) is False
    assert not (tmp_path / "store.json.tmp").exists()


def test_atomic_write_json_cleanup_failure_is_contained(tmp_path: Path, monkeypatch):
    # Even when removing the temp file itself raises, the helper must not raise.
    target = tmp_path / "store.json"
    target.mkdir()
    real_unlink = Path.unlink

    def boom(self, *args, **kwargs):
        if self.name.endswith(".tmp"):
            raise OSError("cannot remove temp")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", boom)
    assert atomic_write_json(target, {"version": 1}) is False


def test_atomic_write_bytes_roundtrip_and_cleanup(tmp_path: Path):
    target = tmp_path / "covers" / "art.png"
    assert atomic_write_bytes(target, b"\x89PNG\r\n\x1a\n") is True
    assert target.read_bytes() == b"\x89PNG\r\n\x1a\n"
    assert not (target.with_name(target.name + ".tmp")).exists()


def test_atomic_write_bytes_replace_failure_cleans_temp(tmp_path: Path):
    target = tmp_path / "art.png"
    target.mkdir()
    assert atomic_write_bytes(target, b"data") is False
    assert not (tmp_path / "art.png.tmp").exists()
