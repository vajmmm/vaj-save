"""End-to-end wiring of the metadata/artwork layers into the Tk inspector.

The pure layers are covered by ``tests/test_metadata.py`` and
``tests/test_artwork.py``.  Here we pin the UI contract:

* the first paint shows the scanned name (fallback) and a background pass then
  promotes the canonical index title -- a failure leaves the existing UI alone;
* a fresh downloaded cover is applied to the detail panel;
* a stale background result never overwrites a newer selection;
* repeat selections coalesce into one background job (no duplicate work).
"""

from __future__ import annotations

import tkinter as tk
import urllib.error
import zlib
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from vajsave.app_ui import build_app
from vajsave.app_state import AppState
from vajsave.artwork import (
    COVER_CACHE_DIR,
    PLACEHOLDER,
    ArtworkDownloader,
    ArtworkLoader,
    ArtworkService,
    CoverCache,
)
from vajsave.models import SaveEntry, VolumeInfo
from vajsave.volume import FakeVolumeProvider


def make_gba_rom(title: str = "APOTRIS", code: str = "APTR", size: int = 0x200) -> bytes:
    data = bytearray(size)
    data[0xA0:0xA0 + 12] = title.ljust(12)[:12].encode("ascii")
    data[0xAC:0xB0] = code.ljust(4)[:4].encode("ascii")
    return bytes(data)


def png_bytes(size=(4, 4)) -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", size, (10, 120, 200, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def digest(blob: bytes):
    import hashlib

    return hashlib.sha1(blob).hexdigest(), format(zlib.crc32(blob) & 0xFFFFFFFF, "08x")


class FakeResponse:
    def __init__(self, data: bytes = b"", status: int = 200):
        self._data = data
        self.status = status
        self.headers = {}

    def read(self, n=-1):
        return self._data if n is None or n < 0 else self._data[:n]

    def close(self):
        pass


class SyncSchedule:
    """Collects ``root.after(0, fn)`` callbacks so tests flush them explicitly."""

    def __init__(self):
        self.pending = []

    def __call__(self, fn):
        self.pending.append(fn)

    def flush(self):
        while self.pending:
            self.pending.pop(0)()


class InlineExecutor:
    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)

    def shutdown(self, wait=False, cancel_futures=False):
        pass


class DeferredExecutor:
    """Queues submitted work until ``run_all`` is called.

    A real app runs enrichment on a thread pool, so a task is never finished by
    the time the refresh returns.  This executor models that gap so tests can
    assert the pre-completion UI and drive completion deterministically.
    """

    def __init__(self):
        self._tasks = []

    def submit(self, fn, *args, **kwargs):
        self._tasks.append((fn, args, kwargs))

    def shutdown(self, wait=False, cancel_futures=False):
        pass

    def run_all(self):
        tasks, self._tasks = self._tasks, []
        for fn, args, kwargs in tasks:
            fn(*args, **kwargs)


@pytest.fixture(scope="module")
def tk_root():
    try:
        root = tk.Tk()
    except Exception:
        pytest.skip("Tkinter display not available")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:
        pass


def _dispose(app, root) -> None:
    try:
        app._stop_background()
    except Exception:
        pass
    for child in list(root.winfo_children()):
        try:
            child.destroy()
        except Exception:
            pass


def _sync_loader():
    schedule = SyncSchedule()
    return ArtworkLoader(schedule, executor=InlineExecutor()), schedule


def _deferred_loader():
    schedule = SyncSchedule()
    executor = DeferredExecutor()
    return ArtworkLoader(schedule, executor=executor), schedule, executor


def _offline_opener(url, timeout=None):
    raise urllib.error.URLError("offline")


