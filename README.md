# vaj-save

像素风掌机存档实验室：备份、版本槽位、收藏和导出。识别 PSP / Vita / Switch / 3DS 的 USB 或 SD 导出目录。

- 卡带柜按机种分类（PSP / Vita / Switch / 3DS / NDS / GBA）
- 备份到 `~/Documents/vaj-save/`，相同内容去重，变化则新开 SAVE SLOT
- 收藏、备注、搜索；版本可恢复到文件夹或导出 ZIP
- 扫描设备只读；写回请把恢复目标选成 Checkpoint / JKSV / SAVEDATA 目录


## macOS App（双击打开）

打包成窗口程序，不需要自己跑 Python 命令：

```bash
PYTHON=python3 ./scripts/build-macos-app.sh
open dist/vaj-save.app
```

产物是 `dist/vaj-save.app`，可拖到「应用程序」里，以后双击启动。

开发机需要 Python 3.11+ 才能**构建**；打好的 `.app` 给别人用时不要求对方先装 Python。

## 开发安装 / 命令行

```bash
pip install -e ".[dev]"

vajsave scan /Volumes/YOUR_VOLUME
vajsave scan /Volumes/YOUR_VOLUME --json
vajsave scan
vajsave watch
```

## 支持的挂载方式

| 方式 | 识别条件 | 说明 |
|------|----------|------|
| PSP USB | `PSP/SAVEDATA/` | 机上打开「USB 连接」后插线，或 Memory Stick 读卡器 |
| Vita USB | `user/00/savedata/` 或 `ux0/user/00/savedata/` | VitaShell USB 模式 |
| Vita 导出 | `data/savegames/` | Vita Save Manager 解密导出 |
| Vita + Adrenaline | 同时有 Vita 目录和 `pspemu/PSP/SAVEDATA/` | 一条卷上会列出两个 source |
| Switch Checkpoint | `switch/Checkpoint/saves/` | Hekate UMS 或拔卡读卡器 |
| Switch JKSV | `JKSV/` | 按游戏 / 用户 / 槽位宽松列举 |
| Switch SD | 仅有 `atmosphere/` 或 `switch/` | 识别为 Switch 卡，存档列表可为空 |
| 3DS Checkpoint | `3ds/Checkpoint/saves/` | 拔卡读卡器 |
| 3DS 加密 SD | 仅有 `Nintendo 3DS/` | 只标记为加密卡，**不当作可管理存档** |

无上述指纹的普通 U 盘会标为 `unknown`，不会误报成某台掌机。

## 本轮不做

- **Switch DBI / Checkpoint MTP**：macOS 上 MTP 不稳定。请用 Hekate UMS 挂整张 SD，或拔卡。
- 存档解密、重签、写回、编辑器。只读管理。

## JKSV 布局假设

真实 JKSV 目录因版本而异。当前检测：存在 `JKSV/` 且有子目录即尝试列举。支持：

- `JKSV/<Game>/`（无子目录）
- `JKSV/<Game>/<slot>/`
- `JKSV/<Game>/<user>/<slot>/`

## 3DS 加密 SD

`Nintendo 3DS/` 里是系统加密容器，本工具不会把其中文件列成存档。请先在机上用 Checkpoint 导出到 `3ds/Checkpoint/saves/`，再扫描。
