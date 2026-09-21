# vaj-save 本地库、任务、GB/GBC 与 FTP 增量

日期：2026-09-21

## 目标

在保持只读、不写掌机的前提下，补齐日常使用缺口，并把已经过厚的 `AppState` 拆成可独立测试的服务。完成后用户可以：按版本删除、把 ZIP 导回本地库、看着进度备份并取消、在 GB/GBC 独立机种下管理存档、增量拉取 FTP、用卡上的文件系统卷序列号绑定存档根目录以免每次全盘探测，以及在 Qt 里使用收藏、ROM 候选、备份有更新、恢复目录记忆和插入后自动备份。

## 非目标

- MTP / WPD 直连
- 存档解密、重签、直接写回掌机、存档编辑器
- 重写 Tk `app_ui.py` 的新功能（Qt 是主界面；Tk 只要还能启动即可）
- 把 `qt_ui.py` 里的画廊/货架再拆一遍（只把对话框和任务接线拆走）
- 系统钥匙串；FTP 密码可选写入 `config.json`
- 云同步、备份加密
- 用 USB 读卡器/转接头的硬件 ID 识别设备（同一只转接头插不同卡会撞号）
- 用卷标或盘符做主标识

## 约束

- Python 3.11+，测试用 `pytest`
- 颜色与布局遵循 `DESIGN.md` 与 `vajsave.ui_theme`
- 跨平台路径，不硬编码盘符
- 新模块单文件控制在约 400 行；禁止把功能继续堆进 `app_state.py` / `qt_ui.py` / `library.py`
- Qt 与现有测试继续只调用 `AppState` 公开方法；服务是内部实现
- 拆分阶段行为与现网一致，先绿再加功能
- 备份、恢复、FTP 仍不写入掌机

---

## 1. AppState 拆分

`AppState` 留下视图状态和转发方法，方法签名保持稳定。

视图状态仍在 `AppState`：`library_mode`、`selected_platform`、`search_query`、`starred_only`、`hide_unchanged`、`current_mount`、`current_result`、`volumes`、`warnings`、`status_text`、`_backup_statuses`、进度快照。

| 服务 | 新文件 | 职责 |
|------|--------|------|
| SettingsStore | `src/vajsave/settings_store.py` | 读写 `config.json`：备份库、ROM 目录、libretro、FTP 主机/端口/用户/可选密码、LLM、上次恢复目录、自动备份开关 |
| DeviceSession | `src/vajsave/device_session.py` | 卷列表、自定义目录、首选可移动设备、热插拔、读取卷序列号并交给 DeviceRegistry |
| DeviceRegistry | `src/vajsave/device_registry.py` | 卷序列号 → 相对存档根目录的持久绑定 |
| ScanSession | `src/vajsave/scan_session.py` | `begin/prepare/apply` 扫描与备份状态哈希；命中绑定则只扫已知目录 |
| LibraryActions | `src/vajsave/library_actions.py` | 备份、恢复、导出、删除游戏/版本、导入 ZIP、备注、收藏、可见列表过滤所需的目录读写 |
| FtpSession | `src/vajsave/ftp_session.py` | 预设、配置、增量拉取 |
| Enrichment | `src/vajsave/enrichment.py` | 身份、元数据、封面、LLM 消歧 |

`PLATFORM_ORDER` / `PLATFORM_LABELS` 从 `app_state.py` 挪到 `src/vajsave/platforms/catalog.py`，避免 UI 为了机种表去拉整个会话。

`AppState.__init__` 组装上述服务。现有 `set_library_root`、`import_save`、`select_mount` 等方法变成一行转发，测试文件不改 import。

---

## 2. 按版本删除

删除单个版本的结果类型与函数放在新文件 `src/vajsave/library_versions.py`，复用 `library.py` 已有的 `_delete_snapshot_payload` / `delete_game`，不把 `library.py` 再加长。`library.py` 可再导出这两个名字，方便旧 import。

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


def delete_snapshot(library_root: Path, game_id: str, snapshot_id: str) -> SnapshotDeletion:
    ...
