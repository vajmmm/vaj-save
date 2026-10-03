"""Read-only FTP access used to pull handheld saves over the network.

The client deliberately exposes only listing and download operations: the app
never writes to a handheld, so every command it can send is read-only.
Passwords are stripped from ``repr``/error text, so a failed connection can be
surfaced in the UI without leaking credentials into logs or status lines.

Two presets ship by default:

* ``checkpoint`` -- the default; a Checkpoint-style server (its root already
  points at the save exports).
* ``ftpd`` -- a generic ftpd server, kept as a switchable fallback.
"""

from __future__ import annotations

import ftplib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

DEFAULT_FTP_PORT = 5000
DEFAULT_FTP_TIMEOUT = 20.0

CHECKPOINT_PRESET_KEY = "checkpoint"
VITA_PRESET_KEY = "vita"
SWITCH_PRESET_KEY = "switch"
THREEDS_PRESET_KEY = "3ds"
PSP_PRESET_KEY = "psp"
NDS_PRESET_KEY = "nds"
PS3_PRESET_KEY = "ps3"
PS4_PRESET_KEY = "ps4"
WIIU_PRESET_KEY = "wiiu"
WII_PRESET_KEY = "wii"
X360_PRESET_KEY = "x360"
FTPD_PRESET_KEY = "ftpd"
DEFAULT_PRESET_KEY = CHECKPOINT_PRESET_KEY

# Filesystem-mutating FTP verbs. ``ensure_read_only`` rejects these so this
# client can never be tricked into writing to (or deleting from) a console.
MUTATING_COMMANDS = frozenset(
    {
        "STOR",
        "STOU",
        "APPE",
        "DELE",
        "RMD",
        "XRMD",
        "MKD",
        "XMKD",
        "RNFR",
        "RNTO",
        "SITE",
        "MFMT",
        "ALLO",
    }
)


class RemoteFtpError(Exception):
    """A remote FTP operation failed (connection, login, listing, download)."""


def ensure_read_only(command: str) -> None:
    """Raise :class:`RemoteFtpError` if ``command`` would mutate the server."""
    verb = str(command or "").strip().split(" ", 1)[0].upper()
    if verb in MUTATING_COMMANDS:
        raise RemoteFtpError(f"只读 FTP：拒绝写操作 {verb}")


def sanitize_component(name: object) -> Optional[str]:
    """Return a safe single path component, or ``None`` when unsafe.

    Rejects separators, NUL bytes, empty names and the ``.``/``..`` entries so a
    hostile server cannot redirect a download outside the local cache.
    """
    text = str(name if name is not None else "").strip()
    if not text or text in (".", ".."):
        return None
    if "/" in text or "\\" in text or "\x00" in text:
        return None
    # Strip trailing colons on drive labels like 'ux0:', 'ms0:', 'fat:'
    # which are invalid on Windows NTFS filesystems.
    if text.endswith(":") and len(text) > 1 and ":" not in text[:-1]:
        text = text[:-1]
    if ":" in text:
        return None
    return text


def is_safe_relative_path(path: object) -> bool:
    """True when ``path`` is a relative, traversal-free path made of safe parts."""
    text = str(path or "").strip()
    if not text or text.startswith("/") or text.startswith("\\"):
        return False
    parts = [part for part in text.replace("\\", "/").split("/") if part]
    if not parts:
        return False
    return all(sanitize_component(part) is not None for part in parts)


def join_remote(base: str, name: str) -> str:
    """Join a sanitized ``name`` onto a remote POSIX ``base`` path."""
    clean = sanitize_component(name)
    if clean is None:
        raise RemoteFtpError(f"不安全的远程文件名: {name!r}")
    base = (base or "/").rstrip("/")
    return f"{base}/{clean}" if base else f"/{clean}"


def redact(text: object, *secrets: str) -> str:
    """Return ``text`` with every non-empty secret replaced by ``***``."""
    result = str(text)
    for secret in secrets:
        if secret:
            result = result.replace(secret, "***")
    return result


