# Library Jobs GB FTP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split AppState into focused services, then add per-version delete, ZIP round-trip, cancellable backup jobs, GB/GBC platforms, volume-serial device binding, incremental FTP, and Qt wiring without growing `app_state.py` / `qt_ui.py` / `library.py`.

**Architecture:** `AppState` stays the public facade used by Qt and tests. New modules own settings, devices, scans, library actions, FTP, enrichment, jobs, ZIP import, version delete, volume IDs, and GB/GBC scanners. Each task lands behind existing `AppState` method names or a single new method listed in **Produces**.

**Tech Stack:** Python 3.11+, pytest, PySide6, Pillow. No new third-party dependencies.

**Spec:** `docs/superpowers/specs/2026-09-21-library-jobs-gb-ftp-design.md`

## Global Constraints

- Python 3.11+; run tests with `pytest`.
- Follow `DESIGN.md` and `vajsave.ui_theme`; platform colors only on Dock icons and 3px gallery pips.
- No OS-specific drive letters or hardcoded paths.
- New modules stay around 400 lines; do not pile features into `app_state.py`, `qt_ui.py`, or `library.py`.
- Qt and existing tests keep calling `AppState` public methods; services are internal.
- Split phase must keep current behavior; existing tests stay green before new features.
- Backup, restore, and FTP never write to a handheld.
- Do not use USB adapter hardware IDs or volume labels as the device primary key.
- TDD: write the failing test, watch it fail, then implement.
- Commit after each task with a focused message.

## File Structure

| File | Responsibility |
|------|----------------|
| `src/vajsave/platforms/catalog.py` | `PLATFORM_ORDER`, `PLATFORM_LABELS` |
| `src/vajsave/settings_store.py` | `config.json` read/write including new keys |
| `src/vajsave/device_session.py` | Volumes, custom folders, watch, preferred device |
| `src/vajsave/volume_id.py` | Filesystem volume serial → stable id or `None` |
| `src/vajsave/device_registry.py` | `devices.json`: volume id → relative save roots |
| `src/vajsave/scan_session.py` | Mount scan + backup-status hashing |
| `src/vajsave/library_actions.py` | Backup/restore/export/delete/notes/stars/filters |
| `src/vajsave/library_versions.py` | `delete_snapshot` |
| `src/vajsave/library_import.py` | ZIP manifest export + import |
| `src/vajsave/ftp_session.py` | FTP configure + pull |
| `src/vajsave/ftp_manifest.py` | Incremental skip list |
| `src/vajsave/enrichment.py` | Identity, metadata, artwork, LLM |
| `src/vajsave/jobs.py` | `JobProgress`, `CancelToken`, `JobCancelled` |
| `src/vajsave/backup_jobs.py` | Sequential cancellable backups |
| `src/vajsave/platforms/cartridge_files.py` | Shared sibling ROM+sav walk |
| `src/vajsave/platforms/gb.py` / `gbc.py` | GB/GBC scanners |
| `src/vajsave/identity/gb.py` / `gbc.py` | Thin ROM identity wrappers |
| `src/vajsave/qt_dialogs.py` | Settings, FTP, LLM, help, device picker, ZIP import |
| `src/vajsave/app_state.py` | Facade + view state |
| `src/vajsave/qt_ui.py` | Window shell; calls AppState only |

---

### Task 1: AppState facade split

**Files:**
- Create: `src/vajsave/platforms/catalog.py`
- Create: `src/vajsave/settings_store.py`
- Create: `src/vajsave/device_session.py`
- Create: `src/vajsave/scan_session.py`
- Create: `src/vajsave/library_actions.py`
- Create: `src/vajsave/ftp_session.py`
- Create: `src/vajsave/enrichment.py`
- Modify: `src/vajsave/app_state.py` (move bodies, keep method names)
- Modify: `src/vajsave/qt_ui.py` import of `PLATFORM_ORDER` / `PLATFORM_LABELS` (re-export from `app_state` so Qt import can stay)
- Test: existing `tests/test_app_state.py` plus `tests/test_settings_store.py`

