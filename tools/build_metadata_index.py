#!/usr/bin/env python3
"""Build the bundled offline metadata index from real No-Intro/libretro DATs.

The app ships a *compact* index (``vajsave/data/libretro/<platform>.json``) so a
fresh install can resolve GBA/NDS ROM hashes to canonical titles without any
network access.  This tool is the only thing that writes that index: the app
never parses a multi-megabyte DAT at runtime, and the generator never runs on a
user machine.

Two on-disk DAT dialects are supported, auto-detected from the first
non-whitespace character:

* **Logiqx XML** -- the No-Intro DAT-o-MATIC ``.dat`` format
  (``<?xml``/``<datafile>`` root, ``<header>`` + ``<game>/<rom>``).  This is the
  format No-Intro actually publishes; its ``<clrmamepro/>`` header element is
  honoured.
* **ClrMamePro text** -- the classic ``clrmamepro ( ... )`` / ``game ( ... )``
  DAT dialect.

The output is deliberately *data*, not code: a JSON object with a list of
records, deterministically ordered, so regenerating from the same DATs produces
byte-identical bytes (no timestamps in the index itself).

Usage::

    python tools/build_metadata_index.py \
        --platform gba \
        --dat "Nintendo - Game Boy Advance (20260707-143610).dat" \
        --out src/vajsave/data/libretro/gba.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

# Bump when the compact on-disk schema changes in a backwards-incompatible way.
FORMAT_NAME = "vajsave.libretro-index"
PROVENANCE_FORMAT_NAME = "vajsave.libretro-provenance"
FORMAT_VERSION = 1

# Platforms the bundled index covers.  Everything else is identified by title id
# rather than ROM digest and is intentionally out of scope here.
SUPPORTED_PLATFORMS: Tuple[str, ...] = ("gba", "nds", "gb", "gbc")

# Substrings (lower-case) found in a DAT header/filename that map to a platform.
_PLATFORM_MARKERS: Tuple[Tuple[str, str], ...] = (
    ("game boy advance", "gba"),
    ("gameboy advance", "gba"),
    ("game boy color", "gbc"),
    ("gameboy color", "gbc"),
    ("nintendo ds", "nds"),
    ("nintendo - ds", "nds"),
    ("nintendo-ds", "nds"),
    ("game boy", "gb"),
    ("gameboy", "gb"),
)


class DatParseError(ValueError):
    """Raised when a DAT is malformed beyond recovery."""


# --------------------------------------------------------------------------- #
# CLI arg types
# --------------------------------------------------------------------------- #

def _existing_file(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"not a file: {value}")
    return path


def _existing_dir(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"not a directory: {value}")
    return path


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def detect_platform(*names: Optional[str]) -> Optional[str]:
    """Best-effort platform from DAT header/filename markers, or ``None``."""
    for name in names:
        if not name:
            continue
        haystack = str(name).strip().lower()
        for marker, platform in _PLATFORM_MARKERS:
            if marker in haystack:
                return platform
    return None


def normalize_hex(value: Optional[str], width: int) -> str:
    """Lower-case, separator-free, zero-padded hex, or ``""`` when unusable."""
    if value is None:
        return ""
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if not text:
        return ""
    try:
        number = int(text, 16)
    except ValueError:
        return ""
    if number < 0:
        return ""
    return format(number, "0{}x".format(width))


# --------------------------------------------------------------------------- #
# Logiqx XML parser
# --------------------------------------------------------------------------- #

_HEADER_TEXT_TAGS = ("name", "description", "version", "id", "author", "homepage", "url")


def _local_name(tag: object) -> str:
    text = str(tag)
    if "}" in text:  # strip an XML namespace
        text = text.rsplit("}", 1)[1]
    return text


def parse_logiqx(xml_text: str) -> Tuple[Dict[str, str], List[Dict[str, object]]]:
    """Parse a Logiqx datafile into ``(header, games)``.

    ``games`` is a list of ``{"name", "id", "cloneofid", "roms": [attrs, ...]}``.
    The DAT No-Intro publishes is Logiqx XML with a ``<clrmamepro/>`` header
    element; that dialect and plain Logiqx files both parse here.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise DatParseError(f"invalid XML datafile: {exc}") from exc
    header: Dict[str, str] = {}
    games: List[Dict[str, object]] = []
    header_el = None
    for child in root:
        local = _local_name(child.tag)
        if local == "header":
            header_el = child
        elif local in ("game", "machine"):
            games.append(_xml_game(child))
    if header_el is not None:
        for child in header_el:
            local = _local_name(child.tag)
            if local in _HEADER_TEXT_TAGS and child.text:
                header.setdefault(local, child.text.strip())
    return header, games


