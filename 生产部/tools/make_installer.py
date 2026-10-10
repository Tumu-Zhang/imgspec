"""一键制作安装包。

tools/build.py 产出的是绿色版（exe + _internal，拷走就能用）；
这个脚本把它再包成真正的安装程序：有安装向导、开始菜单项、桌面快捷方式
和规范的卸载入口，适合直接发给别人。

用法：
    python tools/make_installer.py                  # 两种范围都产出（默认）
    python tools/make_installer.py --scope user     # 只出「当前用户」版
    python tools/make_installer.py --scope machine  # 只出「所有用户」版
    python tools/make_installer.py --version 1.1.0  # 指定版本号

前置条件：
    1. 先跑一次 python tools/build.py 生成绿色版产物
    2. 安装 Inno Setup 6（https://jrsoftware.org/isdl.php）

产物：
    dist\\installer\\图片转换器-<版本>-<范围>-setup.exe
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "图片转换器"

# 版本号。与 imgspec.__version__ 保持一致（发布清单里有同步这一项）。
DEFAULT_VERSION = "1.1.0"

# Inno Setup 脚本与编译器的输出目录
ISS_FILE = ROOT / "installer" / f"{APP_NAME}.iss"
OUTPUT_DIR = ROOT / "dist" / "installer"

# 安装范围：user = 当前用户（免管理员，装到 %LOCALAPPDATA%\\Programs）
#           machine = 所有用户（需管理员，装到 Program Files）
SCOPES = ("user", "machine")
SCOPE_LABEL = {"user": "当前用户", "machine": "所有用户"}

# ISCC.exe 的常见安装位置（Inno Setup 6）。
# 装在别处时用环境变量 INNO_SETUP_DIR 指向安装目录，或用 --iscc 直接给完整路径。
ISCC_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    Path(r"D:\Tools\Inno Setup 6\ISCC.exe"),
    Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe")),
)


def find_iscc(explicit: str | None = None) -> Path | None:
    """定位 Inno Setup 编译器。

    优先级：--iscc 参数 > 环境变量 INNO_SETUP_DIR > PATH > 常见安装位置。
    """
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None

    env_dir = os.environ.get("INNO_SETUP_DIR")
    if env_dir:
        for name in ("ISCC.exe", "Compil32.exe"):
            candidate = Path(env_dir) / name
            if candidate.exists():
                return candidate

    which = shutil.which("ISCC")
    if which:
        return Path(which)

    for candidate in ISCC_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def check_payload() -> list[str]:
    """确认 PyInstaller 产物齐全。返回缺失项列表。"""
    required = {
        "可执行文件": ROOT / f"{APP_NAME}.exe",
        "运行库目录": ROOT / "_internal",
        "使用说明": ROOT / "README.md",
        "应用图标": ROOT / "icon.ico",
    }
    return [name for name, path in required.items() if not path.exists()]


def build_iss_args(iscc: Path, version: str, scope: str) -> list[str]:
    args = [
        str(iscc),
        str(ISS_FILE),
        f"/DMyAppVersion={version}",
    ]
    if scope == "machine":
        # 定义了这个符号，.iss 里就走「所有用户」分支
        args.append("/DMyAppScopeMachine")
    return args


def main() -> int:
    parser = argparse.ArgumentParser(description="制作图片转换器安装包")
    parser.add_argument(
        "--scope",
        choices=(*SCOPES, "both"),
        default="both",
        help="安装范围：user 当前用户 / machine 所有用户 / both 都出（默认）",
    )
    parser.add_argument("--version", default=DEFAULT_VERSION, help=f"版本号（默认 {DEFAULT_VERSION}）")
    parser.add_argument(
        "--iscc",
        help="ISCC.exe 的完整路径。默认按 PATH → INNO_SETUP_DIR → 常见安装位置自动查找",
    )
    args = parser.parse_args()

    missing = check_payload()
    if missing:
        print("缺少打包产物：" + "、".join(missing), file=sys.stderr)
        print("请先执行： python tools/build.py", file=sys.stderr)
        return 1

    iscc = find_iscc(args.iscc)
    if iscc is None:
        print("没有找到 Inno Setup 编译器 ISCC.exe。", file=sys.stderr)
        print("任选其一：", file=sys.stderr)
        print("  · 安装 Inno Setup 6：https://jrsoftware.org/isdl.php", file=sys.stderr)
        print("  · 设置环境变量 INNO_SETUP_DIR 指向安装目录", file=sys.stderr)
        print("  · 用 --iscc 直接给出 ISCC.exe 路径", file=sys.stderr)
        return 1

    scopes = SCOPES if args.scope == "both" else (args.scope,)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"开始制作安装包（版本 {args.version}）…", flush=True)
    started = time.perf_counter()
    produced: list[Path] = []

    for scope in scopes:
        label = SCOPE_LABEL[scope]
        print(f"  [{label}] 编译中…", flush=True)
        completed = subprocess.run(
            build_iss_args(iscc, args.version, scope),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            print(f"  [{label}] 编译失败：", file=sys.stderr)
            print(completed.stdout or completed.stderr, file=sys.stderr)
            return 1

        setup = OUTPUT_DIR / f"{APP_NAME}-{args.version}-{label}-setup.exe"
        if not setup.exists():
            print(f"  [{label}] 没找到预期产物：{setup}", file=sys.stderr)
            return 1
        produced.append(setup)

    elapsed = time.perf_counter() - started
    print(f"\n安装包制作完成，用时 {elapsed:.0f}s")
    for setup in produced:
        size_mb = setup.stat().st_size / 1048576
        print(f"  {size_mb:6.1f} MB  {setup}")
    print("\n验证方式见 installer/README.md 的「验证步骤」。")
    print("提示：未签名的安装包在别人机器上会被 SmartScreen 拦截，")
    print("      首次运行需点「更多信息 → 仍要运行」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
