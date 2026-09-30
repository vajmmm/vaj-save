"""百度网盘单向库同步；可整体移除，不侵入本地备份服务。"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Optional

from .baidu_api import BaiduNetdiskClient, BaiduSyncError
from .jobs import CancelToken, JobCancelled, JobProgress
from .library import Catalog, GameRecord, Snapshot, catalog_path
from .library_import import FORMAT, FORMAT_VERSION, MANIFEST_NAME, PAYLOAD_DIR


SYNC_MANIFEST_FORMAT = "vaj-save-baidu-sync"
SYNC_MANIFEST_VERSION = 1
ZIP_CHUNK_SIZE = 1024 * 1024
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class SyncResult:
    uploaded: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    cancelled: bool = False


@dataclass(frozen=True)
class _Artifact:
    path: Path
    filename: str


class BaiduLibrarySync:
    """将本地版本库打包为追加式文件并上传到应用沙盒目录。"""

    def __init__(self, client: BaiduNetdiskClient) -> None:
        self.client = client

    def sync(
        self,
        library_root: Path,
        *,
        token: Optional[CancelToken] = None,
        report: Optional[Callable[[JobProgress], None]] = None,
    ) -> SyncResult:
        uploaded: list[str] = []
        skipped: list[str] = []
        try:
            credentials = self.client.credentials.load()
            if credentials is None or not credentials.connected:
                raise BaiduSyncError("请先在设置中连接百度网盘。")
            self._check_cancel(token)
            catalog = _load_catalog_strict(Path(library_root))
            snapshots = _verified_snapshots(
                catalog, Path(library_root), token=token
            )
            if not snapshots:
                raise BaiduSyncError("本地备份库中没有可同步的版本。")

            with tempfile.TemporaryDirectory(prefix="vaj-save-baidu-sync-") as temporary:
                work_root = Path(temporary)
                artifacts, manifest = self._prepare_artifacts(
                    catalog,
                    snapshots,
                    work_root,
                    token=token,
                    report=report,
                )
                manifest_name = _digest_name("manifest", manifest, ".json")
                manifest_path = work_root / manifest_name
                manifest_path.write_bytes(manifest)
                artifacts.append(_Artifact(manifest_path, manifest_name))

                existing = self.client.list_app_files(credentials.app_name)
                total = len(artifacts)
                for index, artifact in enumerate(artifacts):
                    self._check_cancel(token)
                    if artifact.filename in existing:
                        remote_size = existing[artifact.filename].get("size")
                        if remote_size is not None:
                            try:
                                size_matches = int(remote_size) == artifact.path.stat().st_size
                            except (TypeError, ValueError, OSError) as exc:
                                raise BaiduSyncError(
                                    f"无法核对网盘中的同名文件：{artifact.filename}"
                                ) from exc
                            if not size_matches:
                                raise BaiduSyncError(
                                    f"网盘中存在大小不一致的同名文件：{artifact.filename}"
                                )
                        skipped.append(artifact.filename)
                        self._report(
                            report,
                            f"已存在，跳过 {index + 1}/{total}：{artifact.filename}",
                            index + 1,
                            total,
                        )
                        continue
                    remote_path = f"/apps/{credentials.app_name}/{artifact.filename}"

                    def chunk_progress(done: int, count: int) -> None:
                        self._report(
                            report,
                            f"正在同步 {index + 1}/{total}：{artifact.filename}（分片 {done}/{count}）",
                            index,
                            total,
                        )

                    self.client.upload_file(
                        artifact.path,
                        remote_path,
                        cancel_check=(token.raise_if_cancelled if token else None),
                        chunk_progress=chunk_progress,
                    )
                    uploaded.append(artifact.filename)
                    self._report(
                        report,
                        f"已同步 {index + 1}/{total}：{artifact.filename}",
                        index + 1,
                        total,
                    )
                return SyncResult(tuple(uploaded), tuple(skipped))
        except JobCancelled:
            return SyncResult(tuple(uploaded), tuple(skipped), cancelled=True)

    def _prepare_artifacts(
        self,
        catalog: Catalog,
        snapshots: list[tuple[GameRecord, int, Snapshot, Path, str]],
        work_root: Path,
        *,
        token: Optional[CancelToken],
        report: Optional[Callable[[JobProgress], None]],
    ) -> tuple[list[_Artifact], bytes]:
        catalog_data = catalog.to_dict()
        game_indexes = {game_id: index for index, game_id in enumerate(catalog.games)}
        archive_artifacts: list[_Artifact] = []
        references: list[dict[str, str]] = []
        total = len(snapshots)

        for index, (game, version_index, snapshot, source, digest) in enumerate(
            snapshots, 1
        ):
            self._check_cancel(token)
            self._report(report, f"正在打包版本 {index}/{total}", index - 1, total)
            verified = replace(snapshot, sha256=digest)
            metadata = _snapshot_manifest(verified, game)
            fingerprint = hashlib.sha256(
                json.dumps(
                    [game.id, metadata],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            filename = f"vaj-save-snapshot-{digest}-{fingerprint}.zip"
            archive_path = work_root / filename
            _write_snapshot_zip(
                archive_path,
                verified,
                game,
                source,
                cancel_check=(token.raise_if_cancelled if token else None),
            )
            archive_artifacts.append(_Artifact(archive_path, filename))
            references.append(
                {
                    "game_id": game.id,
                    "snapshot_id": snapshot.id,
                    "sha256": digest,
                    "filename": filename,
                }
            )
            version_data = catalog_data["games"][game_indexes[game.id]]["versions"][
                version_index
            ]
            version_data.pop("source_path", None)
            version_data["sha256"] = digest

        catalog_bytes = json.dumps(
            catalog_data, ensure_ascii=False, sort_keys=True, indent=2
        ).encode("utf-8")
        catalog_filename = _digest_name("catalog", catalog_bytes, ".json")
        catalog_path = work_root / catalog_filename
        catalog_path.write_bytes(catalog_bytes)

        manifest = {
            "format": SYNC_MANIFEST_FORMAT,
            "format_version": SYNC_MANIFEST_VERSION,
            "snapshot_format": FORMAT,
            "snapshot_format_version": FORMAT_VERSION,
            "catalog": catalog_filename,
            "snapshots": sorted(
                references,
                key=lambda item: (item["game_id"], item["snapshot_id"], item["sha256"]),
            ),
        }
        manifest_bytes = json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, indent=2
        ).encode("utf-8")
        ordered = sorted(archive_artifacts, key=lambda item: item.filename)
        ordered.append(_Artifact(catalog_path, catalog_filename))
        return ordered, manifest_bytes

    @staticmethod
    def _check_cancel(token: Optional[CancelToken]) -> None:
        if token is not None:
            token.raise_if_cancelled()

    @staticmethod
    def _report(
        callback: Optional[Callable[[JobProgress], None]],
        message: str,
        current: int,
        total: int,
    ) -> None:
        if callback is not None:
            callback(
                JobProgress(
                    message=message,
                    current=current,
                    total=total,
                    cancellable=True,
                )
            )


def _load_catalog_strict(library_root: Path) -> Catalog:
    try:
        root = library_root.resolve(strict=True)
    except OSError as exc:
        raise BaiduSyncError("本地备份库路径无法访问。") from exc
    if not root.is_dir() or library_root.is_symlink():
        raise BaiduSyncError("本地备份库必须是普通目录。")
    path = catalog_path(root)
    if path.is_symlink():
        raise BaiduSyncError("本地备份目录清单不能是符号链接。")
    if not path.exists():
        return Catalog()
    if not path.is_file():
        raise BaiduSyncError("本地备份目录清单不是普通文件。")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BaiduSyncError("本地备份目录清单无法读取或解析。") from exc
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or not isinstance(data.get("games"), list)
    ):
        raise BaiduSyncError("本地备份目录清单版本或结构不受支持。")
    try:
        return Catalog.from_dict(data)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise BaiduSyncError("本地备份目录清单结构无效。") from exc


def _verified_snapshots(
    catalog: Catalog,
    library_root: Path,
    *,
    token: Optional[CancelToken] = None,
) -> list[tuple[GameRecord, int, Snapshot, Path, str]]:
    root = library_root.resolve(strict=True)
    result: list[tuple[GameRecord, int, Snapshot, Path, str]] = []
    for game in catalog.games.values():
        if not all(
            isinstance(value, str)
            for value in (game.id, game.platform, game.title_id, game.display_name)
        ):
            raise BaiduSyncError("本地备份目录中的游戏信息格式无效。")
        for index, snapshot in enumerate(game.versions):
            if token is not None:
                token.raise_if_cancelled()
            if not all(
                isinstance(value, str)
                for value in (snapshot.id, snapshot.path, snapshot.sha256, snapshot.created_at)
            ):
                raise BaiduSyncError("本地备份目录中的版本信息格式无效。")
            relative = PurePosixPath(snapshot.path)
            windows_path = PureWindowsPath(snapshot.path)
            if (
                not snapshot.path
                or not relative.parts
                or relative.is_absolute()
                or windows_path.is_absolute()
                or windows_path.drive
                or "\\" in snapshot.path
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise BaiduSyncError(f"版本路径不安全，无法同步：{snapshot.id}")
            cursor = root
            for part in relative.parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    raise BaiduSyncError(f"版本路径不能经过符号链接：{snapshot.id}")
            source = root.joinpath(*relative.parts)
            try:
                resolved = source.resolve(strict=True)
                resolved.relative_to(root)
            except (OSError, ValueError) as exc:
                raise BaiduSyncError(f"版本不在本地备份库中或已丢失：{snapshot.id}") from exc
            if source.is_symlink():
                raise BaiduSyncError(f"版本路径不能是符号链接：{snapshot.id}")
            _assert_no_symlinks_or_special_files(
                source,
                snapshot.id,
                cancel_check=(token.raise_if_cancelled if token else None),
            )
            try:
                digest = _snapshot_content_digest(
                    source,
                    snapshot.sha256,
                    cancel_check=(token.raise_if_cancelled if token else None),
                )
            except (OSError, ValueError) as exc:
                raise BaiduSyncError(f"无法校验版本内容：{snapshot.id}") from exc
            result.append((game, index, snapshot, source, digest))
    return result


def _snapshot_content_digest(
    source: Path,
    recorded_digest: str,
    *,
    cancel_check: Optional[Callable[[], None]],
) -> str:
    if source.is_file():
        digest = _uncached_tree_hash(source, cancel_check=cancel_check)
        if recorded_digest and recorded_digest.lower() != digest:
            raise BaiduSyncError("版本内容与目录记录不一致。")
        return digest
    children = sorted(source.iterdir()) if source.is_dir() else []
    if not recorded_digest:
        target = children[0] if len(children) == 1 else source
        return _uncached_tree_hash(target, cancel_check=cancel_check)
    if len(children) == 1:
        child_digest = _uncached_tree_hash(children[0], cancel_check=cancel_check)
        if child_digest == recorded_digest.lower():
            return child_digest
    digest = _uncached_tree_hash(source, cancel_check=cancel_check)
    if recorded_digest.lower() == digest:
        return digest
    raise BaiduSyncError("版本内容与目录记录不一致。")


def _uncached_tree_hash(
    path: Path, *, cancel_check: Optional[Callable[[], None]]
) -> str:
    def check() -> None:
        if cancel_check is not None:
            cancel_check()

    root = Path(path)
    try:
        initial = root.stat(follow_symlinks=False)
    except OSError as exc:
        raise BaiduSyncError(f"无法读取版本内容：{root.name}") from exc
    if stat.S_ISREG(initial.st_mode):
        digest = hashlib.sha256()
        digest.update(b"file\0")
        _hash_file_into(digest, root, initial, check)
        return digest.hexdigest()
    if not stat.S_ISDIR(initial.st_mode):
        raise BaiduSyncError(f"版本包含不支持的文件类型：{root.name}")

    files: list[tuple[Path, str, os.stat_result]] = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        check()
        dirnames.sort()
        for name in dirnames:
            info = (Path(directory) / name).stat(follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode):
                raise BaiduSyncError(f"版本包含不支持的文件类型：{name}")
        for name in filenames:
            check()
            child = Path(directory) / name
            info = child.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise BaiduSyncError(f"版本包含不支持的文件类型：{child.name}")
            files.append((child, child.relative_to(root).as_posix(), info))
    files.sort(key=lambda item: item[1])
    digest = hashlib.sha256()
    for child, relative, info in files:
        check()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(info.st_size).encode("ascii"))
        digest.update(b"\0")
        _hash_file_into(digest, child, info, check)
    return digest.hexdigest()


def _hash_file_into(
    digest: Any,
    path: Path,
    initial: os.stat_result,
    check: Callable[[], None],
) -> None:
    total = 0
    try:
        with path.open("rb") as handle:
            while True:
                check()
                chunk = handle.read(ZIP_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                digest.update(chunk)
        final = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise BaiduSyncError(f"无法读取版本内容：{path.name}") from exc
    if (
        total != initial.st_size
        or final.st_size != initial.st_size
        or final.st_mtime_ns != initial.st_mtime_ns
        or final.st_ino != initial.st_ino
    ):
        raise BaiduSyncError(f"版本内容在校验时发生变化：{path.name}")


def _assert_no_symlinks_or_special_files(
    path: Path,
    snapshot_id: str,
    *,
    cancel_check: Optional[Callable[[], None]],
) -> None:
    try:
        root_stat = path.lstat()
    except OSError as exc:
        raise BaiduSyncError(f"无法读取版本：{snapshot_id}") from exc
    if stat.S_ISLNK(root_stat.st_mode):
        raise BaiduSyncError(f"版本路径不能是符号链接：{snapshot_id}")
    if stat.S_ISREG(root_stat.st_mode):
        return
    if not stat.S_ISDIR(root_stat.st_mode):
        raise BaiduSyncError(f"版本包含不支持的文件类型：{snapshot_id}")
    for directory, dirnames, filenames in os.walk(path, followlinks=False):
        if cancel_check is not None:
            cancel_check()
        for name in dirnames + filenames:
            if cancel_check is not None:
                cancel_check()
            child = Path(directory) / name
            try:
                mode = child.lstat().st_mode
            except OSError as exc:
                raise BaiduSyncError(f"无法读取版本：{snapshot_id}") from exc
            if stat.S_ISLNK(mode):
                raise BaiduSyncError(f"版本中包含符号链接：{snapshot_id}")
            if name in dirnames and not stat.S_ISDIR(mode):
                raise BaiduSyncError(f"版本目录结构无效：{snapshot_id}")
            if name in filenames and not stat.S_ISREG(mode):
                raise BaiduSyncError(f"版本包含不支持的文件类型：{snapshot_id}")


def _snapshot_manifest(snapshot: Snapshot, game: GameRecord) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "platform": game.platform or "",
        "title_id": game.title_id or "",
        "display_name": game.display_name or "",
        "identity_key": game.identity_key or None,
        "slot": snapshot.slot or "default",
        "user": snapshot.user,
        "created_at": snapshot.created_at or "",
        "sha256": snapshot.sha256 or "",
    }


def _write_snapshot_zip(
    archive_path: Path,
    snapshot: Snapshot,
    game: GameRecord,
    source: Path,
    *,
    cancel_check: Optional[Callable[[], None]],
) -> None:
    manifest = json.dumps(
        _snapshot_manifest(snapshot, game), ensure_ascii=False, indent=2
    ).encode("utf-8")
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        _write_zip_bytes(zf, MANIFEST_NAME, manifest)
        if source.is_file():
            payload_files = [(source.name, source)]
        else:
            payload_files = [
                (child.relative_to(source).as_posix(), child)
                for child in sorted(source.rglob("*"))
                if child.is_file()
            ]
        for relative, payload in payload_files:
            if cancel_check is not None:
                cancel_check()
            info = _zip_info(f"{PAYLOAD_DIR}/{relative}")
            with zf.open(info, "w", force_zip64=True) as target, payload.open("rb") as origin:
                while True:
                    if cancel_check is not None:
                        cancel_check()
                    chunk = origin.read(ZIP_CHUNK_SIZE)
                    if not chunk:
                        break
                    target.write(chunk)


def _write_zip_bytes(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    with zf.open(_zip_info(name), "w", force_zip64=True) as target:
        target.write(data)


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def _digest_name(kind: str, data: bytes, suffix: str) -> str:
    return f"vaj-save-{kind}-{hashlib.sha256(data).hexdigest()}{suffix}"


__all__ = ["BaiduLibrarySync", "SyncResult"]
