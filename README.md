# MIDI Player（pyglgame / xyazh Engine）

基于自研 OpenGL 引擎 **pyglgame** 的 Windows 桌面端 MIDI 可视化播放器。

程序读取标准 MIDI 文件（`.mid` / `.midi`），解析出音符的音高、力度、起始时间与时长，
以“钢琴瀑布流（piano-roll waterfall）”形式实时渲染音符轨迹，并使用引擎内置的
**钢琴合成器**（加法合成，含谐波、不谐和、齐奏、制音器等建模）实时发声 —— 不依赖
SoundFont 或任何外部音源。界面提供主页、曲库、播放、设置四个页面，支持曲库分页浏览、
暂停 / 重播、动态或图片背景，以及整曲 WAV 音频与 MP4 视频导出。

- 入口程序：`MIDIPlayer.py`
- 播放器实现：`MIDIPlayer/`（`MainPage` / `MidiListPage` / `WaterfallPage` / `SettingPage`）
- 引擎：`pyglgame/`（渲染、粒子、物理、OpenAL 音频、计算着色器、资源包、内置依赖）
- 许可：本仓库 MIT，第三方组件见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)

## 主要特性

| 特性 | 说明 |
|---|---|
| 内置钢琴合成器 | 三种音色预设 `grand` / `upright` / `honkyTonk`；力度同时决定响度与亮度 |
| 画面后处理 | MSAA、Bloom 与 WBOIT 加权混合透明渲染 |
| 背景 | 默认的程序化动态背景或自定义 PNG 图片，亮度 0.0–2.0 可调 |
| 曲库管理 | 扫描 `midi` 目录、按文件名自然排序、每页 8 条、支持重新扫描 |
| 导出 | 整曲导出 `output.<曲名>.wav`；逐帧抓图导出 `output.<曲名>.mp4` |
| 发行 | `build.bat`  Nuitka 打包发行版 |

## 运行环境

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10 / 11 x64 |
| Python | **CPython 3.10** |
| 显卡 / 驱动 | 支持 OpenGL 4.3 及以上的驱动 |

## 快速开始

### 源码运行

```bat
py -3.10 MIDIPlayer.py
```

请在**项目根目录**下运行：曲库目录 `midi`、配置文件 `config.json` 以及导出的
`output.*` 产物均相对于当前工作目录（引擎素材 `res` 与背景目录 `background` 会先按
工作目录、再按程序所在目录查找）。

### 打包发行

```bat
build_MIDIPlayer.bat        :: → dist\MIDIPlayer.dist\MIDIPlayer.exe
build_MIDIPlayer_slim.bat   :: 同上，但构建时丢弃 cv2（约省 95 MB），SAVE VIDEO 不可用
```

- 构建需要 CPython 3.10 与 Nuitka；`build.bat` 会自动使用 `py -3.10`（若无则回退到 PATH 上的 `python`），
  并把 `pyglgame\libs` 通过 `PYTHONPATH` 交给 Nuitka。