def _write_dat(directory: Path, header: str, games) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    parts = ['<?xml version="1.0"?>', "<datafile>", f"<header><name>{header}</name></header>"]
    for title, sha1, crc in games:
        parts.append(f'<game name="{title}">')
        parts.append(f'<rom name="{title}.gba" crc="{crc}" sha1="{sha1}"/>')
        parts.append("</game>")
    parts.append("</datafile>")
    path = directory / "gba.dat"
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def _gba_volume(tmp_path: Path, pairs) -> Path:
    """``pairs`` is a list of ``(save_name, rom_name)``."""
    vol = tmp_path / "SD"
    (vol / "SAVEGAME").mkdir(parents=True)
    (vol / "GBA").mkdir()
    for save_name, rom_name in pairs:
        (vol / "SAVEGAME" / f"{save_name}.sav").write_bytes(b"save-data")
        (vol / "GBA" / f"{rom_name}.gba").write_bytes(make_gba_rom(title=rom_name.upper()))
    return vol


def _state(tmp_path: Path, vol: Path) -> AppState:
    state = AppState(
        provider=FakeVolumeProvider([VolumeInfo(name="SD", mount_point=vol)]),
        library_root=tmp_path / "lib",
    )
    # Never let the background enrichment layer reach the real network in tests;
    # individual tests opt in to a recording downloader via ``_inject_downloader``.
    state._artwork_service = ArtworkService(
        cache=CoverCache(state.library_root / COVER_CACHE_DIR),
        downloader=ArtworkDownloader(urlopen=_offline_opener),
    )
    state.refresh_volumes()
    return state


def _inject_downloader(state: AppState, *, data=None, fail=False):
    calls = []

    def opener(url, timeout=None):
        calls.append(url)
        if fail:
            raise urllib.error.URLError("offline")
        return FakeResponse(data if data is not None else png_bytes())

    state._artwork_service = ArtworkService(
        cache=CoverCache(state.library_root / COVER_CACHE_DIR),
        downloader=ArtworkDownloader(urlopen=opener),
    )
    return calls


# --- canonical title ---------------------------------------------------------


def test_canonical_title_promoted_after_background_metadata(tk_root, tmp_path, monkeypatch):
    vol = _gba_volume(tmp_path, [("Apotris", "Apotris")])
    rom = vol / "GBA" / "Apotris.gba"
    sha1, crc = digest(rom.read_bytes())
    dat_dir = _write_dat(tmp_path / "dats", "Nintendo - Game Boy Advance", [("Apotris - Rhythm Game (USA)", sha1, crc)])

    state = _state(tmp_path, vol)
    state.set_libretro_dir(dat_dir)
    loader, schedule, executor = _deferred_loader()
    app = build_app(state=state, root=tk_root, loader=loader)
    try:
        state.select_mount(vol)
        app.refresh_saves_ui()
        monkeypatch.setattr(state, "ensure_save_cover", lambda *a, **k: PLACEHOLDER, raising=False)

        app.save_list.select_index(0)
        tk_root.update_idletasks()
        # First paint: scanned file name, before the index answered.
        assert app.detail_name.get() == "Apotris"
        executor.run_all()
        schedule.flush()
        assert app.detail_name.get() == "Apotris - Rhythm Game (USA)"
    finally:
        _dispose(app, tk_root)


def test_metadata_failure_keeps_existing_ui(tk_root, tmp_path, monkeypatch):
    vol = _gba_volume(tmp_path, [("Apotris", "Apotris")])
    # No libretro index configured -> metadata lookup returns None.
    state = _state(tmp_path, vol)
    loader, schedule = _sync_loader()
    app = build_app(state=state, root=tk_root, loader=loader)
    try:
        state.select_mount(vol)
        app.refresh_saves_ui()
        monkeypatch.setattr(state, "ensure_save_cover", lambda *a, **k: PLACEHOLDER, raising=False)

        app.save_list.select_index(0)
        schedule.flush()
        assert app.detail_name.get() == "Apotris"
    finally:
        _dispose(app, tk_root)


# --- downloaded cover --------------------------------------------------------


