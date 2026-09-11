# vaj-save

掌机存档实验室：备份、版本槽位、收藏和导出。识别 PSP / Vita / Switch / 3DS / NDS / GBA 的 USB 或 SD 导出目录。

- 卡带柜按机种分类（PSP / Vita / Switch / 3DS / NDS / GBA）
- 备份到 `~/Documents/vaj-save/`（可在「设置」里改），相同内容去重，变化则新开 SAVE SLOT
- 收藏、备注、搜索；版本可恢复到文件夹或导出 ZIP
- 扫描设备只读；写回请把恢复目标选成 Checkpoint / JKSV / SAVEDATA 目录


## 设备检测（Windows）

用 Win32 `GetLogicalDrives` / `GetDriveTypeW` / `GetVolumeInformationW` 枚举盘符，不再硬编码 D..Z 或依赖目录是否存在：

- 列出可移动盘与固定盘，跳过光驱、网络映射盘、无根目录的盘
- 显示卷标，例如 `F: KINGSTON`；空卷标显示为 `E: 可移动磁盘`
- 可移动盘排在固定盘前面；同组按盘符字母序
- 对每个盘做**浅层**指纹识别（只看根目录和少数固定子目录，不做全盘递归），能认出来就在卷信息里标注机种

**默认选中**：启动和点「刷新」时，优先选**可移动盘**，同组里选**盘符靠后**的那个。所以 C/D/E 内置盘 + U 盘 F: 的场景会默认选中 F:；没有可移动盘时回退到字母最大的盘（不会选 C:）。你自己点选的设备不会被自动选中逻辑替换。

U 盘，以及以 USB 大容量存储（UMS）方式直连的掌机（Hekate UMS 挂整张 SD、读卡器、PSP/Vita 的 USB 连接模式）都会得到一个盘符，因此都能被检测到。


## 备份路径设置

工具栏「设置」里可以改本地备份库路径，选择后会立即生效并记住（下次启动沿用）：

| 平台 | 配置文件位置 |
|------|--------------|
| Windows | `%APPDATA%\vaj-save\config.json` |
| macOS | `~/Library/Application Support/vaj-save/config.json` |
| Linux | `$XDG_CONFIG_HOME/vaj-save/config.json`（或 `~/.config/vaj-save/config.json`） |

配置读写失败会安全回退到默认路径（`~/Documents/vaj-save/`），不影响启动。


## 封面

中间「游戏」列的每一行存档会优先显示真实封面缩略图，按下面的优先级取图：

1. **存档目录内的内嵌图标**（扫描设备时自动发现，只读，不做整盘递归）：
   - 先按固定名单匹配（大小写不敏感，优先级从高到低）：`icon0.*` → `icon.*` → `pic1.*` → `thumb.*` → `preview.*` → `folder.*` → `cover.*` → `banner.*` → `boxart.*`，扩展名支持 `png` `jpg` `jpeg` `webp` `bmp` `gif`
   - 例：PSP `SAVEDATA/<游戏>/ICON0.PNG`、Vita `.../savedata/<游戏>/sce_sys/icon0.png`
   - 查找顺序：存档目录的固定名单 → 一级子目录的固定名单（`sce_sys/` → `media/` → `icon/` 优先，其余按名排序）→ 存档目录的**通用扫描** → 一级子目录的通用扫描
   - 通用扫描只取图片扩展名，按「文件名含 `cover`/`icon`/`box` 优先，其次按小写升序」的确定顺序，跳过 symlink
   - 固定名单**始终优先于**通用扫描（任意深度），所以存档目录里多出来的截图不会顶掉官方图标（Vita 的 `sce_sys/icon0.png` 一定胜出）
   - 裸存档文件（GBA/NDS 的 `.sav`）会找同名的 `<游戏名>.png` / `.jpg`
   - **超过 8 MiB 的候选文件一律跳过**（避免极小体积却声明超大画布的 PNG 拖垮界面）
2. **用户投放的封面**：放到本地备份库的
   `<备份库>/covers/<机种>/<名称>.<扩展名>`
   - `<机种>` 取 `psp` / `vita` / `switch` / `3ds` / `nds` / `gba`
   - `<名称>` 取该存档的 `title_id`（没有时用游戏名），文件名中的 Windows 非法字符（`:` `*` `?` 等）会被替换成 `_`，所以**不会**用含 `:` 的 game key 当文件名
   - 支持的扩展名：`png` `jpg` `jpeg` `webp` `bmp` `gif`，同名时任选一种即可
3. 都没有时，该行显示一个浅灰色方块占位（不会出现留白或字母水印）。

示例（备份库为默认的 `~/Documents/vaj-save/`）：

