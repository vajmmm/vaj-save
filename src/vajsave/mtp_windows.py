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

# Storage / object property GUIDs and PIDs used to build the directory tree.
_WPD_STORAGE_FMTID = "{EF6B490D-5CD8-437A-AFFC-DA8B60EE4A3C}"
_WPD_RESOURCE_FMTID = "{E81E79BE-34F0-41BF-B53F-F1A06AE87842}"
_WPD_CLIENT_INFO_FMTID = "{204D9F0C-2292-4080-9F42-40664E70F859}"

_WPD_CONTENT_TYPE_FOLDER = "{27E2E392-A111-48E0-AB0C-E17705A05F85}"
_WPD_CONTENT_TYPE_FUNCTIONAL_OBJECT = "{99ED0160-17FF-4C44-9D98-1D7A6F941921}"


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
    _types_module = None
    _k_name = None
    _k_orig_name = None
    _k_content_type = None
    _k_size = None
    _k_modified = None
    _k_resource_default = None

    def _com(self):
        if self._manager is not None:
            return self._module, self._manager
        import comtypes.client  # local import: absent/irrelevant off Windows

        module = comtypes.client.GetModule("portabledeviceapi.dll")
        types_module = comtypes.client.GetModule("PortableDeviceTypes.dll")
        manager = comtypes.client.CreateObject(
            module.PortableDeviceManager,
            interface=module.IPortableDeviceManager,
        )
        self._module = module
        self._types_module = types_module
        self._manager = manager
        return module, manager

    def _types(self):
        if self._types_module is None:
            self._com()
        return self._types_module

    def _key_name(self):
        if self._k_name is None:
            import comtypes

            module, _ = self._com()
            fmt = comtypes.GUID(_WPD_STORAGE_FMTID)
            self._k_name = module._tagpropertykey(fmt, 4)
        return self._k_name

    def _key_orig_name(self):
        if self._k_orig_name is None:
            import comtypes

            module, _ = self._com()
            fmt = comtypes.GUID(_WPD_STORAGE_FMTID)
            self._k_orig_name = module._tagpropertykey(fmt, 12)
        return self._k_orig_name

    def _key_content_type(self):
        if self._k_content_type is None:
            import comtypes

            module, _ = self._com()
            fmt = comtypes.GUID(_WPD_STORAGE_FMTID)
            self._k_content_type = module._tagpropertykey(fmt, 7)
        return self._k_content_type

    def _key_size(self):
        if self._k_size is None:
            import comtypes

            module, _ = self._com()
            fmt = comtypes.GUID(_WPD_STORAGE_FMTID)
            self._k_size = module._tagpropertykey(fmt, 11)
        return self._k_size

    def _key_modified(self):
        if self._k_modified is None:
            import comtypes

            module, _ = self._com()
            fmt = comtypes.GUID(_WPD_STORAGE_FMTID)
            self._k_modified = module._tagpropertykey(fmt, 19)
        return self._k_modified

    def _key_resource_default(self):
        if self._k_resource_default is None:
            import comtypes

            module, _ = self._com()
            fmt = comtypes.GUID(_WPD_RESOURCE_FMTID)
            self._k_resource_default = module._tagpropertykey(fmt, 0)
        return self._k_resource_default

    def list_devices(self) -> List[MtpDevice]:
        import ctypes

        module, manager = self._com()
        count = ctypes.c_ulong(0)
        try:
            manager._IPortableDeviceManager__com_GetDevices(None, ctypes.byref(count))
        except Exception:
            return []
        if count.value == 0:
            return []
        device_ids = (ctypes.c_wchar_p * count.value)()
        try:
            manager._IPortableDeviceManager__com_GetDevices(device_ids, ctypes.byref(count))
        except Exception:
            return []
        devices: List[MtpDevice] = []
        for index in range(count.value):
            device_id = device_ids[index]
            if not device_id:
                continue
            friendly = self._friendly_name(manager, device_id)
            if not friendly:
                friendly = self._description(manager, device_id)
            if not friendly and "057e" in str(device_id).lower():
                friendly = "Switch"
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
        try:
            manager._IPortableDeviceManager__com_GetDeviceFriendlyName(
                device_id, None, ctypes.byref(length)
            )
        except Exception:
            return ""
        if length.value == 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length.value)
        p_buf = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ushort))
        try:
            manager._IPortableDeviceManager__com_GetDeviceFriendlyName(
                device_id, p_buf, ctypes.byref(length)
            )
        except Exception:
            return ""
        return buffer.value or ""

    def _description(self, manager, device_id: str) -> str:
        import ctypes

        length = ctypes.c_ulong(0)
        try:
            manager._IPortableDeviceManager__com_GetDeviceDescription(
                device_id, None, ctypes.byref(length)
            )
        except Exception:
            return ""
        if length.value == 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length.value)
        p_buf = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ushort))
        try:
            manager._IPortableDeviceManager__com_GetDeviceDescription(
                device_id, p_buf, ctypes.byref(length)
            )
        except Exception:
            return ""
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
            properties = content.Properties()
            storages: List[MtpStorage] = []
            for object_id in self._iter_objects(content, _WPD_DEVICE_OBJECT_ID):
                name = self._object_name(properties, object_id)
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
        import ctypes
        import comtypes
        import comtypes.client

        module, _manager = self._com()
        types_module = self._types()
        try:
            client_info = comtypes.client.CreateObject(
                types_module.PortableDeviceValues,
                interface=module.IPortableDeviceValues,
            )
            wpd_client_name = module._tagpropertykey(
                comtypes.GUID(_WPD_CLIENT_INFO_FMTID), 2
            )
            try:
                client_info.SetStringValue(ctypes.byref(wpd_client_name), "vajsave")
            except Exception:
                pass
            device_cls = getattr(module, "PortableDeviceFTM", module.PortableDevice)
            device = comtypes.client.CreateObject(
                device_cls,
                interface=module.IPortableDevice,
            )
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

        try:
            enumerator = content.EnumObjects(0, parent_id, None)
        except Exception:
            return
        while True:
            ids = (ctypes.c_wchar_p * 1)()
            count = ctypes.c_ulong(0)
            try:
                hr = enumerator._IEnumPortableDeviceObjectIDs__com_Next(
                    1, ids, ctypes.byref(count)
                )
            except Exception:
                break
            if hr != 0 or count.value == 0:
                break
            if ids[0]:
                yield str(ids[0])

    def _object_name(self, properties, object_id: str) -> str:
        import ctypes

        key_name = self._key_name()
        key_orig = self._key_orig_name()
        try:
            values = properties.GetValues(object_id, None)
        except Exception:
            return ""
        try:
            val = values.GetStringValue(ctypes.byref(key_name))
            if val:
                return str(val)
        except Exception:
            pass
        try:
            val = values.GetStringValue(ctypes.byref(key_orig))
            if val:
                return str(val)
        except Exception:
            pass
        return ""

    def _object_properties(self, properties, object_id: str) -> dict:
        import ctypes

        out = {"name": "", "is_dir": True, "size": 0, "modified": ""}
        try:
            values = properties.GetValues(object_id, None)
        except Exception:
            return out

        key_name = self._key_name()
        key_orig = self._key_orig_name()
        key_size = self._key_size()
        key_mod = self._key_modified()
        key_ctype = self._key_content_type()

        try:
            out["name"] = str(values.GetStringValue(ctypes.byref(key_name)) or "")
        except Exception:
            try:
                out["name"] = str(values.GetStringValue(ctypes.byref(key_orig)) or "")
            except Exception:
                pass
        try:
            out["size"] = int(values.GetUnsignedLargeIntegerValue(ctypes.byref(key_size)))
        except Exception:
            out["size"] = 0
        try:
            out["modified"] = str(values.GetStringValue(ctypes.byref(key_mod)) or "")
        except Exception:
            out["modified"] = ""
        try:
            g = str(values.GetGuidValue(ctypes.byref(key_ctype))).upper()
            out["is_dir"] = (
                g == _WPD_CONTENT_TYPE_FOLDER or g == _WPD_CONTENT_TYPE_FUNCTIONAL_OBJECT
            )
        except Exception:
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
        self._resources = None

    def connect(self) -> "_WindowsMtpClient":
        device = self._backend._open_device(self._device_id)
        if device is None:
            raise MtpError(f"无法连接到 MTP 设备: {self._device_id}")
        self._device = device
        self._content = device.Content()
        self._properties = self._content.Properties()
        self._resources = self._content.Transfer()
        return self

    def close(self) -> None:
        if self._device is not None:
            self._backend._release(self._device)
            self._device = None
            self._content = None
            self._properties = None
            self._resources = None

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
        parts = [part for part in str(remote_path or "/").replace("\\", "/").split("/") if part]
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
        import ctypes
        from comtypes import POINTER
        from comtypes.gen.PortableDeviceApiLib import IStream

        object_id = self._resolve(storage_id, remote_path)
        if object_id is None:
            raise MtpNotFound(f"no such object: {remote_path}")
        target = Path(local_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")

        key_default = self._backend._key_resource_default()
        buf_size = ctypes.c_ulong(0)
        p_stream = POINTER(IStream)()
        try:
            hr = self._resources._IPortableDeviceResources__com_GetStream(
                object_id,
                ctypes.byref(key_default),
                0,  # STGM_READ
                ctypes.byref(buf_size),
                ctypes.byref(p_stream),
            )
            if hr != 0 or not p_stream:
                raise MtpError(f"GetStream 失败 (HRESULT: {hr:#x})")

            chunk_len = max(buf_size.value, 65536)
            chunk = (ctypes.c_ubyte * chunk_len)()
            read_bytes = ctypes.c_ulong(0)
            total = 0
            with open(part, "wb") as handle:
                while True:
                    hr = p_stream._ISequentialStream__com_RemoteRead(
                        chunk, chunk_len, ctypes.byref(read_bytes)
                    )
                    if read_bytes.value == 0:
                        break
                    handle.write(bytes(chunk[: read_bytes.value]))
                    total += int(read_bytes.value)
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