def test_downloaded_cover_replaces_placeholder(tk_root, tmp_path, monkeypatch):
    vol = _gba_volume(tmp_path, [("Apotris", "Apotris")])
    rom = vol / "GBA" / "Apotris.gba"
    sha1, crc = digest(rom.read_bytes())
    dat_dir = _write_dat(tmp_path / "dats", "Nintendo - Game Boy Advance", [("Apotris (USA)", sha1, crc)])

    state = _state(tmp_path, vol)
    state.set_libretro_dir(dat_dir)
    calls = _inject_downloader(state)
    loader, schedule, executor = _deferred_loader()
    app = build_app(state=state, root=tk_root, loader=loader)
    try:
        state.select_mount(vol)
        app.refresh_saves_ui()
        app.save_list.select_index(0)
        assert app._detail_cover_path is None  # placeholder before the download
        executor.run_all()
        schedule.flush()

        assert calls and calls[0].startswith("https://thumbnails.libretro.com/")
        assert app._detail_cover_path is not None
        assert app._detail_cover_path.endswith(".png")
        assert Path(app._detail_cover_path).is_file()
    finally:
        _dispose(app, tk_root)


# --- stale / duplicate guards ------------------------------------------------


def test_stale_background_result_does_not_override_newer_selection(tk_root, tmp_path, monkeypatch):
    vol = _gba_volume(tmp_path, [("Apotris", "Apotris"), ("Bare", "Bare")])
    games = []
    for save_name, canonical in (("Apotris", "Apotris - Rhythm Game (USA)"), ("Bare", "Bare Bones (Japan)")):
        sha1, crc = digest((vol / "GBA" / f"{save_name}.gba").read_bytes())
        games.append((canonical, sha1, crc))
    dat_dir = _write_dat(tmp_path / "dats", "Nintendo - Game Boy Advance", games)

    state = _state(tmp_path, vol)
    state.set_libretro_dir(dat_dir)
    loader, schedule = _sync_loader()
    app = build_app(state=state, root=tk_root, loader=loader)
    try:
        state.select_mount(vol)
        app.refresh_saves_ui()
        monkeypatch.setattr(state, "ensure_save_cover", lambda *a, **k: PLACEHOLDER, raising=False)

        names = [save.display_name for save in app._saves_index]
        first, second = names.index("Apotris"), names.index("Bare")

        app.save_list.select_index(first)  # schedules result #1
        app.save_list.select_index(second)  # newer selection invalidates #1
        schedule.flush()

        assert app.detail_name.get() == "Bare Bones (Japan)"
    finally:
        _dispose(app, tk_root)


def test_repeat_selection_coalesces_background_work(tk_root, tmp_path, monkeypatch):
    vol = _gba_volume(tmp_path, [("Apotris", "Apotris")])
    rom = vol / "GBA" / "Apotris.gba"
    sha1, crc = digest(rom.read_bytes())
    dat_dir = _write_dat(tmp_path / "dats", "Nintendo - Game Boy Advance", [("Apotris (USA)", sha1, crc)])

    state = _state(tmp_path, vol)
    state.set_libretro_dir(dat_dir)
    loader, schedule = _sync_loader()
    app = build_app(state=state, root=tk_root, loader=loader)
    try:
        state.select_mount(vol)
        app.refresh_saves_ui()
        schedule.flush()  # let the eager list enrichment finish first

        calls = []

        def counting(*args, **kwargs):
            calls.append(1)
            return PLACEHOLDER

        monkeypatch.setattr(state, "ensure_save_cover", counting, raising=False)

        app.save_list.select_index(0)
        assert app._artwork_loader.is_inflight(app._saves_index[0].path) is True
        app.save_list.select_index(0)  # coalesced onto the in-flight task
        assert calls == [1]
        schedule.flush()
        assert app.detail_name.get() == "Apotris (USA)"
    finally:
        _dispose(app, tk_root)


# --- eager list enrichment ---------------------------------------------------


