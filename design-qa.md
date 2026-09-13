# vaj-save PySide6 视觉核对

## 核对基准

- 视觉真值：`/var/folders/sd/25slhv5133g3k5cvhp5gnyy00000gn/T/codex-clipboard-4633abd1-ff84-4e48-8c90-2b328992db3b.png`
- 重点局部：
  - `/var/folders/sd/25slhv5133g3k5cvhp5gnyy00000gn/T/codex-clipboard-efeeef68-ea20-4f03-8df7-5754666955ef.png`
  - `/var/folders/sd/25slhv5133g3k5cvhp5gnyy00000gn/T/codex-clipboard-a41e816f-8080-4677-bdaa-3d2522bfbc7b.png`
- 实现截图：`build/ui-preview/qt-window-selected.png`
- 同画面对比：`build/ui-preview/qt-design-comparison.png`
- 状态：选中《塞尔达传说 王国之泪》，详情抽屉打开，存在 5 个版本并显示未绑定 ROM。

## 尺寸与归一化

- 原始参考图：1608×976 px。
- 参考归一化图：1480×900 px，直接缩放到实现截图画布。
- 实现截图：1480×900 px，Qt offscreen 平台，设备像素比 1。
- 对比图：2960×900 px，左侧参考、右侧实现，不含额外裁切。
- 参考图包含系统标题栏；offscreen 截图不包含系统标题栏。该差异只属于宿主窗口装饰，不用于判断应用内容区。

## 必查视觉面

- 字体与排版：使用平台中文 UI 字体栈；品牌、抽屉标题、游戏名、字段和辅助文字层级与参考一致。小字号在 offscreen 抗锯齿上略有差异，属于平台渲染差异。
- 间距与布局：顶栏、104px Dock、四列首层收藏架、442px 详情抽屉和 42px 状态栏比例通过。第二排按 3DS/GBA 实体比例降低高度，两层货架可在默认窗口完整显示。
- 颜色与 Token：所有运行时颜色取自 `vajsave.ui_theme.SWITCH`、`PLATFORM_COLORS` 或其混色函数；蓝色只承担选中态和主操作。
- 图片质量：封面使用真实本地封面解析链路，以平滑缩放填入不同平台盒型；平台 Dock 使用 PSP、PlayStation Vita、Nintendo Switch、Nintendo 3DS、Nintendo DS 和 Game Boy Advance 的官方标识，以原始比例渲染为统一中性色，其他标准操作图标来自 QtAwesome。
- 文案与内容：参考图中的搜索、排序、更新筛选、平台、备份/恢复/导出、ROM 警告、版本历史与备注均已覆盖。

## 全画面对比

首轮发现：

- P1：详情抽屉被叠层容器放在画廊后方，无法显示。
- P2：3DS/GBA 沿用 Switch 高度，第二层货架被底栏截断。
- P2：详情抽屉缺少参考状态中的 ROM 警告卡和版本数据。
- P2：抽屉初始高度取自 sizeHint，内部区域互相挤压。

修复：

- 抽屉改为主内容容器上的直接覆盖层，并用 `QPropertyAnimation` 控制水平位置。
- 盒型根据平台纵横比同时回算宽高，调整两层之间的节奏，并为第三层增加滚动前留白。
- 补齐未绑定 ROM 警告卡、五行版本表、查看全部提示和备注区域。
- 抽屉打开前同步主内容区高度，避免英雄区、按钮和版本表重叠。

复核结果：`build/ui-preview/qt-design-comparison.png` 中没有剩余 P0、P1 或 P2 差异。

## 重点局部对比

### 陈列架

- 卡盒底边落在托面后缘，具备接触暗边。
- 货架具有浅色托面、1px 顶部高光、23px 前沿和向下连续消散的宽阴影。
- 选中盒型保持尺寸不变，通过蓝色多层描边与低强度外光表达焦点。
- 第二排盒型更矮，货架完整显示，没有悬空斜板、硬条纹或底栏裁切。

### 详情抽屉

- 抽屉宽度、左侧软阴影、圆角和雾白表面与参考区域一致。
- 封面、标题、平台标签、六项定义列表、蓝色主按钮和三项次操作顺序一致。
- ROM 警告、版本历史、单选行、时间/大小列及备注输入均保持稳定布局。
- 190ms 滑入/滑出动画只改变位置，不执行文件扫描或图片解码。

## 交互与执行验证

- 已验证：平台筛选、搜索、排序、仅显示更新、单选、Ctrl/Command 多选、Shift 连选、方向键、回车主操作、抽屉关闭、版本选择、备注提交、监听切换。
- 已检查运行时截图；Qt offscreen 渲染无界面异常。
- `python -m py_compile src/vajsave/qt_ui.py src/vajsave/app.py scripts/ui_preview.py`：通过。
- `pytest -q`：849 passed。
- `git diff --check`：通过。

## 剩余 P3

- 系统标题栏由 macOS/Windows 原生窗口管理器绘制，offscreen 证据不包含标题栏；真实运行时会使用当前系统的原生标题栏。
- 预览夹具使用 64 KB 数据以避免生成数百 MB 临时文件，因此截图中的版本大小不是参考图的 64 MB；真实界面按实际文件大小显示。

final result: passed
