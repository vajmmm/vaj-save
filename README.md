# vaj-save

掌机存档管理器（只读扫描）。识别已挂载的 USB / SD 读卡器，列出 PSP、Vita、Switch、3DS 导出目录中的存档。

不对设备写入，不解密原生存档容器。

## 安装

```bash
pip install -e ".[dev]"
```

## 用法

```bash
# 扫描指定路径（U 盘、SD 读卡器挂载点、目录拷贝）
vajsave scan /Volumes/YOUR_VOLUME
vajsave scan /Volumes/YOUR_VOLUME --json

# 扫描当前发现的可移动卷
vajsave scan

# 监听插拔，新卷出现时自动扫描
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
- 存档解密、重签、写回、编辑器、GUI。

## JKSV 布局假设

真实 JKSV 目录因版本而异。当前检测：存在 `JKSV/` 且有子目录即尝试列举。支持：

- `JKSV/<Game>/`（无子目录）
- `JKSV/<Game>/<slot>/`
- `JKSV/<Game>/<user>/<slot>/`

## 3DS 加密 SD

`Nintendo 3DS/` 里是系统加密容器，本工具不会把其中文件列成存档。请先在机上用 Checkpoint 导出到 `3ds/Checkpoint/saves/`，再扫描。