def test_list_refresh_enriches_every_resolved_row(tk_root, tmp_path, monkeypatch):
    vol = _gba_volume(tmp_path, [("One", "One"), ("Two", "Two")])
    state = _state(tmp_path, vol)
    loader, schedule, executor = _deferred_loader()
    app = build_app(state=state, root=tk_root, loader=loader)
    try:
        state.select_mount(vol)
        calls = []

        def counting(entry, result=None, metadata=None):
            calls.append(entry.path)
            return PLACEHOLDER

        monkeypatch.setattr(state, "ensure_save_cover", counting, raising=False)

        app.refresh_saves_ui()
        executor.run_all()
        schedule.flush()

        assert set(calls) == {
            str(vol / "SAVEGAME" / "One.sav"),
            str(vol / "SAVEGAME" / "Two.sav"),
        }
    finally:
        _dispose(app, tk_root)


def test_list_refresh_defers_cover_work_off_the_ui_thread(tk_root, tmp_path, monkeypatch):
    vol = _gba_volume(tmp_path, [("One", "One")])
    state = _state(tmp_path, vol)
    loader, schedule, executor = _deferred_loader()
    app = build_app(state=state, root=tk_root, loader=loader)
    try:
        state.select_mount(vol)
        calls = []
        monkeypatch.setattr(
            state, "ensure_save_cover", lambda *a, **k: calls.append(1) or PLACEHOLDER, raising=False
        )

        app.refresh_saves_ui()
        # The refresh (UI) path must not perform the network-bound resolution.
        assert calls == []

        executor.run_all()
        schedule.flush()
        assert calls
    finally:
        _dispose(app, tk_root)


def test_list_row_cover_is_filled_in_background(tk_root, tmp_path):
    vol = _gba_volume(tmp_path, [("Apotris", "Apotris")])
    rom = vol / "GBA" / "Apotris.gba"
    sha1, crc = digest(rom.read_bytes())
    dat_dir = _write_dat(
        tmp_path / "dats", "Nintendo - Game Boy Advance", [("Apotris (USA)", sha1, crc)]
    )

    state = _state(tmp_path, vol)
    state.set_libretro_dir(dat_dir)
    calls = _inject_downloader(state)
    loader, schedule = _sync_loader()
    app = build_app(state=state, root=tk_root, loader=loader)
    try:
        state.select_mount(vol)
        app.refresh_saves_ui()
        schedule.flush()

        assert calls  # a download was attempted for the visible row
        row = app.save_list._rows[0]
        assert row["cover"] is not None
        assert row["cover"].endswith(".png")
        assert Path(row["cover"]).is_file()
    finally:
        _dispose(app, tk_root)


def test_unresolved_rows_do_not_start_cover_download(tk_root, tmp_path, monkeypatch):
    vol = tmp_path / "SD"
    (vol / "SAVEGAME").mkdir(parents=True)
    (vol / "SAVEGAME" / "Ghost.sav").write_bytes(b"g")

    state = _state(tmp_path, vol)
    loader, schedule, executor = _deferred_loader()
    app = build_app(state=state, root=tk_root, loader=loader)
    try:
        state.select_mount(vol)
        entry = state.all_saves()[0]
        assert state.resolve_save_identity(entry).status == "unresolved"
        calls = []
        monkeypatch.setattr(
            state, "ensure_save_cover", lambda *a, **k: calls.append(1) or PLACEHOLDER, raising=False
        )

        app.refresh_saves_ui()
        executor.run_all()
        schedule.flush()
        assert calls == []
    finally:
        _dispose(app, tk_root)


def test_ambiguous_rows_do_not_start_cover_download(tk_root, tmp_path, monkeypatch):
    vol = tmp_path / "SD"
    (vol / "SAVEGAME").mkdir(parents=True)
    (vol / "SAVEGAME" / "Apotris.sav").write_bytes(b"save")
    (vol / "GBA").mkdir()
    (vol / "GBA" / "Apotris.gba").write_bytes(make_gba_rom(title="APOTRIS A"))
    (vol / "GBA" / "Apotris (Japan).gba").write_bytes(make_gba_rom(title="APOTRIS B"))

    state = _state(tmp_path, vol)
    loader, schedule, executor = _deferred_loader()
    app = build_app(state=state, root=tk_root, loader=loader)
    try:
        state.select_mount(vol)
        entry = state.all_saves()[0]
        assert state.resolve_save_identity(entry).status == "ambiguous"
        calls = []
        monkeypatch.setattr(
            state, "ensure_save_cover", lambda *a, **k: calls.append(1) or PLACEHOLDER, raising=False
        )

        app.refresh_saves_ui()
        executor.run_all()
        schedule.flush()
        assert calls == []
    finally:
        _dispose(app, tk_root)