```

规则：

- 只删除解析后严格位于 `library_root` 内的版本目录
- 从不修改设备上的文件
- 目标版本不存在：`found=False`
- 删除成功后从 `GameRecord.versions` 去掉该快照并写回 catalog
- **删掉该游戏最后一个版本时调用现有 `delete_game`**：catalog 条目、封面、备注、收藏一并消失，`game_removed=True`
- 不安全路径或 `OSError`：保留 catalog 条目，`removed=False`，写入 `errors`

`AppState.delete_library_snapshot(entry, snapshot) -> SnapshotDeletion`

Qt 详情抽屉版本表选中一行后出现「删除此版本」，确认文案说明不可撤销；若这是最后一版，文案改为将删除整条游戏。

---

## 3. ZIP 导出清单与导入

新模块 `src/vajsave/library_import.py`（导出清单 + 导入，避免继续膨胀 `library.py`）。

### 导出

现有 `export_snapshot_zip` 改为写出：

```
archive.zip
  vaj-save.json
  payload/<原快照目录内容>
```

`vaj-save.json`：

```json
{
  "format": "vaj-save-snapshot",
  "format_version": 1,
  "platform": "psp",
  "title_id": "ULJM05800",
  "display_name": "Persona 2",
  "identity_key": "psp:ULJM05800",
  "slot": "default",
  "user": null,
  "created_at": "2026-01-01T12:00:00",
  "sha256": "<snapshot.sha256>"
}
```

缺少的字段用空字符串或 `null`。旧调用方只关心得到一个 `.zip` 路径，返回值不变。

### 导入

```python
def import_snapshot_zip(
    library_root: Path,
    zip_path: Path,
    *,
    attach_game_id: str | None = None,
    new_platform: str | None = None,
    new_title_id: str | None = None,
    new_display_name: str | None = None,
    new_slot: str | None = None,
) -> BackupResult:
    ...
```

安全：

- Zip Slip：每个成员解码后必须落在临时解压根之内，否则拒绝整个 ZIP
- 解压到库内临时目录，成功后再走现有 `backup_save` 的内容寻址拷贝，失败则删除临时目录
- 相同 `sha256` 的内容复用已有版本（`is_new=False`）

挂接顺序：

1. 调用方传入 `attach_game_id` 且 catalog 中存在 → 挂到该游戏
2. 清单里的 `identity_key` 命中某条 `GameRecord.identity_key` → 挂上
3. 用清单的 `platform` + (`title_id` 或 `display_name`) + `slot` 生成 `game_key` 命中 → 挂上
4. 否则用清单字段新建游戏
5. **无清单的旧 ZIP**：必须提供 `attach_game_id`，或提供 `new_platform` + `new_display_name`（`title_id`/`slot` 可选）。缺这些参数则失败，不猜测

Qt：本地库模式下 Dock「导入 ZIP」；顶栏菜单同样有此项。无清单时弹出：挂到当前选中游戏 / 填写机种与名称新建。

---

## 4. 可取消备份任务

新模块：

- `src/vajsave/jobs.py`：`JobProgress`（`message`, `current`, `total`, `cancellable`）、`JobCancelled`、协作式 `CancelToken`
- `src/vajsave/backup_jobs.py`：对一组 `SaveEntry` 顺序调用现有 `backup_save`

一次只跑一个任务（扫描哈希、备份、FTP 互斥）。底栏进度条复用；`cancellable=True` 时显示「取消」。

取消粒度：两个存档之间。已经写入的版本保留。状态：「已备份 N 个，已取消」。

`AppState`：

- `import_selected_saves` 改为走任务（仍可同步给测试：测试传入立即完成的 token）
- `backup_updated_saves()`：当前设备可见列表里 `status in {new, changed}` 的项
- `cancel_job()`

Qt 顶栏「备份有更新」调用 `backup_updated_saves`。备份过程中主按钮禁用，避免重入。

### 插入后自动备份

`config.json`：`"auto_backup_on_insert": false`（默认关）。

设置里复选框「插入后自动备份有更新的存档」。

触发：热插拔 `appeared` → 扫描 + 哈希完成 → 若开关打开、非本地库模式、存在 new/changed → 启动 `backup_updated_saves`。用户正在手动备份或 FTP 时不打断。

---

## 5. GB / GBC 独立机种

Dock 顺序：

`all / psp / vita / switch / 3ds / nds / gb / gbc / gba`

| 键 | 标签 | 平台色（仅 pip / Dock 图标） |
|----|------|------------------------------|
| gb | GB | `#9aa56a`（DMG 橄榄） |
| gbc | GBC | `#ff6b8a` |

需要新 SVG：`src/vajsave/data/platform-gb.svg`、`platform-gbc.svg`。

ROM 扩展名（`rom_formats.py`）：

- gb: `.gb`
- gbc: `.gbc`

存档扩展名：`.sav`、`.srm`。

### 扫描

