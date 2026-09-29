"""Windows WPD/MTP portable-device enumeration (no-op elsewhere).

The real transport uses the Windows Portable Device COM API through
``comtypes`` (never ``pywin32``).  All COM access is isolated in this module and
every import/operation is guarded, so importing the app on macOS/Linux never
touches WPD and ``list_mtp_devices()`` simply returns ``[]``.

The backend is injectable so tests can drive discovery without a real device.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, List, Optional

from .remote_mtp import MtpDevice, MtpEntry, MtpError, MtpNotFound, MtpStorage

# ``WPD_DEVICE_OBJECT_ID`` from the WPD API.
_WPD_DEVICE_OBJECT_ID = "DEVICE"

# Storage / object property GUIDs used to build the directory tree.
_WPD_OBJECT_NAME = "{EF6B490D-5CD8-437A-AFFC-DA8B60EE4A3C} 12"
_WPD_OBJECT_CONTENT_TYPE = "{EF6B490D-5CD8-437A-AFFC-DA8B60EE4A3C} 15"
_WPD_OBJECT_SIZE = "{EF6B490D-5CD8-437A-AFFC-DA8B60EE4A3C} 11"
_WPD_OBJECT_DATE_MODIFIED = "{EF6B490D-5CD8-437A-AFFC-DA8B60EE4A3C} 19"
_WPD_FUNCTIONAL_CATEGORY_STORAGE = "{08EA466B-E3A4-4336-A1F3-A44D2B5C9C4D}"
_WPD_RESOURCE_DEFAULT = "{E81E79BE-34F0-41BF-B53F-F1A06AE87842} 0"


def is_supported() -> bool:
    """True only on Windows, the sole platform with a WPD/MTP stack here."""
    return sys.platform == "win32"


def list_mtp_devices(backend: Optional[Any] = None) -> List[MtpDevice]:
    """Enumerate WPD/MTP portable devices.

    On any non-Windows platform this returns ``[]`` without raising.  A backend
    failure (WPD unavailable, COM error, a yanked cable) is swallowed and also
    yields ``[]`` so discovery can never crash the caller.
    """
    if sys.platform != "win32":
        return []
    if backend is None:
        backend = _WindowsMtpBackend()
    try:
        return list(backend.list_devices())
    except Exception:  # noqa: BLE001 - discovery must never raise
        return []


def open_mtp_client(device_id: str, backend: Optional[Any] = None):
    """Open a read-only client for ``device_id`` via the Windows WPD backend."""
    if sys.platform != "win32":
        raise MtpError("MTP 仅在 Windows 上可用")
    if backend is None:
        backend = _WindowsMtpBackend()
    return backend.open(device_id)


class _WindowsMtpBackend:  # pragma: no cover - requires real WPD/COM
    """Thin wrapper around the Portable Device COM API (comtypes)."""

    _manager = None
    _module = None

    def _com(self):
        if self._manager is not None:
            return self._module, self._manager
        import comtypes.client  # local import: absent/irrelevant off Windows

        module = comtypes.client.GetModule("portabledeviceapi.dll")
        manager = comtypes.client.CreateObject(
            module.PortableDeviceManager,
            interface=module.IPortableDeviceManager,
        )
        self._module = module
        self._manager = manager
        return module, manager

    def list_devices(self) -> List[MtpDevice]:
        import ctypes

        module, manager = self._com()
        count = ctypes.c_ulong(0)
        manager.GetDevices(None, ctypes.byref(count))
        if count.value == 0:
            return []
        device_ids = (ctypes.c_wchar_p * count.value)()
        manager.GetDevices(device_ids, ctypes.byref(count))
        devices: List[MtpDevice] = []
        for index in range(count.value):
            device_id = device_ids[index]
            if not device_id:
                continue
            friendly = self._friendly_name(manager, device_id)
            storages = self._storages(device_id)
            devices.append(
                MtpDevice(
                    device_id=str(device_id),
                    friendly_name=friendly,
                    storages=tuple(storages),
                )
            )
        return devices

    def _friendly_name(self, manager, device_id: str) -> str:
        import ctypes

        length = ctypes.c_ulong(0)
        manager.GetDeviceFriendlyName(device_id, None, ctypes.byref(length))
        if length.value == 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length.value)
        manager.GetDeviceFriendlyName(device_id, buffer, ctypes.byref(length))
        return buffer.value or ""

    def open(self, device_id: str):
        return _WindowsMtpClient(self, device_id)

    def _content(self, device) -> Any:
        return device.Content()

    def _storages(self, device_id: str) -> List[MtpStorage]:
        device = self._open_device(device_id)
        if device is None:
            return []
        try:
            content = device.Content()
            resources = content.Properties()
            storages: List[MtpStorage] = []
            for object_id in self._iter_objects(content, _WPD_DEVICE_OBJECT_ID):
                name = self._object_name(resources, object_id)
                if not name:
                    continue
                storages.append(
                    MtpStorage(storage_id=object_id, name=name, description="")
                )
            return storages
        except Exception:  # noqa: BLE001
            return []
        finally:
            self._release(device)

    def _open_device(self, device_id: str):
        module, _manager = self._com()
        try:
            device = module.PortableDevice()
            client_info = module.PortableDevice
            device.Open(device_id, client_info)
            return device
        except Exception:  # noqa: BLE001
            return None

    def _release(self, device) -> None:
        try:
            device.Close()
        except Exception:  # noqa: BLE001
            pass

    def _iter_objects(self, content, parent_id: str):
        import ctypes

        enumerator = content.EnumObjects(0, parent_id, None)
        while True:
            count = ctypes.c_ulong(1)
            ids = (ctypes.c_wchar_p * 1)()
            fetched = enumerator.Next(1, ids, ctypes.byref(count))
            if not fetched or count.value == 0:
                break
            if ids[0]:
                yield str(ids[0])

    def _object_name(self, properties, object_id: str) -> str:
        try:
            value = properties.GetStringValue(object_id, _WPD_OBJECT_NAME)
        except Exception:  # noqa: BLE001
            return ""
        return str(value or "")

    def _object_properties(self, properties, object_id: str) -> dict:
        out = {"name": "", "is_dir": True, "size": 0, "modified": ""}
        try:
            out["name"] = str(
                properties.GetStringValue(object_id, _WPD_OBJECT_NAME) or ""
            )
        except Exception:  # noqa: BLE001
            pass
        try:
            out["size"] = int(properties.GetUnsignedLargeIntegerValue(object_id, _WPD_OBJECT_SIZE))
        except Exception:  # noqa: BLE001
            out["size"] = 0
        try:
            out["modified"] = str(
                properties.GetStringValue(object_id, _WPD_OBJECT_DATE_MODIFIED) or ""
            )
        except Exception:  # noqa: BLE001
            out["modified"] = ""
        try:
            content_type = str(
                properties.GetStringValue(object_id, _WPD_OBJECT_CONTENT_TYPE) or ""
            )
            out["is_dir"] = "FOLDER" in content_type.upper() or "FUNCTIONAL" in content_type.upper()
        except Exception:  # noqa: BLE001
            pass
        return out


class _WindowsMtpClient:  # pragma: no cover - requires real WPD/COM
    """Read-only MTP client over the WPD COM API."""

    def __init__(self, backend: _WindowsMtpBackend, device_id: str) -> None:
        self._backend = backend
        self._device_id = device_id
        self._device = None
        self._content = None
        self._properties = None

    def connect(self) -> "_WindowsMtpClient":
        module, _manager = self._backend._com()
        device = module.PortableDevice()
        device.Open(self._device_id, module.PortableDevice)
        self._device = device
        self._content = device.Content()
        self._properties = self._content.Properties()
        return self

    def close(self) -> None:
        if self._device is not None:
            self._backend._release(self._device)
            self._device = None

    def list_storages(self) -> List[MtpStorage]:
        storages: List[MtpStorage] = []
        for object_id in self._backend._iter_objects(self._content, _WPD_DEVICE_OBJECT_ID):
            name = self._backend._object_name(self._properties, object_id)
            if name:
                storages.append(MtpStorage(storage_id=object_id, name=name))
        return storages

    def _storage_object_id(self, storage_id: str) -> str:
        return storage_id

    def list_dir(self, storage_id: str, remote_path: str) -> List[MtpEntry]:
        parent = self._resolve(storage_id, remote_path)
        if parent is None:
            raise MtpNotFound(f"no such object: {remote_path}")
        entries: List[MtpEntry] = []
        for object_id in self._backend._iter_objects(self._content, parent):
            props = self._backend._object_properties(self._properties, object_id)
            name = props.get("name") or ""
            if not name:
                continue
            entries.append(
                MtpEntry(
                    name=str(name),
                    is_dir=bool(props.get("is_dir", True)),
                    size=int(props.get("size", 0) or 0),
                    modified=str(props.get("modified", "") or ""),
                )
            )
        return entries

    def _resolve(self, storage_id: str, remote_path: str) -> Optional[str]:
        parts = [part for part in str(remote_path or "/").split("/") if part]
        current = self._storage_object_id(storage_id)
        for part in parts:
            found = None
            for object_id in self._backend._iter_objects(self._content, current):
                name = self._backend._object_name(self._properties, object_id)
                if name == part:
                    found = object_id
                    break
            if found is None:
                return None
            current = found
        return current

    def download(self, storage_id: str, remote_path: str, local_path: Path) -> int:
        object_id = self._resolve(storage_id, remote_path)
        if object_id is None:
            raise MtpNotFound(f"no such object: {remote_path}")
        target = Path(local_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        try:
            stream = self._content.Transfer().Download(
                self._device_id, object_id, 0, 0, _WPD_RESOURCE_DEFAULT
            )
            import ctypes

            chunk = ctypes.create_string_buffer(262144)
            written = ctypes.c_ulong(0)
            total = 0
            with open(part, "wb") as handle:
                while True:
                    stream.Read(chunk, len(chunk), ctypes.byref(written))
                    if written.value == 0:
                        break
                    handle.write(chunk.raw[: written.value])
                    total += int(written.value)
            import os

            os.replace(part, target)
            return total
        except Exception as exc:  # noqa: BLE001
            try:
                if part.exists():
                    part.unlink()
            except OSError:
                pass
            raise MtpError(f"下载失败 {remote_path}: {exc}") from exc


__all__ = ["is_supported", "list_mtp_devices", "open_mtp_client"]