def test_stale_list_generation_result_is_dropped(tk_root, tmp_path, monkeypatch):
    vol = _gba_volume(tmp_path, [("One", "One")])
    state = _state(tmp_path, vol)
    loader, schedule, executor = _deferred_loader()
    app = build_app(state=state, root=tk_root, loader=loader)
    try:
        state.select_mount(vol)
        applied = []
        monkeypatch.setattr(
            app, "_apply_row_enrichment", lambda *a, **k: applied.append(a), raising=False
        )

        app.refresh_saves_ui()  # generation 1 -> task queued
        app.refresh_saves_ui()  # generation 2 -> same key coalesced, old callback stale
        executor.run_all()
        schedule.flush()

        assert len(applied) == 1  # only the current generation was applied
    finally:
        _dispose(app, tk_root)


def test_list_enrichment_task_failure_is_swallowed(tk_root, tmp_path, monkeypatch):
    vol = _gba_volume(tmp_path, [("One", "One")])
    state = _state(tmp_path, vol)
    loader, schedule = _sync_loader()
    app = build_app(state=state, root=tk_root, loader=loader)
    try:
        state.select_mount(vol)

        def boom(*args, **kwargs):
            raise RuntimeError("worker exploded")

        monkeypatch.setattr(state, "ensure_save_cover", boom, raising=False)
        app.refresh_saves_ui()
        schedule.flush()  # a failed background task must not break the list

        assert app.save_list.size() == 1
        assert app.save_list._rows[0]["cover"] is None
    finally:
        _dispose(app, tk_root)


def test_list_enrichment_submit_failure_is_swallowed(tk_root, tmp_path, monkeypatch):
    vol = _gba_volume(tmp_path, [("One", "One")])
    state = _state(tmp_path, vol)
    loader, schedule = _sync_loader()
    app = build_app(state=state, root=tk_root, loader=loader)
    try:
        state.select_mount(vol)

        def boom(*args, **kwargs):
            raise RuntimeError("executor is gone")

        monkeypatch.setattr(app._artwork_loader, "submit", boom, raising=False)
        app.refresh_saves_ui()  # must not raise when the pool is unavailable
        assert app.save_list.size() == 1
    finally:
        _dispose(app, tk_root)


def test_row_enrichment_for_missing_row_is_ignored(tk_root, tmp_path):
    vol = _gba_volume(tmp_path, [("One", "One")])
    state = _state(tmp_path, vol)
    app = build_app(state=state, root=tk_root)
    try:
        state.select_mount(vol)
        app.refresh_saves_ui()
        ghost = SaveEntry(
            platform="gba", source_id="gba", display_name="Gone", path="/tmp/does-not-exist.sav"
        )
        # A late result for a save no longer in the list must be a no-op.
        app._apply_row_enrichment(ghost, None, (None, PLACEHOLDER))
        assert app.save_list.size() == 1
    finally:
        _dispose(app, tk_root)


# --- save_display metadata promotion ----------------------------------------


def test_save_display_prefers_canonical_title(tmp_path):
    from vajsave.app_ui import save_display
    from vajsave.metadata import GameMetadata

    state = AppState(library_root=tmp_path / "lib")
    entry = SaveEntry(platform="gba", source_id="gba", display_name="Apotris", path="/tmp/Apotris.sav")
    metadata = GameMetadata(identity_key="gba:sha1:x", canonical_title="Apotris - Rhythm Game (USA)", platform="gba", region="USA")
    view = save_display(state, entry, metadata=metadata)
    assert view["title"] == "Apotris - Rhythm Game (USA)"
    assert save_display(state, entry)["title"] == "Apotris"