**Interfaces:**
- Consumes: current `AppState` methods in `src/vajsave/app_state.py`
- Produces:
  - `PLATFORM_ORDER: list[str]` and `PLATFORM_LABELS: dict[str, str]` in `platforms/catalog.py`; `app_state.py` re-exports them
  - `class SettingsStore` with `load() -> dict`, `save(data: dict) -> bool`, `config_dir() -> Path`, `update(**keys) -> dict`
  - `class DeviceSession` owning `volumes`, `refresh_volumes()`, `register_custom_path()`, `preferred_volume()`, `start_watch()`, `stop_watch()`, `drain_events()` — AppState forwards
  - `class ScanSession` owning `begin_mount_scan`, `prepare_mount_scan`, `apply_prepared_mount_scan`, `prepare_backup_statuses`, `apply_backup_statuses`, `select_mount`
  - `class LibraryActions` owning backup/restore/export/delete/note/star/visible list helpers used by AppState
  - `class FtpSession` owning `configure_ftp`, `pull_ftp_saves`, `ftp_presets`
  - `class Enrichment` owning identity/metadata/artwork/LLM helpers
  - `AppState.__init__` constructs these services; public method signatures unchanged

- [ ] **Step 1: Write the failing test for SettingsStore**

```python
# tests/test_settings_store.py
from vajsave.settings_store import SettingsStore

def test_settings_store_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(tmp_path / "config.json"))
    store = SettingsStore()
    store.update(library_root=str(tmp_path / "lib"), auto_backup_on_insert=False)
    loaded = SettingsStore().load()
    assert loaded["library_root"] == str(tmp_path / "lib")
    assert loaded["auto_backup_on_insert"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_settings_store.py::test_settings_store_round_trip -v`
Expected: FAIL with `ModuleNotFoundError: vajsave.settings_store`

- [ ] **Step 3: Implement SettingsStore and extract the other services by moving existing AppState methods**

`SettingsStore` wraps `load_app_config` / `save_app_config` from `library.py`. Unknown keys pass through. `config_dir()` is `config_path().parent`.

Move code, do not rewrite behavior. `AppState` methods remain and delegate, e.g. `def refresh_volumes(self): return self.devices.refresh_volumes()`. Keep `PLATFORM_ORDER` as `["all", "psp", "vita", "switch", "3ds", "nds", "gba"]` in this task (GB/GBC is Task 6). Re-export `PLATFORM_ORDER` and `PLATFORM_LABELS` from `app_state.py`.

Target: `app_state.py` under ~500 lines of facade + view state. Each new service file under ~400 lines. If a service would exceed that, split only along the table in File Structure.

- [ ] **Step 4: Run existing AppState tests plus the new store test**

Run: `pytest tests/test_app_state.py tests/test_settings_store.py tests/test_app_ui_ftp.py tests/test_app_ui_restore.py tests/test_platform_filter.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/vajsave/platforms/catalog.py src/vajsave/settings_store.py src/vajsave/device_session.py src/vajsave/scan_session.py src/vajsave/library_actions.py src/vajsave/ftp_session.py src/vajsave/enrichment.py src/vajsave/app_state.py tests/test_settings_store.py
git commit -m "refactor: split AppState into focused session services"
```

---

### Task 2: Volume serial + device registry + bound scan

**Files:**
- Create: `src/vajsave/volume_id.py`
- Create: `src/vajsave/device_registry.py`
- Modify: `src/vajsave/volume.py` (Windows: read serial DWORD into `extra["volume_id"]`)
- Modify: `src/vajsave/scanner.py` (`scan(..., bound_sources=None)`)
- Modify: `src/vajsave/models.py` (optional `BoundSource` dataclass, or define it in `device_registry.py`)
- Modify: `src/vajsave/scan_session.py` / `app_state.py` to consult registry
- Test: `tests/test_volume_id.py`, `tests/test_device_registry.py`, extend `tests/test_scanner.py`