- 产物为 **`--standalone` 单目录**，请整体分发 `dist\MIDIPlayer.dist\`，不要只取 `.exe`。
- 引擎素材以资源包 `res.xypack` 随 `.exe` 一同发布；`LICENSES\` 与 `THIRD_PARTY_NOTICES.md` 会自动附带。
- `midi` 与 `background` **不参与打包**，发行时请自行放在 `.exe` 所在目录（见下文）。
- `build.bat` 还支持 `XYA_SLIM`、`XYA_FULL`、`XYA_LTO`、`XYA_PACK_COMPRESS`、`XYA_SMOKE`、
  `XYA_REPORT`、`XYA_CLEAN` 等环境变量开关，详见脚本头部注释。

## 使用说明

### 页面与操作

| 页面 | 进入方式 | 操作 |
|---|---|---|
| 主页 MainPage | 启动后默认页面 | 点击 `START` 进入曲库；点击 `SETTINGS` 进入设置 |
| 曲库 MidiListPage | 主页 `START` | 单击列表条目开始播放；`←` / `PageUp` 上一页，`→` / `PageDown` 下一页；`R` 重新扫描曲库；单击顶部目录路径在资源管理器中打开 `midi` 目录；`ESC` 返回主页 |
| 播放 WaterfallPage | 曲库中单击曲目 | `ESC` 暂停 / 继续（暂停时显示 `RESUME`、`EXIT` 按钮）；加载中或加载失败时按 `ESC` 退出并返回曲库 |
| 设置 SettingPage | 主页 `SETTINGS` | 每行用 `<` / `>` 调整取值；`SAVE` 保存；`RESET DEFAULTS` 恢复默认；`BACK` 或 `ESC` 返回主页 |

### 播放流程

1. 选中曲目后进入播放页，状态机依次为
   `READING MIDI`（解析音符）→ `PREPARING AUDIO`（音色去重并逐帧预热，显示 `(已算/总数)`）
   → 可选 `EXPORTING AUDIO`（启用了 SAVE AUDIO 时离线混音导出）→ `READY`。
2. 解析失败时显示 `READ MIDI FAILED`，曲中无音符时显示 `NO NOTES IN THIS MIDI`。
3. `READY` 后音符开始下落，左端触达判定线（`x = -2`）的那一帧发声并生成粒子；
   整曲播放结束打印 `FINISHED: <曲名>`。

### 设置项

| 配置键 | 界面名称 | 取值 | 生效时机 |
|---|---|---|---|
| `window_size` | WINDOW SIZE | 960×540 / 1280×720 / 1600×900 / 1920×1080 / 2560×1440 | 重启后生效 |
| `fullscreen` | FULLSCREEN | ON / OFF | 重启后生效 |
| `background_mode` | BACKGROUND | DYNAMIC / IMAGE | 立即生效 |
| `background_image` | BG IMAGE | NONE 或 `background\*.png` | 立即生效（需 BACKGROUND = IMAGE） |
| `background_brightness` | BG BRIGHTNESS | 0.0 – 2.0，步长 0.1 | 立即生效 |
| `save_video` | SAVE VIDEO | ON / OFF | 下次载入曲目时生效 |
| `save_audio` | SAVE AUDIO | ON / OFF | 下次载入曲目时生效 |
| `video_fps` | VIDEO FPS | 30 / 60 / 90 / 120 | 下次载入曲目时生效 |
| `note_speed` | NOTE SPEED | 4 – 40，步长 2 | 下次载入曲目时生效 |
| `note_brightness` | NOTE BRIGHT | 0.2 – 2.0，步长 0.1 | 下次载入曲目时生效 |
| `piano_preset` | PIANO VOICE | GRAND / UPRIGHT / HONKY TONK | 下次载入曲目时生效 |

设置页的每一次调整都会立即应用并写回 `config.json`；窗口大小与全屏需要在重启程序后生效

### 导出产物

| 产物 | 触发条件 | 说明 |
|---|---|---|
| `output.<曲名>.wav` | `save_audio = true` | 在载入阶段离线整曲混音（16 bit WAV），与实时播放相互独立 |
| `output.<曲名>.mp4` | `save_video = true` | 在 `READY` 状态逐帧抓取画面编码（不含音轨），帧率取 `video_fps` |

两者均写入**当前工作目录**。视频录制依赖 opencv-python；在 `build_MIDIPlayer_slim.bat`
的构建中 cv2 已被裁掉，此时会在控制台提示 `缺少 cv2/numpy，无法录制视频`，其余功能不受影响。
录制视频时逻辑帧被固定为 `video_fps`，因此录制过程通常快于实时播放。

## 如何添加 MIDI 曲目

程序不会在启动时递归扫描磁盘，所有曲目都来自**曲库目录 `midi`**（即“当前工作目录”下的
`midi` 文件夹）：

- 源码运行：项目根目录下的 `midi` 目录；
- 打包版双击 `.exe`：可执行文件所在目录下的 `midi` 目录；
- 从其他目录启动 `.exe`：启动时的工作目录下的 `midi` 目录。

添加步骤：

1. 在曲库目录中放入 `*.mid` / `*.midi` 文件（目录不存在时，可在曲库页单击顶部目录路径，
   程序会自动创建并在资源管理器中打开）。
2. 回到曲库页按 `R` 重新扫描，或重新进入曲库页。
3. 单击列表条目即可播放。

规则与注意事项：

- 只列出 `midi` **一级目录**下的文件，不会递归子目录，也不读取播放列表文件。
- 文件名按“自然排序”（数字部分按数值比较），因此 `2.mid` 排在 `10.mid` 之前；
  以数字前缀命名的曲库（如 `001-xxx.mid`）会得到稳定的顺序。
- 每页 8 条，使用 `←` / `→`（或 `PageUp` / `PageDown`）翻页；列表为空时显示 `NO MIDI FILE IN midi/`。
- 请使用标准 MIDI 文件；曲目需要用到的音色会在载入时被预热（去重后逐个渲染），
  因此音符数量极多、音符时长极长的曲子载入时间会相应变长。

## 如何添加背景

背景由 `MIDIPlayer/BackGround.py` 绘制，支持两种模式：

| 模式 | 取值 | 说明 |
|---|---|---|
| 动态背景 | `DYNAMIC`（默认） | 由计算着色器实时生成（约 1920×1080 RGBA32F） **不需要任何素材文件** |
| 图片背景 | `IMAGE` | 使用 `background` 目录下的 PNG 图片 |

添加图片背景步骤：

1. 把 PNG 图片放入**背景目录**：

   - 源码运行：项目根目录下的 `background` 目录（仓库已自带两张示例图）；
   - 打包版：可执行文件所在目录下的 `background` 目录。

2. 进入 `SETTINGS`：
   - `BACKGROUND` 用 `<` / `>` 切换为 `IMAGE`；
   - `BG IMAGE` 用 `<` / `>` 选择图片（候选项 = `NONE` + `background\*.png`，按文件名排序）；
   - `BG BRIGHTNESS` 调整亮度（0.0 – 2.0，`1.0` 为原亮度）。
3. 设置即刻生效（背景对象约每 1/3 秒重新读取一次设置），无需重启。

也可以直接编辑 `config.json`：

```json
{
  "background_mode": "image",
  "background_image": "background/my_wallpaper.png",
  "background_brightness": 0.8
}
```

规则与注意事项：

- `BG IMAGE` 仅在 `BACKGROUND = IMAGE` 时生效；若图片路径为空或文件不存在，会自动回退到
  动态背景，并在控制台打印 `背景图片不存在: <路径>`。修改图片内容后重新选择一次即可刷新。
- 背景图片会被拉伸铺满整个窗口，不做等比裁切，请自行准备与窗口比例接近的图片。
- 背景作为最底层元素绘制，位于页面 UI 与瀑布流音符之下；调整 `BG BRIGHTNESS` 可避免背景过亮
  影响曲库与设置页文字的可读性。


## 目录结构

```
MIDIPlayer.py                入口：装配 App、相机与四个页面，启动主循环
MIDIPlayer/                  播放器本体
  config.py                  Settings 类、config.json 读写、可选值表与默认值
  pages.py                   页面注册 / 切换、相机模式切换、设置对象共享
  MainPage.py                主页（标题、START / SETTINGS 按钮）
  MidiListPage.py            曲库列表（扫描、排序、分页、打开文件夹）
  WaterfallPage.py           瀑布流播放（MIDI 解析、音符渲染、粒子、音视频导出）
  SettingPage.py             设置页（行式设置项、保存 / 恢复默认）
  BackGround.py              背景（动态计算着色器纹理 / 图片纹理、亮度）
  MainCamera.py              相机（UI / 瀑布流两套机位、MSAA、Bloom、WBOIT）
  widgets.py                 通用控件（文本按钮、进度条、可穿透点击的文本）
