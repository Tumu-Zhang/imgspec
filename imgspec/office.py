"""通过本机 PowerPoint 把 PPT/PPTX 导出为 PDF（矢量保真路径）。

为什么绕道 PDF 而不是直接 `Slide.Export` 出 PNG：
    1. PDF 保留矢量文字与图形，之后由 PyMuPDF 按任意目标 DPI 光栅化，
       600 dpi、1200 dpi 都依然锐利；
    2. `Slide.Export` 的像素尺寸有上限，做大尺寸高 DPI 输出会受限；
    3. PPT 与 PDF 两种输入共用同一条渲染后端，行为一致、可复现。

本模块是唯一依赖 Microsoft Office 的地方。没有装 PowerPoint 时不做静默降级
（python-pptx 无法渲染页面），而是抛出可读的错误提示用户改用 PDF。

关于关闭 PowerPoint 的实测结论（Office 16 / Windows，本机反复验证）
------------------------------------------------------------------
用 COM 优雅退出非常慢，而且慢得没有道理：
    app.Quit()                        约 16 秒
    紧接着 Release 掉 Application 代理  约 60 秒
    合计                              ~76 秒全部阻塞在调用线程里
把这段挪到后台线程也不行：从非创建线程调用未 marshal 的 COM 接口，Quit 实际上
根本没送达，PowerPoint 会一直留在后台（实测 180 秒仍在，占 600 MB 内存）。

所以这里改为直接结束我们自己启动的那个进程：
    DispatchEx 保证它是独立实例，和用户正在编辑的 PowerPoint 互不影响；
    我们只让它「只读打开 + 另存为临时 PDF」，PDF 写完并关闭后没有任何未保存内容，
    此时终止它不会造成数据丢失，也不会触发「上次异常退出」的文档恢复提示。
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path

PP_SAVE_AS_PDF = 32
PP_ALERTS_NONE = 1
PP_FIXED_FORMAT_PDF = 2

_POWERPOINT_IMAGE = "POWERPNT.EXE"


class OfficeUnavailableError(RuntimeError):
    """本机缺少可用的 PowerPoint，或 COM 调用失败。"""


def _list_powerpoint_pids() -> set[int]:
    """列出当前所有 POWERPNT.EXE 的进程号。"""
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {_POWERPOINT_IMAGE}", "/FO", "CSV", "/NH"],
            capture_output=True,
            timeout=20,
        )
    except Exception:  # noqa: BLE001 - 取不到就退化成温和关闭
        return set()

    # 中文 Windows 上 tasklist 输出是 GBK；我们只关心 "POWERPNT.EXE","<pid>"
    # 这段纯 ASCII，所以按 ASCII 解码并把其余内容替换掉即可，不依赖系统代码页。
    pids: set[int] = set()
    for line in completed.stdout.decode("ascii", errors="replace").splitlines():
        fields = line.split('","')
        if len(fields) >= 2 and fields[0].strip('"').upper().startswith("POWERPNT"):
            with contextlib.suppress(ValueError):
                pids.add(int(fields[1].strip('"')))
    return pids


def _kill_processes(pids: Iterable[int]) -> None:
    for pid in pids:
        with contextlib.suppress(Exception):
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=20,
            )


def _import_win32():
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except ImportError as exc:  # pragma: no cover - 非 Windows 或未装 pywin32
        raise OfficeUnavailableError(
            "未安装 pywin32，无法驱动 PowerPoint。请执行 pip install pywin32，"
            "或把幻灯片另存为 PDF 后再输入。"
        ) from exc
    return pythoncom, win32com.client


def is_available() -> tuple[bool, str]:
    """检测本机是否可以驱动 PowerPoint。返回 (是否可用, 说明)。"""
    if os.name != "nt":
        return False, "当前系统非 Windows，无法调用 PowerPoint"
    try:
        pythoncom, win32 = _import_win32()
    except OfficeUnavailableError as exc:
        return False, str(exc)

    pythoncom.CoInitialize()
    before = _list_powerpoint_pids()
    app = None
    try:
        app = win32.DispatchEx("PowerPoint.Application")
        version = ""
        with contextlib.suppress(Exception):
            version = str(app.Version)
        return True, f"PowerPoint {version}".strip()
    except Exception as exc:  # noqa: BLE001
        return False, f"未检测到可用的 PowerPoint：{exc}"
    finally:
        # 探测用的实例立刻回收，不走那套 76 秒的优雅退出
        spawned = _list_powerpoint_pids() - before
        if spawned:
            _kill_processes(spawned)
        elif app is not None:
            with contextlib.suppress(Exception):
                app.Quit()


class PowerPointSession:
    """复用同一个 PowerPoint 进程批量导出，避免每个文件启停一次。

    必须在创建它的线程内使用（COM 单元模型）。转换工作线程里用 with 语句即可。
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = Path(
            cache_dir or Path(tempfile.mkdtemp(prefix="imgspec_ppt_"))
        )
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._owns_cache = cache_dir is None
        self._app = None
        self._own_pids: set[int] = set()

    # -- 生命周期 -------------------------------------------------------
    def __enter__(self) -> "PowerPointSession":
        pythoncom, win32 = _import_win32()
        # COM 单元必须在使用它的线程里初始化。这里刻意不配对调用 CoUninitialize：
        # 反初始化会同步等待 PowerPoint 代理释放，而那正是我们要规避的几十秒阻塞。
        # 单元随线程/进程结束由系统回收。
        pythoncom.CoInitialize()

        before = _list_powerpoint_pids()
        try:
            self._app = win32.DispatchEx("PowerPoint.Application")
            # PowerPoint 不允许把主窗口设为不可见，只能接受它出现；
            # 用 DispatchEx 保证是独立实例，不干扰用户正在编辑的文稿。
            with contextlib.suppress(Exception):
                self._app.Visible = True
            with contextlib.suppress(Exception):
                self._app.DisplayAlerts = PP_ALERTS_NONE
            with contextlib.suppress(Exception):
                self._app.AutomationSecurity = 1  # 保守值，禁用宏相关提示
        except Exception as exc:  # noqa: BLE001
            self.__exit__(None, None, None)
            raise OfficeUnavailableError(
                f"无法启动 PowerPoint：{exc}。请确认已安装 Microsoft PowerPoint，"
                "或把幻灯片另存为 PDF 后再输入。"
            ) from exc

        # 进程刚起来时 tasklist 可能还没列出它，重试几次
        for _ in range(10):
            self._own_pids = _list_powerpoint_pids() - before
            if self._own_pids:
                break
            time.sleep(0.3)
        return self

    def __exit__(self, *exc_info) -> None:
        pids = self._own_pids
        self._own_pids = set()
        app = self._app
        self._app = None

        if pids:
            # 结束进程（瞬时）。进程消失后释放 COM 代理不会再等它做退出握手。
            _kill_processes(pids)
        elif app is not None:
            # 极端情况下没识别出自己的进程号，只能走可能很慢的优雅退出
            with contextlib.suppress(Exception):
                app.Quit()

        if app is not None:
            with contextlib.suppress(Exception):
                del app

        if self._owns_cache:
            shutil.rmtree(self._cache_dir, ignore_errors=True)

    # -- 能力 -----------------------------------------------------------
    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    # -- 导出 -----------------------------------------------------------
    def export_pdf(self, src: Path | str, dst: Path | None = None) -> Path:
        """把演示文稿导出为 PDF，返回 PDF 路径。

        同一源文件重复调用会命中缓存，方便「一次导出、多档 DPI 输出」。
        """
        if self._app is None:
            raise OfficeUnavailableError("PowerPoint 会话尚未启动")

        src = Path(src).resolve()
        if not src.exists():
            raise FileNotFoundError(f"找不到文件：{src}")

        target = Path(dst).resolve() if dst else self._cache_for(src)
        if target.exists() and target.stat().st_size > 0:
            return target

        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            with contextlib.suppress(OSError):
                target.unlink()

        presentation = None
        try:
            # WithWindow=False 能显著加快导出，且必须与 ReadOnly=True 同时使用
            presentation = self._app.Presentations.Open(str(src), True, False, False)
            presentation.SaveAs(str(target), PP_SAVE_AS_PDF)
        except Exception as exc:  # noqa: BLE001
            raise OfficeUnavailableError(
                f"PowerPoint 导出 PDF 失败：{src.name} —— {exc}"
            ) from exc
        finally:
            if presentation is not None:
                with contextlib.suppress(Exception):
                    presentation.Close()

        if not target.exists() or target.stat().st_size == 0:
            raise OfficeUnavailableError(
                f"PowerPoint 未生成有效 PDF：{src.name}。"
                "文件可能已损坏，或包含被禁用宏的受限内容。"
            )
        return target

    def describe(self, src: Path | str) -> tuple[int, float | None]:
        """读取幻灯片页数与宽高比（旧版 .ppt 无法用 python-pptx 探测时使用）。"""
        if self._app is None:
            raise OfficeUnavailableError("PowerPoint 会话尚未启动")
        src = Path(src).resolve()
        presentation = None
        try:
            presentation = self._app.Presentations.Open(str(src), True, False, False)
            count = int(presentation.Slides.Count)
            width = float(presentation.PageSetup.SlideWidth)
            height = float(presentation.PageSetup.SlideHeight)
            aspect = width / height if height else None
            return count, aspect
        except Exception as exc:  # noqa: BLE001
            raise OfficeUnavailableError(f"PowerPoint 读取失败：{src.name} —— {exc}") from exc
        finally:
            if presentation is not None:
                with contextlib.suppress(Exception):
                    presentation.Close()

    # -- 内部 -----------------------------------------------------------
    def _cache_for(self, src: Path) -> Path:
        digest = hashlib.sha1(
            f"{src}:{src.stat().st_mtime_ns}:{src.stat().st_size}".encode("utf-8")
        ).hexdigest()[:16]
        return self._cache_dir / f"{digest}.pdf"


__all__ = [
    "OfficeUnavailableError",
    "PowerPointSession",
    "is_available",
]