@dataclass(frozen=True)
class FtpEntry:
    """One directory entry returned by a remote listing."""

    name: str
    is_dir: bool
    size: int = 0
    modified: str = ""


@dataclass(frozen=True)
class FtpProfile:
    """Connection + root settings for one FTP preset."""

    key: str
    label: str
    host: str = "127.0.0.1"
    port: int = DEFAULT_FTP_PORT
    user: str = "anonymous"
    password: str = ""
    path: str = "/"
    description: str = ""
    target_paths: Tuple[str, ...] = ()

    def redacted(self) -> str:
        """A password-free description safe for logs and status text."""
        return (
            f"FtpProfile({self.key}@{self.host}:{self.port} "
            f"user={self.user or 'anonymous'} path={self.path})"
        )

    def __repr__(self) -> str:  # never include the password
        return self.redacted()


CHECKPOINT_PRESET = FtpProfile(
    key=CHECKPOINT_PRESET_KEY,
    label="Checkpoint",
    port=5000,
    path="/",
    description="Checkpoint 存档服务器（默认预设）",
)

VITA_PRESET = FtpProfile(
    key=VITA_PRESET_KEY,
    label="PS Vita",
    port=1337,
    path="/",
    description="PS Vita (VitaShell FTP · 默认端口 1337)",
    target_paths=(
        "ux0:/user/00/savedata",
        "ux0:/pspemu/PSP/SAVEDATA",
        "ux0:/data/savegames",
        "ux0/user/00/savedata",
        "ux0/pspemu/PSP/SAVEDATA",
        "ux0/data/savegames",
        "user/00/savedata",
    ),
)

SWITCH_PRESET = FtpProfile(
    key=SWITCH_PRESET_KEY,
    label="Nintendo Switch",
    port=5000,
    path="/",
    description="Nintendo Switch (sys-ftpd / JKSV · 默认端口 5000)",
    target_paths=(
        "JKSV",
        "switch/Checkpoint/saves",
        "atmosphere/saves",
    ),
)

THREEDS_PRESET = FtpProfile(
    key=THREEDS_PRESET_KEY,
    label="Nintendo 3DS",
    port=5000,
    path="/",
    description="Nintendo 3DS (FTPD · 默认端口 5000)",
    target_paths=(
        "3ds/Checkpoint/saves",
        "JKSV",
        "roms/nds/saves",
    ),
)

PSP_PRESET = FtpProfile(
    key=PSP_PRESET_KEY,
    label="PSP 实机",
    port=21,
    path="/",
    description="PSP 实机 (PSP-FTPD · 默认端口 21)",
    target_paths=(
        "PSP/SAVEDATA",
        "ms0:/PSP/SAVEDATA",
        "ms0/PSP/SAVEDATA",
    ),
)

NDS_PRESET = FtpProfile(
    key=NDS_PRESET_KEY,
    label="NDS 实机",
    port=21,
    path="/",
    description="NDS 实机 (ftpd-nds · 默认端口 21)",
    target_paths=(
        "saves",
        "save",
        "roms/nds/saves",
        "roms/nds",
        "_nds",
        "fat:/saves",
        "fat:/roms/nds/saves",
    ),
)

PS3_PRESET = FtpProfile(
    key=PS3_PRESET_KEY,
    label="PS3 实机",
    port=21,
    path="/",
    description="PlayStation 3 (webMAN MOD · 默认端口 21)",
    target_paths=(
        "dev_hdd0/home",
        "/dev_hdd0/home",
        "dev_usb000/PS3/SAVEDATA",
        "dev_usb001/PS3/SAVEDATA",
        "PS3/SAVEDATA",
    ),
)

