# RatingClassifyTool4Canon

按 **XMP 评分**（`xmp:Rating`）把佳能相机照片（JPG / CR3）自动归类到 `0-未评分` / `1星` … `5星` 文件夹的小工具。命令行 + 图形界面两个入口。

评分读的是照片文件**内嵌的 XMP 评分**——也就是 Lightroom、佳能相机自己评分写进去的那份，不是存储卡上的 `.CTG` 缓存文件。

## 功能

- 只读 XMP 评分，不解码 RAW 像素、不加载缩略图，大目录也快；
- 命令行 + tkinter 图形界面（预览每个评分等级的文件名和数量）；
- 移动 / 复制、递归扫描、中文路径都支持；
- 同名冲突自动加 `_1`、`_2` 后缀，不覆盖；
- 可携带并优先使用同目录下的 ExifTool（`exiftool\`），不污染系统 PATH。

## 技术原理

### 评分存在哪里

佳能微单的评分写在**每张照片文件自身的 XMP 里**，标签是 `xmp:Rating`（0–5，0 = 未评分）：

- CR3 / CR2：文件内部（ISO BMFF / TIFF 结构）嵌了一段 XMP record；
- JPEG：XMP 在 APP1 段（`http://ns.adobe.com/xap/1.0/`）；
- 卡根目录的 `CANONMSC\*.CTG` 只是佳能的目录 / 索引缓存（Catalog），不是评分正本——删了会重建，评分不受影响。

因为评分在文件里，复制、移动、改名都不会丢。Lightroom 的 sidecar（`.xmp`）里也有 Rating，但照片内嵌 XMP 优先级更高，两边不一致时以文件内为准。

### 怎么读

程序不自己解析 XMP，而是调用 [ExifTool](https://exiftool.org/)：

```
exiftool -j -n -q -q -fast -charset filename=utf8 -XMP:Rating -@ 文件列表
```

- `-j`：输出 JSON；`-n`：数值模式（Rating 直接给数字 0–5）；
- `-XMP:Rating`：只提取这一个标签，**不请求预览图、不解码 RAW 像素**，ExifTool 只解析元数据结构；
- `-fast`：跳过耗时且不需要的深层扫描；
- `-@ 文件列表`：路径走 UTF-8 临时文件，中文 / 空格路径安全，不依赖命令行编码；
- 每批 500 个文件共用一个 ExifTool 进程，避免逐个启动的开销。

没有 XMP 评分的文件（Canon 没评过分的照片其实也会写 `Rating=0`，非 Canon 文件可能没有这个字段）统一进 `0-未评分`。

### 分类与安全

- 1–5 星 → `1星` … `5星`；0 或读不到 → `0-未评分`；
- 默认**移动**，`--copy` 复制；目标目录有同名文件时自动加 `_1`、`_2`，不覆盖；
- `--recursive` 递归扫描时自动跳过输出目录（包括之前生成的分类文件夹），反复运行不会把已分好的文件再扫一遍；
- ExifTool 查找顺序：程序同目录 `exiftool\exiftool.exe`（PyInstaller 版是 exe 同级）→ PATH 里的 `exiftool`。

## 依赖

- Python 3.9+（只用标准库，GUI 用内置 tkinter；Windows 官方安装包自带）
- ExifTool 13+：去 [exiftool.org](https://exiftool.org/) 下载 Windows 版（`exiftool-xx.xx_64.zip`），解压后把整个目录（`exiftool.exe` + `exiftool_files\`）放到本程序同目录的 `exiftool\` 下，或者安装后加进 PATH。

> 不想装东西的话，[Releases](../../releases) 里的打包版已经把 ExifTool 一起放进去了，解压双击 exe 即用。

## 命令行参数

| 参数 | 说明 |
| --- | --- |
| `source` | 源目录（必填） |
| `-o, --output` | 输出目录，默认就是源目录 |
| `--copy` | 复制而不是移动 |
| `--dry-run` | 只预览，不动文件 |
| `--recursive` | 递归扫描子目录（自动跳过输出目录） |
| `--exiftool` | ExifTool 可执行文件路径，默认自动查找 |

示例：

```powershell
python rating_sort.py "D:\Photos" --dry-run        # 预览，不动文件
python rating_sort.py "D:\Photos"                   # 原地移动整理
python rating_sort.py "D:\Photos" -o "D:\Sorted" --copy --recursive
```

## 图形界面

```powershell
pythonw rating_gui.py
```

或者双击 `启动GUI.bat`（需要 `pythonw.exe` 在 PATH 里）。界面功能：

- 选源目录 / 输出目录（输出留空 = 源目录）；
- 复制 / 移动切换、是否递归扫描；
- **预览**：按等级分组显示每个等级有多少张、以及文件名（树形节点可展开），不加载缩略图；
- **执行**：先弹确认框，然后在后台线程里跑，进度条实时更新。

## 自测

```powershell
python _selftest.py
```

会在临时目录里建测试文件，覆盖文件发现、排除规则、评分读取、分组、复制移动和 GUI 冒烟。

## 打包版

不用 Python 的版本在 [Releases](../../releases)：下载 `RatingClassifyTool4Canon-发布.zip`，解压后双击 `RatingClassifyTool4Canon.exe` 即可（Windows x64）。

## 注意

- 默认是**移动**，不确定先跑 `--dry-run`；
- 只认 `.jpg` / `.jpeg` / `.cr3`，大小写不敏感；
- 同名冲突自动加 `_1`，不会覆盖原文件。
