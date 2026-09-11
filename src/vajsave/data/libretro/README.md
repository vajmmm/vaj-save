# Libretro metadata indexes

Drop libretro / No-Intro `Logiqx` XML datafiles (`.dat` or `.xml`) here, or in a
directory configured through the app config key `libretro_dir`, or in
`<library_root>/metadata/libretro/`.

Search order (first match wins for a given ROM digest):

1. `libretro_dir` from the app config;
2. `<library_root>/metadata/libretro/`;
3. this folder.

The parser indexes only the cartridge platforms identified by ROM digest
(GBA/NDS). Relevant datafiles include:

- `Nintendo - Game Boy Advance.dat`
- `Nintendo - Nintendo DS.dat`

Each `<game>` supplies a canonical name; the `crc`/`sha1` attributes of its
`<rom>` entries become the lookup keys. Region is derived from the canonical
name (e.g. `(USA)` -> `USA`).

This folder is intentionally empty in the repository: full indexes are tens of
megabytes and are not distributed with the app.