`platforms/gb.py` 与 `platforms/gbc.py` 分开，共用 `platforms/cartridge_files.py`（同名 ROM+存档遍历、顶层 `roms/<platform>`、`saves/`）。不回改 NDS/GBA 扫描器。

GB 指纹：

- `roms/gb/`
- EverDrive GB：`EDGB/`、`GBOS/`
- 目录内有 `.gb` 且旁有同名 `.sav`/`.srm` 或 `saves/`

GBC 指纹：`roms/gbc/`、目录内 `.gbc` 配合同名存档。

**SAVER 分流（EZ-Flash）**：现有 GBA `SAVER/*.sav` 保留。若同卷上能找到同名 ROM：

- `foo.gb` → 该 `foo.sav` 记为 `gb`
- `foo.gbc` → `gbc`
- `foo.gba` / `.agb` → `gba`
- 找不到 ROM → 仍为 `gba`（兼容已有备份键）

`scanner._detected_platforms` / `guess_platform` 加入 gb/gbc 浅层指纹。

### 身份与封面

`identity/gb.py`、`identity/gbc.py` 与 GBA 一样走 `resolve_rom_identity`。ROM 头标题偏移 `0x134`。设置增加「GB ROM 目录」「GBC ROM 目录」，并允许在掌机卡上搜索（与 GBA/NDS 相同的手持标记逻辑，标记集加上 `roms/gb`、`roms/gbc`、`EDGB`、`GBOS`）。

Libretro：

- 系统名：`Nintendo - Game Boy`、`Nintendo - Game Boy Color`
- `metadata/libretro.py` 的 `SUPPORTED_PLATFORMS` 纳入 `gb`、`gbc`
- 实现时若工作区没有 No-Intro GB/GBC DAT，不提交空的 `gb.json`/`gbc.json`。身份仍用 ROM 头与文件名；封面按标题走 libretro Named_Boxarts。README 写明用 `build_metadata_index.py` 生成索引的命令。用户自定义 `libretro_dir` 里的 DAT 仍按现有查找顺序生效。

`set_rom_dirs` 保持现有位置参数 `(gba_rom_dir, nds_rom_dir)`，新增可选关键字 `gb_rom_dir`、`gbc_rom_dir`，默认哨兵表示不改。

---

## 6. 设备卷绑定

识别的是 **卡上的文件系统**，不是 USB 转接头。同一张 TF/SD 换读卡器或走机内 UMS，序列号不变；同一只转接头换另一张卡，序列号不同。

### 标识

`src/vajsave/volume_id.py` 只负责读号，失败返回 `None`，不抛给 UI。

| 系统 | 来源 |
|------|------|
| Windows | `GetVolumeInformationW` 的卷序列号（现有枚举已调用该 API，补读 DWORD，格式 `win:{8位十六进制}`） |
| macOS | 卷 UUID（`getattrlist` ATTR_VOL_UUID，失败则忽略绑定） |
| Linux | 该挂载点的文件系统 UUID（`findmnt -n -o UUID`，失败则忽略绑定） |

没有稳定序列号 → 这次仍全量扫描，不写绑定。不用卷标、盘符、USB VID/PID 凑弱指纹。

自定义文件夹（「添加设备」）用解析后的绝对路径当键，前缀 `path:`。FTP 缓存用 `ftp:{preset_key}`，不走卷号。

### 存储

与 `config.json` 同级的 `devices.json`（`SettingsStore` 的配置目录，由 `DeviceRegistry` 读写）：

```json
{
  "format": "vaj-save-devices",
  "format_version": 1,
  "devices": {
    "win:ABCD1234": {
      "label": "SWITCH SD",
      "updated_at": "2026-09-21T12:00:00",
      "sources": [
        {
          "platform": "switch",
          "source_id": "switch_checkpoint",
          "relative_root": "switch/Checkpoint/saves"
        }
      ]
    }
  }
}
```

`relative_root` 相对挂载根，POSIX 斜杠。写入前验证相对路径不含 `..`。原子写，损坏则当空表。

### 扫描

第一次（或无绑定 / 绑定目录全部失踪）：现有全量扫描。成功后把 `ScanResult.sources` 转成相对路径写入该卷记录。一条卷可有多个 source（Vita + Adrenaline PSP）。

之后插入同一张卡：

1. 读卷序列号
2. 绑定里至少有一个 `mount / relative_root` 仍是目录
3. `scan(root, bound_sources=...)` 只跑这些目录对应的平台扫描器，枚举其中存档（新游戏会出现）
4. 跳过其它机种的全盘探测

