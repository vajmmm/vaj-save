"""Comprehensive tests for console-specific wireless/remote FTP features.

Covers:
- Handheld presets (PS Vita, Switch, 3DS, PSP, NDS)
- Targeted path pulling (only downloading saves, skipping multi-GB game/media dirs)
- Safe drive name mapping (ux0:, ms0:, fat:)
- Wireless restore to handheld consoles via FTP
- FtpDialog UI hints and port synchronization
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from vajsave.app_state import AppState
from vajsave.ftp_fetch import cache_dir_for, ftp_cache_root, pull_preset
from vajsave.library import backup_save
from vajsave.models import SaveEntry, SaveSource, VolumeInfo
from vajsave.qt_dialogs import FtpDialog, PRESET_HINTS
from vajsave.remote_ftp import (
    CHECKPOINT_PRESET_KEY,
    FTPD_PRESET_KEY,
    NDS_PRESET_KEY,
    PSP_PRESET_KEY,
    SWITCH_PRESET_KEY,
    THREEDS_PRESET_KEY,
    VITA_PRESET_KEY,
    FtpProfile,
    RemoteFtpClient,
    get_preset,
    presets,
    sanitize_component,
)
from vajsave.scanner import scan

from conftest import FakeRemoteFtpClient, fake_client_factory


def test_console_presets_metadata():
    all_presets = {p.key: p for p in presets()}
    assert VITA_PRESET_KEY in all_presets
    assert SWITCH_PRESET_KEY in all_presets
    assert THREEDS_PRESET_KEY in all_presets
    assert PSP_PRESET_KEY in all_presets
    assert NDS_PRESET_KEY in all_presets

    vita = all_presets[VITA_PRESET_KEY]
    assert vita.port == 1337
    assert any("user/00/savedata" in p for p in vita.target_paths)

    switch = all_presets[SWITCH_PRESET_KEY]
    assert switch.port == 5000
    assert "JKSV" in switch.target_paths

    threeds = all_presets[THREEDS_PRESET_KEY]
    assert threeds.port == 5000
    assert "3ds/Checkpoint/saves" in threeds.target_paths

    psp = all_presets[PSP_PRESET_KEY]
    assert psp.port == 21
    assert "PSP/SAVEDATA" in psp.target_paths

    nds = all_presets[NDS_PRESET_KEY]
    assert nds.port == 21
    assert any("saves" in p for p in nds.target_paths)


def test_sanitize_component_handles_console_drive_labels():
    assert sanitize_component("ux0:") == "ux0"
    assert sanitize_component("ms0:") == "ms0"
    assert sanitize_component("fat:") == "fat"
    assert sanitize_component("ur0:") == "ur0"
    assert sanitize_component("normal_dir") == "normal_dir"
    assert sanitize_component("bad:colon:in:middle") is None


def test_vita_targeted_pull_downloads_saves_and_skips_huge_app_dir(tmp_path: Path):
    remote_tree: Dict[str, Any] = {
        "ux0:": {
            "user": {
                "00": {
                    "savedata": {
                        "PCSE00001": {
                            "PARAM.SFO": b"\x00PSF\x01\x01\x00\x00",
                            "save.bin": b"VITA-SAVE-DATA",
                        }
                    }
                }
            },
            "pspemu": {
                "PSP": {
                    "SAVEDATA": {
                        "ULUS10001": {
                            "PARAM.SFO": b"\x00PSF\x01\x01\x00\x00",
                            "DATA.BIN": b"PSP-SAVE-DATA",
                        }
                    }
                }
            },
            "app": {
                "PCSE00001": {
                    "eboot.bin": b"HUGE-GAME-BINARY" * 1000,
                }
            },
        }
    }
    factory = fake_client_factory(remote_tree)
    cache_root = ftp_cache_root(tmp_path / "lib")
    vita_profile = get_preset(VITA_PRESET_KEY)

    result = pull_preset(vita_profile, cache_root, client_factory=factory)
    assert result.ok is True
    assert result.path == cache_dir_for(tmp_path / "lib", VITA_PRESET_KEY)

    # Saves exist and are located in clean non-colon folders
    vita_save_dir = result.path / "ux0" / "user" / "00" / "savedata" / "PCSE00001"
    assert vita_save_dir.is_dir()
    assert (vita_save_dir / "save.bin").read_bytes() == b"VITA-SAVE-DATA"

    psp_save_dir = result.path / "ux0" / "pspemu" / "PSP" / "SAVEDATA" / "ULUS10001"
    assert psp_save_dir.is_dir()
    assert (psp_save_dir / "DATA.BIN").read_bytes() == b"PSP-SAVE-DATA"

    # The huge app directory was never downloaded
    assert not (result.path / "ux0" / "app").exists()

    # Scanner identifies both platforms in the pulled cache
    scan_res = scan(result.path)
    platforms = {s.platform for s in scan_res.saves}
    assert "vita" in platforms
    assert "psp" in platforms


def test_switch_targeted_pull_downloads_jksv_and_skips_nintendo_dir(tmp_path: Path):
    remote_tree: Dict[str, Any] = {
        "JKSV": {
            "Super Mario Odyssey": {
                "2026-01-01": {
                    "save.bin": b"SWITCH-SAVE",
                }
            }
        },
        "Nintendo": {
            "Contents": {
                "huge.nca": b"GIGABYTES-OF-GAME",
            }
        },
    }
    factory = fake_client_factory(remote_tree)
    cache_root = ftp_cache_root(tmp_path / "lib")
    switch_profile = get_preset(SWITCH_PRESET_KEY)

    result = pull_preset(switch_profile, cache_root, client_factory=factory)
    assert result.ok is True
    assert (result.path / "JKSV" / "Super Mario Odyssey" / "2026-01-01" / "save.bin").exists()
    assert not (result.path / "Nintendo").exists()


def test_psp_targeted_pull(tmp_path: Path):
    remote_tree: Dict[str, Any] = {
        "PSP": {
            "SAVEDATA": {
                "NPJH50040DATA00": {
                    "PARAM.SFO": b"\x00PSF\x01\x01\x00\x00",
                    "SECURE.BIN": b"SECURE",
                }
            }
        },
        "ISO": {
            "game.iso": b"MULTI_GB_ISO",
        },
    }
    factory = fake_client_factory(remote_tree)
    cache_root = ftp_cache_root(tmp_path / "lib")
    psp_profile = get_preset(PSP_PRESET_KEY)

    result = pull_preset(psp_profile, cache_root, client_factory=factory)
    assert result.ok is True
    assert (result.path / "PSP" / "SAVEDATA" / "NPJH50040DATA00" / "SECURE.BIN").exists()
    assert not (result.path / "ISO").exists()


def test_nds_targeted_pull(tmp_path: Path):
    remote_tree: Dict[str, Any] = {
        "saves": {
            "pokemon.sav": b"NDS-POKEMON-SAVE",
        },
        "roms": {
            "nds": {
                "pokemon.nds": b"ROM",
            }
        },
    }
    factory = fake_client_factory(remote_tree)
    cache_root = ftp_cache_root(tmp_path / "lib")
    nds_profile = get_preset(NDS_PRESET_KEY)

    result = pull_preset(nds_profile, cache_root, client_factory=factory)
    assert result.ok is True
    assert (result.path / "saves" / "pokemon.sav").exists()


from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def qt_state(tmp_path: Path):
    return AppState(library_root=tmp_path / "lib")


def test_restore_version_wirelessly_pushes_to_ftp_volume(tmp_path: Path):
    remote_tree: Dict[str, Any] = {
        "ux0:": {
            "user": {
                "00": {
                    "savedata": {
                        "PCSE00001": {
                            "PARAM.SFO": b"OLD-SFO",
                            "data.bin": b"OLD-DATA",
                        }
                    }
                }
            }
        }
    }
    factory = fake_client_factory(remote_tree)

    app = AppState(library_root=tmp_path / "lib", ftp_client_factory=factory)
    app.ftp.configure_ftp(preset_key=VITA_PRESET_KEY)

    # Pull saves over FTP
    pull_res = app.pull_ftp_saves()
    assert pull_res.ok is True
    assert pull_res.path is not None

    # Now make a backup into the library
    local_save_dir = pull_res.path / "ux0" / "user" / "00" / "savedata" / "PCSE00001"
    entry = SaveEntry(
        platform="vita",
        source_id="PCSE00001",
        display_name="Test Game",
        path=str(local_save_dir),
        title_id="PCSE00001",
    )
    backup_res = backup_save(entry, app.library_root)
    snap = backup_res.snapshot

    # Modify local file to simulate newer/different version
    (local_save_dir / "data.bin").write_bytes(b"UPDATED-LOCALLY")

    # Restore the backup snapshot back into local_save_dir
    # Because local_save_dir is inside the active FTP volume, it should wirelessly push to remote
    restored = app.restore_version(snap, local_save_dir)
    assert restored is not None

    # Verify FakeRemoteFtpClient received STOR commands and remote_tree was updated
    client = factory.clients[-1]
    stor_commands = [c for c in client.commands if isinstance(c, tuple) and c[0] == "STOR"]
    assert len(stor_commands) > 0
    assert "无线同步至掌机" in app.status_text


def test_ftp_dialog_preset_switching_updates_port_and_hint(qt_app, qt_state):
    dialog = FtpDialog(qt_state)
    try:
        # Default starts at current preset
        idx_vita = dialog.preset.findData(VITA_PRESET_KEY)
        assert idx_vita >= 0
        dialog.preset.setCurrentIndex(idx_vita)
        assert dialog.port.text() == "1337"
        assert "VitaShell" in dialog.hint.text()

        idx_switch = dialog.preset.findData(SWITCH_PRESET_KEY)
        assert idx_switch >= 0
        dialog.preset.setCurrentIndex(idx_switch)
        assert dialog.port.text() == "5000"
        assert "sys-ftpd" in dialog.hint.text()

        idx_psp = dialog.preset.findData(PSP_PRESET_KEY)
        assert idx_psp >= 0
        dialog.preset.setCurrentIndex(idx_psp)
        assert dialog.port.text() == "21"
        assert "PSP-FTPD" in dialog.hint.text()

        idx_nds = dialog.preset.findData(NDS_PRESET_KEY)
        assert idx_nds >= 0
        dialog.preset.setCurrentIndex(idx_nds)
        assert dialog.port.text() == "21"
        assert "ftpd-nds" in dialog.hint.text()
    finally:
        dialog.reject()


def test_clean_host_and_port():
    from vajsave.qt_dialogs import _clean_host_and_port

    host, port = _clean_host_and_port("ftp://192.168.1.50:1337", "")
    assert host == "192.168.1.50"
    assert port == "1337"

    host, port = _clean_host_and_port("ftp://192.168.1.50:1337/", "")
    assert host == "192.168.1.50"
    assert port == "1337"

    host, port = _clean_host_and_port("192.168.1.50", "5000")
    assert host == "192.168.1.50"
    assert port == "5000"


def test_unix_list_parsing_vitashell_and_psp():
    class DummyFTP:
        def __init__(self, lines):
            self.lines = lines

        def dir(self, path, callback):
            for line in self.lines:
                callback(line)

    lines = [
        "total 3",
        "drwxrwxrwx 1 ftp ftp 0 Jan 01 1980 PCSE00001",
        "drwxr-xr-x 2 ftp ftp 4096 Oct 03 11:00 ULUS10001",
        "-rw-rw-rw- 1 ftp ftp 12345 Jan 01 1980 PARAM.SFO",
        "drwxrwxrwx 1 ftp ftp 0 Jan 01 1980 .",
        "drwxrwxrwx 1 ftp ftp 0 Jan 01 1980 ..",
    ]
    client = RemoteFtpClient(get_preset(VITA_PRESET_KEY))
    entries = client._list_unix_list(DummyFTP(lines), "/")
    assert len(entries) == 3
    names = {e.name: e for e in entries}
    assert "PCSE00001" in names
    assert names["PCSE00001"].is_dir is True
    assert "ULUS10001" in names
    assert names["ULUS10001"].is_dir is True
    assert "PARAM.SFO" in names
    assert names["PARAM.SFO"].is_dir is False
    assert names["PARAM.SFO"].size == 12345


def test_ftp_dialog_test_connection_button(qt_app, qt_state):
    factory = fake_client_factory({})
    qt_state._ftp_client_factory = factory

    dialog = FtpDialog(qt_state)
    try:
        dialog.host.setText("192.168.1.100")
        dialog.port.setText("1337")
        dialog.btn_test.click()
        assert "连接成功" in dialog.test_status.text()
    finally:
        dialog.reject()
