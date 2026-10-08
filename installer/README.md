# 安装包打包说明

把 `tools/build.py` 产出的绿色版（exe + `_internal`）再包成**真正的安装程序**：
有安装向导、开始菜单项、桌面快捷方式和规范的卸载入口，可以直接发给别人。

```
python tools/build.py          # 第一步：生成绿色版产物
python tools/make_installer.py # 第二步：包成安装程序
```

---

## 一、打包文件清单

### 进包的文件

打包源是 PyInstaller 的产物，即**项目根目录下的三项**：

| 类别 | 文件 | 说明 |
|---|---|---|
| **可执行入口** | `图片转换器.exe` | PyInstaller bootloader，约 2 MB。**必须与同级 `_internal` 一起存在才能运行** |
| **运行时** | `_internal\python310.dll`、`python3.dll`、`VCRUNTIME140.dll`、`ucrtbase.dll` | CPython 3.10 解释器与 VC++ 运行时 —— 这是目标机免装 Python 的关键 |
| | `_internal\api-ms-win-*.dll`（45 个） | Windows API Set 转发层，随 VC++ 运行时一起分发 |
| | `_internal\base_library.zip` | PyInstaller 启动所需的最小标准库 |
| **依赖库** | `_internal\PySide6\`（142 文件）、`shiboken6\` | 桌面界面（Qt 6） |
| | `_internal\PIL\`、`pymupdf\`、`numpy\` | 编码与 DPI（Pillow）、PDF/SVG 矢量渲染（PyMuPDF）、高位深插值（numpy） |
| | `_internal\win32\`、`Pythonwin\`、`pywin32_system32\` | PowerPoint COM 自动化（pywin32） |
| **扩展模块** | `_internal\*.pyd`（`_ssl`、`_hashlib`、`_ctypes`、`_lzma`、`_socket`、`_queue`…） | Python C 扩展，已在 `build.py` 里按需要裁剪过 |
| **静态资源** | `icon.ico` | 编译进 exe；同时作为安装向导与卸载项的图标 |
| | `README.md` | 随包装到 `{app}`，用户在安装目录里能翻到说明 |

### 明确排除的文件

| 类别 | 内容 | 排除原因 |
|---|---|---|
| **源码** | `main.py`、`imgspec\*.py`、`gui\*.py` | 已编译进 `_internal`（PYZ / `base_library.zip`），运行时不需要源码 |
| **测试** | `tests\`、`conftest.py`、`.pytest_cache\` | 仅开发期使用 |
| **打包脚本** | `tools\build.py`、`tools\make_icon.py`、`installer\图片转换器.iss` | 分发给终端用户无意义 |
| **调试产物** | `build\`、`*.spec`、`*.pyc`、`__pycache__\` | PyInstaller 中间产物，每次打包重新生成 |
| **版本管理** | `.gitignore`、`.git\` | 与运行无关 |
| **输出样本** | `converted\` | 用户自己的转换产物 |
| **网页端代码** | **无** | 本项目从未引入任何 Web 端代码 —— Web 化只做过可行性评估，没有落地文件，无需排除 |

> 以上排除是**天然的**：`build.py` 产出的 `_internal` 里本就不含 `.py` 源码，
> 安装包只收这三项，不需要额外的白名单过滤。

### 可选瘦身（未做，需自行决定）

`_internal` 里有几个包是开发环境装了、被 PyInstaller 顺着可选导入拖进来的，**并非本工具依赖**：

`pptx\`（8 文件）、`psutil\`、`fontTools\`、`yaml\`、`charset_normalizer\`

`office.py` 走 PowerPoint COM，`ingest.py` 只用 `fitz` + `PIL`，都不 import `pptx`。
把它们加进 `tools/build.py` 的 `EXCLUDES` 可再省数 MB。
**本次未改动 `build.py`** —— 现有产物已验证可用，为重打包省几 MB 去动可运行的配置不划算。
要瘦身就改 `EXCLUDES` 后重跑一次打包并完整自检。

---

## 二、打包配置

配置分两处，与项目现有风格一致：

| 文件 | 作用 |
|---|---|
| `installer\图片转换器.iss` | Inno Setup 脚本：软件名、版本号、安装目录、快捷方式、卸载入口、语言。可直接用 Inno Setup 打开编译 |
| `tools\make_installer.py` | 驱动脚本：检查产物、定位 ISCC、按 scope 编译、打印产物。风格对齐 `tools\build.py` |

### 基本信息

| 项 | 值 | 位置 |
|---|---|---|
| 软件名称 | 图片转换器 | `.iss` → `MyAppName` |
| 版本号 | **1.0.0** | `.iss` → `MyAppVersion`；改 `make_installer.py` 的 `DEFAULT_VERSION` 或用 `--version` |
| 发布者 | imgspec | `.iss` → `MyAppPublisher` |
| 安装目录（当前用户） | `%LOCALAPPDATA%\Programs\图片转换器` | `.iss` → `MyAppDirName` |
| 安装目录（所有用户） | `%ProgramFiles%\图片转换器` | 同上，走 `#ifdef MyAppScopeMachine` 分支 |
| 开始菜单 | `图片转换器` 组，含主程序与「卸载 图片转换器」 | `[Icons]` |
| 桌面快捷方式 | 默认创建；向导里可取消勾选 | `[Tasks] desktopicon` |
| 卸载入口 | 开始菜单项 + 系统卸载列表（`unins000.exe`） | Inno 自动生成 |
| 界面语言 | 英文（默认）；换中文的方法见下节 | `[Languages]` |
| 架构 | 仅 x64 | `ArchitecturesAllowed=x64compatible` |

