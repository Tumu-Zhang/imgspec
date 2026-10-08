"""打包与直接运行用的入口。

双击运行、或由 PyInstaller 打包成 exe 时都以这个文件为入口。
支持一个自检开关，用来确认打包后的程序真的还能干活：

    图片转换器.exe --selftest
"""

from __future__ import annotations

import multiprocessing
import sys

SELFTEST_FLAG = "--selftest"


def main() -> int:
    # 打包成 exe 后，若子进程被意外拉起，没有这行会反复启动新窗口
    multiprocessing.freeze_support()

    if SELFTEST_FLAG in sys.argv:
        # 不开窗口，跑一次真实的 位图->TIFF、PDF->PNG 转换并校验结果
        from imgspec.selftest import main as run_selftest

        return run_selftest()

    from gui.app import main as run_app

    return run_app()


if __name__ == "__main__":
    sys.exit(main())
