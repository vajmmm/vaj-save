import gc
import struct
from pathlib import Path
from typing import Any, Dict

import pytest

from vajsave.remote_ftp import FtpEntry, RemoteFtpError
from vajsave.remote_mtp import (
    MtpDevice,
    MtpEntry,
    MtpError,
    MtpNotFound,
    MtpStorage,
)


@pytest.fixture(autouse=True)
def _isolate_app_config(tmp_path_factory, monkeypatch):
    """Keep the app config file out of the real user profile.

    ``AppState()`` now reads a persisted library path, so without this every test
    that builds a default AppState would depend on the developer's own config.
    """
    config_dir = tmp_path_factory.mktemp("app-config")
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(config_dir / "config.json"))
    yield
    # Tk variables from torn-down test windows must be finalised on the main
    # thread; a background worker thread triggering the cyclic GC instead makes
    # tkinter's ``Variable.__del__`` run off-thread and can abort the process at
    # shutdown on macOS.  Collect here, while the main thread is still in charge.
    gc.collect()


def build_sfo(entries: Dict[str, Any]) -> bytes:
    """Helper to generate standard binary PARAM.SFO (PSF) bytes."""
    sorted_items = sorted(entries.items(), key=lambda x: x[0])
    num_entries = len(sorted_items)

    key_table = bytearray()
    data_table = bytearray()
    index_entries = []

    for key, val in sorted_items:
        key_offset = len(key_table)
        key_table.extend(key.encode("utf-8") + b"\x00")

        data_offset = len(data_table)
        if isinstance(val, int):
            data_fmt = 0x0404
            data_bytes = struct.pack("<I", val)
            data_len = 4
            data_max_len = 4
        else:
            data_fmt = 0x0204
            raw_bytes = str(val).encode("utf-8") + b"\x00"
            data_len = len(raw_bytes)
            data_max_len = (data_len + 3) & ~3
            data_bytes = raw_bytes.ljust(data_max_len, b"\x00")

        data_table.extend(data_bytes)
        index_entries.append((key_offset, data_fmt, data_len, data_max_len, data_offset))

    header_len = 20
    index_table_len = num_entries * 16
    key_table_offset = header_len + index_table_len

    padding = (4 - (len(key_table) % 4)) % 4
    key_table.extend(b"\x00" * padding)
    data_table_offset = key_table_offset + len(key_table)

    header = struct.pack(
        "<4sIIII",
        b"\x00PSF",
        0x00000101,
        key_table_offset,
        data_table_offset,
        num_entries,
    )

    index_bytes = bytearray()
    for k_off, fmt, d_len, d_max, d_off in index_entries:
        index_bytes.extend(struct.pack("<HHIII", k_off, fmt, d_len, d_max, d_off))

    return bytes(header + index_bytes + key_table + data_table)


def checkpoint_ftp_tree() -> Dict[str, Any]:
    """A tiny remote tree shaped like a Checkpoint export over FTP.

    Directories are nested dicts; files are ``bytes`` leaves.  Both the 3DS and
    Switch Checkpoint save roots are present so a pull/ex scan exercises the
    real platform scanners without touching the network.
    """
    return {
        "3ds": {
            "Checkpoint": {
                "saves": {
                    "0x011C4 Pokemon Moon": {
                        "slot0": {"main": b"MOON-SAVE"},
                    }
                }
            }
        },
        "switch": {
            "Checkpoint": {
                "saves": {
                    "0100000000010000 Super Mario Odyssey": {
                        "slot0": {"main": b"ODYSSEY-SAVE"},
                    }
                }
            }
        },
    }


class FakeRemoteFtpClient:
    """In-memory stand-in for :class:`vajsave.remote_ftp.RemoteFtpClient`.

    It mirrors the real client's read-only surface (``connect``/``close``/
    ``list_dir``/``download``) so ``ftp_fetch.pull_preset`` can be driven
    deterministically.  ``fail_paths`` makes a specific remote path raise, which
    is how partial-pull failure is reproduced.
    """

    def __init__(
        self,
        tree: Dict[str, Any],
        *,
        fail_paths: Any = (),
        error_message: str = "",
        modified: Any = None,
    ) -> None:
        self.tree = tree
        self.fail_paths = set(fail_paths)
        self.error_message = error_message
        self.modified = dict(modified or {})
        self.commands: list = []
        self.connected = False
        self.closed = False

    @staticmethod
    def _child_path(remote_path: str, name: str) -> str:
        base = str(remote_path or "/").rstrip("/")
        return f"{base}/{name}" if base else f"/{name}"

    def connect(self) -> "FakeRemoteFtpClient":
        self.commands.append("CONNECT")
        self.connected = True
        return self

    def close(self) -> None:
        self.closed = True

    def _node(self, remote_path: str) -> Any:
        parts = [part for part in str(remote_path).split("/") if part]
        node: Any = self.tree
        for part in parts:
            node = node[part]
        return node

    def list_dir(self, remote_path: str):
        if remote_path in self.fail_paths:
            raise RemoteFtpError(
                self.error_message or f"no such directory: {remote_path}"
            )
        self.commands.append(("MLSD", remote_path))
        node = self._node(remote_path)
        assert isinstance(node, dict), f"not a directory: {remote_path}"
        entries = []
        for name, value in node.items():
            child = self._child_path(remote_path, name)
            if isinstance(value, dict):
                entries.append(FtpEntry(name=name, is_dir=True))
                continue
            size = len(value) if isinstance(value, (bytes, bytearray)) else 0
            entries.append(
                FtpEntry(
                    name=name,
                    is_dir=False,
                    size=size,
                    modified=str(self.modified.get(child, "") or ""),
                )
            )
        return entries

    def download(self, remote_path: str, local_path: Path) -> int:
        if remote_path in self.fail_paths:
            raise RemoteFtpError(
                self.error_message or f"no such file: {remote_path}"
            )
        self.commands.append(("RETR", remote_path))
        data = self._node(remote_path)
        assert isinstance(data, (bytes, bytearray)), f"not a file: {remote_path}"
        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes(data))
        return len(data)