```
# PSP 存档 ULJM05800 的封面
~/Documents/vaj-save/covers/psp/ULJM05800.png

# Vita 游戏 Persona 4 Golden（无 title_id 时用游戏名）
~/Documents/vaj-save/covers/vita/Persona 4 Golden.jpg
```

封面缩略图采用 **「cover」填充**语义：先按 32x32 方块比例居中裁剪、再缩放到 6px 圆角的方形图。因此缩略图一定会被填满，但极端宽高比的图片会被**裁掉上下或左右边缘**（不会被拉伸变形，也不会留黑边）。超过 8 MiB 的文件、以及超出上限的目标尺寸会被跳过；文件缺失或损坏时静默回退到浅灰色方块，UI 不会报错。


## macOS App（双击打开）

打包成窗口程序，不需要自己跑 Python 命令：

```bash
PYTHON=python3 ./scripts/build-macos-app.sh
open dist/vaj-save.app
```

产物是 `dist/vaj-save.app`，可拖到「应用程序」里，以后双击启动。

开发机需要 Python 3.11+ 才能**构建**；打好的 `.app` 给别人用时不要求对方先装 Python。

## Windows 应用（双击 .exe）

必须在 **Windows** 上构建（PyInstaller 不能从 macOS 交叉编译出可用的 Windows 包）：

```powershell
# PowerShell，Python 3.11+ 已安装
$env:PYTHON = "python"
.\scripts\build-windows-app.ps1
```

或 `scripts\build-windows-app.bat`。产物是 `dist\vaj-save\vaj-save.exe`，请把整个 `dist\vaj-save\` 文件夹拷走（不要只拷 exe）。对方不需要先装 Python。

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
| Switch JKSV | `JKSV/<Game>/...` | 按游戏 / 用户 / 槽位宽松列举；跳过 Saves/ExtData 等保留名 |
| Switch SD | 仅有 `atmosphere/` 或 `switch/` | 识别为 Switch 卡，存档列表可为空 |
| 3DS Checkpoint | `3ds/Checkpoint/saves/` | 拔卡读卡器 |
| 3DS JKSM | `JKSV/Saves/`（或 ExtData/SysSave） | 与 Switch JKSV 同根目录名，按子目录区分 |
| 3DS 加密 SD | 仅有 `Nintendo 3DS/` | 只标记为加密卡，**不当作可管理存档** |
| GBA EZ-Flash | `SAVER/*.sav` | 只认固定目录，不全盘搜 `.sav` |
| GBA EverDrive | `GBASYS/SAVE/*.{sav,srm,fla,eep}` | Mini / X5 |
| GBA EverDrive Pro | `EDGBA/gamedata/<rom>/bram.*` | display_name 为游戏文件夹名 |
| GBA SuperChis / SuperFW | `SAVEGAME/*.sav`；有 `.superfw/` 时也认 `SAVES/*.sav` | SuperCard/SuperChis 默认目录；`SAVES/` 太泛，无指纹不收 |
| NDS TWiLight | 目录内有 `.nds` 且 `saves/*.sav` | 常见于 `roms/nds/` |
| NDS R4/Wood | 同目录 `.nds` + 同名 `.sav` | 需卡根指纹（`_nds/` / `R4.dat` / `TTMenu/` / `_system_/`）或 `roms/nds`；孤立 `.sav` 不收 |

无上述指纹的普通 U 盘会标为 `unknown`，不会误报成某台掌机。

## 本轮不做

- **Switch DBI / Checkpoint MTP**：macOS 上 MTP 不稳定。请用 Hekate UMS 挂整张 SD，或拔卡。
- **MTP / WPD 直连探测**：以 MTP 模式直连的掌机（如部分 Switch DBI 连接）不分配盘符，本工具的盘符枚举看不到它，需要一个单独的 WPD 协议栈才能枚举；目前不做。
- 存档解密、重签、写回、编辑器。只读管理。

## JKSV / JKSM 布局假设

真实 JKSV 目录因版本而异。**Switch JKSV** 与 **3DS JKSM** 都可能使用根目录名 `JKSV/`，靠子目录区分：

Switch（跳过保留名 `Saves` / `ExtData` / `SysSave` / `Boss` / `Shared` / `_TRASH_`）：

- `JKSV/<Game>/`（无子目录）
- `JKSV/<Game>/<slot>/`
- `JKSV/<Game>/<user>/<slot>/`

3DS JKSM：

- `JKSV/Saves/<Game>/<slot>/`
- 同理 `ExtData` / `SysSave`

## 3DS 加密 SD

`Nintendo 3DS/` 里是系统加密容器，本工具不会把其中文件列成存档。请先在机上用 Checkpoint 或 JKSM 导出，再扫描。
