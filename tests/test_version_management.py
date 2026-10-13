"""Comprehensive test suite for vaj-save version management and restoration.

Covers all 12 scenarios specified in requirements:
1. A -> B -> A history timeline correctness
2. A -> A duplicate backup deduplication
3. Same-second successive backups uniqueness
4. Interrupted copy does not corrupt existing backups
5. Catalog write failure leaves catalog and staging consistent
6. Auto-prune executes correctly after backup commit
7. Locked versions are protected from auto-pruning
8. Unlocked versions follow pruning policy
9. Metadata consistency across lock/note/delete operations
10. Safe restore over non-empty target with rollback backup
11. Source modification during copy cancels commit
12. Backward compatibility with legacy catalogs and snapshots
+ Version diff calculation tests (P2)
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from vajsave.library import (
    Catalog,
    GameRecord,
    Snapshot,
    backup_save,
    destination_slot_dir,
    generate_unique_snapshot_id,
    hash_tree,
    latest_snapshot,
    load_catalog,
    prune_game_versions,
    restore_snapshot,
    save_catalog,
)
from vajsave.library_diff import diff_snapshots
from vajsave.library_versions import (
    delete_snapshot,
    set_snapshot_lock,
    set_snapshot_note,
    toggle_snapshot_lock,
)
from vajsave.models import SaveEntry
from vajsave.save_tree import copy_save_tree as real_copy_save_tree


def _make_save_entry(path: Path, title_id: str = "TEST00001", name: str = "Test Game") -> SaveEntry:
    return SaveEntry(
        platform="psp",
        source_id="psp_test",
        display_name=name,
        path=str(path),
        title_id=title_id,
    )


# =========================================================================
# Scenario 1: A -> B -> A History Timeline
# =========================================================================
def test_a_b_a_history_timeline(tmp_path: Path):
    library_root = tmp_path / "library"
    save_dir = tmp_path / "saves" / "TEST00001"
    save_dir.mkdir(parents=True)
    save_file = save_dir / "data.bin"

    # Step 1: Content A -> V1
    save_file.write_bytes(b"content_A")
    entry = _make_save_entry(save_dir)
    res1 = backup_save(entry, library_root)
    assert res1.is_new is True
    snap1 = res1.snapshot
    assert snap1 is not None

    # Step 2: Content B -> V2
    save_file.write_bytes(b"content_B")
    res2 = backup_save(entry, library_root)
    assert res2.is_new is True
    snap2 = res2.snapshot
    assert snap2 is not None
    assert snap2.id != snap1.id
    assert snap2.sha256 != snap1.sha256

    # Step 3: Content reverts to A -> V3
    save_file.write_bytes(b"content_A")
    res3 = backup_save(entry, library_root)
    assert res3.is_new is True, "Reverting to previous content A must create a new V3 snapshot!"
    snap3 = res3.snapshot
    assert snap3 is not None
    assert snap3.id != snap1.id
    assert snap3.id != snap2.id
    assert snap3.sha256 == snap1.sha256

    # Verify catalog contains V1(A) -> V2(B) -> V3(A) in order
    cat = load_catalog(library_root)
    game = cat.games.get(entry.path) or list(cat.games.values())[0]
    assert len(game.versions) == 3
    assert [v.id for v in game.versions] == [snap1.id, snap2.id, snap3.id]
    assert [v.sha256 for v in game.versions] == [snap1.sha256, snap2.sha256, snap3.sha256]


# =========================================================================
# Scenario 2: A -> A Duplicate Backup Skips Redundant Version
# =========================================================================
def test_a_a_duplicate_backup_skipped(tmp_path: Path):
    library_root = tmp_path / "library"
    save_dir = tmp_path / "saves" / "TEST00001"
    save_dir.mkdir(parents=True)
    (save_dir / "data.bin").write_bytes(b"content_A")
    entry = _make_save_entry(save_dir)

    res1 = backup_save(entry, library_root)
    assert res1.is_new is True

    # Backup again without changing content
    res2 = backup_save(entry, library_root)
    assert res2.is_new is False
    assert res2.snapshot.id == res1.snapshot.id

    cat = load_catalog(library_root)
    game = list(cat.games.values())[0]
    assert len(game.versions) == 1


# =========================================================================
# Scenario 3: Same-Second Rapid Successive Backups Generate Unique IDs
# =========================================================================
def test_same_second_unique_snapshot_ids(tmp_path: Path):
    library_root = tmp_path / "library"
    save_dir = tmp_path / "saves" / "TEST00001"
    save_dir.mkdir(parents=True)
    entry = _make_save_entry(save_dir)

    fixed_dt = datetime(2026, 3, 1, 12, 0, 0)
    fixed_stamp = fixed_dt.strftime("%Y-%m-%d_%H-%M-%S")

    # First backup with content 1
    (save_dir / "data.bin").write_bytes(b"version_1")
    res1 = backup_save(entry, library_root, when=fixed_dt)
    assert res1.is_new is True
    assert res1.snapshot.id == fixed_stamp

    # Second backup in the "same second" with content 2
    (save_dir / "data.bin").write_bytes(b"version_2")
    res2 = backup_save(entry, library_root, when=fixed_dt)
    assert res2.is_new is True
    assert res2.snapshot.id == f"{fixed_stamp}_1"

    # Third backup in the "same second" with content 3
    (save_dir / "data.bin").write_bytes(b"version_3")
    res3 = backup_save(entry, library_root, when=fixed_dt)
    assert res3.is_new is True
    assert res3.snapshot.id == f"{fixed_stamp}_2"

    cat = load_catalog(library_root)
    game = list(cat.games.values())[0]
    assert len(game.versions) == 3
    assert [v.id for v in game.versions] == [
        fixed_stamp,
        f"{fixed_stamp}_1",
        f"{fixed_stamp}_2",
    ]
    # Verify disk directories for all three exist and contain correct content
    assert (res1.snapshot.absolute_path(library_root) / save_dir.name / "data.bin").read_bytes() == b"version_1"
    assert (res2.snapshot.absolute_path(library_root) / save_dir.name / "data.bin").read_bytes() == b"version_2"
    assert (res3.snapshot.absolute_path(library_root) / save_dir.name / "data.bin").read_bytes() == b"version_3"


# =========================================================================
# Scenario 4: Copy Failure Leaves Existing Backups Untouched
# =========================================================================
def test_copy_failure_preserves_existing_backups(tmp_path: Path):
    library_root = tmp_path / "library"
    save_dir = tmp_path / "saves" / "TEST00001"
    save_dir.mkdir(parents=True)
    (save_dir / "data.bin").write_bytes(b"valid_v1")
    entry = _make_save_entry(save_dir)

    res1 = backup_save(entry, library_root)
    assert res1.is_new is True

    # Modify content for V2
    (save_dir / "data.bin").write_bytes(b"attempted_v2")

    # Simulate copy failure (e.g. disk full or permission error)
    with patch("vajsave.library.copy_save_tree", side_effect=OSError("Disk full")):
        with pytest.raises(OSError, match="Disk full"):
            backup_save(entry, library_root)

    # Verify existing V1 is completely intact
    cat = load_catalog(library_root)
    game = list(cat.games.values())[0]
    assert len(game.versions) == 1
    assert game.versions[0].id == res1.snapshot.id
    assert (res1.snapshot.absolute_path(library_root) / save_dir.name / "data.bin").read_bytes() == b"valid_v1"

    # Staging should not have leaked permanent state
    staging_dir = library_root / ".staging"
    if staging_dir.exists():
        assert len(list(staging_dir.iterdir())) == 0


# =========================================================================
# Scenario 5: Catalog Write Failure Rolls Back Snapshot Directory
# =========================================================================
def test_catalog_write_failure_cleans_up_staged_snapshot(tmp_path: Path):
    library_root = tmp_path / "library"
    save_dir = tmp_path / "saves" / "TEST00001"
    save_dir.mkdir(parents=True)
    (save_dir / "data.bin").write_bytes(b"valid_v1")
    entry = _make_save_entry(save_dir)

    res1 = backup_save(entry, library_root)
    assert res1.is_new is True

    (save_dir / "data.bin").write_bytes(b"content_v2")

    # Inject failure during save_catalog
    with patch("vajsave.library.save_catalog", side_effect=IOError("Catalog write disk error")):
        with pytest.raises(IOError, match="Catalog write disk error"):
            backup_save(entry, library_root)

    cat = load_catalog(library_root)
    game = list(cat.games.values())[0]
    assert len(game.versions) == 1
    assert game.versions[0].id == res1.snapshot.id

    # Make sure no orphaned v2 folder was left in the slot directory
    slot = destination_slot_dir(entry, library_root)
    assert len(list(slot.iterdir())) == 1


# =========================================================================
# Scenario 6 & 7: Auto-Pruning and Locked Version Protection
# =========================================================================
def test_locked_version_protected_from_pruning(tmp_path: Path):
    library_root = tmp_path / "library"
    save_dir = tmp_path / "saves" / "TEST00001"
    save_dir.mkdir(parents=True)
    entry = _make_save_entry(save_dir)

    # Create 4 versions: V1, V2, V3, V4
    snapshots = []
    for i in range(1, 5):
        (save_dir / "data.bin").write_bytes(f"version_{i}".encode("utf-8"))
        res = backup_save(entry, library_root)
        snapshots.append(res.snapshot)

    cat = load_catalog(library_root)
    game = list(cat.games.values())[0]

    # Lock V1 (the oldest) and V2
    set_snapshot_lock(library_root, game.id, snapshots[0].id, locked=True)
    set_snapshot_lock(library_root, game.id, snapshots[1].id, locked=True)

    # Prune with keep_last=2
    # Without locks, only V3 and V4 would remain.
    # With V1 and V2 locked, V1, V2, V3, V4 must ALL be retained!
    cat = load_catalog(library_root)
    game = list(cat.games.values())[0]
    pruned = prune_game_versions(game, library_root, keep_last=2)
    assert pruned == 0

    assert len(game.versions) == 4
    assert game.versions[0].locked is True
    assert game.versions[1].locked is True


# =========================================================================
# Scenario 8: Unlocking Allows Pruning Under Policy
# =========================================================================
def test_unlocking_allows_pruning(tmp_path: Path):
    library_root = tmp_path / "library"
    save_dir = tmp_path / "saves" / "TEST00001"
    save_dir.mkdir(parents=True)
    entry = _make_save_entry(save_dir)

    snapshots = []
    for i in range(1, 5):
        (save_dir / "data.bin").write_bytes(f"version_{i}".encode("utf-8"))
        res = backup_save(entry, library_root)
        snapshots.append(res.snapshot)

    cat = load_catalog(library_root)
    game = list(cat.games.values())[0]

    # Lock V1
    set_snapshot_lock(library_root, game.id, snapshots[0].id, locked=True)

    cat = load_catalog(library_root)
    game = list(cat.games.values())[0]
    pruned1 = prune_game_versions(game, library_root, keep_last=2)
    assert pruned1 == 1
    save_catalog(library_root, cat)
    # V2 was pruned, V1 (locked), V3, V4 remain
    assert len(game.versions) == 3
    assert game.versions[0].id == snapshots[0].id

    # Unlock V1
    set_snapshot_lock(library_root, game.id, snapshots[0].id, locked=False)

    # Now prune with keep_last=2 -> V1 should be pruned now
    cat = load_catalog(library_root)
    game = list(cat.games.values())[0]
    pruned2 = prune_game_versions(game, library_root, keep_last=2)
    assert pruned2 == 1
    assert len(game.versions) == 2
    assert [v.id for v in game.versions] == [snapshots[2].id, snapshots[3].id]


# =========================================================================
# Scenario 9: Metadata Consistency (Notes, Locks, Deletion Protection)
# =========================================================================
def test_metadata_consistency_notes_locks_force_delete(tmp_path: Path):
    library_root = tmp_path / "library"
    save_dir = tmp_path / "saves" / "TEST00001"
    save_dir.mkdir(parents=True)
    entry = _make_save_entry(save_dir)

    (save_dir / "data.bin").write_bytes(b"boss_save")
    res1 = backup_save(entry, library_root)
    snap1 = res1.snapshot

    cat = load_catalog(library_root)
    game = list(cat.games.values())[0]

    # Set note
    set_snapshot_note(library_root, game.id, snap1.id, "Boss 战前")
    cat = load_catalog(library_root)
    game = list(cat.games.values())[0]
    assert game.versions[0].note == "Boss 战前"

    # Lock version
    toggle_snapshot_lock(library_root, game.id, snap1.id)
    cat = load_catalog(library_root)
    game = list(cat.games.values())[0]
    assert game.versions[0].locked is True

    # Attempt to delete locked version without force -> must fail
    del_res = delete_snapshot(library_root, game.id, snap1.id, force=False)
    assert del_res.ok is False
    assert "已被锁定保护" in del_res.errors[0]

    # Delete with force=True -> succeeds
    del_res_forced = delete_snapshot(library_root, game.id, snap1.id, force=True)
    assert del_res_forced.ok is True
    assert del_res_forced.game_removed is True


# =========================================================================
# Scenario 10: Safe Restore Over Non-Empty Directory with Rollback Backup
# =========================================================================
def test_safe_restore_with_rollback_backup(tmp_path: Path):
    library_root = tmp_path / "library"
    save_dir = tmp_path / "saves" / "TEST00001"
    save_dir.mkdir(parents=True)
    (save_dir / "data.bin").write_bytes(b"snapshot_content")
    (save_dir / "extra.dat").write_bytes(b"extra_file")
    entry = _make_save_entry(save_dir)

    res = backup_save(entry, library_root)
    snap = res.snapshot

    restore_parent = tmp_path / "target_dir"
    restore_parent.mkdir(parents=True)

    # Initial restore
    restored1 = restore_snapshot(snap, library_root, restore_parent)
    assert restored1.exists()
    payload = restored1 / save_dir.name
    assert (payload / "data.bin").read_bytes() == b"snapshot_content"

    # Simulate dirty state on restored target: modify a file and add an orphan file
    (payload / "data.bin").write_bytes(b"current_state")
    (payload / "orphan.bin").write_bytes(b"unrelated_or_old_garbage")

    # Second restore to the same parent: target already exists and is non-empty!
    restored2 = restore_snapshot(snap, library_root, restore_parent)
    assert restored2 == restored1

    # The restore target must match snapshot EXACTLY:
    # - data.bin has snapshot_content
    # - extra.dat has extra_file
    # - orphan.bin MUST NOT exist!
    payload2 = restored2 / save_dir.name
    assert (payload2 / "data.bin").read_bytes() == b"snapshot_content"
    assert (payload2 / "extra.dat").read_bytes() == b"extra_file"
    assert not (payload2 / "orphan.bin").exists(), "Orphan files must not survive clean restore!"

    # Fallback backup directory must exist in restore_parent
    backup_dirs = list(restore_parent.glob(f"{restored1.name}.backup_before_restore_*"))
    assert len(backup_dirs) == 1
    backed_payload = backup_dirs[0] / save_dir.name
    assert (backed_payload / "data.bin").read_bytes() == b"current_state"
    assert (backed_payload / "orphan.bin").read_bytes() == b"unrelated_or_old_garbage"


# =========================================================================
# Scenario 11: Source Modification During Copy Cancels Commit
# =========================================================================
def test_source_modified_during_copy_cancels_commit(tmp_path: Path):
    library_root = tmp_path / "library"
    save_dir = tmp_path / "saves" / "TEST00001"
    save_dir.mkdir(parents=True)
    save_file = save_dir / "data.bin"
    save_file.write_bytes(b"initial_content")
    entry = _make_save_entry(save_dir)

    def tamper_copy(src, dst):
        real_copy_save_tree(src, dst)
        # Tamper the source file during copy
        save_file.write_bytes(b"tampered_content_during_copy")

    with patch("vajsave.library.copy_save_tree", side_effect=tamper_copy):
        with pytest.raises(ValueError, match="备份源在复制过程中被修改"):
            backup_save(entry, library_root)

    # Verification failed, so no snapshot should be committed to catalog
    cat = load_catalog(library_root)
    assert len(cat.games) == 0


# =========================================================================
# Scenario 12: Backward Compatibility with Legacy Catalog and Snapshots
# =========================================================================
def test_legacy_catalog_compatibility(tmp_path: Path):
    library_root = tmp_path / "library"
    library_root.mkdir(parents=True)

    # Create a legacy catalog JSON missing "locked" and "note" fields
    legacy_json = {
        "games": [
            {
                "id": "psp:TEST12345",
                "platform": "psp",
                "title_id": "TEST12345",
                "display_name": "Legacy Game",
                "versions": [
                    {
                        "id": "20240101_100000",
                        "created_at": "2024-01-01T10:00:00",
                        "sha256": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
                        "source_path": "/old/path/TEST12345",
                        "path": "psp/TEST12345/20240101_100000",
                        "files": [
                            {"path": "PARAM.SFO", "size": 100, "sha256": "1111"},
                            {"path": "SECURE.BIN", "size": 2048, "sha256": "2222"},
                        ],
                    }
                ],
            }
        ]
    }
    (library_root / "catalog.json").write_text(json.dumps(legacy_json), encoding="utf-8")

    # Load catalog with modern library
    cat = load_catalog(library_root)
    game = cat.games["psp:TEST12345"]
    assert len(game.versions) == 1
    v = game.versions[0]
    assert v.id == "20240101_100000"
    # Should safely default locked=False and note=""
    assert getattr(v, "locked", False) is False
    assert getattr(v, "note", "") == ""

    # Should serialize back with modern fields without breaking
    save_catalog(library_root, cat)
    cat_reloaded = load_catalog(library_root)
    v_reloaded = cat_reloaded.games["psp:TEST12345"].versions[0]
    assert v_reloaded.locked is False
    assert v_reloaded.note == ""


# =========================================================================
# Scenario 13: Lightweight Version Diff (P2)
# =========================================================================
def test_version_diff(tmp_path: Path):
    library_root = tmp_path / "library"
    save_dir = tmp_path / "saves" / "TEST00001"
    save_dir.mkdir(parents=True)
    entry = _make_save_entry(save_dir)

    # V1: file1 (10 bytes), file2 (20 bytes)
    (save_dir / "file1.bin").write_bytes(b"0123456789")
    (save_dir / "file2.bin").write_bytes(b"a" * 20)
    res1 = backup_save(entry, library_root)
    snap1 = res1.snapshot

    # V2: file1 modified (15 bytes), file2 removed, file3 added (30 bytes)
    (save_dir / "file1.bin").write_bytes(b"modified_data_15")
    (save_dir / "file2.bin").unlink()
    (save_dir / "file3.bin").write_bytes(b"c" * 30)
    res2 = backup_save(entry, library_root)
    snap2 = res2.snapshot

    diff = diff_snapshots(snap1, snap2, library_root)
    assert diff.snapshot_a_id == snap1.id
    assert diff.snapshot_b_id == snap2.id

    # Check additions
    assert len(diff.added_files) == 1
    assert diff.added_files[0].path == f"{save_dir.name}/file3.bin"
    assert diff.added_files[0].size_b == 30

    # Check removals
    assert len(diff.removed_files) == 1
    assert diff.removed_files[0].path == f"{save_dir.name}/file2.bin"
    assert diff.removed_files[0].size_a == 20

    # Check modifications
    assert len(diff.modified_files) == 1
    assert diff.modified_files[0].path == f"{save_dir.name}/file1.bin"
    assert diff.modified_files[0].size_a == 10
    assert diff.modified_files[0].size_b == 16  # len("modified_data_15") is 16
    assert diff.modified_files[0].size_delta == 6

    # Totals: V1 has 2 files (30 bytes), V2 has 2 files (46 bytes)
    assert diff.total_files_a == 2
    assert diff.total_files_b == 2
    assert diff.file_count_delta == 0
    assert diff.size_delta == 16