def _xml_game(element) -> Dict[str, object]:
    roms: List[Dict[str, object]] = []
    for child in element:
        if _local_name(child.tag) != "rom":
            continue
        attrs: Dict[str, object] = dict(child.attrib)
        for grandchild in child:
            if _local_name(grandchild.tag) in ("crc", "sha1", "md5", "sha256", "serial") and grandchild.text:
                attrs.setdefault(_local_name(grandchild.tag), grandchild.text.strip())
        roms.append(attrs)
    return {
        "name": element.get("name", ""),
        "id": element.get("id", ""),
        "cloneofid": element.get("cloneofid", ""),
        "roms": roms,
    }


# --------------------------------------------------------------------------- #
# ClrMamePro text parser
# --------------------------------------------------------------------------- #

_TOKEN_RE = re.compile(
    r"""
      (?P<string>"(?:\\.|[^"\\])*")     # quoted string
    | (?P<lparen>\()
    | (?P<rparen>\))
    | (?P<ident>[^\s()"]+)
    """,
    re.VERBOSE,
)


def _tokenize_clrmamepro(text: str) -> Iterator[Tuple[str, str]]:
    for match in _TOKEN_RE.finditer(text):
        if match.group("string") is not None:
            raw = match.group("string")[1:-1]
            raw = raw.replace('\\"', '"').replace("\\\\", "\\")
            yield "string", raw
        elif match.group("lparen") is not None:
            yield "lparen", "("
        elif match.group("rparen") is not None:
            yield "rparen", ")"
        else:
            yield "ident", match.group("ident")


def parse_clrmamepro(text: str) -> Tuple[Dict[str, str], List[Dict[str, object]]]:
    """Parse a ClrMamePro text DAT into ``(header, games)``."""
    tokens = list(_tokenize_clrmamepro(text))
    header: Dict[str, str] = {}
    games: List[Dict[str, object]] = []
    i = 0
    n = len(tokens)
    while i < n:
        kind, value = tokens[i]
        if kind == "ident" and value in ("clrmamepro", "header") and i + 1 < n and tokens[i + 1][0] == "lparen":
            block, i = _parse_cmp_block(tokens, i + 1)
            header.update({k: v for k, v in block.items() if isinstance(v, str)})
            continue
        if kind == "ident" and value == "game" and i + 1 < n and tokens[i + 1][0] == "lparen":
            block, i = _parse_cmp_block(tokens, i + 1)
            roms = block.get("rom")
            if isinstance(roms, dict):
                rom_list: List[Dict[str, object]] = [roms]
            elif isinstance(roms, list):
                rom_list = [r for r in roms if isinstance(r, dict)]
            else:
                rom_list = []
            games.append(
                {
                    "name": block.get("name", "") if isinstance(block.get("name"), str) else "",
                    "id": "",
                    "cloneofid": block.get("cloneofid", "") if isinstance(block.get("cloneofid"), str) else "",
                    "roms": rom_list,
                }
            )
            continue
        i += 1
    return header, games


