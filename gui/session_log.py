"""会话日志落盘与崩溃归档。

为什么需要它
------------
日志只存在内存里（界面上那个卡片），程序一关就没了 —— 用户遇到错误时往往
也说不清、截图也不全。这里把每次会话的日志同步写进本地文件，并让**下一次
启动**发现「上次没有正常退出」的会话：归档成 crash-*.log 并提示用户导出。

合规说明：全程只写用户自己的机器（`%LOCALAPPDATA%`），不上传任何数据 ——
不涉及个人信息对外提供，因此没有服务器与隐私政策负担。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

# 正常退出时写进文件末尾的标记；下次启动靠它判断上次是否干净退出
OK_FOOTER = "=== 正常退出 ==="
SESSION_NAME = "last-session.log"
CRASH_PREFIX = "crash-"
SESSION_PREFIX = "session-"
# 归档上限（崩溃 + 正常会话合计），避免长期使用后无限堆积
KEEP_ARCHIVES = 10


class SessionLog:
    """一次会话的日志文件；由下一次启动负责归档上一次的会话。"""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.path: Path | None = None
        # start() 时归档出的上次会话（未正常退出时指向 crash-*.log，否则为 None）
        self.previous_crash: Path | None = None
        self.previous_session: Path | None = None
        self._handle = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self, header: str) -> Path:
        """开始新会话：归档上次会话 → 清理旧归档 → 新建本次日志文件。

        返回本次日志文件路径。归档结果见 `previous_crash`（仅上次异常退出时有值）。
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        self._archive_previous()
        self._prune_archives()

        self.path = self.directory / SESSION_NAME
        self._handle = self.path.open("w", encoding="utf-8")
        self.write(header)
        return self.path

    def write(self, line: str) -> None:
        """追加一行。每行 flush —— 崩溃时也要尽量留下最后几行现场。"""
        if self._handle is None:
            return
        self._handle.write(line.rstrip("\n") + "\n")
        self._handle.flush()

    def append_record(self, stamp: str, level: str, message: str) -> None:
        """按界面日志同样的格式写一行（导出文本与落盘内容保持一致）。"""
        self.write(f"{stamp} [{level.upper()}] {message}")

    def finish(self) -> None:
        """正常退出：写结束标记再关闭。下次启动据此判定「干净退出」。"""
        if self._handle is None:
            return
        self._handle.write(OK_FOOTER + "\n")
        self._handle.close()
        self._handle = None

    def abandon(self) -> None:
        """不写结束标记直接关闭文件。

        正常流程用不到（那个走 finish）；这是给「模拟异常退出」的场景用的：
        Windows 上仍被打开的文件无法改名，测试里模拟崩溃必须先释放文件句柄
        —— 真实崩溃时进程已消失，句柄由系统释放，不存在这个问题。
        """
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def crash_text(self) -> str | None:
        """上次异常退出会话的日志全文（没有则 None）。"""
        if self.previous_crash is None:
            return None
        try:
            return self.previous_crash.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _archive_previous(self) -> None:
        """把上次的会话文件改名归档：异常退出命名 crash-*，正常退出命名 session-*。"""
        last = self.directory / SESSION_NAME
        if not last.exists():
            return
        try:
            text = last.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return

        clean_exit = text.rstrip().endswith(OK_FOOTER)
        try:
            stamp = datetime.fromtimestamp(last.stat().st_mtime).strftime("%Y%m%d-%H%M%S")
        except OSError:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        prefix = SESSION_PREFIX if clean_exit else CRASH_PREFIX
        target = self.directory / f"{prefix}{stamp}.log"
        try:
            last.replace(target)
        except OSError:
            return

        self.previous_session = target
        self.previous_crash = None if clean_exit else target

    def _prune_archives(self) -> None:
        """只保留最近的 KEEP_ARCHIVES 份归档（崩溃与正常会话一起算）。"""
        archives = sorted(
            list(self.directory.glob(f"{CRASH_PREFIX}*.log"))
            + list(self.directory.glob(f"{SESSION_PREFIX}*.log")),
            key=lambda path: path.stat().st_mtime if path.exists() else 0,
        )
        for old in archives[:-KEEP_ARCHIVES]:
            try:
                old.unlink()
            except OSError:
                pass


__all__ = ["CRASH_PREFIX", "KEEP_ARCHIVES", "OK_FOOTER", "SESSION_NAME", "SessionLog"]