pyglgame/                    引擎
  libs/                      固化的第三方依赖（无需单独安装）
  openal/                    音频后端与钢琴合成器（PianoPreset / Synthesizer）
  compute/                   把受限 Python 数值函数编译为 GLSL 430 计算着色器
  render/ shader/ particle/  渲染、着色器与粒子系统
  tools/                     路径解析、依赖固化、许可收集、资源打包等工具
res/                         引擎素材（着色器、字体、图片），打包后进入 res.xypack
background/                  背景图片目录（不打包，随发行单独提供）
midi/                        曲库目录（不打包，随发行单独提供）
tools/                       构建辅助脚本（资源打包、许可收集、发行裁剪）
build.bat                    通用打包脚本；build_MIDIPlayer*.bat 为双击便捷入口
LICENSES/                   第三方许可正文（由 tools/collect_licenses.py 生成）
THIRD_PARTY_NOTICES.md       第三方组件汇总与合规义务清单
```

## 许可与第三方声明

- 本仓库代码（pyglgame 引擎与各示例程序）：**MIT**，Copyright (c) 2022 xyazh，见 [`LICENSE`](LICENSE)。
- 随包分发的第三方组件及其合规义务见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)，
  许可正文位于 `LICENSES/`（索引：`LICENSES/INDEX.md`）。发行时请将本文件与 `LICENSES/` 一并附带
  （`build.bat` 已自动包含）。

```
Portions of this software are copyright © The FreeType Project
(https://www.freetype.org/). All rights reserved.
```
