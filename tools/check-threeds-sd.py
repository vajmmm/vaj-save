#!/usr/bin/env python3
"""在真实的 3DS SD 卡上核对加密存档直读链路。

用法::

    python tools/check-threeds-sd.py /Volumes/YOUR_SD \\
        --movable-sed ~/keys/movable.sed --boot9 ~/keys/boot9_prot.bin

它会做三件主机数据相关的自检：

1. 用 ``movable.sed`` 推出 ID0，确认卡上真的存在这个目录（密钥属于这张卡的最强证据）；
2. 列出 ``title/*/*/data/*.sav`` 与 ``extdata`` 条目；
3. 解密每个存档的头部，检查是否出现 ``DISA`` / ``DIFF`` 容器魔数。

只读：不写卡、不改文件。密钥只用于当场派生，不落盘。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SRC = PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from vajsave import threeds_sd  # noqa: E402
from vajsave.scanner import scan  # noqa: E402

CONTAINER_MAGIC_OFFSET = 0x100


def _container_kind(payload: bytes) -> str:
    """解密后的前 0x110 字节里是否有 DISA/DIFF 容器魔数。"""
    window = payload[CONTAINER_MAGIC_OFFSET:CONTAINER_MAGIC_OFFSET + 0x10]
    if window[:4] == b"DISA":
        return "DISA"
    if window[:4] == b"DIFF":
        return "DIFF"
    if payload[:0x20] == b"\0" * 0x20:
        return "未格式化（全零）"
    return "无容器魔数"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="核对 3DS 加密 SD 直读链路")
    parser.add_argument("root", help="SD 卡挂载点或 Nintendo 3DS 目录")
    parser.add_argument("--movable-sed", required=True, help="主机导出的 movable.sed")
    parser.add_argument("--boot9", required=True, help="GodMode9 导出的 boot9_prot.bin 或 boot9.bin")
    args = parser.parse_args(argv)

    root = Path(args.root)
    try:
        keys = threeds_sd.ThreedsKeys.from_paths(Path(args.movable_sed), Path(args.boot9))
    except threeds_sd.ThreedsSdError as exc:
        print(f"[失败] 密钥文件不可用: {exc}")
        return 2

    id0 = threeds_sd.id0_for(keys.key_y)
    base = root if root.name == "Nintendo 3DS" else root / "Nintendo 3DS"
    id0_dir = base / id0
    print(f"ID0           {id0}")
    print(f"卡上 ID0 目录  {id0_dir}  ->  {'存在（密钥匹配本机）' if id0_dir.is_dir() else '不存在（密钥可能来自另一台主机）'}")
    if not id0_dir.is_dir():
        print("后续检查已跳过：ID0 不匹配时解密必然失败。")
        return 1

    result = scan(root, threeds_keys=keys)
    for warning in result.warnings:
        print(f"[警告] {warning}")
    print(f"发现存档      {len(result.saves)} 个")

    reader = threeds_sd.ThreedsSdReader(keys)
    ok = 0
    for save in result.saves:
        rel = threeds_sd.relative_path_of(save)
        source = Path(save.path)
        kind_of_save = str((save.extra or {}).get("kind", "?"))
        if source.is_dir():
            # extdata：一组各自独立加密的文件，没有容器魔数可查
            files = sorted(child for child in source.iterdir() if child.is_file())
            decrypted = 0
            for child in files:
                try:
                    reader.decrypt_file(child, f"{threeds_sd.normalize_relative(rel).rstrip('/')}/{child.name}")
                    decrypted += 1
                except Exception as exc:  # noqa: BLE001 - 自检脚本要把失败原因打全
                    print(f"  解码失败  {save.display_name} {child.name}: {exc}")
            print(f"  {save.display_name:<10} {kind_of_save:<8} extdata（{decrypted}/{len(files)} 个文件已解密）  {rel}")
            ok += 1 if decrypted == len(files) and files else 0
            continue
        try:
            head = reader.decrypt_file(source, rel)[:CONTAINER_MAGIC_OFFSET + 0x10]
        except Exception as exc:  # noqa: BLE001 - 自检脚本要把失败原因打全
            print(f"  解码失败  {save.display_name} {rel}: {exc}")
            continue
        magic = _container_kind(head)
        if kind_of_save == "title" and magic in {"DISA", "DIFF"}:
            ok += 1
        print(f"  {save.display_name:<10} {kind_of_save:<8} {magic:<12} {rel}")

    print(f"通过校验      {ok}/{len(result.saves)} 个条目")
    return 0 if ok == len(result.saves) and result.saves else 1


if __name__ == "__main__":
    sys.exit(main())
