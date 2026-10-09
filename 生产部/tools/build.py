"""一键打包。

把之前手工敲的那长串 PyInstaller 参数固化下来，并在打包后做两件收尾：
把使用说明放到 dist 根目录（用户不会进 _internal 翻），以及打印产物概况。

用法：
    python tools/build.py
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    # 保证「python 生产部/tools/build.py」无论从哪个目录启动，
    # 都能 import tools.make_icon（icon 缺失时的兜底生成）
    sys.path.insert(0, str(ROOT))
APP_NAME = "图片转换器"

# 这些包本身不是本工具的依赖，但环境里装了它们，PyInstaller 会顺着某些
# 可选导入把它们整个拖进来 —— 光是 torch 就有 364MB。全部排除。
EXCLUDES = (
    "tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "IPython",
    "pytest", "torch", "torchvision", "torchaudio", "pyarrow", "scipy",
    "onnxruntime", "pandas", "cryptography", "sklearn", "cv2", "numba",
    "sympy", "h5py", "sqlalchemy", "lxml", "wx", "notebook", "jupyter",
    "setuptools",
)

HIDDEN_IMPORTS = ("pythoncom", "win32com", "win32com.client")


def main() -> int:
    try:
        import PyInstaller.__main__
    except ImportError:
        print("没有安装 PyInstaller。请执行： pip install pyinstaller", file=sys.stderr)
        return 1

    icon = ROOT / "icon.ico"
    if not icon.exists():
        print("缺少 icon.ico，正在生成…")
        from tools.make_icon import main as make_icon

        if make_icon() != 0:
            return 1

    # README 跟仓库走：优先项目根（生产部/），没有就找仓库根
    readme = ROOT / "README.md"
    if not readme.exists():
        readme = ROOT.parent / "README.md"

    args = [
        "--noconfirm",
        "--windowed",
        "--name", APP_NAME,
        "--icon", str(icon),
        # 产物路径显式锚定到 ROOT（生产部/），与启动时的工作目录无关 ——
        # PyInstaller 默认用「当前目录」，从仓库根启动会把 dist/build 撒在根目录
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT),
        # 使用说明随包带上；PyInstaller 6 会把它放进 _internal，
        # 下面再复制一份到 dist 根目录，让用户一眼能看到
        "--add-data", f"{readme}{';.'}",
    ]
    for name in HIDDEN_IMPORTS:
        args += ["--hidden-import", name]
    for name in EXCLUDES:
        args += ["--exclude-module", name]
    args.append(str(ROOT / "main.py"))

    print("开始打包…", flush=True)
    started = time.perf_counter()
    PyInstaller.__main__.run(args)
    elapsed = time.perf_counter() - started

    dist_dir = ROOT / "dist" / APP_NAME
    if not (dist_dir / f"{APP_NAME}.exe").exists():
        print("打包似乎失败了：找不到 exe", file=sys.stderr)
        return 1

    # 把说明复制到产物根目录 —— 用户打开文件夹第一眼就能看到
    if readme.exists():
        shutil.copy2(readme, dist_dir / "README.md")

    # 用户要求「双击就能找到，别藏太深」：把 exe 和运行库从 dist/ 提到项目根，
    # 这样打开项目文件夹就直接看到 图片转换器.exe，不用再点进两层目录。
    for name in (f"{APP_NAME}.exe", "_internal", "README.md"):
        source = dist_dir / name
        if not source.exists():
            continue
        target = ROOT / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink()
        shutil.move(str(source), str(target))
    shutil.rmtree(ROOT / "dist", ignore_errors=True)

    exe = ROOT / f"{APP_NAME}.exe"
    size_mb = sum(
        f.stat().st_size
        for base in (exe, ROOT / "_internal")
        if base.exists()
        for f in ([base] if base.is_file() else base.rglob("*"))
        if f.is_file()
    ) / 1048576

    print(f"\n打包完成，用时 {elapsed:.0f}s")
    print(f"  体积：{size_mb:.0f} MB")
    print(f"  双击：{exe}")
    print("\n提示：exe 必须和同级的 _internal 文件夹一起拷走才能用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
