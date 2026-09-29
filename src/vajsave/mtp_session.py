"""MTP discovery, VolumeInfo mapping, pull, and AppState injection."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from . import mtp_windows
from .mtp_fetch import MtpPullResult, device_cache_dir, pull_device
from .models import VolumeInfo
from .platforms.common import ScanProgress
from .remote_mtp import MtpDevice, is_switch_mtp_device

if TYPE_CHECKING:
    from .app_state import AppState

_SWITCH_LIKE_NAME_RE = re.compile(r"switch|nintendo", re.IGNORECASE)


def volume_name_for_device(device: MtpDevice) -> str:
    """A stable, human-friendly device row name (e.g. ``Switch · DBI``)."""
    friendly = (device.friendly_name or "").strip() or "DBI MTP"
    if _SWITCH_LIKE_NAME_RE.search(friendly):
        return friendly
    return f"Switch · {friendly}"


def volume_for_device(device: MtpDevice, library_root) -> VolumeInfo:
    """Expose one discovered Switch MTP device as a removable device row.

    ``mount_point`` is the local cache directory the saves are pulled into; the
    device itself is never mounted as a pathlib drive.  ``extra`` carries the
    stable ``mtp_id`` so ``device_key_for`` can bind the cache relatively.
    """
    return VolumeInfo(
        name=volume_name_for_device(device),
        mount_point=device_cache_dir(library_root, device.device_id),
        is_removable=True,
        extra={
            "mtp": True,
            "mtp_id": device.device_id,
            "platform": "switch",
            "label": device.friendly_name or "",
        },
    )


class MtpSession:
    """Discovery + pull integration for DBI MTP devices."""

    def __init__(self, app: "AppState") -> None:
        self.app = app

    def _lister(self):
        return self.app._mtp_device_lister or mtp_windows.list_mtp_devices

    def devices(self) -> List[MtpDevice]:
        """Discovered devices filtered to DBI/Checkpoint Switch units."""
        try:
            raw = self._lister()() or []
        except Exception:  # noqa: BLE001 - discovery must never raise
            return []
        out: List[MtpDevice] = []
        for device in raw:
            try:
                if is_switch_mtp_device(device):
                    out.append(device)
            except Exception:  # noqa: BLE001
                continue
        return out

    def list_volumes(self) -> List[VolumeInfo]:
        """MTP devices as :class:`VolumeInfo` rows (empty off Windows)."""
        library_root = self.app.library_root
        return [volume_for_device(device, library_root) for device in self.devices()]

    def _device_id_for_mount(self, mount: Path) -> Optional[str]:
        for volume in self.app.volumes:
            try:
                if Path(volume.mount_point) == mount and (volume.extra or {}).get("mtp"):
                    mtp_id = (volume.extra or {}).get("mtp_id")
                    if mtp_id:
                        return str(mtp_id)
            except OSError:
                continue
        for device in self.devices():
            if device_cache_dir(self.app.library_root, device.device_id) == mount:
                return device.device_id
        return None

    def _progress(self, message: str) -> None:
        self.app.report_scan_progress(ScanProgress(message=message))

    def pull_mount(self, mount, token: object = None) -> MtpPullResult:
        """Incrementally pull the device behind ``mount`` into its cache.

        Failures (device unplugged, COM error, cancel) are returned as a failed
        result; they never raise into the scanning/UI thread.
        """
        path = Path(mount)
        device_id = self._device_id_for_mount(path)
        if not device_id:
            return MtpPullResult(ok=False, device_id="", error="未找到 MTP 设备")
        factory = self.app._mtp_client_factory or mtp_windows.open_mtp_client
        try:
            client = factory(device_id)
        except Exception as exc:  # noqa: BLE001
            return MtpPullResult(ok=False, device_id=device_id, error=str(exc) or exc.__class__.__name__)
        return pull_device(
            client, path, device_id=device_id, token=token, progress=self._progress
        )


__all__ = ["MtpSession", "volume_for_device", "volume_name_for_device"]