**Interfaces:**
- Consumes: `SettingsStore.config_dir()`, `ScanResult.sources`, `SaveSource`
- Produces:
  - `def volume_id_for(mount: Path, extra: dict | None = None) -> str | None`
  - `def format_windows_serial(serial_dword: int) -> str` → `"win:{8 uppercase hex}"`
  - `class BoundSource`: `platform: str`, `source_id: str`, `relative_root: str`
  - `class DeviceRegistry`:
    - `__init__(self, path: Path | None = None)` default `<config_dir>/devices.json`
    - `get(self, volume_id: str) -> list[BoundSource]`
    - `record(self, volume_id: str, *, label: str, sources: list[BoundSource], merge: bool) -> None`
    - `usable_sources(self, volume_id: str, mount: Path) -> list[BoundSource]` (those whose `mount / relative_root` is a directory)
  - `def relative_source_root(mount: Path, source_root: str) -> str | None` (POSIX, reject `..`)
  - `scan(root_path, progress=None, bound_sources: Sequence[BoundSource] | None = None)`
  - Custom folders use key `path:{resolved}`; FTP uses `ftp:{preset_key}`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_volume_id.py
from vajsave.volume_id import format_windows_serial, volume_id_for

def test_format_windows_serial_is_stable_hex():
    assert format_windows_serial(0xABCD1234) == "win:ABCD1234"

def test_volume_id_for_missing_returns_none(tmp_path):
    assert volume_id_for(tmp_path) is None

def test_volume_id_for_uses_extra_without_usb_adapter_fields(tmp_path):
    assert volume_id_for(tmp_path, extra={"volume_id": "win:DEADBEEF", "usb": "vid:pid"}) == "win:DEADBEEF"


# tests/test_device_registry.py
from vajsave.device_registry import BoundSource, DeviceRegistry, relative_source_root

def test_relative_source_root_rejects_parent(tmp_path):
    assert relative_source_root(tmp_path, str(tmp_path.parent)) is None

def test_record_and_usable_sources(tmp_path):
    mount = tmp_path / "card"
    (mount / "PSP" / "SAVEDATA").mkdir(parents=True)
    path = tmp_path / "devices.json"
    registry = DeviceRegistry(path)
    registry.record(
        "win:ABCD1234",
        label="MS",
        sources=[BoundSource("psp", "psp", "PSP/SAVEDATA")],
        merge=False,
    )
    usable = registry.usable_sources("win:ABCD1234", mount)
    assert usable[0].relative_root == "PSP/SAVEDATA"
    again = DeviceRegistry(path)
    assert again.get("win:ABCD1234")[0].platform == "psp"