PS4_PRESET = FtpProfile(
    key=PS4_PRESET_KEY,
    label="PS4 实机",
    port=2121,
    path="/",
    description="PlayStation 4 (GoldHEN FTP · 默认端口 2121)",
    target_paths=(
        "user/home",
        "/user/home",
        "data/apollo",
        "/data/apollo",
        "PS4/SAVEDATA",
    ),
)

WIIU_PRESET = FtpProfile(
    key=WIIU_PRESET_KEY,
    label="Wii U 实机",
    port=21,
    path="/",
    description="Wii U 实机 (FTPiiU Everywhere · 默认端口 21)",
    target_paths=(
        "storage_mlc/usr/save/00050000",
        "/storage_mlc/usr/save/00050000",
        "storage_usb/usr/save/00050000",
        "/storage_usb/usr/save/00050000",
        "usr/save/00050000",
        "wiiu/backups",
        "/wiiu/backups",
    ),
)

WII_PRESET = FtpProfile(
    key=WII_PRESET_KEY,
    label="Wii 实机",
    port=21,
    path="/",
    description="Wii 实机 (ftpii · 默认端口 21)",
    target_paths=(
        "savegames",
        "/savegames",
        "wiisaves",
        "title/00010000",
        "/title/00010000",
    ),
)

X360_PRESET = FtpProfile(
    key=X360_PRESET_KEY,
    label="Xbox 360 实机",
    port=21,
    path="/",
    description="Xbox 360 (Aurora / DashLaunch · 默认端口 21)",
    target_paths=(
        "Hdd1/Content",
        "Hdd1:/Content",
        "Content",
        "/Content",
        "Usb0/Content",
        "Usb0:/Content",
    ),
)

FTPD_PRESET = FtpProfile(
    key=FTPD_PRESET_KEY,
    label="通用 FTP",
    port=21,
    path="/",
    description="通用 FTP 服务器（回退预设）",
)

_PRESETS: Tuple[FtpProfile, ...] = (
    CHECKPOINT_PRESET,
    VITA_PRESET,
    SWITCH_PRESET,
    THREEDS_PRESET,
    PSP_PRESET,
    NDS_PRESET,
    PS3_PRESET,
    PS4_PRESET,
    WIIU_PRESET,
    WII_PRESET,
    X360_PRESET,
    FTPD_PRESET,
)


def presets() -> Tuple[FtpProfile, ...]:
    """All built-in presets, default first."""
    return _PRESETS


def preset_keys() -> Tuple[str, ...]:
    return tuple(preset.key for preset in _PRESETS)


def get_preset(key: object) -> FtpProfile:
    """Resolve a preset by key, falling back to the default on an unknown key."""
    text = str(key or "").strip().lower()
    for preset in _PRESETS:
        if preset.key == text:
            return preset
    return CHECKPOINT_PRESET


def default_preset() -> FtpProfile:
    return CHECKPOINT_PRESET


