"""Tests for the pure cover-resolution helpers in ``vajsave.covers``.

Everything here is display-free: covers never imports tkinter, never raises, and
is strictly read-only. The UI wraps the returned Pillow images itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vajsave import covers
from vajsave.models import SaveEntry

PIL = pytest.importorskip("PIL.Image")


def _write_image(path: Path, size=(64, 64), color=(220, 40, 40, 255)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = PIL.new("RGBA", size, color)
    if path.suffix.lower() in (".jpg", ".jpeg"):
        image = image.convert("RGB")
    image.save(path)
    return path


def _entry(**kwargs) -> SaveEntry:
    base = dict(
        platform="psp",
        source_id="psp",
        display_name="Monster Hunter Portable 3rd",
        path="/tmp/save",
        title_id="ULJM05800",
    )
    base.update(kwargs)
    return SaveEntry(**base)


# --- find_embedded_cover ----------------------------------------------------


def test_find_embedded_cover_psp_icon0_case_insensitive(tmp_path: Path):
    save_dir = tmp_path / "ULJM05800"
    save_dir.mkdir()
    icon = _write_image(save_dir / "ICON0.PNG")
    assert covers.find_embedded_cover(save_dir) == icon


def test_find_embedded_cover_vita_sce_sys(tmp_path: Path):
    save_dir = tmp_path / "PCSE00120"
    (save_dir / "sce_sys").mkdir(parents=True)
    icon = _write_image(save_dir / "sce_sys" / "icon0.png")
    assert covers.find_embedded_cover(save_dir, "vita") == icon


def test_find_embedded_cover_sibling_for_raw_sav_file(tmp_path: Path):
    sav = tmp_path / "Pokemon Emerald.sav"
    sav.write_bytes(b"save")
    art = _write_image(tmp_path / "Pokemon Emerald.png")
    assert covers.find_embedded_cover(sav) == art


def test_find_embedded_cover_missing_returns_none(tmp_path: Path):
    save_dir = tmp_path / "empty"
    save_dir.mkdir()
    assert covers.find_embedded_cover(save_dir) is None
    assert covers.find_embedded_cover(tmp_path / "does_not_exist") is None
    assert covers.find_embedded_cover(None) is None
    # A raw save with no sibling artwork is still fine.
    lone = tmp_path / "lonely.sav"
    lone.write_bytes(b"x")
    assert covers.find_embedded_cover(lone) is None


def test_find_embedded_cover_is_read_only(tmp_path: Path):
    save_dir = tmp_path / "ULJM05800"
    save_dir.mkdir()
    icon = _write_image(save_dir / "ICON0.PNG")
    before = icon.stat().st_mtime_ns
    covers.find_embedded_cover(save_dir)
    assert icon.stat().st_mtime_ns == before


# --- user_cover_path --------------------------------------------------------


def test_user_cover_path_finds_title_id_file(tmp_path: Path):
    lib = tmp_path / "lib"
    cover = _write_image(lib / "covers" / "psp" / "ULJM05800.png")
    assert covers.user_cover_path(_entry(), lib) == cover


def test_user_cover_path_never_uses_game_key_colons(tmp_path: Path):
    # The sanitized name must not contain Windows-illegal characters.
    lib = tmp_path / "lib"
    entry = _entry(title_id=None, display_name="Half-Life: Alyx")
    cover = _write_image(lib / "covers" / "psp" / "Half-Life_ Alyx.png")
    found = covers.user_cover_path(entry, lib)
    assert found == cover
    assert ":" not in found.name


def test_user_cover_path_falls_back_to_display_name(tmp_path: Path):
    lib = tmp_path / "lib"
    cover = _write_image(lib / "covers" / "vita" / "Persona 4 Golden.jpg")
    entry = _entry(platform="vita", title_id=None, display_name="Persona 4 Golden")
    assert covers.user_cover_path(entry, lib) == cover


def test_user_cover_path_missing_returns_none(tmp_path: Path):
    lib = tmp_path / "lib"
    assert covers.user_cover_path(_entry(), lib) is None
    assert covers.user_cover_path(_entry(), None) is None
    assert covers.user_cover_path(None, lib) is None


def test_cover_helpers_never_raise_on_exotic_entry(tmp_path: Path):
    class Weird:
        @property
        def platform(self):
            raise RuntimeError("boom")

        @property
        def cover_path(self):
            raise RuntimeError("boom")

    weird = Weird()
    assert covers.user_cover_path(weird, tmp_path) is None
    assert covers.resolve_cover(weird, tmp_path) is None
    assert covers.find_embedded_cover(object()) is None


# --- resolve_cover ----------------------------------------------------------


def test_resolve_cover_priority_embedded_then_user_then_none(tmp_path: Path):
    save_dir = tmp_path / "ULJM05800"
    save_dir.mkdir()
    embedded = _write_image(save_dir / "ICON0.PNG")
    lib = tmp_path / "lib"
    user = _write_image(lib / "covers" / "psp" / "ULJM05800.png")

    entry = _entry(path=str(save_dir), cover_path=str(embedded))
    assert covers.resolve_cover(entry, lib) == embedded

    entry.cover_path = None
    assert covers.resolve_cover(entry, lib) == user

    (lib / "covers" / "psp" / "ULJM05800.png").unlink()
    assert covers.resolve_cover(entry, lib) is None
    assert covers.resolve_cover(None, lib) is None


def test_resolve_cover_ignores_stale_embedded_path(tmp_path: Path):
    lib = tmp_path / "lib"
    user = _write_image(lib / "covers" / "psp" / "ULJM05800.png")
    entry = _entry(cover_path=str(tmp_path / "gone.png"))
    assert covers.resolve_cover(entry, lib) == user


# --- load_thumbnail ---------------------------------------------------------


def test_load_thumbnail_exact_size_rgba_and_rounded(tmp_path: Path):
    source = _write_image(tmp_path / "art.png", size=(200, 100))
    thumb = covers.load_thumbnail(source, 60, 40, radius=10)
    assert thumb is not None
    assert thumb.size == (60, 40)
    assert thumb.mode == "RGBA"
    # Rounded corners are transparent, the centre stays opaque.
    assert thumb.getpixel((0, 0))[3] == 0
    assert thumb.getpixel((59, 0))[3] == 0
    assert thumb.getpixel((30, 20))[3] == 255


def test_load_thumbnail_preserves_aspect_ratio_by_cover_cropping(tmp_path: Path):
    # 200x100 image with a vertical marker at x=50 (25% from the left).
    image = PIL.new("RGBA", (200, 100), (255, 255, 255, 255))
    for y in range(100):
        image.putpixel((50, y), (0, 200, 0, 255))
    source = tmp_path / "marker.png"
    image.save(source)

    thumb = covers.load_thumbnail(source, 50, 50, radius=0)
    assert thumb.size == (50, 50)
    # Cover-fit scales uniformly (0.5) then centre-crops, so the marker lands on
    # the left edge. A naive stretch would have put it at x=12.
    assert thumb.getpixel((0, 25))[1] > 150
    assert thumb.getpixel((12, 25)) == (255, 255, 255, 255)


def test_load_thumbnail_never_raises_on_bad_input(tmp_path: Path):
    corrupt = tmp_path / "bad.png"
    corrupt.write_bytes(b"not an image at all")
    assert covers.load_thumbnail(corrupt, 32, 32) is None
    assert covers.load_thumbnail(tmp_path / "missing.png", 32, 32) is None
    assert covers.load_thumbnail(None, 32, 32) is None
    # Invalid target sizes degrade to None instead of raising.
    good = _write_image(tmp_path / "good.png")
    assert covers.load_thumbnail(good, 0, 32) is None
    assert covers.load_thumbnail(good, 32, -1) is None
    assert covers.load_thumbnail(good, "wide", 32) is None


# --- module hygiene ---------------------------------------------------------


def test_covers_module_does_not_import_tkinter():
    source = Path(covers.__file__).read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("import tkinter")
        assert not stripped.startswith("from tkinter")


def test_models_to_dict_includes_cover_only_when_set(tmp_path: Path):
    entry = _entry()
    assert "cover_path" not in entry.to_dict()
    entry.cover_path = "/tmp/icon0.png"
    assert entry.to_dict()["cover_path"] == "/tmp/icon0.png"