def _parse_cmp_block(tokens: Sequence[Tuple[str, str]], start: int):
    """Parse ``( key value ... ( ... ) )`` starting at the ``(`` token index."""
    assert tokens[start][0] == "lparen"
    result: Dict[str, object] = {}
    i = start + 1
    n = len(tokens)
    last_key: Optional[str] = None
    while i < n:
        kind, value = tokens[i]
        if kind == "rparen":
            return result, i + 1
        if kind == "lparen":
            if last_key is None:
                # Anonymous sub-block: skip it defensively.
                _, i = _parse_cmp_block(tokens, i)
                continue
            sub, i = _parse_cmp_block(tokens, i)
            existing = result.get(last_key)
            if existing is None:
                result[last_key] = sub
            elif isinstance(existing, list):
                existing.append(sub)
            else:
                result[last_key] = [existing, sub]
            last_key = None
            continue
        # ``ident`` or ``string`` in key/value position.
        if last_key is None:
            last_key = value
        else:
            existing = result.get(last_key)
            if existing is None:
                result[last_key] = value
            elif isinstance(existing, list):
                existing.append(value)
            else:
                result[last_key] = [existing, value]
            last_key = None
        i += 1
    return result, i


# --------------------------------------------------------------------------- #
# Index building
# --------------------------------------------------------------------------- #

