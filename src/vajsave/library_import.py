"""Snapshot ZIP export with a vaj-save.json manifest, and ZIP import."""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Optional

from .library import (
    BackupResult,
    GameRecord,
    Snapshot,
    backup_save,
    game_key,
    library_game_slot,
    load_catalog,
)
from .models import SaveEntry

MANIFEST_NAME = "vaj-save.json"
FORMAT = "vaj-save-snapshot"
FORMAT_VERSION = 1
PAYLOAD_DIR = "payload"


def export_snapshot_zip(
    snapshot: Snapshot,
    library_root: Path,
    zip_path: Path,
    game: Optional[GameRecord] = None,
) -> Path:
    """Write ``vaj-save.json`` plus ``payload/<snapshot files>`` into ``zip_path``."""
    source = snapshot.absolute_path(library_root)
    if not source.exists():
        raise FileNotFoundError(f"版本目录不存在: {source}")
    zip_path = Path(zip_path)
    if zip_path.suffix.lower() != ".zip":
        zip_path = zip_path.with_suffix(".zip")
    if game is None:
        game = _game_for_snapshot(Path(library_root), snapshot)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            MANIFEST_NAME,
            json.dumps(_manifest_dict(snapshot, game), ensure_ascii=False, indent=2),
        )
        for rel, path in _iter_payload_files(source):
            zf.write(path, arcname=f"{PAYLOAD_DIR}/{rel}")
    return zip_path


def import_snapshot_zip(
    library_root: Path,
    zip_path: Path,
    *,
    attach_game_id: str | None = None,
    new_platform: str | None = None,
    new_title_id: str | None = None,
    new_display_name: str | None = None,
    new_slot: str | None = None,
) -> BackupResult:
    """Import a snapshot ZIP into the library via content-addressed ``backup_save``.

    Zip Slip members are rejected before anything is extracted. Identical
    ``sha256`` reuses an existing snapshot (``is_new=False``). A legacy ZIP
    without ``vaj-save.json`` requires ``attach_game_id`` or both
    ``new_platform`` and ``new_display_name``.
    """
    root = Path(library_root)
    archive = Path(zip_path)
    if not archive.is_file():
        raise FileNotFoundError(f"ZIP 不存在: {archive}")
    root.mkdir(parents=True, exist_ok=True)
    tmpdir: Optional[Path] = None
    try:
        with zipfile.ZipFile(archive, "r") as zf:
            tmpdir = Path(tempfile.mkdtemp(prefix=".import-", dir=root))
            _assert_zip_members_safe(zf, tmpdir)
            _extract_zip(zf, tmpdir)
            manifest = _read_manifest(zf)
        source = _payload_source(tmpdir, has_manifest=manifest is not None)
        entry, identity_key = _resolve_import_entry(
            root,
            source,
            manifest=manifest,
            attach_game_id=attach_game_id,
            new_platform=new_platform,
            new_title_id=new_title_id,
            new_display_name=new_display_name,
            new_slot=new_slot,
        )
        return backup_save(entry, root, identity_key=identity_key)
    finally:
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _manifest_dict(snapshot: Snapshot, game: Optional[GameRecord]) -> dict[str, Any]:
    slot = snapshot.slot
    if not slot and game is not None:
        slot = library_game_slot(game.id)
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "platform": (game.platform if game else "") or "",
        "title_id": (game.title_id if game else "") or "",
        "display_name": (game.display_name if game else "") or "",
        "identity_key": (game.identity_key if game else None) or None,
        "slot": slot or "default",
        "user": snapshot.user,
        "created_at": snapshot.created_at or "",
        "sha256": snapshot.sha256 or "",
    }


def _game_for_snapshot(library_root: Path, snapshot: Snapshot) -> Optional[GameRecord]:
    catalog = load_catalog(library_root)
    for game in catalog.games.values():
        for snap in game.versions:
            if snap.path and snap.path == snapshot.path:
                return game
            if snap.id == snapshot.id and snap.sha256 == snapshot.sha256:
                return game
    return None


def _iter_payload_files(source: Path) -> list[tuple[str, Path]]:
    source = Path(source)
    if source.is_file():
        return [(source.name, source)]
    files: list[tuple[str, Path]] = []
    for child in sorted(source.rglob("*")):
        if child.is_symlink() or not child.is_file():
            continue
        files.append((child.relative_to(source).as_posix(), child))
    return files


