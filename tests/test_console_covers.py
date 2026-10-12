"""PS3 box-art download stays on the same cover path as the handhelds."""

from __future__ import annotations

import base64
from pathlib import Path

from vajsave.app_state import AppState
from vajsave.models import SaveEntry

# 8x16 red PNG. Portrait, so the cover cache accepts it. Kept as bytes so this
# test does not need Pillow's encoder (that crashes in some Qt-loaded processes).
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAQCAIAAACk6KkqAAAAFklEQVR4nGMU0QhgwAaY"
    "sIqOStBYAgDz/ACsmvXwQQAAAABJRU5ErkJggg=="
)


def test_ps3_cover_downloads_by_serial(tmp_path: Path):
    state = AppState(library_root=tmp_path / "library")
    entry = SaveEntry(
        platform="ps3",
        source_id="ps3",
        display_name="Demon's Souls",
        path=str(tmp_path / "save"),
        title_id="BLUS30123",
    )
    wanted = "https://art.gametdb.com/ps3/cover/US/BLUS30123.jpg"

    def fetch(url):
        return _PNG if url == wanted else None

    state.artwork_service.downloader.fetch = fetch
    result = state.resolve_save_identity(entry)
    cover = state.ensure_save_cover(entry, result)
    assert cover.source == "downloaded"
    assert cover.path
    assert Path(cover.path).is_file()