def fake_client_factory(tree: Dict[str, Any], **kwargs: Any):
    """Return a ``client_factory`` plus the created clients (for assertions)."""

    def factory(profile):
        client = FakeRemoteFtpClient(tree, **kwargs)
        factory.clients.append(client)
        return client

    factory.clients = []  # type: ignore[attr-defined]
    return factory


def _coerce_storage(value: Any) -> MtpStorage:
    if isinstance(value, MtpStorage):
        return value
    storage_id, name, *rest = value
    return MtpStorage(storage_id=str(storage_id), name=str(name), description=rest[0] if rest else "")


class FakeMtpClient:
    """In-memory stand-in for a WPD/MTP portable-device client.

    ``storages`` is a list of :class:`MtpStorage` (or ``(id, name)`` tuples) and
    ``tree`` maps a storage id to a nested dict: directories are dicts, files
    are ``bytes`` leaves.  ``fail_paths`` makes a specific remote object raise
    so partial-pull failure can be reproduced deterministically.
    """

    def __init__(
        self,
        storages: Any,
        tree: Dict[str, Any],
        *,
        fail_paths: Any = (),
        error_message: str = "",
        modified: Any = None,
    ) -> None:
        self.storages = [_coerce_storage(item) for item in storages]
        self.tree = tree
        self.fail_paths = set(fail_paths)
        self.error_message = error_message
        self.modified = dict(modified or {})
        self.commands: list = []
        self.downloads: list = []
        self.connected = False
        self.closed = False

    @staticmethod
    def _parts(remote_path: str):
        return [part for part in str(remote_path or "/").split("/") if part]

    def connect(self) -> "FakeMtpClient":
        self.commands.append("CONNECT")
        self.connected = True
        return self

    def close(self) -> None:
        self.closed = True

    def list_storages(self):
        self.commands.append("STORAGES")
        return list(self.storages)

    def _fails(self, storage_id: str, remote_path: str) -> bool:
        return (storage_id, remote_path) in self.fail_paths or remote_path in self.fail_paths

    def _node(self, storage_id: str, remote_path: str) -> Any:
        node: Any = self.tree[storage_id]
        for part in self._parts(remote_path):
            node = node[part]
        return node

    def list_dir(self, storage_id: str, remote_path: str):
        self.commands.append(("LS", storage_id, remote_path))
        if self._fails(storage_id, remote_path):
            raise MtpNotFound(self.error_message or f"no such object: {remote_path}")
        try:
            node = self._node(storage_id, remote_path)
        except (KeyError, TypeError) as exc:
            raise MtpNotFound(f"no such object: {remote_path}") from exc
        if not isinstance(node, dict):
            raise MtpNotFound(f"not a directory: {remote_path}")
        entries = []
        for name, value in node.items():
            if isinstance(value, dict):
                entries.append(MtpEntry(name=str(name), is_dir=True))
                continue
            child = f"{str(remote_path).rstrip('/')}/{name}"
            entries.append(
                MtpEntry(
                    name=str(name),
                    is_dir=False,
                    size=len(value) if isinstance(value, (bytes, bytearray)) else 0,
                    modified=str(self.modified.get(f"{storage_id}:{child}", "") or ""),
                )
            )
        return entries

    def download(self, storage_id: str, remote_path: str, local_path: Path) -> int:
        self.downloads.append((storage_id, remote_path))
        if self._fails(storage_id, remote_path):
            raise MtpError(self.error_message or f"cannot read: {remote_path}")
        data = self._node(storage_id, remote_path)
        if not isinstance(data, (bytes, bytearray)):
            raise MtpError(f"not a file: {remote_path}")
        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes(data))
        return len(data)


def fake_mtp_client_factory(storages: Any, tree: Dict[str, Any], **kwargs: Any):
    """Return a ``client_factory`` plus the created clients (for assertions)."""

    def factory(device_id: str):
        client = FakeMtpClient(storages, tree, **kwargs)
        factory.clients.append(client)
        return client

    factory.clients = []  # type: ignore[attr-defined]
    return factory


def dbi_saves_tree() -> Dict[str, Any]:
    """A Saves storage with two user saves plus Device/BCAT metadata dirs."""
    return {
        "saves": {
            "Installed games": {
                "0100000000010000 Super Mario Odyssey": {
                    "Alice": {"main": b"ALICE-SAVE"},
                    "Bob": {"main": b"BOB-SAVE"},
                    "Device": {"cfg": b"skip-device"},
                    "BCAT": {"data": b"skip-bcat"},
                }
            }
        }
    }


def mtp_device(
    device_id: str,
    friendly_name: str,
    storages: Any,
) -> MtpDevice:
    return MtpDevice(
        device_id=device_id,
        friendly_name=friendly_name,
        storages=tuple(_coerce_storage(item) for item in storages),
    )


@pytest.fixture
def psp_sfo_bytes() -> bytes:
    return build_sfo({
        "TITLE": "Monster Hunter Portable 3rd",
        "TITLE_ID": "ULJM05800",
        "CATEGORY": "MS",
        "SAVEDATA_DIRECTORY": "ULJM05800",
    })


@pytest.fixture
def vita_sfo_bytes() -> bytes:
    return build_sfo({
        "TITLE": "Persona 4 Golden",
        "TITLE_ID": "PCSE00120",
        "CATEGORY": "gd",
    })