class RemoteFtpClient:
    """A thin, read-only wrapper around ``ftplib.FTP``.

    ``ftp_factory`` is injectable so the transport can be driven without a
    socket in tests.  Only listing and download operations are exposed; every
    command issued is read-only.
    """

    def __init__(
        self,
        profile: FtpProfile,
        ftp_factory: Callable[[], object] = ftplib.FTP,
        timeout: float = DEFAULT_FTP_TIMEOUT,
        read_only: bool = True,
    ) -> None:
        self.profile = profile
        self._ftp_factory = ftp_factory
        self._timeout = timeout
        self._read_only = read_only
        self._ftp = None

    def __enter__(self) -> "RemoteFtpClient":
        return self.connect()

    def __exit__(self, *_exc: object) -> bool:
        self.close()
        return False

    def connect(self) -> "RemoteFtpClient":
        try:
            ftp = self._ftp_factory()
            ftp.connect(self.profile.host, self.profile.port, timeout=self._timeout)
            ftp.login(self.profile.user, self.profile.password)
            try:
                ftp.set_pasv(True)
            except Exception:  # noqa: BLE001 - passive mode is best-effort
                pass
        except Exception as exc:  # noqa: BLE001 - normalise to RemoteFtpError
            self.close()
            raise RemoteFtpError(
                redact(
                    f"FTP 连接失败 ({self.profile.host}:{self.profile.port}): {exc}",
                    self.profile.password,
                )
            ) from exc
        self._ftp = ftp
        if self._read_only:
            self._install_read_only_guard(ftp)
        return self

    def _install_read_only_guard(self, ftp) -> None:
        """Wrap the low-level ``putcmd`` so no mutating verb can ever be sent.

        The client only calls read-only ``ftplib`` helpers, but this makes the
        guarantee structural: an accidental upload/delete still fails closed.
        """
        original = getattr(ftp, "putcmd", None)
        if original is None:
            return

        def guarded(command, *args):
            ensure_read_only(str(command))
            return original(command, *args)

        try:
            ftp.putcmd = guarded  # type: ignore[assignment]
        except Exception:  # noqa: BLE001 - guard is best-effort, never fatal
            pass

    def close(self) -> None:
        ftp = self._ftp
        self._ftp = None
        if ftp is None:
            return
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001 - fall back to closing the socket
            try:
                ftp.close()
            except Exception:  # noqa: BLE001
                pass

    def _require(self):
        if self._ftp is None:
            raise RemoteFtpError("FTP 未连接")
        return self._ftp

    def list_dir(self, remote_path: str) -> List[FtpEntry]:
        """List one remote directory (MLSD first, standard Unix LIST second, NLST fallback)."""
        ftp = self._require()
        # 1. RFC 3659 MLSD (mtheall/ftpd on Switch, 3DS, NDS)
        try:
            return self._list_mlsd(ftp, remote_path)
        except Exception:
            pass

        # 2. Standard Unix LIST format (VitaShell, PSP-FTPD, legacy daemons)
        try:
            return self._list_unix_list(ftp, remote_path)
        except Exception:
            pass

        # 3. Fallback: NLST with probe
        try:
            return self._list_nlst(ftp, remote_path)
        except Exception as exc:  # noqa: BLE001
            raise RemoteFtpError(
                redact(f"列出远程目录失败 {remote_path}: {exc}", self.profile.password)
            ) from exc

    def _list_unix_list(self, ftp, remote_path: str) -> List[FtpEntry]:
        raw_lines: List[str] = []
        try:
            ftp.dir(remote_path, raw_lines.append)
        except Exception as exc:
            raise RemoteFtpError(f"LIST failed: {exc}") from exc

        entries: List[FtpEntry] = []
        for line in raw_lines:
            line = line.strip()
            if not line or line.startswith("total"):
                continue
            parts = line.split(None, 8)
            if len(parts) < 9:
                continue
            perms = parts[0]
            if not perms or perms[0] not in ("d", "-", "l"):
                continue
            is_dir = perms[0] == "d"
            try:
                size = int(parts[4])
            except (ValueError, IndexError):
                size = 0
            name = parts[8].strip()
            if perms[0] == "l" and " -> " in name:
                name = name.split(" -> ", 1)[0].strip()
            if not name or name in (".", ".."):
                continue
            entries.append(
                FtpEntry(
                    name=name,
                    is_dir=is_dir,
                    size=size,
                    modified=f"{parts[5]} {parts[6]} {parts[7]}",
                )
            )
        entries.sort(key=lambda entry: entry.name)
        if not entries and raw_lines:
            raise RemoteFtpError("Unrecognized LIST format")
        return entries

    def _list_mlsd(self, ftp, remote_path: str) -> List[FtpEntry]:
        entries: List[FtpEntry] = []
        for name, facts in ftp.mlsd(remote_path):
            if not name or name in (".", ".."):
                continue
            facts = facts or {}
            try:
                size = int(facts.get("size", 0) or 0)
            except (TypeError, ValueError):
                size = 0
            entries.append(
                FtpEntry(
                    name=str(name),
                    is_dir=(str(facts.get("type", "")) == "dir"),
                    size=size,
                    modified=str(facts.get("modify", "")),
                )
            )
        entries.sort(key=lambda entry: entry.name)
        return entries

    def _list_nlst(self, ftp, remote_path: str) -> List[FtpEntry]:
        entries: List[FtpEntry] = []
        for full in ftp.nlst(remote_path):
            base = str(full).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            if not base or base in (".", ".."):
                continue
            entries.append(
                FtpEntry(
                    name=base,
                    is_dir=self._probe_dir(ftp, join_remote(remote_path, base)),
                )
            )
        entries.sort(key=lambda entry: entry.name)
        return entries

    def _probe_dir(self, ftp, path: str) -> bool:
        try:
            previous = ftp.pwd()
        except Exception:  # noqa: BLE001
            previous = "/"
        try:
            ftp.cwd(path)
        except Exception:  # noqa: BLE001 - not a directory (or forbidden)
            return False
        try:
            ftp.cwd(previous)
        except Exception:  # noqa: BLE001
            pass
        return True

    def download(self, remote_path: str, local_path: Path) -> int:
        """Download ``remote_path`` to ``local_path``; return the byte count.

        The write goes to a ``.part`` sibling first and is renamed only once it
        completed, so a dropped connection cannot leave a truncated file behind.
        """
        ftp = self._require()
        target = Path(local_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        try:
            with open(part, "wb") as handle:
                ftp.retrbinary(f"RETR {remote_path}", handle.write, blocksize=65536)
            size = part.stat().st_size
            os.replace(part, target)
            return size
        except Exception as exc:  # noqa: BLE001 - normalise to RemoteFtpError
            try:
                if part.exists():
                    part.unlink()
            except OSError:
                pass
            raise RemoteFtpError(
                redact(f"下载失败 {remote_path}: {exc}", self.profile.password)
            ) from exc

    def make_dir(self, remote_path: str) -> None:
        """Create remote directory and its parents if they don't exist."""
        ftp = self._require()
        parts = [p for p in remote_path.replace("\\", "/").split("/") if p]
        curr = "/" if remote_path.startswith("/") else ""
        for part in parts:
            curr = f"{curr.rstrip('/')}/{part}"
            try:
                ftp.mkd(curr)
            except Exception:
                pass

    def upload(self, local_path: Path, remote_path: str) -> int:
        """Upload local file to remote_path; return the byte count."""
        ftp = self._require()
        source = Path(local_path)
        if not source.is_file():
            raise RemoteFtpError(f"本地文件不存在: {local_path}")
        size = source.stat().st_size
        try:
            with open(source, "rb") as handle:
                ftp.storbinary(f"STOR {remote_path}", handle, blocksize=65536)
            return size
        except Exception as exc:  # noqa: BLE001 - normalise to RemoteFtpError
            raise RemoteFtpError(
                redact(f"上传失败 {remote_path}: {exc}", self.profile.password)
            ) from exc

    def upload_dir(
        self,
        local_dir: Path,
        remote_dir: str,
        token: object = None,
    ) -> int:
        """Recursively upload all files from local_dir to remote_dir."""
        local_dir = Path(local_dir)
        if not local_dir.is_dir():
            raise RemoteFtpError(f"本地目录不存在: {local_dir}")
        self.make_dir(remote_dir)
        total_bytes = 0
        for child in sorted(local_dir.iterdir()):
            if token is not None:
                cancelled = getattr(token, "cancelled", None)
                if callable(cancelled) and cancelled():
                    break
            remote_child = f"{remote_dir.rstrip('/')}/{child.name}"
            if child.is_dir():
                total_bytes += self.upload_dir(child, remote_child, token=token)
            elif child.is_file():
                total_bytes += self.upload(child, remote_child)
        return total_bytes
