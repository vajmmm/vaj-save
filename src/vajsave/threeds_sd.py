"""3DS 加密 SD 卡（``Nintendo 3DS/``）的只读直读。

卡上 ``title/<类型>/<Title ID>/data/*.sav`` 与 ``extdata/`` 里的存档由主机唯一的
keyY（``movable.sed``）和 keyslot ``0x34`` 的 keyX（``boot9_prot.bin``）派生出密钥，
再按「文件相对 ID1 目录的路径」派生 AES-CTR 计数器（见 :func:`sd_ctr_iv`）。

本模块只解密与读取：

* 从不写回设备，也从不修改卡上的加密原文件；
* 密钥文件只用于当场派生密钥，不复制、不写入备份库、不进日志；
* 解密结果落在系统临时目录，供现有备份/版本流程当作普通存档处理。

真正的容器解析交给 pyctr（MIT，``pyctr.crypto``/``pyctr.type.save``），
本模块负责密钥校验、路径推导与缓存；未安装 pyctr 时抛出 :class:`ThreedsUnavailable`。
"""

from __future__ import annotations

import atexit
import hashlib
import shutil
import tempfile
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .models import SaveEntry

# keyslot 0x34「SD」：Nintendo 3DS 目录下所有内容的密钥槽。
_SD_KEYSLOT = 0x34
_CMAC_SD_NAND_KEYSLOT = 0x30

BOOT9_PROT_SHA256 = "7331f7edece3dd33f2ab4bd0b3a5d607229fd19212c10b734cedcaf78c1a7b98"
"""受保护 ARM9 bootROM（``boot9_prot.bin``，0x8000 字节）的 SHA-256。"""

MOVABLE_SED_LENGTHS = (0x10, 0x120, 0x140)
"""``movable.sed`` 允许的长度：裸 keyY、无 CMAC、含 CMAC。"""

MOVABLE_SED_CONFIG_KEY = "threeds_movable_sed"
BOOT9_CONFIG_KEY = "threeds_boot9"
"""应用配置里保存的两个密钥文件路径（只存路径，不存内容）。"""

_KEY_Y_OFFSET = 0x110
_RELATIVE_MARKER = "backup"
_CACHE_LIMIT = 64


class ThreedsSdError(RuntimeError):
    """3DS 加密 SD 直读的通用错误。"""


class ThreedsKeysMissing(ThreedsSdError):
    """尚未配置密钥文件。"""


class ThreedsKeysInvalid(ThreedsSdError):
    """密钥文件内容不符合预期（长度或摘要不匹配）。"""


class ThreedsUnavailable(ThreedsSdError):
    """环境缺少 pyctr，无法解密。"""


# --------------------------------------------------------------------------- #
# 密钥文件
# --------------------------------------------------------------------------- #


def movable_key_y(data: bytes) -> bytes:
    """从 ``movable.sed`` 内容取出 console-unique keyY（偏移 0x110，16 字节）。"""
    length = len(data)
    if length == 0x10:
        return bytes(data)
    if length in (0x120, 0x140):
        return bytes(data[_KEY_Y_OFFSET:_KEY_Y_OFFSET + 0x10])
    raise ThreedsKeysInvalid(
        f"movable.sed 长度应为 {', '.join(hex(n) for n in MOVABLE_SED_LENGTHS)}，实际 {hex(length)}"
    )


def boot9_prot(data: bytes) -> bytes:
    """校验并返回受保护 bootROM 的 0x8000 字节。

    同时接受 0x8000 的 ``boot9_prot.bin`` 和 0x10000 的完整 ``boot9.bin``
    （后者的前 0x8000 字节即受保护区），用内置 SHA-256 判定内容是否正确。
    """
    length = len(data)
    if length == 0x10000:
        candidate = bytes(data[:0x8000])
    elif length == 0x8000:
        candidate = bytes(data)
    else:
        raise ThreedsKeysInvalid(
            f"boot9 长度应为 {hex(0x8000)} 或 {hex(0x10000)}，实际 {hex(length)}"
        )
    digest = hashlib.sha256(candidate).hexdigest()
    if digest != BOOT9_PROT_SHA256:
        raise ThreedsKeysInvalid(
            "boot9 内容校验失败（需要 GodMode9 导出的 boot9_prot.bin 或 boot9.bin，"
            "不能用 boot9 的未加密部分代替）"
        )
    return candidate