「刷新设备」：忽略绑定做全量扫描，用新的 source 列表 **合并** 进该卷记录（不删仍存在的旧目录；目录已不存在的条目丢掉）。

`scanner.scan` 增加可选参数 `bound_sources: Sequence[BoundSource] | None = None`。缺省行为与现在完全一致。

### 与恢复目标

`suggested_restore_dir` 优先用当前卷绑定里、与该存档 `platform`/`source_id` 匹配且仍存在的 `relative_root`，再回退到规格第 8 节的机种默认路径和 `last_restore_dir`。

---

## 7. FTP 增量与记住密码

### 增量

`ftp_fetch.py` 保持原子替换。增量细节放到 `src/vajsave/ftp_manifest.py`，避免 `ftp_fetch.py` 再涨。

成功拉取后在缓存目录写 `.ftp-manifest.json`：

```json
{
  "format": "vaj-save-ftp-manifest",
  "format_version": 1,
  "files": {
    "relative/path.sav": {"size": 1234, "modified": "20260101120000"}
  }
}
```

下次拉取：

1. LIST 远程树
2. 清单中 size 与 modified 都与 LIST 一致 → 从旧缓存拷到 staging（不下载）
3. 否则下载
4. 远程已删除的路径不进入 staging
5. `os.replace` 换成新树；失败则删除 staging，旧缓存与旧清单不动

LIST 没有 modified 时：仅 size 一致也跳过下载（用户选择的启发式）。拷贝失败则改为下载该文件。

后台进度 + 取消：每个文件之后检查 `CancelToken`。取消：扔掉 staging，旧缓存保留。

### 密码

FTP 对话框增加「记住密码」。

- 勾选：`config.json` 写入 `ftp_password` 与 `ftp_remember_password: true`
- 不勾选：删除 `ftp_password`，`ftp_remember_password: false`，密码只留在本次内存
- 日志与状态继续走现有 `redact`

---

## 8. Qt 接线与恢复目标

从 `qt_ui.py` 抽出 `src/vajsave/qt_dialogs.py`：设置、FTP、LLM、帮助、设备选择、无清单 ZIP 导入。窗口类只组布局和转发。

详情抽屉：

- 标题旁收藏星标（实心/空心），调用已有 `toggle_star`
- 多 ROM 候选：`STATUS_AMBIGUOUS` 时列表 +「绑定选中项」；仍保留手动选 ROM
- 选中版本后「删除此版本」
- 去掉不能点的「查看全部」

顶栏：

- 「只看收藏」开关 → `toggle_starred_only`
- 「备份有更新」
- 菜单按钮：导入 ZIP、打开备份库

Dock：本地库模式显示「导入 ZIP」。平台按钮含 GB/GBC。

设置：每个目录行带「浏览…」；「插入后自动备份」复选框；GB/GBC ROM 目录。

帮助文案补上：版本删除、ZIP 导入、增量 FTP、自动备份、GB/GBC。只读、不写掌机仍写明。

### 恢复目标

`config.json`：`last_restore_dir`。

`AppState.suggested_restore_dir(entry) -> Path | None`：

1. 当前挂载有卷绑定且非本地库模式时，用匹配该存档机种/source 且仍存在的相对根目录
2. 否则按机种找已存在的目录：PSP `PSP/SAVEDATA`，Vita `user/00/savedata` 或 `ux0/user/00/savedata`，Switch Checkpoint / JKSV，3DS Checkpoint / JKSV/Saves，GBA `SAVER` 或 `GBASYS/SAVE`，NDS `roms/nds/saves`，GB `roms/gb/saves`，GBC `roms/gbc/saves`
3. 否则用 `last_restore_dir`（仍存在时）

文件对话框从该路径打开。恢复成功后写入 `last_restore_dir`。确认框保留：「将所选版本复制到指定文件夹？不会写入掌机。」

---

## 9. 错误处理

| 情况 | 行为 |
|------|------|
| 删除版本路径越界 | 不删，`errors` 说明，catalog 不动 |
| ZIP 路径穿越或损坏 | 导入失败，库不变 |
| 无清单 ZIP 且用户未选挂接 | 失败，提示补全 |
| 备份中途 OS 错误 | 该条失败，继续下一条，最后汇总 |
| 备份取消 | 已成功的版本保留 |
| FTP 中途失败/取消 | 旧缓存保留 |
| GB 扫描权限错误 | 写入 `ScanResult.warnings`，不抛到 UI |
| 记住的 FTP 密码读失败 | 视为未记住，对话框留空 |
| 读不到卷序列号 | 全量扫描，不写 `devices.json` |
| 绑定目录全部失踪 | 全量扫描，扫描成功后重写该卷记录 |
| `devices.json` 损坏 | 当作空表，下一次成功扫描再写 |