### 两种安装范围

```bash
python tools/make_installer.py --scope user      # 只出当前用户版
python tools/make_installer.py --scope machine   # 只出所有用户版
python tools/make_installer.py                   # 默认两种都出
python tools/make_installer.py --version 1.1.0   # 指定版本号
```

| | 当前用户（user） | 所有用户（machine） |
|---|---|---|
| 安装目录 | `%LOCALAPPDATA%\Programs\图片转换器` | `%ProgramFiles%\图片转换器` |
| 管理员权限 | 不需要，双击即装 | 需要，弹 UAC |
| 卸载入口位置 | 设置 → 应用 → 已安装的应用 | 控制面板 → 程序和功能 |
| AppId | `imgspec.图片转换器.user` | `imgspec.图片转换器` |
| 适用 | 发给同事、个人电脑 | 机房统一部署 |

两种版本的 AppId 不同，**装在同一台机器上不会互相覆盖**。

### 界面语言：默认英文，如何换中文

**Inno Setup 官方不自带简体中文语言文件** —— 安装目录的 `Languages\` 里没有
`ChineseSimplified.isl`，官方把它放在单独的翻译站（`jrsoftware.org/files/istrans/`），
需要单独下载。当前 `.iss` 用的是官方自带的 `Default.isl`，所以**安装向导是英文界面**
（Next / Install / Finish 等）。

要换成中文界面，二选一：

1. 下载 `ChineseSimplified.isl` 放进 `D:\Tools\Inno Setup 6\Languages\`，再把
   `.iss` 的 `[Languages]` 改回：
   ```
   Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
   ```
2. 基于 `Default.isl` 自制一份中文语言文件（键名照抄，只翻译需要的条目），
   未翻译的条目会保持英文，不会导致编译失败。

> 注意：`.iss` 含中文，**必须保存为 UTF-8 with BOM**（`EF BB BF`），
> 否则 Inno Setup 按系统 ANSI 解析，会在 `[Languages]` 段报「找不到文件」之类的假错误。

---

## 三、依赖与运行时环境

### 目标机器不需要什么

- 不需要 Python（解释器已随包：`python310.dll` + `base_library.zip`）
- 不需要 pip，也不需要安装任何 Python 包
- 不需要 VC++ 可再发行组件（`VCRUNTIME140.dll`、`ucrtbase.dll`、`api-ms-win-*.dll` 已随包）
- 不需要 Qt 运行时（PySide6 的 DLL 已随包）
- 不需要联网（工具无任何网络请求）

### 目标机器需要什么（唯一可变项）

**Microsoft PowerPoint —— 仅在转换 PPT/PPTX/PPS 时需要。**

没装 PowerPoint 时，`imgspec/office.py` 会给出明确提示并建议「先另存为 PDF 再拖进来」，
不会静默产出空白图。PDF、SVG、图片这三类输入完全不受影响。

### 系统与配置

- **系统要求**：Windows 10 / 11，64 位（项目在 win_amd64 下打包）
- **用户设置**：首次启动由 Qt 的 `QSettings("imgspec", "ImageSpecTool")` 自动在
  `HKCU\Software\imgspec\ImageSpecTool` 创建。无需预置配置文件，也不需要写权限到安装目录 ——
  所以当前用户版在普通权限下功能完整。

---

## 四、目标平台与产物路径

| 项 | 值 |
|---|---|
| 目标平台 | Windows 10 / 11 x64 |
| 安装包格式 | Inno Setup 生成的**单个 setup.exe**（自解压 + 安装向导 + 内置卸载程序） |
| 产物目录 | `dist\installer\` |
| 产物文件名 | `图片转换器-1.0.0-当前用户-setup.exe`<br>`图片转换器-1.0.0-所有用户-setup.exe` |
| 实测体积 | **59.3 MB / 个**（源文件约 206 MB，LZMA2/max 固体压缩）；编译约 60–70 秒/个 |

`dist\` 已在 `.gitignore` 中排除，产物不会被提交。

---

## 五、验证步骤

### 前置：本机快速自测（打完包先跑这个）

```bat
"%LOCALAPPDATA%\Programs\图片转换器\图片转换器.exe" --selftest
```

自检会生成测试图、跑一次真实的「位图→TIFF」与「PDF→PNG」转换，再读回校验像素与 DPI。
结果同时写入 `%TEMP%\imgspec_selftest_report.txt`（无控制台的 exe 以这个文件为准）。

```bat
"%LOCALAPPDATA%\Programs\图片转换器\图片转换器.exe" --smoke
```

启动 GUI 后 800 ms 自动退出，用来确认界面能起来、不会崩。

### 干净环境验证

推荐用 **Windows Sandbox**（Win10/11 专业版自带，开箱即用、关掉即销毁）或一台没装过 Python 的电脑。

**1. 安装**

- 双击 setup.exe
- 当前用户版：**不应出现 UAC 弹窗**；所有用户版：出现 UAC，确认后继续
- 走完向导，勾选「安装完成后启动 图片转换器」
- 检查安装目录：`%LOCALAPPDATA%\Programs\图片转换器\`（或 `%ProgramFiles%\图片转换器\`）下有 `图片转换器.exe` + `_internal\` + `README.md`

**2. 启动与功能**

```bat
cd /d "%LOCALAPPDATA%\Programs\图片转换器"
图片转换器.exe --smoke
图片转换器.exe --selftest
type "%TEMP%\imgspec_selftest_report.txt"
```

报告应全部通过。再做一次真实转换：拖入一个 PDF 或 PNG，设 8.5 cm / 300 dpi / TIFF，
点「全部转换」，确认输出文件存在且 DPI 正确。

**3. 卸载**

三选一：

- 开始菜单 → `图片转换器` → 「卸载 图片转换器」
- 当前用户版：设置 → 应用 → 已安装的应用 → 图片转换器 → 卸载
- 所有用户版：控制面板 → 程序和功能 → 图片转换器 → 卸载

卸载后检查：

- [ ] 安装目录已被删除
- [ ] 开始菜单组消失
- [ ] 桌面快捷方式（若建了）消失
- [ ] 系统卸载列表中不再有该条目

> **刻意保留的两项**：卸载不会删 `HKCU\Software\imgspec` 下的用户参数设置，
> 也不会删任何 `converted\` 输出目录 —— 重装后设置还在，用户产出不会被误删。

**4. 边界检查**

- 目标机没装 PowerPoint 时，拖入 `.pptx` 应给出**明确提示**（改用 PDF），而不是空白图或崩溃
- 断网状态下功能不受影响

### 脚本化验证（不想点向导时）

```
setup.exe /VERYSILENT /NORESTART /SP- /LOG=install.log
"%LOCALAPPDATA%\Programs\图片转换器\图片转换器.exe" --smoke
"%LOCALAPPDATA%\Programs\图片转换器\图片转换器.exe" --selftest
"%LOCALAPPDATA%\Programs\图片转换器\unins000.exe" /VERYSILENT
```

> 用 `Start-Process -Wait` 跑安装程序，不要直接 `& setup.exe` ——
> 后者会异步返回，退出码拿不到，看起来像"装了但目录是空的"。

### 本机实测结果（2026-09-28，Windows 11 x64，非提升 PowerShell）

**当前用户版：**

| 检查项 | 结果 |
|---|---|
| 静默安装 | exit=0，落地 `%LOCALAPPDATA%\Programs\图片转换器`，285 个文件 |
| 卸载注册位置 | **HKCU**（HKLM 中为 0）—— 确认是当前用户级 |
| 开始菜单 | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\`（非 ProgramData） |
| 桌面快捷方式 | 已创建 |
| `--smoke` | exit=0（GUI 起来 800 ms 后正常退出） |
| `--selftest` | exit=0，**10 项全通过**，含 PowerPoint 16.0 检测 |
| 卸载 | exit=0；安装目录、开始菜单组、桌面快捷方式、卸载列表条目**全部清除** |
| 用户参数 | `HKCU\Software\imgspec` **保留**（刻意为之） |

自检报告关键行：

```
[通过] 图像库 Pillow —— Pillow 12.2.0
[通过] PDF 引擎 PyMuPDF —— PyMuPDF 1.27.2.3
[通过] 转换位图 -> TIFF —— 1004 x 803 px @ 300 dpi / 117.27 KB / lzw
[通过] 校验 DPI 元数据 —— (300.0, 300.0)
[通过] 转换 PDF -> PNG（逐页） —— 成功 2 页 / 失败 0 页
[通过] PowerPoint 自动化 —— PowerPoint 16.0
结论：全部通过，可以正常使用。
```

**验证时踩到的坑（已修）**：`.iss` 原本写了
`PrivilegesRequiredOverridesAllowed=dialog`，结果非提升运行也被当成机器级安装 ——
卸载键写进 HKLM、开始菜单建到 ProgramData。改成 `=commandline` 后行为才符合预期。
改这个值后必须重新编译才能生效。

---

## 六、方案取舍

| 决策点 | 选择 | 依据 |
|---|---|---|
| **安装包工具** | Inno Setup 6 | 免费开源；原生中文语言文件；自动生成本地化的卸载入口与开始菜单项；脚本可读性最好，能直接读。NSIS 更灵活但中文与卸载逻辑要手写更多；WiX/MSI 学习成本最高，对这个量级的工具不值当 |
| **产物形态** | one-dir（exe + `_internal`） | 沿用 `build.py` 现有配置。若改 `--onefile`，每次启动都要把 206 MB 解压到临时目录，启动明显变慢，且单文件 exe 被杀软误报率更高 |
| **安装范围** | 两种都出，参数切换 | 增量成本极低（同一份 `.iss`，一个 `/D` 开关）。发给同事用 user 版免管理员；机房部署用 machine 版 |
| **卸载入口** | 依赖 Inno 自动生成 | 不再自造卸载脚本，减少出错面 |
| **代码签名** | 暂不签名 | 见下节 |

### 关于未签名的 SmartScreen 警告

未签名的安装包在别人机器上会触发 **Windows Defender SmartScreen**：
> 「Windows 已保护你的电脑」→ 蓝色警告页

首次运行需点击 **「更多信息」→「仍要运行」** 才能继续。发给同事时请在消息里直接说明这一步，
否则对方很可能以为安装包有问题而放弃。

彻底解决需要购买代码签名证书（OV 证书约千元/年，EV 更贵且需 USB 硬件令牌）。
自签名证书对本机组外无效，不解决分发问题。
若日后有了证书，在 `tools/make_installer.py` 编译后加一步 `signtool sign` 即可，位置已预留说明。

---

## 七、前置依赖

打包机（区别于运行机）需要：

1. Python 3.10+ 与 `requirements.txt` 里的依赖
2. PyInstaller：`pip install pyinstaller`
3. **Inno Setup 6**：<https://jrsoftware.org/isdl.php>（约 5 MB）

本机装在 `D:\Tools\Inno Setup 6`（`D:\Tools` 是这台机器的工具目录）。
装在别处时用 `--iscc` 或环境变量 `INNO_SETUP_DIR` 指过去即可。

> Inno Setup 官方只通过 GitHub Releases 分发。若网络不通，
> 手动下载 `innosetup-6.7.3.exe` 后安装即可 —— 编译器不在 PATH 也能被脚本找到。

`make_installer.py` 会依次检查产物是否齐全、ISCC 是否可找到，缺什么就明确提示，不会静默失败。