def _read_file(path: Path, label: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise ThreedsKeysInvalid(f"无法读取{label}: {path} ({exc})") from exc


@dataclass(frozen=True)
class ThreedsKeys:
    """一次直读所需的密钥材料。

    ``key_x_sd`` 仅用于测试或高级用法：正常路径是从 ``boot9_prot`` 的 keyblob 里
    取 keyslot 0x34 的 keyX；显式给出时优先使用它。
    """

    key_y: bytes
    boot9_prot: Optional[bytes] = None
    key_x_sd: Optional[bytes] = None
    source_paths: Dict[str, str] = field(default_factory=dict)

    @property
    def token(self) -> Tuple[str, ...]:
        """缓存键：密钥变化时旧缓存自动失效（不含密钥本身，只含摘要）。"""
        return (
            hashlib.sha256(self.key_y).hexdigest()[:16],
            hashlib.sha256(self.boot9_prot).hexdigest()[:16] if self.boot9_prot else "",
            hashlib.sha256(self.key_x_sd).hexdigest()[:16] if self.key_x_sd else "",
        )

    @classmethod
    def from_paths(
        cls,
        movable_sed_path: Path,
        boot9_path: Optional[Path] = None,
        *,
        key_x_sd: Optional[bytes] = None,
    ) -> "ThreedsKeys":
        """读取并校验密钥文件。"""
        movable_path = Path(movable_sed_path)
        key_y = movable_key_y(_read_file(movable_path, "movable.sed"))
        boot9: Optional[bytes] = None
        if boot9_path:
            boot9 = boot9_prot(_read_file(Path(boot9_path), "boot9"))
        if boot9 is None and key_x_sd is None:
            raise ThreedsKeysMissing("未提供 boot9（boot9_prot.bin / boot9.bin），无法派生密钥")
        if key_x_sd is not None and len(key_x_sd) != 0x10:
            raise ThreedsKeysInvalid("注入的 keyX 长度应为 0x10")
        paths: Dict[str, str] = {"movable_sed": str(movable_path)}
        if boot9_path:
            paths["boot9"] = str(Path(boot9_path))
        return cls(key_y=key_y, boot9_prot=boot9, key_x_sd=key_x_sd, source_paths=paths)


# --------------------------------------------------------------------------- #
# 路径推导（与主机固件算法一致，测试里与 pyctr 交叉验证）
# --------------------------------------------------------------------------- #


def id0_for(key_y: bytes) -> str:
    """ID0 目录名的 32 位十六进制。

    ID0 = ``sha256(keyY)`` 的前 16 字节，每 4 字节按小端读、大端写（等价于逐组反转）。
    """
    digest = hashlib.sha256(bytes(key_y)).digest()[:16]
    return "".join(digest[index:index + 4][::-1].hex() for index in range(0, 16, 4))


def normalize_relative(rel_path: str) -> str:
    """把相对 ID1 目录的路径规范成以 ``/`` 开头的 POSIX 小写路径。"""
    text = str(rel_path).replace("\\", "/").strip()
    if not text.startswith("/"):
        text = "/" + text
    return text.lower()


def sd_ctr_iv(rel_path: str) -> int:
    """SD 文件内容 AES-CTR 计数器（128 位整数）。

    计数器 = ``sha256(utf16le(路径) + b"\\0\\0")`` 的前后两个 16 字节大端异或。
    路径相对 ID1 目录，例如 ``/title/00040000/000f7000/data/00000001.sav``。
    """
    path = normalize_relative(rel_path)
    # 卡上的「存档备份」目录是原样拷走的加密文件，按原位置派生计数器。
    if path.startswith("/" + _RELATIVE_MARKER) and len(path) > 28:
        title_upper = path[12:20]
        title_lower = path[20:28]
        path = f"/title/{title_upper}/{title_lower}/data" + path[28:]
    digest = hashlib.sha256(path.encode("utf-16le") + b"\0\0").digest()
    return int.from_bytes(digest[0:16], "big") ^ int.from_bytes(digest[16:32], "big")


# --------------------------------------------------------------------------- #
# 解密
# --------------------------------------------------------------------------- #


def _engine(keys: ThreedsKeys):
    """构造 pyctr 的 CryptoEngine 并注入 SD 密钥。"""
    try:
        from pyctr.crypto import CryptoEngine, Keyslot
    except ImportError as exc:  # pragma: no cover - 依赖缺失时的清晰报错
        raise ThreedsUnavailable("3DS 直读需要 pyctr，请先安装（pip install pyctr）") from exc

    # 不读取本机默认路径的 boot9：密钥一律来自显式配置的文件或注入值。
    engine = CryptoEngine(setup_b9_keys=False)
    if keys.boot9_prot is not None:
        engine.setup_keys_from_boot9(keys.boot9_prot)
    if keys.key_x_sd is not None:
        engine.set_keyslot("x", Keyslot.SD, keys.key_x_sd)
        engine.set_keyslot("x", Keyslot.CMACSDNAND, keys.key_x_sd)
    engine.setup_sd_key(keys.key_y)
    return engine


class ThreedsSdReader:
    """按 ID1 相对路径解密 SD 上的文件。"""

    def __init__(self, keys: ThreedsKeys) -> None:
        self._keys = keys
        self._engine = _engine(keys)
        self._lock = threading.Lock()

    @property
    def keys(self) -> ThreedsKeys:
        return self._keys

    @property
    def id0(self) -> str:
        """本机 ``Nintendo 3DS/<ID0>`` 目录名。"""
        return id0_for(self._keys.key_y)

    def decrypt_file(self, source: Path, rel_path: str) -> bytes:
        """解密卡上的一个文件，返回明文（不写盘）。"""
        from pyctr.crypto import CTRFileIO, Keyslot

        with Path(source).open("rb") as handle:
            stream = CTRFileIO(handle, self._engine, Keyslot.SD, sd_ctr_iv(rel_path))
            with self._lock:
                return stream.read()

    def decrypt_file_to(self, source: Path, rel_path: str, dest: Path) -> Path:
        """解密一个文件到 ``dest``，返回写入路径。"""
        from pyctr.crypto import CTRFileIO, Keyslot

        target = Path(dest)
        target.parent.mkdir(parents=True, exist_ok=True)
        with Path(source).open("rb") as handle:
            stream = CTRFileIO(handle, self._engine, Keyslot.SD, sd_ctr_iv(rel_path))
            with self._lock, target.open("wb") as out:
                shutil.copyfileobj(stream, out, 1 << 20)
        return target


# --------------------------------------------------------------------------- #
# 条目 → 解密载荷（供备份/状态判定复用）
# --------------------------------------------------------------------------- #

_MATERIALIZED: "OrderedDict[Tuple[Any, ...], Tuple[Path, Path]]" = OrderedDict()
_MATERIALIZED_LOCK = threading.Lock()
_READERS: Dict[Tuple[str, ...], ThreedsSdReader] = {}


def _reader_for(keys: ThreedsKeys) -> ThreedsSdReader:
    token = keys.token
    with _MATERIALIZED_LOCK:
        reader = _READERS.get(token)
    if reader is None:
        reader = ThreedsSdReader(keys)
        with _MATERIALIZED_LOCK:
            _READERS[token] = reader
    return reader


def is_encrypted_sd_entry(entry: SaveEntry) -> bool:
    """该条目是否来自加密 SD（需要在备份/判定时先解密）。"""
    return bool(entry.extra and entry.extra.get("encrypted_sd"))


def relative_path_of(entry: SaveEntry) -> str:
    """条目在 ID1 目录下的相对路径。"""
    extra = entry.extra or {}
    rel = extra.get("sd_rel")
    if not isinstance(rel, str) or not rel:
        raise ThreedsSdError(f"条目缺少 SD 相对路径: {entry.path}")
    return rel


def _source_signature(path: Path) -> Tuple[Any, ...]:
    """廉价的内容指纹：文件用大小+mtime，目录用一级子项签名。"""
    target = Path(path)
    try:
        if target.is_file():
            st = target.stat()
            return ("file", st.st_size, st.st_mtime_ns)
        parts = []
        for child in sorted(target.iterdir()):
            try:
                st = child.stat()
            except OSError:
                continue
            parts.append((child.name, st.st_size, st.st_mtime_ns))
        return ("dir", tuple(parts))
    except OSError as exc:
        raise ThreedsSdError(f"无法读取存档: {path} ({exc})") from exc


def hash_source(entry: SaveEntry, keys: Optional[ThreedsKeys]) -> Path:
    """Path whose bytes stand for this entry (decrypted for encrypted SD saves).

    Used by the status check and the backup so both compare the same plaintext
    payload instead of the raw encrypted file on the card.
    """
    if not is_encrypted_sd_entry(entry):
        return Path(entry.path)
    if keys is None:
        raise ThreedsKeysMissing("未配置 3DS 密钥文件（movable.sed / boot9）")
    return materialized_path(entry, keys)


def materialized_path(entry: SaveEntry, keys: ThreedsKeys) -> Path:
    """把加密条目解密到临时目录并返回明文路径（按内容指纹缓存）。"""
    source = Path(entry.path)
    key = (str(source), _source_signature(source), keys.token)
    with _MATERIALIZED_LOCK:
        cached = _MATERIALIZED.get(key)
        if cached is not None and cached[0].exists():
            _MATERIALIZED.move_to_end(key)
            return cached[0]

    reader = _reader_for(keys)
    root = Path(tempfile.mkdtemp(prefix="vajsave-3ds-"))
    rel = relative_path_of(entry).rstrip("/")
    if source.is_file():
        result = reader.decrypt_file_to(source, rel, root / source.name)
    else:
        result = root / source.name
        for child in sorted(source.iterdir()):
            if not child.is_file():
                continue
            reader.decrypt_file_to(child, f"{normalize_relative(rel)}/{child.name}", result / child.name)

    with _MATERIALIZED_LOCK:
        _MATERIALIZED[key] = (result, root)
        while len(_MATERIALIZED) > _CACHE_LIMIT:
            _evicted, (_path, temp_root) = _MATERIALIZED.popitem(last=False)
            shutil.rmtree(temp_root, ignore_errors=True)
    return result


def clear_cache() -> None:
    """丢弃已解密的临时文件（测试与退出时调用）。"""
    with _MATERIALIZED_LOCK:
        roots = [root for _path, root in _MATERIALIZED.values()]
        _MATERIALIZED.clear()
        _READERS.clear()
    for root in roots:
        shutil.rmtree(root, ignore_errors=True)


atexit.register(clear_cache)


def display_name_for(low_id: str) -> str:
    """直读条目的显示名。

    卡上没有任何可离线读取的游戏名（名称在主机 NAND 的 title.db，封面在需要
    titlekey 的加密内容里），所以先用 8 位 Title ID 占位；用户在本地备份库里改名，
    或投放 ``covers/3ds/<Title ID>.png``，都能让画廊正常识别。
    """
    return low_id.upper()
