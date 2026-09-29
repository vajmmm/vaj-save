"""Tests for pulling DBI MTP saves into a local, scannable cache.

The MTP transport is deterministic (no WPD/COM): an in-memory fake client
serves nested storage trees.  The tests cover the happy path, incremental
reuse, atomicity (a failed/cancelled pull must never expose a half-populated
cache), SD-subtree scoping and path-escape protection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vajsave.mtp_fetch import (
    MtpPullResult,
    device_cache_dir,
    mtp_cache_root,
    pull_device,
)
from vajsave.scanner import scan

from conftest import (
    FakeMtpClient,
    dbi_saves_tree,
    fake_mtp_client_factory,
)

SAVES_STORAGES = [("saves", "7: Saves")]


def _sd_tree():
    return {
        "sd": {
            "switch": {
                "Checkpoint": {"saves": {"Game A": {"slot0": {"main": b"cp"}}}},
                "DBI": {"saves": {"Game B": {"User": {"main": b"dbi"}}}},
            },
            "JKSV": {"Game C": {"User": {"main": b"jk"}}},
            "Nintendo": {"Album": {"big.bin": b"X" * 200000}},
            "games": {"huge.nsp": b"N" * 200000},
        }
    }


# -- happy path --------------------------------------------------------------


def test_pull_saves_then_scan_finds_user_saves(tmp_path: Path):
    cache = device_cache_dir(tmp_path / "lib", "dev1")
    factory = fake_mtp_client_factory(SAVES_STORAGES, dbi_saves_tree())

    result = pull_device(factory("dev1"), cache, device_id="dev1")

    assert isinstance(result, MtpPullResult)
    assert result.ok is True
    assert result.path == cache
    assert result.files >= 2
    assert (
        cache / "Installed games" / "0100000000010000 Super Mario Odyssey" / "Alice" / "main"
    ).read_bytes() == b"ALICE-SAVE"

    scanned = scan(cache)
    assert len(scanned.saves) == 2
    assert {entry.user for entry in scanned.saves} == {"Alice", "Bob"}
    assert all(entry.platform == "switch" for entry in scanned.saves)
    assert all(entry.source_id == "switch_dbi" for entry in scanned.saves)
    assert all(str(cache) in entry.path for entry in scanned.saves)
    assert all(
        entry.display_name == "Super Mario Odyssey" for entry in scanned.saves
    )
    assert all(entry.title_id == "0100000000010000" for entry in scanned.saves)
    # Device/BCAT metadata are not game slots.
    assert not any("Device" in entry.path or "BCAT" in entry.path for entry in scanned.saves)


def test_uninstalled_games_are_scanned_too(tmp_path: Path):
    tree = {
        "saves": {
            "Uninstalled games": {
                "0100000000030000 Old Game": {"UserA": {"main": b"OLD"}},
            }
        }
    }
    cache = device_cache_dir(tmp_path / "lib", "dev1")
    result = pull_device(FakeMtpClient(SAVES_STORAGES, tree), cache, device_id="dev1")
    assert result.ok

    scanned = scan(cache)
    assert [(entry.display_name, entry.user) for entry in scanned.saves] == [
        ("Old Game", "UserA")
    ]
    assert scanned.saves[0].source_id == "switch_dbi"


# -- incremental reuse -------------------------------------------------------


def test_second_pull_reuses_unchanged_files(tmp_path: Path):
    cache = device_cache_dir(tmp_path / "lib", "dev1")
    factory = fake_mtp_client_factory(SAVES_STORAGES, dbi_saves_tree())

    first = pull_device(factory("dev1"), cache, device_id="dev1")
    assert first.ok and first.files >= 2

    second_client = factory("dev1")
    second = pull_device(second_client, cache, device_id="dev1")

    assert second.ok
    assert second.files == first.files
    assert second_client.downloads == [], "unchanged files must be copied, not downloaded"


def test_changed_file_is_downloaded_again(tmp_path: Path):
    cache = device_cache_dir(tmp_path / "lib", "dev1")
    pull_device(fake_mtp_client_factory(SAVES_STORAGES, dbi_saves_tree())("dev1"), cache)

    changed = dbi_saves_tree()
    changed["saves"]["Installed games"]["0100000000010000 Super Mario Odyssey"]["Alice"][
        "main"
    ] = b"ALICE-SAVE-CHANGED-LONGER"
    client = FakeMtpClient(SAVES_STORAGES, changed)
    result = pull_device(client, cache, device_id="dev1")

    assert result.ok
    assert (cache / "Installed games" / "0100000000010000 Super Mario Odyssey" / "Alice" / "main").read_bytes() == b"ALICE-SAVE-CHANGED-LONGER"
    assert any("Alice/main" in path for _, path in client.downloads)


# -- atomicity / failure / cancel -------------------------------------------


def test_failed_first_pull_leaves_no_cache(tmp_path: Path):
    cache = device_cache_dir(tmp_path / "lib", "dev1")
    tree = dbi_saves_tree()
    client = FakeMtpClient(
        SAVES_STORAGES,
        tree,
        fail_paths={"/Installed games/0100000000010000 Super Mario Odyssey/Alice/main"},
        error_message="device disconnected",
    )

    result = pull_device(client, cache, device_id="dev1")

    assert result.ok is False
    assert "device disconnected" in result.error
    assert not cache.exists()
    assert list(cache.parent.glob(".*staging*")) == []


def test_failed_pull_keeps_previous_cache_intact(tmp_path: Path):
    cache = device_cache_dir(tmp_path / "lib", "dev1")
    good = pull_device(fake_mtp_client_factory(SAVES_STORAGES, dbi_saves_tree())("dev1"), cache)
    assert good.ok
    saved = cache / "Installed games" / "0100000000010000 Super Mario Odyssey" / "Alice" / "main"
    assert saved.read_bytes() == b"ALICE-SAVE"

    changed = dbi_saves_tree()
    changed["saves"]["Installed games"]["0100000000010000 Super Mario Odyssey"]["Alice"][
        "main"
    ] = b"CHANGED"
    bad = FakeMtpClient(
        SAVES_STORAGES,
        changed,
        fail_paths={"/Installed games/0100000000010000 Super Mario Odyssey/Alice/main"},
        error_message="connection reset",
    )
    result = pull_device(bad, cache, device_id="dev1")

    assert result.ok is False
    assert saved.read_bytes() == b"ALICE-SAVE"
    assert cache.exists()
    assert list(cache.parent.glob(".*staging*")) == []
    assert len(scan(cache).saves) == 2


def test_missing_dir_mid_saves_fails_without_committing_half_tree(tmp_path: Path):
    cache = device_cache_dir(tmp_path / "lib", "dev1")
    tree = {
        "saves": {
            "Installed games": {
                "0100000000010000 Super Mario Odyssey": {
                    "Alice": {"main": b"ALICE-SAVE"}
                },
                "0100000000020000 Second Game": {"Bob": {"main": b"BOB-SAVE"}},
            }
        }
    }
    client = FakeMtpClient(
        SAVES_STORAGES,
        tree,
        fail_paths={"/Installed games/0100000000020000 Second Game"},
        error_message="object disappeared",
    )

    result = pull_device(client, cache, device_id="dev1")

    assert result.ok is False
    assert "object disappeared" in result.error
    assert not cache.exists()
    assert list(cache.parent.glob(".*staging*")) == []


def test_missing_saves_root_keeps_previous_cache(tmp_path: Path):
    cache = device_cache_dir(tmp_path / "lib", "dev1")
    tree = {
        "saves": {
            "Installed games": {
                "0100000000010000 Super Mario Odyssey": {
                    "Alice": {"main": b"ALICE-SAVE"}
                },
                "0100000000020000 Second Game": {"Bob": {"main": b"BOB-SAVE"}},
            }
        }
    }
    first = pull_device(FakeMtpClient(SAVES_STORAGES, tree), cache, device_id="dev1")
    assert first.ok
    sav = cache / "Installed games" / "0100000000010000 Super Mario Odyssey" / "Alice" / "main"
    assert sav.read_bytes() == b"ALICE-SAVE"
    before = {p: p.read_bytes() for p in cache.rglob("*") if p.is_file()}

    client = FakeMtpClient(
        SAVES_STORAGES, tree, fail_paths={"/"}, error_message="saves storage lost"
    )
    result = pull_device(client, cache, device_id="dev1")

    assert result.ok is False
    assert "saves storage lost" in result.error
    after = {p: p.read_bytes() for p in cache.rglob("*") if p.is_file()}
    assert after == before
    assert len(scan(cache).saves) == 2
    assert list(cache.parent.glob(".*staging*")) == []


class _CancelAfter:
    def __init__(self, predicate):
        self._predicate = predicate

    def cancelled(self):
        return bool(self._predicate())


def test_cancel_removes_staging_and_keeps_old_cache(tmp_path: Path):
    cache = device_cache_dir(tmp_path / "lib", "dev1")
    assert pull_device(fake_mtp_client_factory(SAVES_STORAGES, dbi_saves_tree())("dev1"), cache).ok

    holder = {}

    def factory(_device_id):
        changed = dbi_saves_tree()
        changed["saves"]["Installed games"]["0100000000010000 Super Mario Odyssey"][
            "Alice"
        ]["main"] = b"CHANGED-DURING-PULL"
        client = FakeMtpClient(SAVES_STORAGES, changed)
        holder["client"] = client
        return client

    token = _CancelAfter(lambda: bool(holder.get("client") and holder["client"].downloads))
    result = pull_device(factory("dev1"), cache, device_id="dev1", token=token)

    assert result.ok is False
    assert result.error == "已取消"
    assert cache.exists()
    assert list(cache.parent.glob(".*staging*")) == []


def test_immediate_cancel_leaves_no_cache(tmp_path: Path):
    cache = device_cache_dir(tmp_path / "lib", "dev1")
    token = _CancelAfter(lambda: True)
    result = pull_device(
        FakeMtpClient(SAVES_STORAGES, dbi_saves_tree()), cache, device_id="dev1", token=token
    )
    assert result.ok is False
    assert not cache.exists()
    assert list(cache.parent.glob(".*staging*")) == []


# -- SD subtree scoping ------------------------------------------------------


def test_sd_card_pulls_only_save_subtrees(tmp_path: Path):
    cache = device_cache_dir(tmp_path / "lib", "dev1")
    client = FakeMtpClient([("sd", "1: SD Card")], _sd_tree())

    result = pull_device(client, cache, device_id="dev1")

    assert result.ok
    assert (cache / "switch" / "Checkpoint" / "saves" / "Game A" / "slot0" / "main").is_file()
    assert (cache / "JKSV" / "Game C" / "User" / "main").is_file()
    assert (cache / "switch" / "DBI" / "saves" / "Game B" / "User" / "main").is_file()
    assert not (cache / "Nintendo").exists()
    assert not (cache / "games").exists()
    downloaded = {path for _, path in client.downloads}
    assert downloaded == {
        "/switch/Checkpoint/saves/Game A/slot0/main",
        "/JKSV/Game C/User/main",
        "/switch/DBI/saves/Game B/User/main",
    }


def test_sd_card_alone_scans_as_switch_checkpoint(tmp_path: Path):
    cache = device_cache_dir(tmp_path / "lib", "dev1")
    tree = {"sd": {"switch": {"Checkpoint": {"saves": {"Game A": {"slot0": {"main": b"cp"}}}}}}}
    assert pull_device(FakeMtpClient([("sd", "SD Card")], tree), cache).ok

    scanned = scan(cache)
    assert any(source.source_id == "switch_checkpoint" for source in scanned.sources)
    assert scanned.saves


# -- path safety -------------------------------------------------------------


def test_path_traversal_names_are_rejected(tmp_path: Path):
    cache = device_cache_dir(tmp_path / "lib", "dev1")
    tree = {
        "saves": {
            "Installed games": {
                "Game": {
                    "../evil": {"main": b"escape"},
                    "..": {"main": b"escape2"},
                    "User": {"main": b"ok"},
                }
            }
        }
    }
    result = pull_device(FakeMtpClient(SAVES_STORAGES, tree), cache, device_id="dev1")

    assert result.ok
    assert not (cache.parent / "evil").exists()
    assert not (cache / "Installed games" / "Game" / "evil").exists()
    files = {path.name for path in cache.rglob("*") if path.is_file()}
    assert "escape" not in files


def test_unknown_storages_produce_empty_cache(tmp_path: Path):
    cache = device_cache_dir(tmp_path / "lib", "dev1")
    result = pull_device(
        FakeMtpClient([("sys", "6: System")], {"sys": {"whatever": b"x"}}), cache
    )
    assert result.ok is False
    assert not cache.exists()


def test_cache_root_is_under_library(tmp_path: Path):
    assert mtp_cache_root(tmp_path / "lib") == tmp_path / "lib" / "mtp-cache"
    assert device_cache_dir(tmp_path / "lib", "dev1") == tmp_path / "lib" / "mtp-cache" / "dev1"
    # A hostile device id cannot escape the cache root.
    hostile = device_cache_dir(tmp_path / "lib", "../../escape")
    assert hostile.parent == mtp_cache_root(tmp_path / "lib")


# -- helpers -----------------------------------------------------------------


def test_pull_result_truthiness():
    assert MtpPullResult(ok=True, device_id="x")
    assert not MtpPullResult(ok=False, device_id="x")


class _Raiser:
    def raise_if_cancelled(self):
        raise RuntimeError("stop now")


def test_cancel_via_raise_if_cancelled(tmp_path: Path):
    cache = device_cache_dir(tmp_path / "lib", "dev1")
    result = pull_device(
        FakeMtpClient(SAVES_STORAGES, dbi_saves_tree()), cache, token=_Raiser()
    )
    assert result.ok is False
    assert result.error == "已取消"
    assert not cache.exists()


def test_close_error_is_swallowed(tmp_path: Path):
    class _BadClose(FakeMtpClient):
        def close(self):
            raise RuntimeError("close failed")

    cache = device_cache_dir(tmp_path / "lib", "dev1")
    result = pull_device(_BadClose(SAVES_STORAGES, dbi_saves_tree()), cache)
    assert result.ok is True


def test_progress_callback_errors_are_ignored(tmp_path: Path):
    cache = device_cache_dir(tmp_path / "lib", "dev1")

    def boom(_message):
        raise RuntimeError("progress sink down")

    result = pull_device(
        FakeMtpClient(SAVES_STORAGES, dbi_saves_tree()), cache, progress=boom
    )
    assert result.ok is True
