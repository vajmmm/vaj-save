"""``Named_Boxarts`` directory listing fallback and Checkpoint DS routing.

When every generated candidate filename 404s, the official libretro server's
directory index already lists the real file name.  The contract is:

* parse the Apache autoindex page into decoded ``*.png`` leaf names;
* resolve several region/language variants of one normalised title to the USA
  release, while two genuinely different titles stay unresolvable;
* route a 3DS Checkpoint entry that has **no** 3DS title id and a display name
  shaped like a real DS cartridge (``XXXX Title``) through the NDS system folder.
"""

from __future__ import annotations

import urllib.error

from vajsave.artwork.boxart_index import (
    ambiguous_boxart_matches,
    fetch_boxart_listing,
    normalize_boxart_name,
    parse_boxart_listing,
    resolve_boxart_system,
    strip_ds_cartridge_code,
    unique_boxart_match,
)

_LISTING_HTML = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
<html><head><title>Index of /Nintendo - Nintendo 3DS/Named_Boxarts</title></head>
<body><h1>Index of /Nintendo - Nintendo 3DS/Named_Boxarts</h1>
<table>
<tr><th><a href="?C=N;O=D">Name</a></th></tr>
<tr><td><a href="/Nintendo%20-%20Nintendo%203DS/">Parent Directory</a></td></tr>
<tr><td><a href="Kirby%20Super%20Star%20Ultra%20(USA).png">Kirby Super Star Ultra (USA).png</a></td></tr>
<tr><td><a href="2in1%20-%20Life%20with%20Horses%203D%20+%20My%20Baby%20Pet%20Hotel%203D%20(Europe)%20(En,Fr,De,Es,It,Nl).png">2in1 - Life with Horses 3D + My Baby Pet Hotel 3D (Europe) (En,Fr,De,Es,It,Nl).png</a></td></tr>
<tr><td><a href="Kirby%20Super%20Star%20Ultra%20(USA).png">Kirby Super Star Ultra (USA).png</a></td></tr>
<tr><td><a href="notes.txt">notes.txt</a></td></tr>
</table></body></html>
"""


class FakeResponse:
    def __init__(self, data=b"", status=200):
        self._data = data
        self.status = status
        self.headers = {}
        self.closed = False

    def read(self, n=-1):
        if n is None or n < 0:
            return self._data
        return self._data[:n]

    def close(self):
        self.closed = True


# --- listing parsing ---------------------------------------------------------


def test_parse_boxart_listing_decodes_filters_and_dedupes():
    names = parse_boxart_listing(_LISTING_HTML)
    assert "Kirby Super Star Ultra (USA).png" in names
    assert (
        "2in1 - Life with Horses 3D + My Baby Pet Hotel 3D (Europe) (En,Fr,De,Es,It,Nl).png"
        in names
    )
    assert names.count("Kirby Super Star Ultra (USA).png") == 1
    assert all(name.lower().endswith(".png") for name in names)
    assert not any("Parent" in name for name in names)
    assert "notes.txt" not in names
    assert "?C=N;O=D" not in names


def test_parse_boxart_listing_handles_bytes_and_junk():
    assert parse_boxart_listing(b'<a href="Game.png">Game.png</a>') == ("Game.png",)
    assert parse_boxart_listing("") == ()
    assert parse_boxart_listing("<html>no links</html>") == ()


# --- name normalisation + unique match --------------------------------------


def test_normalize_boxart_name_folds_case_diacritics_and_punctuation():
    assert normalize_boxart_name("Pokémon - FireRed: Version") == normalize_boxart_name(
        "pokemon firered version"
    )
    assert normalize_boxart_name("Kid  Icarus - Uprising") == "kid icarus uprising"
    assert normalize_boxart_name("") == ""
    assert normalize_boxart_name(None) == ""


def test_unique_boxart_match_accepts_only_unambiguous_names():
    names = (
        "Mario Kart 7 (USA).png",
        "Mario Kart 7 (Europe).png",
        "Kirby Super Star Ultra (USA).png",
        "Super Mario 3D Land (USA).png",
    )
    assert (
        unique_boxart_match(names, "Kirby Super Star Ultra")
        == "Kirby Super Star Ultra (USA).png"
    )
    # Case/punctuation/diacritics differences still match.
    assert (
        unique_boxart_match(names, "super mario 3d land")
        == "Super Mario 3D Land (USA).png"
    )
    # Two region variants of the same base title prefer the USA release.
    assert unique_boxart_match(names, "Mario Kart 7") == "Mario Kart 7 (USA).png"
    # Nothing close -> no match.
    assert unique_boxart_match(names, "Metroid") is None
    # A mid-title fragment that appears in exactly one entry still resolves.
    assert unique_boxart_match(names, "Star Ultra") == "Kirby Super Star Ultra (USA).png"
    assert unique_boxart_match(names, "Ultra") == "Kirby Super Star Ultra (USA).png"
    # But a fragment shared by two entries is ambiguous again.
    assert unique_boxart_match(names, "Super") is None
    assert unique_boxart_match((), "Anything") is None
    assert unique_boxart_match(names, "") is None


def test_unique_boxart_match_prefers_usa_among_same_title_regions():
    """Several region variants of one normalised title resolve to the USA
    release instead of being refused as ambiguous."""
    names = (
        "Mario Kart 7 (Japan).png",
        "Mario Kart 7 (Europe).png",
        "Mario Kart 7 (USA).png",
    )
    assert unique_boxart_match(names, "Mario Kart 7") == "Mario Kart 7 (USA).png"
    # Case/punctuation differences still resolve to the same USA variant.
    assert unique_boxart_match(names, "mario kart 7") == "Mario Kart 7 (USA).png"
    # A query that only names the region-free base still picks USA.
    assert unique_boxart_match(names, "Mario Kart") == "Mario Kart 7 (USA).png"


def test_ambiguous_boxart_matches_only_for_genuinely_conflicting_titles():
    """Region variants of one title are resolved deterministically (USA) and
    must *not* be handed to the optional LLM; only two genuinely different
    titles matching one query are ambiguous candidates."""
    names = (
        "Mario Kart 7 (USA).png",
        "Mario Kart 7 (Europe).png",
        "Mario Party (USA).png",
        "Kirby Super Star Ultra (USA).png",
    )
    assert ambiguous_boxart_matches(names, "Mario Kart 7") == ()
    assert ambiguous_boxart_matches(names, "Mario") == (
        "Mario Kart 7 (USA).png",
        "Mario Kart 7 (Europe).png",
        "Mario Party (USA).png",
    )
    # A unique match, no match at all, or an empty listing is never ambiguous.
    assert ambiguous_boxart_matches(names, "Kirby Super Star Ultra") == ()
    assert ambiguous_boxart_matches(names, "Metroid") == ()
    assert ambiguous_boxart_matches((), "Mario") == ()
    assert ambiguous_boxart_matches(names, "") == ()


def test_unique_boxart_match_conflicting_games_stay_unresolved():
    """Two *different* normalised titles matching one query remain ambiguous:
    the region preference must never turn a genuine game conflict into a pick."""
    names = (
        "Mario Kart 7 (USA).png",
        "Mario Party (USA).png",
    )
    assert unique_boxart_match(names, "Mario") is None
    assert unique_boxart_match(names, "Mario Kart") == "Mario Kart 7 (USA).png"
    assert unique_boxart_match(names, "Mario Party") == "Mario Party (USA).png"


# --- listing fetch -----------------------------------------------------------


def test_fetch_boxart_listing_reads_and_closes():
    response = FakeResponse(_LISTING_HTML.encode("utf-8"))
    names = fetch_boxart_listing(lambda url, timeout=None: response, "https://x/listing/")
    assert "Kirby Super Star Ultra (USA).png" in names
    assert response.closed is True


def test_fetch_boxart_listing_degrades_on_failure():
    def raising(exc):
        def opener(url, timeout=None):
            raise exc
        return opener

    for exc in (
        urllib.error.HTTPError("https://x", 404, "Not Found", {}, None),
        urllib.error.URLError("offline"),
        TimeoutError("timed out"),
        OSError("boom"),
    ):
        assert fetch_boxart_listing(raising(exc), "https://x/listing/") == ()

    not_found = FakeResponse(b"nope", status=404)
    assert fetch_boxart_listing(lambda url, timeout=None: not_found, "https://x/") == ()

    empty = FakeResponse(b"")
    assert fetch_boxart_listing(lambda url, timeout=None: empty, "https://x/") == ()

    assert fetch_boxart_listing(lambda url, timeout=None: FakeResponse(), None) == ()


# --- Checkpoint DS routing ---------------------------------------------------


def test_strip_ds_cartridge_code_only_matches_uppercase_code_prefix():
    assert (
        strip_ds_cartridge_code("AZEJ Kirby Super Star Ultra")
        == "Kirby Super Star Ultra"
    )
    assert strip_ds_cartridge_code("AZEJ - Kirby Super Star Ultra") == (
        "Kirby Super Star Ultra"
    )
    # A normal mixed-case 3DS title must not be mistaken for a cartridge code.
    assert strip_ds_cartridge_code("Cube Creator 3D") is None
    assert strip_ds_cartridge_code("Persona Q2 New Cinema Labyrinth") is None
    assert strip_ds_cartridge_code("AZEJ") is None
    assert strip_ds_cartridge_code("") is None


def test_strip_ds_cartridge_code_rejects_plain_title_word_prefix():
    """The 3DS title ``Nano Assault`` starts with a 4-letter word, not a DS
    cartridge code; the rule must not mistake it for one."""
    assert strip_ds_cartridge_code("NANO Assault") is None
    assert strip_ds_cartridge_code("NANO - Assault") is None
    # A real DS serial (Pokemon HeartGold, Japan) still resolves.
    assert strip_ds_cartridge_code("IPKJ POKEMON HG") == "POKEMON HG"


def test_resolve_boxart_system_routes_ds_cartridge_to_nds():
    assert resolve_boxart_system("3ds", "AZEJ Kirby Super Star Ultra", None) == (
        "nds",
        "Kirby Super Star Ultra",
    )
    # Case-insensitive platform and a stripped title both survive.
    assert resolve_boxart_system("3DS", "AZEJ Kirby Super Star Ultra", "") == (
        "nds",
        "Kirby Super Star Ultra",
    )
    # A real DS serial (IPKJ, Pokemon HeartGold Japan) routes to NDS.
    assert resolve_boxart_system("3ds", "IPKJ POKEMON HG", None) == (
        "nds",
        "POKEMON HG",
    )


def test_resolve_boxart_system_does_not_reroute_plain_word_title():
    # ``NANO`` is the start of the 3DS title ``Nano Assault``, not a cartridge
    # code, so it must stay on the 3DS system folder.
    assert resolve_boxart_system("3ds", "NANO Assault", None) == (
        "3ds",
        "NANO Assault",
    )


def test_resolve_boxart_system_keeps_3ds_when_a_title_id_exists():
    # A Checkpoint short id expands to a 3DS Title ID -> never NDS.
    assert resolve_boxart_system("3ds", "MARIO KART 7", "0x00306") == (
        "3ds",
        "MARIO KART 7",
    )
    assert resolve_boxart_system("3ds", "MARIO KART 7", "0004000000030600") == (
        "3ds",
        "MARIO KART 7",
    )
    # A non-3DS platform is never rerouted.
    assert resolve_boxart_system("psp", "AZEJ Thing", None) == ("psp", "AZEJ Thing") 
    # A plain mixed-case 3DS name without a title id stays on 3DS.
    assert resolve_boxart_system("3ds", "Persona Q2", None) == ("3ds", "Persona Q2")


def test_known_ds_serials_loader_degrades_without_index(tmp_path, monkeypatch):
    """A missing/corrupt/short NDS index must not break DS routing: the
    region-marker rule still rejects a bare word and accepts a real code."""
    from vajsave.artwork import boxart_index

    monkeypatch.setattr(boxart_index, "bundled_libretro_dir", lambda: tmp_path)
    boxart_index._known_nds_serials.cache_clear()
    try:
        # Missing file.
        assert boxart_index._known_nds_serials() == frozenset()
        assert boxart_index.strip_ds_cartridge_code("NANO Assault") is None
        assert boxart_index.strip_ds_cartridge_code("IPKJ POKEMON HG") == "POKEMON HG"

        # Corrupt JSON.
        (tmp_path / "nds.json").write_text("{not json", encoding="utf-8")
        boxart_index._known_nds_serials.cache_clear()
        assert boxart_index._known_nds_serials() == frozenset()

        # Well-formed JSON but the wrong shape, then a real serial list.
        (tmp_path / "nds.json").write_text('{"records": "nope"}', encoding="utf-8")
        boxart_index._known_nds_serials.cache_clear()
        assert boxart_index._known_nds_serials() == frozenset()

        (tmp_path / "nds.json").write_text(
            '{"records": [null, "x", ["a", "b", "c"], ["a", "b", "c", "IRBO"]]}',
            encoding="utf-8",
        )
        boxart_index._known_nds_serials.cache_clear()
        assert boxart_index._known_nds_serials() == frozenset({"IRBO"})
        # An NDSi serial ending in ``O`` is recognised only through the index.
        assert boxart_index.strip_ds_cartridge_code("IRBO Pokemon Black") == (
            "Pokemon Black"
        )
    finally:
        boxart_index._known_nds_serials.cache_clear()
