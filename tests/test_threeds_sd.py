"""3DS 加密 SD 卡直读：密钥校验、路径派生、解密与扫描集成。

真实主机数据无法离线取得，因此派生结果全部与 pyctr（ninfs 的底层实现）交叉验证，
加解密用 pyctr 的写入路径造出「加密卡上的文件」再要求本模块解回原文。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from vajsave import threeds_sd
from vajsave.app_state import AppState
from vajsave.library import backup_save, classify_save_status, load_catalog
from vajsave.models import SaveEntry
from vajsave.scanner import scan
from vajsave.settings_store import SettingsStore

KEY_Y = bytes(range(16))
KEY_X = bytes(range(16, 32))
ID1 = "1234567890abcdef1234567890abcdef"
TITLE_REL = "/title/00040000/000f7000/data/00000001.sav"
TITLE_ID = "00040000000f7000"


def reference_engine(key_y: bytes = KEY_Y, key_x: bytes = KEY_X):
    """与主机固件算法一致的参考实现（pyctr）。"""
    from pyctr.crypto import CryptoEngine, Keyslot

    engine = CryptoEngine(setup_b9_keys=False)
    engine.set_keyslot("x", Keyslot.SD, key_x)
    engine.set_keyslot("x", Keyslot.CMACSDNAND, key_x)
    engine.setup_sd_key(key_y)
    return engine


def encrypt_with_reference(path: Path, rel_path: str, payload: bytes) -> None:
    """把明文按主机的方式加密写到 ``path``（模拟卡上的加密文件）。"""
    from pyctr.crypto import CTRFileIO, Keyslot

    engine = reference_engine()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        stream = CTRFileIO(handle, engine, Keyslot.SD, threeds_sd.sd_ctr_iv(rel_path))
        stream.write(payload)


def keys(*, key_y: bytes = KEY_Y, key_x: bytes | None = KEY_X) -> threeds_sd.ThreedsKeys:
    return threeds_sd.ThreedsKeys(key_y=key_y, key_x_sd=key_x)


def build_card(root: Path, *, id0: str | None = None) -> Path:
    """造一张加密 SD：一个 title 存档 + 一个 extdata。"""
    id1_dir = root / "Nintendo 3DS" / (id0 or threeds_sd.id0_for(KEY_Y)) / ID1
    encrypt_with_reference(
        id1_dir / "title" / "00040000" / "000f7000" / "data" / "00000001.sav",
        TITLE_REL,
        b"\x00" * 0x100 + b"DISA\x00\x00\x04\x00" + b"\x11" * 0x100,
    )
    ext_rel = "/extdata/00000000/000f7000/00000001"
    encrypt_with_reference(id1_dir / "extdata" / "00000000" / "000f7000" / "00000001" / "00000001", f"{ext_rel}/00000001", b"ext-payload")
    encrypt_with_reference(id1_dir / "extdata" / "00000000" / "000f7000" / "00000001" / "00000002", f"{ext_rel}/00000002", b"ext-second")
    return id1_dir


def entry_for(path: Path, rel: str, *, title_id: str = TITLE_ID, kind: str = "title") -> SaveEntry:
    return SaveEntry(
        platform="3ds",
        source_id="3ds_sd_direct",
        display_name="000F7000",
        path=str(path),
        title_id=title_id,
        extra={"encrypted_sd": True, "sd_rel": rel, "id1": ID1, "kind": kind},
    )


@pytest.fixture(autouse=True)
def _clean_decrypted_cache():
    threeds_sd.clear_cache()
    yield
    threeds_sd.clear_cache()


# --------------------------------------------------------------------------- #
# 密钥文件
# --------------------------------------------------------------------------- #


def test_movable_key_y_reads_offset_for_all_supported_lengths():
    key_y = bytes(range(0x10, 0x20))
    assert threeds_sd.movable_key_y(key_y) == key_y
    padded = b"\x00" * 0x110 + key_y
    assert threeds_sd.movable_key_y(padded) == key_y
    assert threeds_sd.movable_key_y(padded + b"\x00" * 0x20) == key_y


def test_movable_key_y_rejects_unexpected_length():
    with pytest.raises(threeds_sd.ThreedsKeysInvalid) as excinfo:
        threeds_sd.movable_key_y(b"\x00" * 0x30)
    assert "movable.sed" in str(excinfo.value)


def test_boot9_accepts_both_dump_shapes(monkeypatch):
    payload = bytes(range(256)) * 0x80  # 0x8000
    monkeypatch.setattr(threeds_sd, "BOOT9_PROT_SHA256", hashlib.sha256(payload).hexdigest())
    assert threeds_sd.boot9_prot(payload) == payload
    assert threeds_sd.boot9_prot(payload + b"\xff" * 0x8000) == payload


def test_boot9_rejects_wrong_hash_and_length(monkeypatch):
    monkeypatch.setattr(threeds_sd, "BOOT9_PROT_SHA256", "0" * 64)
    with pytest.raises(threeds_sd.ThreedsKeysInvalid) as excinfo:
        threeds_sd.boot9_prot(b"\x00" * 0x8000)
    assert "boot9" in str(excinfo.value)
    with pytest.raises(threeds_sd.ThreedsKeysInvalid):
        threeds_sd.boot9_prot(b"\x00" * 0x10)


def test_from_paths_requires_boot9_unless_key_x_injected(tmp_path: Path):
    movable = tmp_path / "movable.sed"
    movable.write_bytes(b"\x00" * 0x110 + KEY_Y)
    with pytest.raises(threeds_sd.ThreedsKeysMissing):
        threeds_sd.ThreedsKeys.from_paths(movable)
    built = threeds_sd.ThreedsKeys.from_paths(movable, key_x_sd=KEY_X)
    assert built.key_y == KEY_Y
    assert built.source_paths["movable_sed"] == str(movable)


def test_from_paths_reports_missing_file(tmp_path: Path):
    with pytest.raises(threeds_sd.ThreedsKeysInvalid) as excinfo:
        threeds_sd.ThreedsKeys.from_paths(tmp_path / "nope.sed", key_x_sd=KEY_X)
    assert "无法读取" in str(excinfo.value)


def test_missing_pyctr_raises_unavailable(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "pyctr.crypto", None)
    with pytest.raises(threeds_sd.ThreedsUnavailable):
        threeds_sd.ThreedsSdReader(keys())


# --------------------------------------------------------------------------- #
# 路径派生：与参考实现交叉验证
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key_y", [bytes(16), KEY_Y, bytes(range(0x20, 0x30))])
def test_id0_matches_reference_implementation(key_y):
    assert threeds_sd.id0_for(key_y) == reference_engine(key_y).id0.hex()


@pytest.mark.parametrize(
    "rel_path",
    [
        TITLE_REL,
        "/extdata/00000000/000f7000/00000001/00000001",
        "/TITLE/00040000/000F7000/DATA/00000001.SAV",
        "\\title\\00040000\\000f7000\\data\\00000001.sav",
        "/backup/0000000000000000/000f7000/data/00000001.sav",
    ],
)
def test_sd_ctr_iv_matches_reference_implementation(rel_path):
    assert threeds_sd.sd_ctr_iv(rel_path) == reference_engine().sd_path_to_iv(rel_path)


def test_sd_ctr_iv_normalizes_slashless_and_upper_case_paths():
    """相对路径一律按「以 ID1 为根的路径」处理，与主机一致。"""
    assert threeds_sd.sd_ctr_iv("title/00040000/000f7000/data/00000001.sav") == threeds_sd.sd_ctr_iv(TITLE_REL)
    assert threeds_sd.sd_ctr_iv(TITLE_REL.upper()) == threeds_sd.sd_ctr_iv(TITLE_REL)


def test_relative_path_of_requires_marker():
    plain = SaveEntry(platform="3ds", source_id="s", display_name="x", path="/tmp/a.sav")
    assert not threeds_sd.is_encrypted_sd_entry(plain)
    with pytest.raises(threeds_sd.ThreedsSdError):
        threeds_sd.relative_path_of(plain)


# --------------------------------------------------------------------------- #
# 解密
# --------------------------------------------------------------------------- #


def test_decrypt_file_returns_original_payload(tmp_path: Path):
    source = tmp_path / "00000001.sav"
    payload = b"\x00" * 0x100 + b"DISA\x00\x00\x04\x00" + bytes(range(256)) * 4
    encrypt_with_reference(source, TITLE_REL, payload)
    reader = threeds_sd.ThreedsSdReader(keys())
    assert reader.decrypt_file(source, TITLE_REL) == payload


def test_materialized_path_decrypts_and_caches(tmp_path: Path):
    source = tmp_path / "00000001.sav"
    payload = b"\x00" * 0x100 + b"DISA\x00\x00\x04\x00" + b"\xab" * 0x300
    encrypt_with_reference(source, TITLE_REL, payload)
    entry = entry_for(source, TITLE_REL)

    first = threeds_sd.materialized_path(entry, keys())
    assert first.read_bytes() == payload
    assert threeds_sd.materialized_path(entry, keys()) == first

    # 内容变化后缓存失效并重新解密
    encrypt_with_reference(source, TITLE_REL, b"\x00" * 0x100 + b"DISA\x00\x00\x04\x00" + b"\xcd" * 0x300)
    second = threeds_sd.materialized_path(entry, keys())
    assert second != first
    assert second.read_bytes().endswith(b"\xcd" * 0x300)


def test_materialized_path_with_wrong_keys_is_not_plaintext(tmp_path: Path):
    source = tmp_path / "00000001.sav"
    payload = b"\x00" * 0x100 + b"DISA\x00\x00\x04\x00" + b"\xab" * 0x40
    encrypt_with_reference(source, TITLE_REL, payload)
    entry = entry_for(source, TITLE_REL)
    wrong = threeds_sd.materialized_path(entry, threeds_sd.ThreedsKeys(key_y=KEY_Y, key_x_sd=bytes(16)))
    assert wrong.read_bytes() != payload


def test_materialized_path_decrypts_extdata_directory(tmp_path: Path):
    ext_dir = tmp_path / "00000001"
    ext_rel = "/extdata/00000000/000f7000/00000001"
    encrypt_with_reference(ext_dir / "00000001", f"{ext_rel}/00000001", b"first")
    encrypt_with_reference(ext_dir / "00000002", f"{ext_rel}/00000002", b"second")
    entry = entry_for(ext_dir, ext_rel, kind="extdata")
    entry.slot = "extdata-00000001"

    decrypted = threeds_sd.materialized_path(entry, keys())
    assert decrypted.is_dir()
    assert (decrypted / "00000001").read_bytes() == b"first"
    assert (decrypted / "00000002").read_bytes() == b"second"


# --------------------------------------------------------------------------- #
# 扫描
# --------------------------------------------------------------------------- #


def test_scan_without_keys_keeps_opaque_container(tmp_path: Path):
    build_card(tmp_path)
    result = scan(tmp_path)
    assert result.platform == "3ds"
    assert [s.source_id for s in result.sources] == ["3ds_sd"]
    assert result.sources[0].extra == {"encrypted_container": True}
    assert result.saves == []


def test_scan_with_keys_lists_title_and_extdata(tmp_path: Path):
    build_card(tmp_path)
    result = scan(tmp_path, threeds_keys=keys())

    assert [s.source_id for s in result.sources] == ["3ds_sd_direct"]
    assert result.sources[0].extra["direct_read"] is True
    by_kind = {s.extra["kind"]: s for s in result.saves}
    assert by_kind["title"].title_id == TITLE_ID
    assert by_kind["title"].display_name == "000F7000"
    assert by_kind["title"].extra["sd_rel"] == TITLE_REL
    assert by_kind["extdata"].slot == "extdata-00000001"
    assert by_kind["extdata"].extra["sd_rel"] == "/extdata/00000000/000f7000/00000001"


def test_scan_reports_key_that_belongs_to_another_console(tmp_path: Path):
    build_card(tmp_path, id0=threeds_sd.id0_for(bytes(16)))
    result = scan(tmp_path, threeds_keys=keys())

    assert any("ID0" in warning for warning in result.warnings)
    assert [s.source_id for s in result.sources] == ["3ds_sd"]
    assert result.saves == []


def test_scan_ignores_encrypted_card_when_no_card_root(tmp_path: Path):
    other = tmp_path / "plainusb"
    (other / "notes").mkdir(parents=True)
    result = scan(other, threeds_keys=keys())
    assert result.saves == []
    assert result.sources == []


# --------------------------------------------------------------------------- #
# 备份与状态
# --------------------------------------------------------------------------- #


def test_backup_stores_decrypted_payload_and_keeps_device_path(tmp_path: Path):
    card = tmp_path / "card"
    id1_dir = build_card(card)
    source = id1_dir / "title" / "00040000" / "000f7000" / "data" / "00000001.sav"
    entry = entry_for(source, TITLE_REL)
    library_root = tmp_path / "lib"

    result = backup_save(
        entry,
        library_root,
        source_override=threeds_sd.materialized_path(entry, keys()),
    )

    assert result.is_new
    assert result.snapshot.source_path == str(source)
    stored = Path(result.path) / "00000001.sav"
    assert stored.read_bytes()[0x100:0x104] == b"DISA"
    assert stored.read_bytes() != source.read_bytes()


def test_status_is_unchanged_after_backup_of_encrypted_entry(tmp_path: Path):
    card = tmp_path / "card"
    id1_dir = build_card(card)
    source = id1_dir / "title" / "00040000" / "000f7000" / "data" / "00000001.sav"
    entry = entry_for(source, TITLE_REL)
    library_root = tmp_path / "lib"

    backup_save(entry, library_root, source_override=threeds_sd.materialized_path(entry, keys()))
    catalog = load_catalog(library_root)

    from vajsave.library import hash_tree

    # 解密后的载荷与版本一致 -> 已备份
    decrypted = threeds_sd.hash_source(entry, keys())
    assert classify_save_status(entry, catalog, digest=hash_tree(decrypted)).status == "unchanged"
    # 卡上的加密原文永远不会等于版本哈希：这正是必须走解密来源的原因
    assert classify_save_status(entry, catalog, digest=hash_tree(source)).status == "changed"


def test_hash_source_requires_keys_for_encrypted_entry(tmp_path: Path):
    entry = entry_for(tmp_path / "00000001.sav", TITLE_REL)
    with pytest.raises(threeds_sd.ThreedsKeysMissing):
        threeds_sd.hash_source(entry, None)


# --------------------------------------------------------------------------- #
# AppState 接线
# --------------------------------------------------------------------------- #


def _isolated_state(tmp_path: Path, monkeypatch) -> AppState:
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(tmp_path / "config.json"))
    return AppState(library_root=tmp_path / "lib")


def test_app_state_persists_key_paths_and_reports_errors(tmp_path: Path, monkeypatch):
    movable = tmp_path / "movable.sed"
    movable.write_bytes(b"\x00" * 0x110 + KEY_Y)
    bad = tmp_path / "boot9.bin"
    bad.write_bytes(b"\x00" * 0x8000)

    state = _isolated_state(tmp_path, monkeypatch)
    assert state.threeds_keys is None
    assert state.threeds_keys_error is None

    assert state.set_threeds_key_files(str(movable), str(bad), ) is None
    assert state.threeds_keys_error is not None
    assert "boot9" in state.threeds_keys_error

    stored = SettingsStore().load()
    assert stored["threeds_movable_sed"] == str(movable)
    assert stored["threeds_boot9"] == str(bad)

    # 清空后配置里不留残留
    assert state.set_threeds_key_files("", "") is None
    stored = SettingsStore().load()
    assert "threeds_movable_sed" not in stored
    assert "threeds_boot9" not in stored


def test_app_state_reports_missing_boot9(tmp_path: Path, monkeypatch):
    movable = tmp_path / "movable.sed"
    movable.write_bytes(b"\x00" * 0x110 + KEY_Y)
    state = _isolated_state(tmp_path, monkeypatch)
    state.set_threeds_key_files(str(movable), "")
    assert state.threeds_keys is None
    assert "boot9" in (state.threeds_keys_error or "")


def test_scan_session_forwards_keys_to_scan_fn(tmp_path: Path, monkeypatch):
    movable = tmp_path / "movable.sed"
    movable.write_bytes(b"\x00" * 0x110 + KEY_Y)
    monkeypatch.setattr(
        threeds_sd.ThreedsKeys,
        "from_paths",
        classmethod(lambda cls, *a, **k: keys()),
    )
    state = _isolated_state(tmp_path, monkeypatch)
    state.set_threeds_key_files(str(movable), "")

    seen: dict[str, Any] = {}

    def fake_scan(path, **kwargs):
        seen.update(kwargs)
        from vajsave.models import ScanResult

        return ScanResult(root_path=str(path), platform="unknown", sources=[], saves=[])

    state.scan_fn = fake_scan
    state.scans._invoke_scan(tmp_path)

    assert isinstance(seen.get("threeds_keys"), threeds_sd.ThreedsKeys)


def test_scan_session_omits_keys_when_unconfigured(tmp_path: Path, monkeypatch):
    state = _isolated_state(tmp_path, monkeypatch)
    seen: dict[str, Any] = {}

    def fake_scan(path, **kwargs):
        seen.update(kwargs)
        from vajsave.models import ScanResult

        return ScanResult(root_path=str(path), platform="unknown", sources=[], saves=[])

    state.scan_fn = fake_scan
    state.scans._invoke_scan(tmp_path)
    assert "threeds_keys" not in seen