---

## 10. 测试

TDD：每个新模块先写失败测试。

- 拆分后现有 `tests/test_app_state.py` 等全部保持通过
- `test_library_versions.py`：删中间版本、删最后一版等于删游戏、越界路径、设备文件不动
- `test_library_import.py`：带清单往返、内容去重、Zip Slip 拒绝、旧 ZIP 必须显式挂接
- `test_backup_jobs.py`：只备份 new/changed、取消后保留已写版本、库模式拒绝
- `test_platforms_gb.py` / `test_platforms_gbc.py`：roms 布局、同名 sav、SAVER 按 ROM 后缀分流、无 ROM 的 SAVER 仍为 GBA
- `test_ftp_fetch.py` 增量：未改文件不调 download、改 size 会下载、失败保留旧树、取消保留旧树
- `test_app_state.py`：记住密码开关、恢复目录建议、自动备份默认关
- `test_device_registry.py`：相对路径往返、拒绝 `..`、损坏文件当空表、合并刷新、目录失踪则全量
- `test_volume_id.py`：Windows 序列号格式、读失败为 None；不把 USB 适配器信息写入键
- `test_scanner.py`：`bound_sources` 只扫描给定目录且缺省参数行为不变
- Qt：对话框浏览按钮与候选绑定用现有 Qt 测试风格，能无显示环境跑的才加

---

## 11. 文件清单

新建：

- `src/vajsave/settings_store.py`
- `src/vajsave/device_session.py`
- `src/vajsave/device_registry.py`
- `src/vajsave/volume_id.py`
- `src/vajsave/scan_session.py`
- `src/vajsave/library_actions.py`
- `src/vajsave/ftp_session.py`
- `src/vajsave/enrichment.py`
- `src/vajsave/platforms/catalog.py`
- `src/vajsave/jobs.py`
- `src/vajsave/backup_jobs.py`
- `src/vajsave/library_versions.py`
- `src/vajsave/library_import.py`
- `src/vajsave/ftp_manifest.py`
- `src/vajsave/platforms/cartridge_files.py`
- `src/vajsave/platforms/gb.py`
- `src/vajsave/platforms/gbc.py`
- `src/vajsave/identity/gb.py`
- `src/vajsave/identity/gbc.py`
- `src/vajsave/qt_dialogs.py`
- `src/vajsave/data/platform-gb.svg`
- `src/vajsave/data/platform-gbc.svg`

修改：

- `src/vajsave/app_state.py`（门面）
- `src/vajsave/library.py`（再导出 `delete_snapshot`，不在此实现）
- `src/vajsave/ftp_fetch.py`
- `src/vajsave/remote_ftp.py`（如需从 LIST 带出 size/modified）
- `src/vajsave/scanner.py`
- `src/vajsave/volume.py`（Windows 枚举把卷序列号写入 `VolumeInfo.extra["volume_id"]`）
- `src/vajsave/rom_formats.py`
- `src/vajsave/identity/roms.py`（GB 头偏移）
- `src/vajsave/identity/resolver.py`
- `src/vajsave/artwork/providers.py`
- `src/vajsave/metadata/libretro.py`
- `src/vajsave/ui_theme.py`
- `src/vajsave/qt_ui.py`
- `DESIGN.md`、`README.md`、`AGENTS.md` 中的机种列表与功能链路

---

## 12. 实现顺序

1. 拆分 AppState（行为不变，全量测试绿）
2. 卷序列号 + `DeviceRegistry` + 绑定扫描（`scanner.scan(..., bound_sources=)`）
3. `delete_snapshot` + 抽屉按钮
4. ZIP 清单导出 / 导入
5. `jobs` + `backup_jobs` + 备份有更新 + 进度取消
6. GB/GBC 扫描、身份、Dock、设置
7. FTP 增量 + 记住密码 + 后台进度
8. 收藏 / ROM 候选 / 设置浏览 / 菜单 / 恢复目录（含绑定根） / 自动备份
9. README / DESIGN / 帮助文案

每步可单独测试。Qt 接线集中在步骤 3、4、5、8，避免每步都改 `qt_ui.py` 的同一大段。
