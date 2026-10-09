"""后台转换线程。

界面线程只负责刷新，所有重活（PDF 光栅化、Office 自动化、编码）都在这里跑。
PowerPoint 的 COM 调用必须在创建它的线程里初始化，所以 Office 会话在这里按需
惰性建立，而不是在界面线程预检。
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from imgspec.model import OutputSpec
from imgspec.pipeline import ConversionReport, Converter


class ConversionWorker(QObject):
    """在独立线程执行一批转换。"""

    progress = Signal(int, int, str)   # 已完成文件数, 总数, 当前文件名
    finished = Signal(object)          # ConversionReport
    failed = Signal(str)               # 未预期异常的可读描述

    def __init__(
        self,
        files: list[Path],
        spec: OutputSpec,
        page_overrides: dict[str, str | None] | None = None,
        spec_overrides: dict[str, OutputSpec] | None = None,
    ) -> None:
        super().__init__()
        self._files = list(files)
        self._spec = spec
        # 每个文件自己的页码范围（仅 PDF/PPT 会用到），键为 str(源文件路径)
        self._page_overrides = page_overrides
        # 每个文件自己的尺寸参数（行级），键为 str(源文件路径)
        self._spec_overrides = spec_overrides
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """请求取消。当前文件会处理完，之后不再开始新文件。"""
        self._cancel.set()

    @Slot()
    def run(self) -> None:
        try:
            converter = Converter(
                self._spec,
                progress=lambda event: self.progress.emit(
                    event.completed_files, event.total_files, event.current
                ),
                should_cancel=self._cancel.is_set,
            )
            report: ConversionReport = converter.run(
                self._files, self._page_overrides, self._spec_overrides
            )
        except Exception as exc:  # noqa: BLE001 - 兜住一切，避免线程静默死亡
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished.emit(report)


__all__ = ["ConversionWorker"]
