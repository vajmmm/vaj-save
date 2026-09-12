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
    path="/",
    description="Checkpoint 存档服务器（默认预设）",
)

FTPD_PRESET = FtpProfile(
    key=FTPD_PRESET_KEY,
    label="ftpd",
    path="/",
    description="通用 ftpd 服务器（回退预设）",
)

_PRESETS: Tuple[FtpProfile, ...] = (CHECKPOINT_PRESET, FTPD_PRESET)


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
    ) -> None:
        self.profile = profile
        self._ftp_factory = ftp_factory
        self._timeout = timeout
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
        """List one remote directory (MLSD first, NLST fallback)."""
        ftp = self._require()
        try:
            entries = self._list_mlsd(ftp, remote_path)
        except Exception:  # noqa: BLE001 - servers without MLSD fall back to NLST
            try:
                entries = self._list_nlst(ftp, remote_path)
            except Exception as exc:  # noqa: BLE001
                raise RemoteFtpError(
                    redact(f"列出远程目录失败 {remote_path}: {exc}", self.profile.password)
                ) from exc
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
