"""Tests for the pure cover-resolution helpers in ``vajsave.covers``.

Everything here is display-free: covers never imports tkinter, never raises, and
is strictly read-only. The UI wraps the returned Pillow images itself.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
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
    # ``sce_sys`` is a depth-1 layout, so the default probe reaches it.
    assert covers.find_embedded_cover(save_dir) == icon
    assert covers.find_embedded_cover(save_dir, max_depth=1) == icon
    # A depth-0-only probe must not reach it.
    assert covers.find_embedded_cover(save_dir, max_depth=0) is None


def test_fixed_names_outrank_generic_scan_at_any_depth(tmp_path: Path):
    """A stray image in the save root must not shadow the official icon."""
    save_dir = tmp_path / "PCSE00120"
    (save_dir / "sce_sys").mkdir(parents=True)
    official = _write_image(save_dir / "sce_sys" / "icon0.png")
    _write_image(save_dir / "screenshot.png")
    assert covers.find_embedded_cover(save_dir) == official


def test_canonical_subdirs_outrank_alphabetical_order(tmp_path: Path):
    """``sce_sys`` (official Vita location) must beat ``icon/`` despite sorting later."""
    save_dir = tmp_path / "PCSE00120"
    (save_dir / "sce_sys").mkdir(parents=True)
    (save_dir / "icon").mkdir(parents=True)
    official = _write_image(save_dir / "sce_sys" / "icon0.png")
    _write_image(save_dir / "icon" / "icon0.png")
    assert covers.find_embedded_cover(save_dir) == official


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


def test_embedded_icon_names_cover_common_exports():
    names = list(covers.EMBEDDED_ICON_NAMES)
    name_set = set(names)
    # The design spec's ``EMBEDDED_COVER_NAMES`` name aliases the same list.
    assert covers.EMBEDDED_COVER_NAMES is covers.EMBEDDED_ICON_NAMES
    assert {"icon0.png", "icon0.jpg", "pic1.png", "pic1.jpg"} <= name_set
    assert any(name.startswith("thumb.") for name in names)
    assert any(name.startswith("preview.") for name in names)
    assert any(name.startswith("folder.") for name in names)
    # Every entry is a known stem + a supported image extension.
    for name in names:
        _, _, ext = name.rpartition(".")
        assert ext in covers.IMAGE_EXTENSIONS
    # Case-insensitive matching still resolves an upper-cased PSP icon.
    assert name_set == {name.lower() for name in names}


def test_find_embedded_cover_generic_scan_is_deterministic(tmp_path: Path):
    save_dir = tmp_path / "generic"
    save_dir.mkdir()
    first = _write_image(save_dir / "aaa.png")
    _write_image(save_dir / "bbb.png")
    (save_dir / "notes.txt").write_text("not an image")
    # No fixed name present -> generic scan, alphabetical for equal-rank names.
    assert covers.find_embedded_cover(save_dir) == first


def test_find_embedded_cover_generic_scan_prefers_name_hints(tmp_path: Path):
    save_dir = tmp_path / "hinted"
    save_dir.mkdir()
    _write_image(save_dir / "aaa.png")
    hinted = _write_image(save_dir / "zzz_cover_art.png")
    # ``cover``/``icon``/``box`` names win even though they sort later.
    assert covers.find_embedded_cover(save_dir) == hinted


def test_find_embedded_cover_generic_scan_requires_image_extension(tmp_path: Path):
    save_dir = tmp_path / "no_images"
    save_dir.mkdir()
    (save_dir / "cover.png.bak").write_bytes(b"x")
    (save_dir / "tile.bin").write_bytes(b"x")
    assert covers.find_embedded_cover(save_dir) is None


def test_find_embedded_cover_depth_one_scan(tmp_path: Path):
    save_dir = tmp_path / "sub"
    nested = save_dir / "media"
    nested.mkdir(parents=True)
    icon = _write_image(nested / "art.png")
    assert covers.find_embedded_cover(save_dir, max_depth=0) is None
    assert covers.find_embedded_cover(save_dir) == icon
    assert covers.find_embedded_cover(save_dir, max_depth=1) == icon


def test_find_embedded_cover_depth_one_skips_symlinked_dirs(tmp_path: Path):
    payload = tmp_path / "payload"
    payload.mkdir()
    _write_image(payload / "cover.png")
    save_dir = tmp_path / "save"
    save_dir.mkdir()
    try:
        (save_dir / "linked").symlink_to(payload, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")
    assert covers.find_embedded_cover(save_dir, max_depth=1) is None


def test_find_embedded_cover_skips_oversized_files(tmp_path: Path):
    save_dir = tmp_path / "huge"
    save_dir.mkdir()
    (save_dir / "icon0.png").write_bytes(b"\x00" * (covers.MAX_COVER_BYTES + 1))
    assert covers.find_embedded_cover(save_dir) is None
    # A small fallback still wins over the oversized fixed-name candidate.
    fallback = _write_image(save_dir / "art.png")
    assert covers.find_embedded_cover(save_dir) == fallback


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


def test_user_cover_path_skips_oversized_file(tmp_path: Path):
    lib = tmp_path / "lib"
    directory = lib / "covers" / "psp"
    directory.mkdir(parents=True)
    (directory / "ULJM05800.png").write_bytes(b"\x00" * (covers.MAX_COVER_BYTES + 1))
    assert covers.user_cover_path(_entry(), lib) is None


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


def test_resolve_cover_priority_user_then_downloaded_then_embedded(tmp_path: Path):
    from vajsave.artwork import CoverCache

    save_dir = tmp_path / "ULJM05800"
    save_dir.mkdir()
    embedded = _write_image(save_dir / "ICON0.PNG")
    lib = tmp_path / "lib"
    user = _write_image(lib / "covers" / "psp" / "ULJM05800.png")
    identity_key = "psp:ULJM05800"
    # The downloaded layer is manifest-backed, so register it through the one
    # real CoverCache implementation instead of dropping a bare file.
    downloaded = CoverCache(lib / "covers").store(
        "psp", identity_key, user.read_bytes()
    )
    assert downloaded is not None

    entry = _entry(path=str(save_dir), cover_path=str(embedded))
    # user file outranks downloaded and embedded.
    assert covers.resolve_cover(entry, lib, identity_key=identity_key) == user

    user.unlink()
    assert covers.resolve_cover(entry, lib, identity_key=identity_key) == downloaded

    # A vanished file is no longer a manifest hit.
    downloaded.unlink()
    assert covers.resolve_cover(entry, lib, identity_key=identity_key) == embedded

    # Even without a pre-recorded embedded path, the shared resolver discovers
    # the icon inside the save folder.
    entry.cover_path = None
    assert covers.resolve_cover(entry, lib, identity_key=identity_key) == embedded
    assert covers.resolve_cover(None, lib) is None


def test_resolve_cover_ignores_stale_embedded_path(tmp_path: Path):
    lib = tmp_path / "lib"
    user = _write_image(lib / "covers" / "psp" / "ULJM05800.png")
    entry = _entry(cover_path=str(tmp_path / "gone.png"))
    assert covers.resolve_cover(entry, lib) == user


# --- downloaded_cover_path ---------------------------------------------------


def test_downloaded_cover_path_matches_cover_cache_layout(tmp_path: Path):
    from vajsave.artwork import CoverCache

    lib = tmp_path / "lib"
    key = "psp:ULJM05800"
    expected = CoverCache.path_in(lib / "covers", "psp", key)
    assert expected is not None
    _write_image(expected)
    assert covers.downloaded_cover_path(lib, "psp", key) == expected
    assert covers.downloaded_cover_path(lib, "psp", None) is None
    assert covers.downloaded_cover_path(tmp_path / "missing", "psp", key) is None


def test_downloaded_cover_path_rejects_landscape_file(tmp_path: Path):
    from vajsave.artwork import CoverCache

    lib = tmp_path / "lib"
    key = "psp:landscape"
    expected = CoverCache.path_in(lib / "covers", "psp", key)
    assert expected is not None
    _write_image(expected, size=(144, 80))
    assert covers.downloaded_cover_path(lib, "psp", key) is None


def test_resolve_cover_downloaded_layer_needs_manifest(tmp_path: Path):
    # A bare file dropped into the cache directory is not a manifest hit, so the
    # shared resolver falls through to the embedded icon instead of trusting it.
    save_dir = tmp_path / "save"
    save_dir.mkdir()
    embedded = _write_image(save_dir / "ICON0.PNG")
    lib = tmp_path / "lib"
    key = "psp:ULJM05800"
    bare = lib / "covers" / "psp" / (covers.identity_hash(key) + ".png")
    _write_image(bare)
    entry = _entry(path=str(save_dir), cover_path=str(embedded))
    assert covers.resolve_cover(entry, lib, identity_key=key) == embedded


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


def test_load_thumbnail_skips_oversized_file(tmp_path: Path):
    big = tmp_path / "big.png"
    big.write_bytes(b"\x00" * (covers.MAX_COVER_BYTES + 1))
    assert covers.load_thumbnail(big, 32, 32) is None


def test_max_cover_bytes_is_eight_mib():
    assert covers.MAX_COVER_BYTES == 8 * 1024 * 1024


def test_load_thumbnail_bounds_target_dimensions(tmp_path: Path):
    good = _write_image(tmp_path / "ok.png", size=(64, 64))
    limit = covers.MAX_COVER_RESIZE_DIMENSION
    assert limit >= 104
    assert covers.load_thumbnail(good, limit, 66, radius=0) is not None
    # Targets above the explicit ceiling are refused (bounded scaling target).
    assert covers.load_thumbnail(good, limit + 1, 66) is None
    assert covers.load_thumbnail(good, 32, limit + 1) is None


def test_cover_fit_resize_target_is_bounded(tmp_path: Path, monkeypatch):
    """Deterministic proof that no aspect-inflated intermediate is allocated."""
    calls = []
    real_resize = PIL.Image.resize

    def spy(self, size, *args, **kwargs):
        calls.append(tuple(size))
        return real_resize(self, size, *args, **kwargs)

    monkeypatch.setattr(PIL.Image, "resize", spy)

    wide = tmp_path / "wide.png"
    PIL.new("RGB", (5000, 2), (1, 2, 3)).save(wide)
    tall = tmp_path / "tall.png"
    PIL.new("RGB", (200000, 1), (1, 2, 3)).save(tall)

    for source in (wide, tall):
        thumb = covers.load_thumbnail(source, 104, 66, radius=0)
        assert thumb is not None and thumb.size == (104, 66)
    assert calls  # the bounded resize actually ran
    # The pre-fix code asked Pillow for a 165000x66 / 13200000x66 buffer here.
    assert all(0 < w <= 104 and 0 < h <= 66 for (w, h) in calls)


def test_cover_fit_fills_tile_for_common_ratios(tmp_path: Path):
    for size in ((400, 300), (320, 180)):  # 4:3 and 16:9
        source = tmp_path / f"{size[0]}x{size[1]}.png"
        PIL.new("RGB", size, (200, 30, 30)).save(source)
        thumb = covers.load_thumbnail(source, 104, 66, radius=0)
        assert thumb is not None and thumb.size == (104, 66)
        # The cover fit fills the whole tile (no transparent letterbox bars).
        assert thumb.getpixel((0, 0))[3] == 255
        assert thumb.getpixel((103, 65))[3] == 255
        assert thumb.getpixel((52, 33))[3] == 255


# --- RSS regression (real memory, not tracemalloc) --------------------------

# Run the probe in a child process so ``ru_maxrss`` starts from a clean slate.
# PIL is imported *before* the baseline is captured so the lazy import inside
# ``load_thumbnail`` is not charged to the decode resize.
_RSS_PROBE = r'''
import json, os, resource, sys, tempfile, time
from PIL import Image  # noqa: F401 - pre-import so the lazy import is not timed
from vajsave import covers

path, out = sys.argv[1], sys.argv[2]
width, height = int(sys.argv[3]), int(sys.argv[4])

def rss_bytes():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux/KB; normalise to bytes.
    return int(value) if sys.platform == "darwin" else int(value) * 1024

# Warm up the PNG codec / Pillow internals on a tiny image so the measured
# call reflects the decode+resize of the target only (the warm-up allocates
# almost nothing, so the pathological peak is still fully attributed below).
fd, tiny = tempfile.mkstemp(suffix=".png")
os.close(fd)
Image.new("RGB", (2, 2), (0, 0, 0)).save(tiny)
covers.load_thumbnail(tiny, width, height, radius=0)
os.unlink(tiny)

before = rss_bytes()
start = time.perf_counter()
image = covers.load_thumbnail(path, width, height, radius=0)
elapsed = (time.perf_counter() - start) * 1000.0
peak = rss_bytes()
with open(out, "w", encoding="utf-8") as handle:
    json.dump({"delta_mb": (peak - before) / 1024 / 1024, "ms": elapsed,
               "ok": image is not None,
               "size": list(image.size) if image is not None else None}, handle)
'''


@pytest.mark.skipif(sys.platform == "win32", reason="resource.getrusage is POSIX-only")
@pytest.mark.parametrize(
    "shape, max_mb, max_ms",
    [
        ((5000, 2), 8.0, 50.0),      # pre-fix: ~95-98 MB / ~300 ms
        ((200000, 1), 50.0, 200.0),  # pre-fix: ~3.9 GB / ~20-31 s
    ],
)
def test_load_thumbnail_pathological_aspect_is_bounded(tmp_path: Path, shape, max_mb, max_ms):
    pytest.importorskip("resource")
    source = tmp_path / f"{shape[0]}x{shape[1]}.png"
    PIL.new("RGB", shape, (10, 20, 30)).save(source)
    assert source.stat().st_size < covers.MAX_COVER_BYTES

    out = tmp_path / "rss.json"
    env = dict(os.environ)
    src_root = str(Path(covers.__file__).resolve().parents[1])
    env["PYTHONPATH"] = src_root + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", _RSS_PROBE, str(source), str(out), "104", "66"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert data["size"] == [104, 66]
    assert data["delta_mb"] < max_mb, data
    assert data["ms"] < max_ms, data


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
