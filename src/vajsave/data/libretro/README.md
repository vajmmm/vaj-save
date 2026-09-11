# Bundled libretro / No-Intro metadata index

This directory ships the compact offline index the app uses to turn a GBA/NDS
ROM digest into a canonical No-Intro title:

| file            | contents                                                    |
| --------------- | ----------------------------------------------------------- |
| `gba.json`      | compact index for `gba` (Nintendo - Game Boy Advance)       |
| `nds.json`      | compact index for `nds` (Nintendo - Nintendo DS)            |
| `provenance.json` | source DAT names/versions/SHA-1 and license summary       |
| `TERMS-No-Intro.txt` | verbatim No-Intro DAT-o-MATIC terms of use             |

Indexes are generated **offline** from the real No-Intro DAT files with
`tools/build_metadata_index.py`; the app never parses a raw multi-megabyte DAT
at runtime. See `PROVENANCE` in `provenance.json` and the top-level `README.md`
for the exact regeneration command.

## Compact index schema (format version 1)

```json
{
  "format": "vajsave.libretro-index",
  "format_version": 1,
  "platform": "gba",
  "sources": [
    {"name": "Nintendo - Game Boy Advance", "version": "...", "sha1": "...", "url": "https://datomatic.no-intro.org/"}
  ],
  "records": [
    ["<sha1>", "<crc32>", "<canonical title>", "<serial>", "<no-intro id>"]
  ]
}
```

* `records` is sorted by `(sha1, crc32, title)`, so regenerating from the same
  DATs is byte-for-byte reproducible.
* Only the derived title/hash index is redistributed -- never a ROM image.

## User-supplied DATs

A user can still point the app at real `.dat`/`.xml` files (settings -> libretro
directory). Search order (first match wins for a digest):

1. `libretro_dir` from the app config;
2. `<library_root>/metadata/libretro/`;
3. this bundled directory.
