# vaj-save 开发说明

用户视角的使用说明在仓库根目录的 [README](../README.md)；这里是维护者需要的实现细节。

## 环境与测试

Python 3.11+。

```bash
pip install -e ".[dev]"
pytest
```

`pytest` 默认带覆盖率统计（`--cov=vajsave --cov-branch`）。`vajsave/app.py` 与 `vajsave/qt_ui.py` 在覆盖率里被排除，但 `tests/test_qt_ui.py` 仍会真实执行 UI 逻辑。

## 打包

- macOS：`scripts/build-macos-app.sh` + `packaging/vaj-save.spec`，产物 `dist/vaj-save.app`
- Windows：`scripts/build-windows-app.ps1`（或 `.bat`）+ `packaging/vaj-save-windows.spec`，产物 `dist/vaj-save/`
- 两个 spec 都通过 `collect_submodules("vajsave")` 收集子模块，并打包 `src/vajsave/data/`（内置元数据索引）。`packaging/entrypoint.py` 调用 `vajsave.app:main`。

## 模块结构

- `volume*.py` / `device_registry.py` / `device_session.py`：卷枚举、卷序列号身份、绑定来源
- `scanner.py` + `platforms/`：按机种识别并列出存档（只读、浅层指纹）
- `identity/` + `metadata/`：ROM 摘要身份解析与离线元数据索引
- `artwork/` + `covers.py`：封面解析、下载与缓存
- `library*.py` / `backup_jobs.py` / `jobs.py`：备份库、版本、ZIP 导入导出、可取消任务
- `ftp_*.py` / `mtp_*.py` / `baidu_*.py`：远程拉取与网盘同步
- `qt_ui.py` / `qt_dialogs.py` / `ui_theme.py`：桌面界面，颜色与尺寸全部取自 `ui_theme` 的 Token
- `threeds_sd.py`：3DS 加密 SD 直读的密钥校验、路径派生与解密

## 内置元数据索引

由真实 No-Intro DAT 离线生成，记录按 `(sha1, crc32, title)` 排序，可复现：

```bash
python tools/build_metadata_index.py --platform gba --dat "Nintendo - Game Boy Advance (<version>).dat" --out src/vajsave/data/libretro/gba.json
python tools/build_metadata_index.py --platform nds --dat "Nintendo - Nintendo DS (<version>).dat" --out src/vajsave/data/libretro/nds.json
python tools/build_metadata_index.py --platform gb --dat "Nintendo - Game Boy (<version>).dat" --out src/vajsave/data/libretro/gb.json
python tools/build_metadata_index.py --platform gbc --dat "Nintendo - Game Boy Color (<version>).dat" --out src/vajsave/data/libretro/gbc.json
```

仓库内置 `gba.json` 与 `nds.json`；GB/GBC 未随包附带索引，身份仍可用 ROM 头与文件名，封面按标题走 libretro Named_Boxarts。用户自定义索引的查找顺序：应用配置里的 `libretro_dir` → `<备份库>/metadata/libretro/` → 程序内置目录。解析结果缓存到 `<备份库>/game_metadata.json`（按 `GameIdentity.identity_key` 为键，原子写入、线程安全）。

## JKSV / JKSM 布局假设

真实目录因版本而异，Switch JKSV 与 3DS JKSM 都可能使用根目录名 `JKSV/`，靠子目录区分。

Switch（跳过保留名 `Saves` / `ExtData` / `SysSave` / `Boss` / `Shared` / `_TRASH_`）：

- `JKSV/<Game>/`
- `JKSV/<Game>/<slot>/`
- `JKSV/<Game>/<user>/<slot>/`

3DS JKSM：`JKSV/Saves/<Game>/<slot>/`，同理 `ExtData` / `SysSave`。

`Nintendo 3DS/` 里是系统加密容器；未配置密钥时只标记为加密卡，不把其中文件列成存档。

## 封面细节

- 页眉提到的四层优先级（用户投放 → 自动下载 → 存档内图标 → 灰色占位）是稳定的对外行为，改动前先看 `tests/test_covers.py` 与 `tests/test_artwork.py`。
- 存档内图标的固定名单匹配顺序：`icon0.*` → `icon.*` → `pic1.*` → `thumb.*` → `preview.*` → `folder.*` → `cover.*` → `banner.*` → `boxart.*`；查找顺序为存档目录固定名单 → 一级子目录固定名单（`sce_sys/` → `media/` → `icon/` 优先）→ 存档目录通用扫描 → 一级子目录通用扫描。固定名单始终优先于通用扫描。
- 超过 8 MiB 的候选图一律跳过；横向素材不会进入画廊，但文件不会被删除。
- PSP 的 `144×80` 横向 `ICON0.PNG` 会保留在存档中，但不作为封面；找不到竖版封面时显示中性占位。

## 3DS 加密 SD 直读

目标：不依赖 Checkpoint，直接读取 SD 卡 `Nintendo 3DS/` 里的存档。

- 加密方式：`Nintendo 3DS/<ID0>/<ID1>` 下的内容由 keyslot `0x34` 派生密钥，密钥 = 3DS key scrambler(`keyX_boot9`, `keyY_movable.sed`)；每个文件的 AES-CTR 计数器由「相对 ID1 的路径」推出（`sha256(utf16le(路径) + b"\0\0")` 前后 16 字节大端异或）。ID0 目录名 = `sha256(keyY)` 前 16 字节逐 4 字节反转。
- 密钥来源：`movable.sed`（keyY，偏移 0x110）+ `boot9_prot.bin`（keyX，含 SHA-256 校验）。只保存路径，不复制内容。
- 容器与解密实现复用 [pyctr](https://github.com/ihaveamac/pyctr)（MIT，ninfs 的底层库）；本仓库的 `threeds_sd.py` 负责密钥校验、路径派生、缓存与错误信息。
- 扫描阶段只列路径（不进 `content/`，不解密）；解密发生在备份与状态判定时，结果落在系统临时目录并注册 `atexit` 清理。加密条目的状态比较用**解密后**的载荷哈希，因此 `backup_save` 支持 `source_override`：从解密路径复制，`snapshot.source_path` 仍记录设备真实路径。
- 测试：`tests/test_threeds_sd.py`。派生结果与 pyctr 交叉验证（ID0、路径 IV），加解密用 pyctr 的写入路径造出「卡上的加密文件」再要求本模块解回原文。**真实主机数据无法离线取得**，因此真机验证靠 `tools/check-threeds-sd.py`（只读；核对 ID0 是否匹配这张卡，以及解密后是否为 `DISA` / `DIFF` 容器）。