def parse_dat(path: Path) -> Tuple[Dict[str, str], List[Dict[str, object]]]:
    """Parse ``path`` in either supported dialect."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:  # pragma: no cover - surfaced to the CLI
        raise DatParseError(f"cannot read {path}: {exc}") from exc
    stripped = text.lstrip("\ufeff \t\r\n")
    if stripped.startswith("<?xml") or stripped.startswith("<datafile") or stripped.startswith("<"):
        if "<datafile" not in stripped[:4096] and "<clrmamepro" not in stripped[:4096]:
            # A stray XML file that is not a DAT.
            raise DatParseError(f"{path}: not a Logiqx datafile")
        return parse_logiqx(stripped)
    if "clrmamepro" in stripped[:4096] or "game" in stripped[:4096]:
        return parse_clrmamepro(stripped)
    raise DatParseError(f"{path}: unrecognised DAT dialect")


def _rom_record(rom: Dict[str, object]) -> Optional[List[str]]:
    sha1 = normalize_hex(str(rom.get("sha1", "")), 40)
    crc = normalize_hex(str(rom.get("crc", "")), 8)
    if not sha1 and not crc:
        return None
    serial = str(rom.get("serial", "") or "").strip()
    return [sha1, crc, serial]


def build_records(
    dat_paths: Iterable[Path],
    *,
    platform: Optional[str] = None,
) -> Tuple[str, List[Dict[str, object]], List[List[str]]]:
    """Return ``(platform, sources, records)`` for the given DAT files.

    ``records`` rows are ``[sha1, crc, name, serial, nointro_id]`` sorted by
    ``(sha1, crc, name)`` so the output is deterministic.
    """
    resolved = (platform or "").strip().lower() or None
    sources: List[Dict[str, object]] = []
    # sha1 -> row; crc -> row (dedup, deterministic last-wins only among equals)
    by_sha1: Dict[str, List[str]] = {}
    by_crc: Dict[str, List[str]] = {}
    seen_raw = set()
    for path in sorted((Path(p) for p in dat_paths), key=lambda p: str(p)):
        header, games = parse_dat(path)
        name = header.get("name") or header.get("description") or path.stem
        api_platform = detect_platform(name, path.name)
        if resolved is None:
            resolved = api_platform
        if resolved is None:
            raise DatParseError(f"cannot determine platform for {path}; pass --platform")
        if api_platform is not None and api_platform != resolved:
            # A DAT for another platform would only poison the index.
            raise DatParseError(
                f"{path}: header platform {api_platform!r} does not match {resolved!r}"
            )
        sources.append(
            {
                "name": name,
                "version": header.get("version", ""),
                "sha1": hashlib.sha1(path.read_bytes()).hexdigest(),
                "url": "https://datomatic.no-intro.org/",
            }
        )
        key = str(path.resolve())
        if key in seen_raw:
            continue
        seen_raw.add(key)
        for game in games:
            game_name = str(game.get("name", "") or "").strip()
            if not game_name:
                continue
            nointro_id = str(game.get("id", "") or "").strip()
            for rom in game.get("roms", []):  # type: ignore[union-attr]
                record = _rom_record(rom)
                if record is None:
                    continue
                sha1, crc, serial = record
                row = [sha1, crc, game_name, serial, nointro_id]
                if sha1:
                    by_sha1[sha1] = row
                if crc:
                    by_crc[crc] = row
    if resolved not in SUPPORTED_PLATFORMS:
        raise DatParseError(f"unsupported platform: {resolved!r}")
    merged: Dict[str, List[str]] = {}
    for row in by_sha1.values():
        merged[row[0]] = row
    for row in by_crc.values():
        # Only add CRC-only rows (no sha1) so a real SHA-1 record is never
        # displaced by a CRC alias of a different revision.
        if not row[0]:
            merged["crc:" + row[1]] = row
    records = sorted(merged.values(), key=lambda r: (r[0], r[1], r[2]))
    sources.sort(key=lambda s: (str(s.get("name", "")), str(s.get("version", ""))))
    return resolved, sources, records


def build_index(
    dat_paths: Iterable[Path],
    *,
    platform: Optional[str] = None,
) -> Dict[str, object]:
    resolved, sources, records = build_records(dat_paths, platform=platform)
    return {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "platform": resolved,
        "sources": sources,
        "records": records,
    }


def write_index(index: Dict[str, object], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(index, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    out_path.write_text(payload + "\n", encoding="utf-8")


def write_provenance(indexes: Sequence[Dict[str, object]], out_path: Path) -> None:
    provenance = {
        "generator": "tools/build_metadata_index.py",
        "format": PROVENANCE_FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "license": {
            "name": "No-Intro DAT-o-MATIC datafiles",
            "homepage": "https://www.no-intro.org",
            "datomatic": "https://datomatic.no-intro.org",
            "terms": "https://datomatic.no-intro.org/stuff/terms.txt",
            "notice": (
                "No-Intro datafiles are maintained by the No-Intro project and "
                "distributed under the terms published at DAT-o-MATIC. Product "
                "names are trademarks of their respective owners; No-Intro is not "
                "affiliated with them. This package redistributes only the "
                "derived hash/title index, never ROM images."
            ),
        },
        "indexes": [
            {"platform": index["platform"], "sources": index["sources"]}
            for index in sorted(indexes, key=lambda i: str(i["platform"]))
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _collect(dat_paths: Sequence[Path], dat_dirs: Sequence[Path]) -> List[Path]:
    files: List[Path] = list(dat_paths)
    for directory in dat_dirs:
        files.extend(sorted(directory.glob("*.dat")))
        files.extend(sorted(directory.glob("*.xml")))
    # De-duplicate while keeping a stable order.
    out: List[Path] = []
    seen = set()
    for path in files:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dat", action="append", default=[], type=_existing_file, help="a .dat/.xml file (repeatable)")
    parser.add_argument("--dat-dir", action="append", default=[], type=_existing_dir, help="a directory of DATs (repeatable)")
    parser.add_argument("--platform", choices=SUPPORTED_PLATFORMS, default=None)
    parser.add_argument("--out", required=True, type=Path, help="compact index output path")
    parser.add_argument("--provenance", type=Path, default=None, help="optional provenance JSON output path")
    args = parser.parse_args(argv)

    files = _collect(args.dat, args.dat_dir)
    if not files:
        parser.error("no DAT files given (use --dat or --dat-dir)")
    index = build_index(files, platform=args.platform)
    write_index(index, args.out)
    if args.provenance is not None:
        write_provenance([index], args.provenance)
    print(
        f"wrote {args.out} ({index['platform']}, {len(index['records'])} records)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
