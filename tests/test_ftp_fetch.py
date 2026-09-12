"""Tests for pulling handheld saves over FTP into a local, scannable cache.

The FTP transport is deterministic (no sockets): a fake client serves an
in-memory tree.  The tests cover the happy path, the atomicity guarantee (a
failed pull must never expose a half-populated cache), read-only behaviour and
path-escape protection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vajsave.ftp_fetch import (
    FTP_CACHE_DIRNAME,
    FtpPullResult,
    cache_dir_for,
    ftp_cache_root,
    pull_preset,
)
from vajsave.remote_ftp import (
    CHECKPOINT_PRESET_KEY,
    FTPD_PRESET_KEY,
    DEFAULT_PRESET_KEY,
    FtpProfile,
    RemoteFtpClient,
    RemoteFtpError,
    default_preset,
    ensure_read_only,
    get_preset,
    presets,
)
from vajsave.scanner import scan

from conftest import FakeRemoteFtpClient, checkpoint_ftp_tree, fake_client_factory


def _checkpoint_profile(host: str = "192.168.1.50") -> FtpProfile:
    base = get_preset(CHECKPOINT_PRESET_KEY)
    return FtpProfile(
        key=base.key,
        label=base.label,
        host=host,
        user="user",
        password="",
        path=base.path,
    )


# -- presets -----------------------------------------------------------------


def test_default_preset_is_checkpoint_with_ftpd_fallback():
    keys = [preset.key for preset in presets()]
    assert keys[0] == CHECKPOINT_PRESET_KEY
    assert FTPD_PRESET_KEY in keys
    assert default_preset().key == DEFAULT_PRESET_KEY == CHECKPOINT_PRESET_KEY
    # An unknown key resolves back to the default instead of raising.
    assert get_preset("does-not-exist").key == CHECKPOINT_PRESET_KEY


def test_profile_repr_hides_password():
    profile = FtpProfile(key="checkpoint", label="Checkpoint", password="top-secret")
    assert "top-secret" not in repr(profile)
    assert "top-secret" not in profile.redacted()


# -- happy path --------------------------------------------------------------


def test_pull_preset_mirrors_tree_into_cache(tmp_path: Path):
    cache_root = ftp_cache_root(tmp_path / "lib")
    factory = fake_client_factory(checkpoint_ftp_tree())

    result = pull_preset(_checkpoint_profile(), cache_root, client_factory=factory)

    assert isinstance(result, FtpPullResult)
    assert result.ok is True
    assert result.preset_key == CHECKPOINT_PRESET_KEY
    assert result.path == cache_root / CHECKPOINT_PRESET_KEY
    assert result.files == 2
    assert result.total_bytes > 0
    assert (
        result.path / "3ds" / "Checkpoint" / "saves" / "0x011C4 Pokemon Moon" / "slot0" / "main"
    ).read_bytes() == b"MOON-SAVE"
    assert factory.clients and factory.clients[0].closed is True


def test_pulled_cache_scans_as_a_device(tmp_path: Path):
    cache_root = ftp_cache_root(tmp_path / "lib")
    factory = fake_client_factory(checkpoint_ftp_tree())
    result = pull_preset(_checkpoint_profile(), cache_root, client_factory=factory)
    assert result.ok

    scanned = scan(result.path)
    source_ids = {source.source_id for source in scanned.sources}
    assert "3ds_checkpoint" in source_ids
    assert "switch_checkpoint" in source_ids
    assert scanned.saves, "pulled checkpoint saves must be scannable"


# -- atomicity / failure -----------------------------------------------------


def test_failed_first_pull_leaves_no_cache(tmp_path: Path):
    cache_root = ftp_cache_root(tmp_path / "lib")
    factory = fake_client_factory(
        checkpoint_ftp_tree(),
        fail_paths={"/3ds/Checkpoint/saves"},
        error_message="permission denied",
    )

    result = pull_preset(_checkpoint_profile(), cache_root, client_factory=factory)

    assert result.ok is False
    assert "permission denied" in result.error
    assert not (cache_root / CHECKPOINT_PRESET_KEY).exists()
    # No hidden staging directory survives a failed pull.
    assert list(cache_root.glob(".*")) == []


def test_failed_pull_keeps_previous_cache_intact(tmp_path: Path):
    cache_root = ftp_cache_root(tmp_path / "lib")
    good = pull_preset(
        _checkpoint_profile(), cache_root, client_factory=fake_client_factory(checkpoint_ftp_tree())
    )
    assert good.ok
    saved_file = good.path / "3ds" / "Checkpoint" / "saves" / "0x011C4 Pokemon Moon" / "slot0" / "main"

    bad = pull_preset(
        _checkpoint_profile(),
        cache_root,
        client_factory=fake_client_factory(
            checkpoint_ftp_tree(),
            fail_paths={"/switch/Checkpoint/saves/0100000000010000 Super Mario Odyssey/slot0/main"},
            error_message="connection reset",
        ),
    )

    assert bad.ok is False
    # The previous, complete cache is still there and still scannable.
    assert saved_file.read_bytes() == b"MOON-SAVE"
    assert (cache_root / CHECKPOINT_PRESET_KEY).exists()
    assert list(cache_root.glob(".*staging*")) == []
    assert scan(cache_root / CHECKPOINT_PRESET_KEY).saves


def test_empty_remote_is_a_successful_empty_device(tmp_path: Path):
    cache_root = ftp_cache_root(tmp_path / "lib")
    result = pull_preset(_checkpoint_profile(), cache_root, client_factory=fake_client_factory({}))
    assert result.ok is True
    assert result.files == 0
    assert result.path.is_dir()
    assert not scan(result.path).saves


# -- path safety -------------------------------------------------------------


def test_unsafe_remote_names_cannot_escape_cache(tmp_path: Path):
    lib = tmp_path / "lib"
    cache_root = ftp_cache_root(lib)
    tree = {
        "3ds": {
            "..": {"evil": b"x"},
            "abs": {"/tmp/pwned": b"x"},
            "good": {"main": b"ok"},
        }
    }

    result = pull_preset(_checkpoint_profile(), cache_root, client_factory=fake_client_factory(tree))

    assert result.ok is True
    # Only the safe file was written, and nothing escaped the cache.
    assert result.files == 1
    assert (cache_root / CHECKPOINT_PRESET_KEY / "3ds" / "good" / "main").read_bytes() == b"ok"
    assert not (lib / "evil").exists()
    assert not (tmp_path / "evil").exists()
    assert not (tmp_path / "pwned").exists()


def test_cache_dir_for_sanitizes_preset_key(tmp_path: Path):
    assert cache_dir_for(tmp_path, "checkpoint") == tmp_path / FTP_CACHE_DIRNAME / "checkpoint"
    # A hostile key cannot climb out of the cache root.
    assert cache_dir_for(tmp_path, "../../etc") == tmp_path / FTP_CACHE_DIRNAME / "default"


# -- password handling -------------------------------------------------------


def test_password_never_appears_in_error(tmp_path: Path):
    cache_root = ftp_cache_root(tmp_path / "lib")
    profile = FtpProfile(
        key=CHECKPOINT_PRESET_KEY,
        label="Checkpoint",
        host="10.0.0.2",
        password="s3cr3t-pass",
    )
    factory = fake_client_factory(
        {},
        fail_paths={"/"},
        error_message="login failed for s3cr3t-pass",
    )
    result = pull_preset(profile, cache_root, client_factory=factory)
    assert result.ok is False
    assert "s3cr3t-pass" not in result.error


# -- read-only transport -----------------------------------------------------


def test_ensure_read_only_rejects_mutating_commands():
    for command in ("STOR remote", "DELE /x", "MKD /x", "RMD /x", "RNFR a", "SITE CHMOD 777 f"):
        with pytest.raises(RemoteFtpError):
            ensure_read_only(command)
    for command in ("MLSD /", "RETR /a", "TYPE I", "PASV"):
        ensure_read_only(command)  # must not raise


class _RecordingFtp:
    """A minimal ftplib-compatible double that records method usage."""

    def __init__(self) -> None:
        self.used: list = []

    def connect(self, host, port, timeout=None):
        self.used.append("connect")

    def login(self, user, password):
        self.used.append("login")

    def set_pasv(self, enabled):
        self.used.append("pasv")

    def mlsd(self, path):
        self.used.append("mlsd")
        return iter([("main", {"type": "file", "size": "4"})])

    def retrbinary(self, cmd, callback, blocksize=8192):
        self.used.append(cmd)
        callback(b"data")

    def quit(self):
        self.used.append("quit")


def test_remote_client_uses_only_read_commands(tmp_path: Path):
    recorder = _RecordingFtp()
    client = RemoteFtpClient(_checkpoint_profile(), ftp_factory=lambda: recorder)
    client.connect()
    entries = client.list_dir("/3ds/Checkpoint/saves")
    assert [entry.name for entry in entries] == ["main"]
    written = client.download("/3ds/Checkpoint/saves/main", tmp_path / "main")
    client.close()

    assert written == 4
    mutating = {"STOR", "STOU", "APPE", "DELE", "RMD", "MKD", "RNFR", "RNTO", "SITE"}
    for used in recorder.used:
        verb = str(used).split(" ", 1)[0].upper()
        assert verb not in mutating


# -- transport failure paths -------------------------------------------------


def test_is_safe_relative_path_accepts_and_rejects():
    from vajsave.remote_ftp import is_safe_relative_path, join_remote

    assert is_safe_relative_path("3ds/Checkpoint/saves")
    assert is_safe_relative_path("a/b\\c")
    assert not is_safe_relative_path("/etc/passwd")
    assert not is_safe_relative_path("../x")
    assert not is_safe_relative_path("")
    assert join_remote("/", "a") == "/a"
    assert join_remote("/3ds", "b") == "/3ds/b"
    assert join_remote("", "c") == "/c"
    with pytest.raises(RemoteFtpError):
        join_remote("/", "..")


def test_client_context_manager_closes_and_requires_connection():
    recorder = _RecordingFtp()
    with RemoteFtpClient(_checkpoint_profile(), ftp_factory=lambda: recorder) as client:
        assert [entry.name for entry in client.list_dir("/x")] == ["main"]
    assert "quit" in recorder.used

    # An unconnected client must fail closed rather than silently returning []
    unconnected = RemoteFtpClient(_checkpoint_profile(), ftp_factory=lambda: recorder)
    with pytest.raises(RemoteFtpError):
        unconnected.list_dir("/x")
    unconnected.close()  # closing an unconnected client is a no-op


def test_connect_failure_is_wrapped_and_redacted():
    class Boom:
        def connect(self, *args, **kwargs):
            raise OSError("boom")

        def login(self, *args, **kwargs):
            pass

        def set_pasv(self, *args, **kwargs):
            pass

        def quit(self):
            pass

        def close(self):
            pass

    profile = FtpProfile(key="checkpoint", label="Checkpoint", password="hunter2")
    client = RemoteFtpClient(profile, ftp_factory=lambda: Boom())
    with pytest.raises(RemoteFtpError) as info:
        client.connect()
    assert "boom" in str(info.value)
    assert "hunter2" not in str(info.value)


def test_set_pasv_failure_is_tolerated():
    class NoPasv(_RecordingFtp):
        def set_pasv(self, enabled):
            raise OSError("no passive mode")

    recorder = NoPasv()
    client = RemoteFtpClient(_checkpoint_profile(), ftp_factory=lambda: recorder)
    client.connect()
    assert [entry.name for entry in client.list_dir("/x")] == ["main"]
    client.close()


def test_list_dir_falls_back_to_nlst_without_mlsd(tmp_path: Path):
    class NlstOnly:
        def __init__(self):
            self.dirs = {"/root/sub"}

        def connect(self, *a, **k):
            pass

        def login(self, *a, **k):
            pass

        def set_pasv(self, *a, **k):
            pass

        def mlsd(self, path):
            raise OSError("MLSD unsupported")

        def nlst(self, path):
            return ["/root/a", "/root/sub", "/root/..", "/"]

        def pwd(self):
            raise OSError("pwd unsupported")

        def cwd(self, path):
            if path in self.dirs or path == "/":
                return
            raise OSError("not a directory")

        def quit(self):
            pass

    client = RemoteFtpClient(_checkpoint_profile(), ftp_factory=lambda: NlstOnly())
    client.connect()
    entries = {entry.name: entry for entry in client.list_dir("/root")}
    client.close()
    assert set(entries) == {"a", "sub"}
    assert entries["sub"].is_dir is True
    assert entries["a"].is_dir is False


def test_list_dir_reports_permission_failure():
    class Dead:
        def connect(self, *a, **k):
            pass

        def login(self, *a, **k):
            pass

        def set_pasv(self, *a, **k):
            pass

        def mlsd(self, path):
            raise OSError("mlsd nope")

        def nlst(self, path):
            raise OSError("nlst denied")

        def quit(self):
            pass

    client = RemoteFtpClient(_checkpoint_profile(), ftp_factory=lambda: Dead())
    client.connect()
    with pytest.raises(RemoteFtpError):
        client.list_dir("/x")
    client.close()


def test_probe_dir_tolerates_failed_restore(tmp_path: Path):
    class RestoreFail:
        def connect(self, *a, **k):
            pass

        def login(self, *a, **k):
            pass

        def set_pasv(self, *a, **k):
            pass

        def mlsd(self, path):
            raise OSError("unsupported")

        def nlst(self, path):
            return ["/root/sub"]

        def pwd(self):
            return "/"

        def cwd(self, path):
            if path == "/":
                raise OSError("cannot go back")

        def quit(self):
            pass

    client = RemoteFtpClient(_checkpoint_profile(), ftp_factory=lambda: RestoreFail())
    client.connect()
    entries = client.list_dir("/root")
    client.close()
    assert [entry.is_dir for entry in entries] == [True]


def test_mlsd_skips_dot_entries_and_bad_sizes():
    class Odd(_RecordingFtp):
        def mlsd(self, path):
            return iter(
                [
                    (".", {"type": "dir"}),
                    ("..", {"type": "dir"}),
                    ("f", {"type": "file", "size": "not-a-number"}),
                ]
            )

    client = RemoteFtpClient(_checkpoint_profile(), ftp_factory=lambda: Odd())
    client.connect()
    entries = client.list_dir("/x")
    client.close()
    assert [entry.name for entry in entries] == ["f"]
    assert entries[0].size == 0


def test_download_failure_removes_partial_file(tmp_path: Path):
    class FailRetr(_RecordingFtp):
        def retrbinary(self, cmd, callback, blocksize=8192):
            callback(b"partial")
            raise OSError("connection reset")

    client = RemoteFtpClient(_checkpoint_profile(), ftp_factory=lambda: FailRetr())
    client.connect()
    target = tmp_path / "save.bin"
    with pytest.raises(RemoteFtpError):
        client.download("/x", target)
    assert not target.exists()
    assert not (tmp_path / "save.bin.part").exists()


def test_download_failure_before_writing(tmp_path: Path):
    class DeadRetr(_RecordingFtp):
        def retrbinary(self, cmd, callback, blocksize=8192):
            raise OSError("server refused")

    client = RemoteFtpClient(_checkpoint_profile(), ftp_factory=lambda: DeadRetr())
    client.connect()
    with pytest.raises(RemoteFtpError):
        client.download("/x", tmp_path / "save.bin")
    assert not (tmp_path / "save.bin").exists()


def test_close_falls_back_when_quit_fails():
    class QuitBoom(_RecordingFtp):
        def __init__(self):
            super().__init__()
            self.closed = False

        def quit(self):
            raise OSError("quit failed")

        def close(self):
            self.closed = True

    recorder = QuitBoom()
    client = RemoteFtpClient(_checkpoint_profile(), ftp_factory=lambda: recorder)
    client.connect()
    client.close()
    assert recorder.closed is True


def test_read_only_guard_blocks_mutating_putcmd():
    class Guarded(_RecordingFtp):
        def putcmd(self, command, *args):
            self.used.append(str(command))

    recorder = Guarded()
    client = RemoteFtpClient(_checkpoint_profile(), ftp_factory=lambda: recorder)
    client.connect()
    recorder.putcmd("MLSD /")  # read-only verb passes through
    with pytest.raises(RemoteFtpError):
        recorder.putcmd("STOR remote")
    with pytest.raises(RemoteFtpError):
        recorder.putcmd("DELE remote")
    assert "MLSD /" in recorder.used
    assert not any("STOR" in used or "DELE" in used for used in recorder.used)


def test_close_swallows_a_failing_close():
    class TotalBoom(_RecordingFtp):
        def quit(self):
            raise OSError("quit failed")

        def close(self):
            raise OSError("close failed too")

    client = RemoteFtpClient(_checkpoint_profile(), ftp_factory=lambda: TotalBoom())
    client.connect()
    client.close()  # must not raise


# -- fetch failure / commit paths --------------------------------------------


def test_pull_result_bool_and_safe_local_join(tmp_path: Path):
    from vajsave.ftp_fetch import _safe_local_join

    assert bool(FtpPullResult(ok=True, preset_key="checkpoint")) is True
    assert bool(FtpPullResult(ok=False, preset_key="checkpoint")) is False
    with pytest.raises(RemoteFtpError):
        _safe_local_join(tmp_path, "..")


def test_mirror_respects_depth_limit(tmp_path: Path):
    from vajsave.ftp_fetch import _MAX_DEPTH, _mirror

    client = FakeRemoteFtpClient(checkpoint_ftp_tree())
    client.connect()
    assert _mirror(client, "/", tmp_path, depth=_MAX_DEPTH + 1) == (0, 0)


def test_factory_failure_is_reported(tmp_path: Path):
    def factory(profile):
        raise OSError("cannot build client")

    result = pull_preset(_checkpoint_profile(), ftp_cache_root(tmp_path / "lib"), client_factory=factory)
    assert result.ok is False
    assert "cannot build client" in result.error


def test_pull_ignores_close_errors(tmp_path: Path):
    class CloseBoom(FakeRemoteFtpClient):
        def close(self):
            self.closed = True
            raise OSError("already gone")

    def factory(profile):
        return CloseBoom(checkpoint_ftp_tree())

    cache_root = ftp_cache_root(tmp_path / "lib")
    ok = pull_preset(_checkpoint_profile(), cache_root, client_factory=factory)
    assert ok.ok is True
    # A failing pull whose close() also raises still reports the pull error.
    def failing_factory(profile):
        return CloseBoom(checkpoint_ftp_tree(), fail_paths={"/3ds/Checkpoint/saves"}, error_message="no dir")

    bad = pull_preset(_checkpoint_profile(), cache_root, client_factory=failing_factory)
    assert bad.ok is False
    assert "no dir" in bad.error


def test_repull_over_existing_cache_replaces_it(tmp_path: Path):
    cache_root = ftp_cache_root(tmp_path / "lib")
    first = pull_preset(
        _checkpoint_profile(), cache_root, client_factory=fake_client_factory(checkpoint_ftp_tree())
    )
    assert first.ok
    (first.path / "stale.txt").write_text("old")

    second = pull_preset(
        _checkpoint_profile(), cache_root, client_factory=fake_client_factory(checkpoint_ftp_tree())
    )

    assert second.ok
    # The old cache is swapped out wholesale, not merged in place.
    assert not (second.path / "stale.txt").exists()
    assert list(cache_root.glob(".*old*")) == []


def test_commit_and_rollback_failure_is_reported(tmp_path: Path, monkeypatch):
    import vajsave.ftp_fetch as ftp_fetch

    cache_root = ftp_cache_root(tmp_path / "lib")
    good = pull_preset(
        _checkpoint_profile(), cache_root, client_factory=fake_client_factory(checkpoint_ftp_tree())
    )
    assert good.ok

    real_replace = ftp_fetch.os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] >= 2:  # swap fails and the rollback also fails
            raise OSError("disk full")
        return real_replace(src, dst)

    monkeypatch.setattr(ftp_fetch.os, "replace", flaky)
    bad = pull_preset(
        _checkpoint_profile(), cache_root, client_factory=fake_client_factory(checkpoint_ftp_tree())
    )

    assert bad.ok is False
    assert "disk full" in bad.error
    assert list(cache_root.glob(".*staging*")) == []


def test_remove_tree_swallows_oserror(tmp_path: Path, monkeypatch):
    import vajsave.ftp_fetch as ftp_fetch

    def boom(*args, **kwargs):
        raise OSError("nope")

    monkeypatch.setattr(ftp_fetch.shutil, "rmtree", boom)
    target = tmp_path / "d"
    target.mkdir()
    ftp_fetch._remove_tree(target)  # must not raise


def test_commit_failure_restores_previous_cache(tmp_path: Path, monkeypatch):
    import vajsave.ftp_fetch as ftp_fetch

    cache_root = ftp_cache_root(tmp_path / "lib")
    good = pull_preset(
        _checkpoint_profile(), cache_root, client_factory=fake_client_factory(checkpoint_ftp_tree())
    )
    assert good.ok
    saved = good.path / "3ds" / "Checkpoint" / "saves" / "0x011C4 Pokemon Moon" / "slot0" / "main"

    real_replace = ftp_fetch.os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:  # staging -> final swap fails
            raise OSError("disk full")
        return real_replace(src, dst)

    monkeypatch.setattr(ftp_fetch.os, "replace", flaky)
    bad = pull_preset(
        _checkpoint_profile(), cache_root, client_factory=fake_client_factory(checkpoint_ftp_tree())
    )

    assert bad.ok is False
    assert "disk full" in bad.error
    # The previous complete cache was restored and no staging tree survived.
    assert saved.read_bytes() == b"MOON-SAVE"
    assert list(cache_root.glob(".*staging*")) == []
