import json
from pathlib import Path
import pytest
from vajsave.cli import main
from conftest import build_sfo


def test_cli_scan_json_output(tmp_path: Path, capsys, psp_sfo_bytes: bytes):
    save_dir = tmp_path / "PSP" / "SAVEDATA" / "ULUS01000"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)

    exit_code = main(["scan", str(tmp_path), "--json"])
    assert exit_code == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert isinstance(data, list)
    assert len(data) == 1
    item = data[0]
    assert item["platform"] == "psp"
    assert len(item["saves"]) == 1
    save = item["saves"][0]
    assert save["platform"] == "psp"
    assert save["source_id"] == "psp"
    assert save["title_id"] == "ULJM05800"
    assert save["display_name"] == "Monster Hunter Portable 3rd"


def test_cli_scan_human_readable(tmp_path: Path, capsys, psp_sfo_bytes: bytes):
    save_dir = tmp_path / "PSP" / "SAVEDATA" / "ULUS01000"
    save_dir.mkdir(parents=True)
    (save_dir / "PARAM.SFO").write_bytes(psp_sfo_bytes)

    exit_code = main(["scan", str(tmp_path)])
    assert exit_code == 0

    captured = capsys.readouterr()
    assert "PSP" in captured.out or "psp" in captured.out
    assert "Monster Hunter" in captured.out


def test_cli_scan_no_path_discovered_volumes(tmp_path: Path, capsys, monkeypatch):
    vol_dir = tmp_path / "MOCK_VOL"
    vol_dir.mkdir()
    monkeypatch.setattr(
        "vajsave.cli.MountedVolumeBackend.iter_roots",
        lambda self: [vol_dir],
    )

    exit_code = main(["scan", "--json"])
    assert exit_code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert len(data) == 1
    assert data[0]["root_path"] == str(vol_dir)


def test_cli_scan_no_volumes_found(capsys, monkeypatch):
    monkeypatch.setattr(
        "vajsave.cli.MountedVolumeBackend.iter_roots",
        lambda self: [],
    )

    exit_code = main(["scan"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "No mounted external volumes found" in captured.err


def test_cli_invalid_args(capsys):
    exit_code = main(["--unknown-command"])
    assert exit_code == 2


def test_cli_watch_human_output(monkeypatch, capsys, tmp_path: Path):
    from vajsave.models import VolumeInfo
    vol = VolumeInfo(name="TEST_PSP", mount_point=tmp_path)

    def fake_watch(provider, interval=1.0, callback=None, stop_event=None):
        if callback:
            callback("appeared", vol)
            callback("disappeared", vol)

    monkeypatch.setattr("vajsave.cli.watch_volumes", fake_watch)
    exit_code = main(["watch", "--interval", "0.1"])
    assert exit_code == 0

    captured = capsys.readouterr()
    assert "Appeared" in captured.out
    assert "Disappeared" in captured.out


def test_cli_watch_json_output(monkeypatch, capsys, tmp_path: Path):
    from vajsave.models import VolumeInfo
    vol = VolumeInfo(name="TEST_PSP", mount_point=tmp_path)

    def fake_watch(provider, interval=1.0, callback=None, stop_event=None):
        if callback:
            callback("appeared", vol)
            callback("disappeared", vol)

    monkeypatch.setattr("vajsave.cli.watch_volumes", fake_watch)
    exit_code = main(["watch", "--json", "--interval", "0.1"])
    assert exit_code == 0

    captured = capsys.readouterr()
    lines = [l.strip() for l in captured.out.strip().split("\n") if l.strip()]
    assert len(lines) > 0


def test_cli_watch_keyboard_interrupt(monkeypatch, capsys):
    def fake_watch(provider, interval=1.0, callback=None, stop_event=None):
        raise KeyboardInterrupt()

    monkeypatch.setattr("vajsave.cli.watch_volumes", fake_watch)
    exit_code = main(["watch"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Stopped volume watcher" in captured.out
