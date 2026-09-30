import json
import os
from io import BytesIO
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from vajsave.baidu_api import BaiduCredentials, BaiduSyncError
from vajsave.baidu_sync import BaiduLibrarySync
from vajsave.library import backup_save, catalog_path, hash_tree
from vajsave.library_import import import_snapshot_zip
from vajsave.models import SaveEntry


class _CredentialStore:
    def __init__(self):
        self.value = BaiduCredentials(
            "app-key", "secret", "vaj-save", "access", "refresh", 9999999999
        )

    def load(self):
        return self.value


class _RemoteClient:
    def __init__(self):
        self.credentials = _CredentialStore()
        self.files = {}
        self.calls = []
        self.after_upload = None

    def list_app_files(self, app_name):
        assert app_name == "vaj-save"
        return {name: {"server_filename": name} for name in self.files}

    def upload_file(
        self,
        local_path,
        remote_path,
        *,
        cancel_check=None,
        chunk_progress=None,
    ):
        if cancel_check:
            cancel_check()
        source = Path(local_path)
        remote_name = Path(remote_path).name
        self.calls.append((remote_name, remote_path))
        self.files[remote_name] = source.read_bytes()
        if chunk_progress:
            chunk_progress(1, 1)
        if self.after_upload:
            self.after_upload(remote_name)


def _make_library(root: Path) -> Path:
    source = root / "private-user-folder" / "Persona"
    source.mkdir(parents=True)
    (source / "SAVE.BIN").write_bytes(b"persona-save-data")
    library = root / "library"
    result = backup_save(
        SaveEntry(
            platform="psp",
            source_id="psp",
            display_name="Persona 2",
            title_id="ULJM05800",
            path=str(source),
            user="Player One",
        ),
        library,
        datetime(2026, 9, 1, 12, 30, 0),
        identity_key="psp:ULJM05800",
    )
    return library


def test_sync_uploads_redacted_catalog_zip_then_commit_manifest(tmp_path):
    library = _make_library(tmp_path)
    client = _RemoteClient()
    reports = []

    result = BaiduLibrarySync(client).sync(library, report=reports.append)

    assert len(result.uploaded) == 3
    assert not result.skipped
    uploaded_names = [name for name, _remote in client.calls]
    assert uploaded_names[0].endswith(".zip")
    assert "catalog" in uploaded_names[1]
    assert "manifest" in uploaded_names[2]
    assert all(remote.startswith("/apps/vaj-save/") for _name, remote in client.calls)
    assert reports

    catalog_name = uploaded_names[1]
    remote_catalog = json.loads(client.files[catalog_name])
    snapshot = remote_catalog["games"][0]["versions"][0]
    assert "source_path" not in snapshot
    assert snapshot["sha256"]
    assert "private-user-folder" not in client.files[catalog_name].decode("utf-8")
    archive_name = uploaded_names[0]
    with zipfile.ZipFile(BytesIO(client.files[archive_name])) as archive:
        assert "payload/Persona/SAVE.BIN" in archive.namelist()
        zip_manifest = json.loads(archive.read("vaj-save.json"))
        assert zip_manifest["sha256"] == snapshot["sha256"]
        assert "source_path" not in zip_manifest
    archive_path = tmp_path / "remote-snapshot.zip"
    archive_path.write_bytes(client.files[archive_name])
    restored = import_snapshot_zip(tmp_path / "restored-library", archive_path)
    assert restored.snapshot.sha256 == snapshot["sha256"]

    sync_manifest = json.loads(client.files[uploaded_names[2]])
    assert sync_manifest["format"] == "vaj-save-baidu-sync"
    assert sync_manifest["catalog"] == catalog_name
    assert sync_manifest["snapshots"][0]["filename"] == archive_name


def test_second_sync_skips_deterministic_files(tmp_path):
    library = _make_library(tmp_path)
    client = _RemoteClient()
    service = BaiduLibrarySync(client)

    first = service.sync(library)
    first_files = dict(client.files)
    second = service.sync(library)

    assert len(first.uploaded) == 3
    assert not second.uploaded
    assert len(second.skipped) == 3
    assert client.files == first_files


def test_sync_refuses_modified_snapshot_without_uploading(tmp_path):
    library = _make_library(tmp_path)
    catalog = json.loads(catalog_path(library).read_text(encoding="utf-8"))
    snapshot_path = catalog["games"][0]["versions"][0]["path"]
    (library / snapshot_path / "Persona" / "SAVE.BIN").write_bytes(b"modified")
    client = _RemoteClient()

    with pytest.raises(BaiduSyncError, match="版本内容与目录记录不一致"):
        BaiduLibrarySync(client).sync(library)

    assert not client.calls
    assert client.files == {}


def test_sync_hash_check_does_not_trust_stat_cache(tmp_path):
    library = _make_library(tmp_path)
    catalog = json.loads(catalog_path(library).read_text(encoding="utf-8"))
    snapshot_path = catalog["games"][0]["versions"][0]["path"]
    snapshot_root = library / snapshot_path
    payload = snapshot_root / "Persona" / "SAVE.BIN"
    hash_tree(snapshot_root)
    hash_tree(snapshot_root / "Persona")
    original_stat = payload.stat()
    payload.write_bytes(b"PERSONA-save-data")
    os.utime(payload, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    client = _RemoteClient()

    with pytest.raises(BaiduSyncError, match="版本内容与目录记录不一致"):
        BaiduLibrarySync(client).sync(library)

    assert not client.calls


def test_sync_rejects_catalog_path_traversal(tmp_path):
    library = _make_library(tmp_path)
    path = catalog_path(library)
    catalog = json.loads(path.read_text(encoding="utf-8"))
    catalog["games"][0]["versions"][0]["path"] = "../../outside"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    client = _RemoteClient()

    with pytest.raises(BaiduSyncError, match="版本路径不安全"):
        BaiduLibrarySync(client).sync(library)

    assert not client.calls


def test_cancel_keeps_uploaded_files_but_does_not_write_commit_manifest(tmp_path):
    from vajsave.jobs import CancelToken

    library = _make_library(tmp_path)
    client = _RemoteClient()
    token = CancelToken()
    client.after_upload = lambda _name: token.cancel()

    result = BaiduLibrarySync(client).sync(library, token=token)

    assert result.cancelled
    assert len(result.uploaded) == 1
    assert len(client.calls) == 1
    assert all("manifest" not in name for name in client.files)


def test_sync_rejects_corrupt_catalog(tmp_path):
    library = _make_library(tmp_path)
    catalog_path(library).write_text("{not-json", encoding="utf-8")
    client = _RemoteClient()

    with pytest.raises(BaiduSyncError, match="无法读取或解析"):
        BaiduLibrarySync(client).sync(library)

    assert not client.calls