def _assert_zip_members_safe(zf: zipfile.ZipFile, extract_root: Path) -> None:
    root = extract_root.resolve()
    for info in zf.infolist():
        name = info.filename
        if not name:
            continue
        target = (root / Path(name.replace("\\", "/"))).resolve()
        if not _is_inside(target, root):
            raise ValueError(f"ZIP 包含不安全路径: {name}")


def _is_inside(target: Path, root: Path) -> bool:
    if target == root:
        return True
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def _extract_zip(zf: zipfile.ZipFile, extract_root: Path) -> None:
    root = extract_root.resolve()
    for info in zf.infolist():
        name = info.filename
        if not name:
            continue
        dest = (root / Path(name.replace("\\", "/"))).resolve()
        if not _is_inside(dest, root):
            raise ValueError(f"ZIP 包含不安全路径: {name}")
        if info.is_dir() or name.endswith("/"):
            dest.mkdir(parents=True, exist_ok=True)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info, "r") as src, dest.open("wb") as out:
            shutil.copyfileobj(src, out)


def _read_manifest(zf: zipfile.ZipFile) -> Optional[dict[str, Any]]:
    try:
        raw = zf.read(MANIFEST_NAME)
    except KeyError:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"清单无法解析: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("清单格式无效")
    return data


def _payload_source(extract_root: Path, *, has_manifest: bool) -> Path:
    payload = extract_root / PAYLOAD_DIR
    if has_manifest:
        if not payload.is_dir():
            raise ValueError("ZIP 中没有可导入的存档")
        root = payload
    else:
        root = extract_root
    children = [
        child
        for child in sorted(root.iterdir())
        if child.name != MANIFEST_NAME and not child.is_symlink()
    ]
    if len(children) == 1:
        return children[0]
    if children:
        return root
    raise ValueError("ZIP 中没有可导入的存档")


def _resolve_import_entry(
    library_root: Path,
    source: Path,
    *,
    manifest: Optional[dict[str, Any]],
    attach_game_id: str | None,
    new_platform: str | None,
    new_title_id: str | None,
    new_display_name: str | None,
    new_slot: str | None,
) -> tuple[SaveEntry, Optional[str]]:
    catalog = load_catalog(library_root)
    wanted_id = str(attach_game_id).strip() if attach_game_id else ""
    if wanted_id:
        game = catalog.games.get(wanted_id)
        if game is not None:
            return _entry_from_game(game, source), game.identity_key

    if manifest is not None:
        identity_key = _text(manifest.get("identity_key")) or None
        if identity_key:
            for game in catalog.games.values():
                if game.identity_key == identity_key:
                    return _entry_from_game(game, source), identity_key
        platform = _text(manifest.get("platform"))
        title_id = _text(manifest.get("title_id"))
        display_name = _text(manifest.get("display_name"))
        slot = _text(manifest.get("slot")) or None
        if platform or title_id or display_name:
            probe = SaveEntry(
                platform=platform or "unknown",
                source_id="import",
                display_name=display_name or title_id or source.name,
                path=str(source),
                title_id=title_id or None,
                slot=slot,
            )
            existing = catalog.games.get(game_key(probe))
            if existing is not None:
                return _entry_from_game(existing, source), identity_key or existing.identity_key
            return probe, identity_key

    if _text(new_platform) and _text(new_display_name):
        entry = SaveEntry(
            platform=_text(new_platform),
            source_id="import",
            display_name=_text(new_display_name),
            path=str(source),
            title_id=_text(new_title_id) or None,
            slot=_text(new_slot) or None,
        )
        return entry, None

    raise ValueError(
        "无清单 ZIP 需要指定 attach_game_id，或同时提供 new_platform 与 new_display_name"
    )


def _entry_from_game(game: GameRecord, source: Path) -> SaveEntry:
    return SaveEntry(
        platform=game.platform or "unknown",
        source_id="import",
        display_name=game.display_name or game.title_id or game.id,
        path=str(source),
        title_id=game.title_id or None,
        slot=library_game_slot(game.id),
    )


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
