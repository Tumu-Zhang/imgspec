"""可复用的界面小组件。"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui import theme


class ToastNotification(QFrame):
    """右下角浮层通知，用于转换完成反馈。

    非模态，不打断继续操作：
    - 全部成功时短暂停留后自动消失；
    - 有失败/警告时驻留，由用户手动关闭；
    - 跟随主窗口尺寸变化重新定位。
    """

    MARGIN = 16
    WIDTH = 360

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setVisible(False)
        # 固定宽度：wordWrap 文本的 sizeHint 宽度不稳定，
        # 依赖它定位会把浮层推出窗口右边界
        self.setFixedWidth(self.WIDTH)

        parent.installEventFilter(self)

        self._bar = QFrame()
        self._bar.setFixedWidth(4)

        self._title = QLabel()
        self._title.setObjectName("toastTitle")
        self._body = QLabel()
        self._body.setObjectName("toastBody")
        self._body.setWordWrap(True)
        self._body.setTextFormat(Qt.TextFormat.PlainText)

        self._close_btn = QPushButton("×")
        self._close_btn.setObjectName("toastClose")
        self._close_btn.setFixedSize(24, 24)
        self._close_btn.setToolTip("关闭")
        self._close_btn.clicked.connect(self.dismiss)

        self._action_btn = QPushButton()
        self._action_btn.setObjectName("toastAction")
        self._action_btn.setVisible(False)
        self._action_callback: Callable[[], None] | None = None
        self._action_btn.clicked.connect(self._invoke_action)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(theme.SPACE_SM)
        header.addWidget(self._title, 1)
        header.addWidget(self._close_btn, 0, Qt.AlignmentFlag.AlignTop)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addStretch(1)
        actions.addWidget(self._action_btn)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(theme.SPACE_XS)
        right.addLayout(header)
        right.addWidget(self._body)
        right.addLayout(actions)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(
            theme.SPACE_SM, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD
        )
        outer.setSpacing(theme.SPACE_MD)
        outer.addWidget(self._bar, 0)
        outer.addLayout(right, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)

    # ------------------------------------------------------------------
    # 展示与关闭
    # ------------------------------------------------------------------
    def show_message(
        self,
        title: str,
        body: str = "",
        *,
        level: str = "ok",
        action_text: str = "",
        on_action: Callable[[], None] | None = None,
        auto_close_ms: int = 6000,
    ) -> None:
        """显示通知。level 为 "ok"（绿条）或 "warn"（黄条，默认不自动关闭时传 0）。"""
        self._title.setText(title)
        self._body.setText(body)
        self._body.setVisible(bool(body))

        # 直接内联设置色条背景：避免依赖 objectName + 手动 repolish
        t = theme.current_theme()
        bar_color = t.success if level == "ok" else t.warning
        self._bar.setStyleSheet(
            f"background: {bar_color}; border: none; border-radius: 2px;"
        )

        if action_text and on_action is not None:
            self._action_btn.setText(action_text)
            self._action_btn.setVisible(True)
            self._action_callback = on_action
        else:
            self._action_btn.setVisible(False)
            self._action_callback = None

        self._timer.stop()
        # 先显示再定位：隐藏时布局未激活，wordWrap 高度算不准
        self.setVisible(True)
        self._reposition()
        self.raise_()

        if auto_close_ms > 0:
            self._timer.start(auto_close_ms)

    def dismiss(self) -> None:
        """隐藏通知。"""
        self._timer.stop()
        self.setVisible(False)

    def _invoke_action(self) -> None:
        if self._action_callback is not None:
            self._action_callback()

    # ------------------------------------------------------------------
    # 定位：主窗口右下角，跟随窗口尺寸变化
    # ------------------------------------------------------------------
    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()  # 固定宽度下只按内容重算高度
        x = parent.width() - self.width() - self.MARGIN
        y = parent.height() - self.size().height() - self.MARGIN
        self.move(max(self.MARGIN, x), max(self.MARGIN, y))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            if self.isVisible():
                self._reposition()
        return super().eventFilter(watched, event)


__all__ = ["ToastNotification"]