def test_corrupt_devices_json_is_empty(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text("{not json", encoding="utf-8")
    assert DeviceRegistry(path).get("win:ABCD1234") == []

def test_merge_keeps_existing_present_roots(tmp_path):
    mount = tmp_path / "card"
    (mount / "PSP" / "SAVEDATA").mkdir(parents=True)
    (mount / "switch" / "Checkpoint" / "saves").mkdir(parents=True)
    registry = DeviceRegistry(tmp_path / "devices.json")
    registry.record("win:1", label="x", sources=[BoundSource("psp", "psp", "PSP/SAVEDATA")], merge=False)
    registry.record(
        "win:1",
        label="x",
        sources=[BoundSource("switch", "switch_checkpoint", "switch/Checkpoint/saves")],
        merge=True,
    )
    roots = {item.relative_root for item in registry.get("win:1")}
    assert roots == {"PSP/SAVEDATA", "switch/Checkpoint/saves"}
```

Also add in `tests/test_scanner.py`: a volume with PSP SAVEDATA and a decoy GBA `SAVER` file; `scan(root, bound_sources=[BoundSource("psp","psp","PSP/SAVEDATA")])` returns PSP saves and no GBA saves. Default `scan(root)` still finds both.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_volume_id.py tests/test_device_registry.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Implement volume_id, registry, bound scan, and wire ScanSession**

Windows `GetVolumeInformationW`: pass a `ctypes.c_uint32` for the serial out-param; store `format_windows_serial(value)` on `VolumeInfo.extra["volume_id"]`.

macOS: try `getattrlist` ATTR_VOL_UUID; on failure return `None`.
Linux: run `findmnt -n -o UUID --target <mount>` with timeout; empty/error → `None`.

`scan(..., bound_sources=)`: when the list is non-empty, run only scanners whose platform appears in bound sources, and pass those relative directories so discovery does not walk the rest of the volume. Default `bound_sources=None` preserves current behavior.

After a full scan (refresh or first seen), `DeviceRegistry.record(..., merge=False)` on first bind and `merge=True` on refresh. If `volume_id_for` is `None`, do not write. If `usable_sources` is empty, full scan then rewrite.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_volume_id.py tests/test_device_registry.py tests/test_scanner.py tests/test_app_state.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/vajsave/volume_id.py src/vajsave/device_registry.py src/vajsave/volume.py src/vajsave/scanner.py src/vajsave/scan_session.py src/vajsave/app_state.py tests/test_volume_id.py tests/test_device_registry.py tests/test_scanner.py
git commit -m "feat: bind save roots to filesystem volume serial"
```

---

### Task 3: Delete a single snapshot

**Files:**
- Create: `src/vajsave/library_versions.py`
- Modify: `src/vajsave/library.py` (re-export `delete_snapshot`, `SnapshotDeletion`)
- Modify: `src/vajsave/library_actions.py` / `app_state.py` add `delete_library_snapshot`
- Modify: `src/vajsave/qt_ui.py` (drawer button)
- Test: `tests/test_library_versions.py`

**Interfaces:**
- Consumes: `library._delete_snapshot_payload`, `delete_game`, `load_catalog`, `save_catalog`
- Produces:
```python
@dataclass
class SnapshotDeletion:
    game_id: str
    snapshot_id: str
    found: bool = False
    removed: bool = False
    game_removed: bool = False
    errors: list[str] = field(default_factory=list)
    @property
    def ok(self) -> bool:
        return self.found and self.removed and not self.errors

def delete_snapshot(library_root: Path, game_id: str, snapshot_id: str) -> SnapshotDeletion: ...
# AppState.delete_library_snapshot(entry, snapshot) -> SnapshotDeletion
```

- [ ] **Step 1: Write failing tests** (reuse `_backup` helper pattern from `tests/test_library_delete.py`)

```python
def test_delete_middle_snapshot_keeps_game(tmp_path):
    ...  # two versions; delete first; catalog still has one version; other game untouched

def test_delete_last_snapshot_removes_game(tmp_path):
    ...  # one version; delete_snapshot → game_removed True; id gone from catalog

def test_delete_snapshot_does_not_touch_device_files(tmp_path):
    ...  # device source file still exists

def test_delete_missing_snapshot_found_false(tmp_path):
    ...
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_library_versions.py -v` → import error

- [ ] **Step 3: Implement `delete_snapshot`**. Last remaining version calls `delete_game`. Unsafe paths append errors and keep catalog. AppState method maps `entry` through `_game_id`. Qt: button 「删除此版本」 on the drawer; confirm; if last version, confirm text says the whole game will be removed.

- [ ] **Step 4: `pytest tests/test_library_versions.py tests/test_library_delete.py -q`** PASS

- [ ] **Step 5: Commit** `feat: delete a single library snapshot`

---

### Task 4: ZIP manifest export and import

**Files:**
- Create: `src/vajsave/library_import.py`
- Modify: `src/vajsave/library.py` `export_snapshot_zip` to call the new exporter (keep function name)
- Modify: `app_state.py` `export_version_zip` + `import_snapshot_zip`
- Test: `tests/test_library_import.py`

**Interfaces:**
- Produces:
```python
MANIFEST_NAME = "vaj-save.json"
FORMAT = "vaj-save-snapshot"
FORMAT_VERSION = 1

def export_snapshot_zip(snapshot, library_root: Path, zip_path: Path, game=None) -> Path: ...

def import_snapshot_zip(
    library_root: Path,
    zip_path: Path,
    *,
    attach_game_id: str | None = None,
    new_platform: str | None = None,
    new_title_id: str | None = None,
    new_display_name: str | None = None,
    new_slot: str | None = None,
) -> BackupResult: ...
```

ZIP layout: `vaj-save.json` + `payload/<snapshot files>`.

- [ ] **Step 1: Failing tests**

```python
def test_export_contains_manifest_and_payload(tmp_path): ...
def test_import_round_trip_attaches_by_identity_key(tmp_path): ...
def test_import_identical_content_is_not_new(tmp_path): ...
def test_zip_slip_is_rejected(tmp_path):
    # member named "../outside.txt" → import raises / returns failure, library unchanged
def test_legacy_zip_without_manifest_requires_attach_or_new_fields(tmp_path): ...
```

- [ ] **Step 2: pytest fails on missing module**

- [ ] **Step 3: Implement**. Zip Slip: after `Path(name)` reject if the resolved path is not inside the extract root. Extract to a temp dir under `library_root`, then `backup_save` using a synthetic `SaveEntry`. Match order from the spec. Old ZIP: require `attach_game_id` or (`new_platform` and `new_display_name`).

- [ ] **Step 4: `pytest tests/test_library_import.py tests/test_library.py -q`** PASS

- [ ] **Step 5: Commit** `feat: import and export snapshot ZIP with manifest`

---

### Task 5: Cancellable backup jobs

**Files:**
- Create: `src/vajsave/jobs.py`
- Create: `src/vajsave/backup_jobs.py`
- Modify: `app_state.py` (`import_selected_saves`, `backup_updated_saves`, `cancel_job`, `job_progress`)
- Modify: `qt_ui.py` (progress bar cancel, 「备份有更新」)
- Test: `tests/test_backup_jobs.py`

**Interfaces:**
```python
@dataclass
class JobProgress:
    message: str = ""
    current: int = 0
    total: int = 0
    cancellable: bool = False

class JobCancelled(Exception): ...

class CancelToken:
    def cancel(self) -> None: ...
    def cancelled(self) -> bool: ...
    def raise_if_cancelled(self) -> None: ...

def backup_entries(
    entries: list[SaveEntry],
    backup_one,  # Callable[[SaveEntry], Path | None]
    token: CancelToken | None = None,
    progress: Callable[[JobProgress], None] | None = None,
) -> list[Path]: ...
```

`AppState.backup_updated_saves()` backups `visible_saves()` whose status is `new` or `changed`. Library mode returns empty. One job at a time: if a job is running, new backup/FTP/scan-hash waits or no-ops with status text.

- [ ] **Step 1: Failing tests**

```python
def test_backup_entries_skips_after_cancel(tmp_path):
    token = CancelToken()
    calls = []
    def backup_one(entry):
        calls.append(entry.display_name)
        if len(calls) == 1:
            token.cancel()
        return tmp_path / entry.display_name
    paths = backup_entries([e1, e2, e3], backup_one, token=token)
    assert [e.display_name for e in ...]  # only first backed up
    assert len(paths) == 1

def test_backup_updated_saves_ignores_unchanged(tmp_path): ...
def test_backup_updated_saves_noop_in_library_mode(tmp_path): ...
```

- [ ] **Step 2: pytest fail**

- [ ] **Step 3: Implement**. Cancel between entries. Status `已备份 N 个，已取消`. Qt: reuse bottom progress; show 取消 when `cancellable`. Topbar button 备份有更新.

- [ ] **Step 4: `pytest tests/test_backup_jobs.py tests/test_app_state.py -q`** PASS

- [ ] **Step 5: Commit** `feat: cancellable backup of updated saves`

---

### Task 6: GB and GBC platforms

**Files:**
- Create: `src/vajsave/platforms/cartridge_files.py`
- Create: `src/vajsave/platforms/gb.py`, `src/vajsave/platforms/gbc.py`
- Create: `src/vajsave/identity/gb.py`, `src/vajsave/identity/gbc.py`
- Create: `src/vajsave/data/platform-gb.svg`, `platform-gbc.svg`
- Modify: `rom_formats.py`, `scanner.py`, `platforms/catalog.py`, `ui_theme.py`, `identity/roms.py` (`_HEADER_OFFSETS` gb/gbc `0x134`), `identity/resolver.py`, `artwork/providers.py`, `metadata/libretro.py`, `app_state.py` `set_rom_dirs` kwargs, `qt_ui.py` SVG map, GBA `SAVER` split
- Test: `tests/test_platforms_gb.py`, `tests/test_platforms_gbc.py`

**Interfaces:**
- `ROM_EXTENSIONS["gb"] = (".gb",)`, `["gbc"] = (".gbc",)`
- `PLATFORM_ORDER = ["all", "psp", "vita", "switch", "3ds", "nds", "gb", "gbc", "gba"]`
- `PLATFORM_COLORS["gb"] = "#9aa56a"`, `["gbc"] = "#ff6b8a"`
- `LIBRETRO_SYSTEM_NAMES["gb"] = "Nintendo - Game Boy"`, `["gbc"] = "Nintendo - Game Boy Color"`
- `SUPPORTED_PLATFORMS` includes `gb`, `gbc`
- Do not add empty `gb.json`/`gbc.json`

SAVER split: if `foo.sav` in `SAVER/` and `foo.gb` exists on the volume (bounded search, same as other scanners), platform is `gb`; `.gbc` → `gbc`; `.gba`/`.agb` or no ROM → `gba`.

- [ ] **Step 1: Failing tests** for `roms/gb` sibling sav, `roms/gbc`, SAVER split, no-ROM SAVER stays gba, `guess_platform` / `_detected_platforms` see `roms/gb`

- [ ] **Step 2: pytest fail**

- [ ] **Step 3: Implement scanners + identity wrappers + dock tokens + SVG** (simple cartridge silhouette, token colors). `set_rom_dirs(..., gb_rom_dir=_UNSET, gbc_rom_dir=_UNSET)` keeping positional gba/nds.

- [ ] **Step 4: `pytest tests/test_platforms_gb.py tests/test_platforms_gbc.py tests/test_platforms_gba.py tests/test_scanner.py tests/test_platform_filter.py -q`** PASS

- [ ] **Step 5: Commit** `feat: add Game Boy and Game Boy Color platforms`

---

### Task 7: Incremental FTP + remember password

**Files:**
- Create: `src/vajsave/ftp_manifest.py`
- Modify: `src/vajsave/ftp_fetch.py`, `remote_ftp.py` (LIST size/modified already on `FtpEntry`)
- Modify: `ftp_session.py` / `app_state.py` (`ftp_remember_password`, persist password only when flagged)
- Modify: `qt_dialogs.py` or FTP dialog: checkbox 记住密码; pull uses jobs cancel token
- Test: extend `tests/test_ftp_fetch.py`, `tests/test_app_ui_ftp.py`

**Interfaces:**
```python
MANIFEST_NAME = ".ftp-manifest.json"

def load_manifest(cache_dir: Path) -> dict[str, dict]
def save_manifest(cache_dir: Path, files: dict[str, dict]) -> bool
def should_reuse(entry_size: int, entry_modified: str, recorded: dict | None) -> bool
# size match and (modified strings equal, or recorded/listing modified both empty)
```

Pull: LIST; if should_reuse and old file exists, copy to staging; else download. Write manifest after successful swap. Failure/cancel: delete staging, keep old cache+manifest.

- [ ] **Step 1: Tests** — unchanged file does not call download; size change does; failed pull leaves old tree; cancel leaves old tree; remember password writes `ftp_password` only when flag true

- [ ] **Step 2: pytest fail**

- [ ] **Step 3: Implement**

- [ ] **Step 4: `pytest tests/test_ftp_fetch.py tests/test_app_ui_ftp.py tests/test_remote_ftp.py -q` if present, else the ftp tests that exist** PASS

- [ ] **Step 5: Commit** `feat: incremental FTP pull and optional saved password`

---

### Task 8: Qt wiring, restore targets, auto-backup, dialogs

**Files:**
- Create: `src/vajsave/qt_dialogs.py` (move settings/FTP/LLM/help/device picker/ZIP import dialogs out of `qt_ui.py`)
- Modify: `qt_ui.py`, `app_state.py` (`suggested_restore_dir`, `set_auto_backup_on_insert`, `toggle_starred_only` already exists)
- Test: `tests/test_app_ui_settings.py`, `tests/test_identity_binding_ui.py`, new `tests/test_restore_targets.py`

**Interfaces:**
```python
def suggested_restore_dir(self, entry: SaveEntry) -> Path | None
# 1 bound source for entry.platform/source_id if dir exists
# 2 platform default dirs on current mount
# 3 last_restore_dir if it exists
def set_auto_backup_on_insert(self, enabled: bool) -> bool  # persist, default False
```

Qt:
- Star on drawer; topbar 只看收藏
- Ambiguous ROM: candidate list + bind selected; keep manual ROM
- Remove dead 「查看全部」
- Topbar 备份有更新; menu: 导入 ZIP, 打开备份库
- Dock in library mode: 导入 ZIP
- Settings: browse buttons, auto-backup checkbox, GB/GBC ROM dirs
- Restore dialog starts at `suggested_restore_dir`; on success save `last_restore_dir`
- After insert scan+hash, if auto-backup and not library mode and job idle, call `backup_updated_saves`

- [ ] **Step 1: Tests for suggested_restore_dir and auto_backup default False**

- [ ] **Step 2: pytest fail**

- [ ] **Step 3: Implement dialogs extraction + wiring**. Do not grow `qt_ui.py`; it should shrink.

- [ ] **Step 4: `pytest tests/test_qt_ui.py tests/test_app_ui_settings.py tests/test_restore_targets.py tests/test_identity_binding_ui.py -q`** PASS

- [ ] **Step 5: Commit** `feat: wire library actions, restore targets, and auto-backup in Qt`

---

### Task 9: Docs and help copy

**Files:**
- Modify: `README.md`, `DESIGN.md`, `AGENTS.md`, Qt/Tk help strings
- Test: none beyond `pytest tests/test_app_ui_help.py` if it asserts help text loosely

Document: GB/GBC, ZIP round-trip, per-version delete, incremental FTP, remember password, volume-serial device binding, auto-backup default off, restore still never writes the handheld. README command for building GB/GBC libretro indexes. Dock order includes gb/gbc.

- [ ] **Step 1: Update docs**
- [ ] **Step 2: `pytest tests/test_app_ui_help.py tests/test_packaging.py -q`** PASS
- [ ] **Step 3: Commit** `docs: document library jobs, GB/GBC, and device binding`

---

## Self-review

Spec coverage: split (T1), version delete (T3), ZIP (T4), jobs/auto-backup (T5+T8), GB/GBC (T6), device binding (T2), FTP (T7), Qt wiring/restore (T8), docs (T9).

No TBD placeholders. Type names: `BoundSource`, `SnapshotDeletion`, `JobProgress`, `CancelToken` used consistently.
