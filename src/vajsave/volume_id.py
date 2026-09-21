"""Filesystem volume serial → stable device id, or ``None`` if unavailable.

The id names the *card filesystem*, not the USB reader. Adapter VID/PID,
volume labels, and drive letters are never used as the key.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional


def format_windows_serial(serial_dword: int) -> str:
    """Format a Win32 volume serial DWORD as ``win:{8 uppercase hex}``."""
    return f"win:{int(serial_dword) & 0xFFFFFFFF:08X}"


def volume_id_for(mount: Path, extra: dict | None = None) -> str | None:
    """Return a stable volume id for ``mount``, or ``None`` if none is known.

    ``extra["volume_id"]`` (from Windows enumeration) wins. USB adapter fields
    in ``extra`` are ignored. Native probes run only on real mount points so a
    random directory does not inherit the host disk's UUID.
    """
    if extra:
        stored = extra.get("volume_id")
        if isinstance(stored, str):
            text = stored.strip()
            if text:
                return text
    try:
        path = Path(mount)
    except TypeError:
        return None
    if not _is_mount_point(path):
        return None
    if sys.platform == "win32":
        return _windows_volume_id(path)
    if sys.platform == "darwin":
        return _macos_volume_id(path)
    if sys.platform.startswith("linux"):
        return _linux_volume_id(path)
    return None


def _is_mount_point(path: Path) -> bool:
    try:
        return os.path.ismount(path)
    except OSError:
        return False


def _windows_volume_id(mount: Path) -> Optional[str]:
    try:
        import ctypes
    except Exception:
        return None
    try:
        root = str(mount)
        if len(root) == 2 and root[1] == ":":
            root = root + "\\"
        kernel32 = ctypes.windll.kernel32
        serial = ctypes.c_uint32(0)
        kernel32.GetVolumeInformationW(
            root, None, 0, ctypes.byref(serial), None, None, None, 0
        )
        return format_windows_serial(int(serial.value))
    except Exception:
        return None


def _macos_volume_id(mount: Path) -> Optional[str]:
    """Read ``ATTR_VOL_UUID`` via ``getattrlist``; any failure → ``None``."""
    try:
        import ctypes
        import ctypes.util
    except Exception:
        return None

    attr_bit_map_count = 5
    attr_vol_info = 0x80000000
    attr_vol_uuid = 0x00040000

    class _AttrList(ctypes.Structure):
        _fields_ = [
            ("bitmapcount", ctypes.c_ushort),
            ("reserved", ctypes.c_ushort),
            ("commonattr", ctypes.c_uint),
            ("volattr", ctypes.c_uint),
            ("dirattr", ctypes.c_uint),
            ("fileattr", ctypes.c_uint),
            ("forkattr", ctypes.c_uint),
        ]

    try:
        libc_name = ctypes.util.find_library("c")
        if not libc_name:
            return None
        libc = ctypes.CDLL(libc_name, use_errno=True)
        getattrlist = libc.getattrlist
        getattrlist.argtypes = [
            ctypes.c_char_p,
            ctypes.POINTER(_AttrList),
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        getattrlist.restype = ctypes.c_int

        request = _AttrList()
        request.bitmapcount = attr_bit_map_count
        request.volattr = attr_vol_info | attr_vol_uuid
        buf = ctypes.create_string_buffer(64)
        rc = getattrlist(
            os.fsencode(str(mount)),
            ctypes.byref(request),
            buf,
            len(buf),
            0,
        )
        if rc != 0:
            return None
        length = int.from_bytes(buf.raw[:4], "little")
        if length < 20:
            return None
        parsed = uuid.UUID(bytes=buf.raw[4:20])
        return f"mac:{parsed}"
    except Exception:
        return None


def _linux_volume_id(mount: Path) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["findmnt", "-n", "-o", "UUID", "--target", str(mount)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    except Exception:
        return None
    text = (proc.stdout or "").strip()
    if not text or text.upper() in {"", "UNKNOWN"}:
        return None
    # findmnt may print a blank UUID as a lone empty field.
    first = text.splitlines()[0].strip()
    if not first:
        return None
    return f"linux:{first}"


__all__ = ["format_windows_serial", "volume_id_for"]
