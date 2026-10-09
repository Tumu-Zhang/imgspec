"""可复用的界面小组件。"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QTimer,
    Qt,
    Signal,
    Property,
)
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from gui import theme


class EdgeFade(QWidget):
    """滚动区上下边缘的渐隐遮罩。

    内容被滚动视口「拦腰切开」时最生硬；在视口边缘叠一条
    背景色渐变（边缘实色 -> 向内透明），内容滑出时先淡出 ——
    观感平滑，不再出现一条把控件切两半的直线。
    对鼠标事件全透明，不影响滚动与点击。

    遮罩颜色在绘制时实时取自当前主题的窗口底色，不在构造时定格：
    「系统浅色启动 -> 设置里恢复保存的深色」这条路径不会经过任何
    切换回调，定格色会滞留成浅色，深色界面上下便浮现白色渐变带
    （2026-10-09 用户反馈的「上下白边」即此因）。跟随主题则永不失联。
    """

    def __init__(self, parent: QWidget, *, top: bool, height: int = 20) -> None:
        super().__init__(parent)
        self._top = top
        self.setFixedHeight(height)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            height = self.height()
            grad = QLinearGradient(0.0, 0.0, 0.0, float(height))
            solid = QColor(theme.current_theme().bg_primary)
            clear = QColor(solid)
            clear.setAlpha(0)
            if self._top:
                grad.setColorAt(0.0, solid)   # 顶端实色
                grad.setColorAt(1.0, clear)
            else:
                grad.setColorAt(0.0, clear)
                grad.setColorAt(1.0, solid)   # 底端实色
            painter.fillRect(self.rect(), grad)
        finally:
            painter.end()


class CheckBoxHeader(QHeaderView):
    """带全选复选框的水平表头（复选框绘制在第 0 列）。

    - 三态：全不选 / 全选 / 部分选中（半选）；
    - 点击第 0 列切换全选/全不选，其余列保持表头默认行为；
    - 外观与 QSS 的 QCheckBox 指示器保持一致（accent 底 + 白色对勾）。
    """

    toggle_requested = Signal()

    BOX = 16          # 与 QSS 中 QCheckBox::indicator 的 16px 保持一致
    RADIUS = 5

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._state = Qt.CheckState.Unchecked
        self.setSectionsClickable(True)
        self.setHighlightSections(False)

    # -- 状态维护 --------------------------------------------------------
    @property
    def check_state(self) -> Qt.CheckState:
        return self._state

    def set_check_state(self, state: Qt.CheckState) -> None:
        if state != self._state:
            self._state = state
            self.viewport().update()

    # -- 交互 ------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        index = self.logicalIndexAt(event.position().toPoint())
        if index == 0:
            self.toggle_requested.emit()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        # 第 0 列的按下已被消费，释放也不再下传，避免选中态闪烁
        index = self.logicalIndexAt(event.position().toPoint())
        if index == 0:
            return
        super().mouseReleaseEvent(event)

    # -- 绘制 ------------------------------------------------------------
    def paintSection(self, painter, rect, index) -> None:  # noqa: N802 - Qt 命名
        super().paintSection(painter, rect, index)
        if index != 0 or rect.width() < self.BOX:
            return

        t = theme.current_theme()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        side = float(self.BOX)
        x = rect.x() + (rect.width() - side) / 2.0
        y = rect.y() + (rect.height() - side) / 2.0
        box = QRectF(x, y, side, side)

        checked = self._state is Qt.CheckState.Checked
        partial = self._state is Qt.CheckState.PartiallyChecked

        if checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(t.accent)
            painter.drawRoundedRect(box, self.RADIUS, self.RADIUS)
            self._draw_check(painter, box, t.text_inverse)
        elif partial:
            # 注意：QPen 必须传 QColor 实例 —— QPen(str, float) 会触发
            # PySide6 的重载解析缺陷（access violation 而非 TypeError）
            painter.setPen(QPen(QColor(t.accent), 1.0))
            painter.setBrush(t.bg_secondary)
            painter.drawRoundedRect(box, self.RADIUS, self.RADIUS)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(t.accent)
            dash = QRectF(box.x() + 3, box.y() + side / 2 - 1.5, side - 6, 3)
            painter.drawRoundedRect(dash, 1.5, 1.5)
        else:
            painter.setPen(QPen(QColor(t.border_strong), 1.0))
            painter.setBrush(t.bg_secondary)
            painter.drawRoundedRect(box, self.RADIUS, self.RADIUS)

        painter.restore()

    @staticmethod
    def _draw_check(painter: QPainter, box: QRectF, color: str) -> None:
        pen = QPen(QColor(color))
        pen.setWidthF(1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        # 对勾三个关键点，按盒子比例取位
        points = [
            QPointF(box.x() + box.width() * f1, box.y() + box.height() * f2)
            for f1, f2 in ((0.24, 0.52), (0.43, 0.72), (0.78, 0.30))
        ]
        for start, end in zip(points, points[1:]):
            painter.drawLine(start, end)


class EditableChipDelegate(QStyledItemDelegate):
    """可编辑单元格的「输入框感」引导绘制。

    可编辑（ItemIsEditable）的单元格画成一枚中性底 + 细描边的 chip：
    与普通单元格区分开、又不和主题强调色打架；accent 只落在文字上，
    铅笔符用三级文字色 —— 一眼看出「这里可以点击修改」，但不吵。
    """

    PENCIL = "✎"  # ✎ U+270E，Qt 字体回退会自动从符号字体取形

    # -- 就地无缝编辑：编辑器与 chip 同位同形同色 --------------------------
    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        # objectName 交给 QSS：底色/描边与 chip 一致，仅描边转 accent
        # 表示编辑态 —— 单元格是「原地变成输入态」，不是浮起一个框
        editor.setObjectName("pagesEditor")
        editor.setFrame(False)
        return editor

    def updateEditorGeometry(self, editor, option, index) -> None:  # noqa: N802
        # 编辑器完全覆盖 chip 绘制区（与 paint 里的 chip 内缩一致）
        editor.setGeometry(option.rect.adjusted(4, 5, -4, -5))

    def setEditorData(self, editor, index) -> None:  # noqa: N802
        super().setEditorData(editor, index)
        editor.selectAll()  # 单击进入编辑即全选，直接输入即替换

    def paint(self, painter: QPainter, option, index) -> None:
        if not (index.flags() & Qt.ItemFlag.ItemIsEditable):
            super().paint(painter, option, index)
            return

        t = theme.current_theme()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = option.rect
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        # 选中 / 悬停时先铺行底色，chip 才不会悬空
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, QColor(t.accent_wash))
        elif hovered:
            painter.fillRect(rect, QColor(t.bg_hover))

        chip = QRectF(rect.x() + 4, rect.y() + 5, rect.width() - 8, rect.height() - 10)
        # 中性底 + 细描边（悬停时描边转 accent，给出明确的可点反馈）
        painter.setPen(QPen(QColor(t.accent if hovered else t.border_strong), 1.0))
        painter.setBrush(QColor(t.bg_tertiary))
        painter.drawRoundedRect(chip, 6, 6)

        # 铅笔符靠右，文字靠左；都给 chip 留出内边距
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        metrics = painter.fontMetrics()
        pencil_w = metrics.horizontalAdvance(self.PENCIL) + 8

        text_rect = QRectF(
            chip.x() + 8, chip.y(), chip.width() - pencil_w - 12, chip.height()
        )
        painter.setPen(QColor(t.accent))
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(text_rect.width())),
        )

        pencil_rect = QRectF(
            chip.right() - pencil_w, chip.y(), pencil_w - 4, chip.height()
        )
        painter.setPen(QColor(t.text_tertiary))
        painter.drawText(
            pencil_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
            self.PENCIL,
        )
        painter.restore()


class PillToggle(QWidget):
    """胶囊分段开关：两项互斥，点击即切换，高亮块带滑动动画。

    外观：圆角到底的轨道里一枚 accent 高亮胶囊，选中项反白；
    交互：点击任意一侧直接选中（两项时等于拨动开关），
    高亮块以 160ms OutCubic 滑过去 —— 切换有明确的「确认感」。

    API 与 QComboBox 的最小常用子集兼容（addItem/findData/setCurrentIndex/
    currentData/currentText/count/setItemText/currentIndexChanged），
    调用方与测试无需感知控件类型差异。
    """

    currentIndexChanged = Signal(int)

    HEIGHT = 32
    PAD = 3          # 轨道内边距（高亮胶囊与轨道边缘的距离）
    MIN_SEG = 56     # 分段最小宽度

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._texts: list[str] = []
        self._datas: list[object] = []
        self._current = -1
        self._hovered = -1
        self._hl_x = float(self.PAD)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._anim = QPropertyAnimation(self, b"highlightPos", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    # -- 几何 ------------------------------------------------------------
    def _seg_width(self) -> int:
        if not self._texts:
            return self.MIN_SEG
        metrics = self.fontMetrics()
        widest = max(metrics.horizontalAdvance(text) for text in self._texts)
        return max(self.MIN_SEG, widest + 26)

    def sizeHint(self):  # noqa: N802 - Qt 命名
        from PySide6.QtCore import QSize
        return QSize(self._seg_width() * max(1, len(self._texts)) + self.PAD * 2, self.HEIGHT)

    def _seg_rect(self, index: int) -> QRectF:
        seg = self._seg_width()
        return QRectF(
            self.PAD + index * seg,
            self.PAD,
            seg,
            self.height() - self.PAD * 2,
        )

    def _segment_at(self, x: float) -> int:
        seg = self._seg_width()
        index = int((x - self.PAD) // seg)
        return index if 0 <= index < len(self._texts) else -1

    # -- QComboBox 兼容接口 -------------------------------------------------
    def addItem(self, text: str, userData: object = None) -> None:  # noqa: N802
        self._texts.append(text)
        self._datas.append(userData)
        if self._current < 0:
            self._current = 0
            self._hl_x = self._seg_rect(0).x()
        self.updateGeometry()
        self.update()

    def count(self) -> int:
        return len(self._texts)

    def currentIndex(self) -> int:
        return self._current

    def currentData(self) -> object:
        if 0 <= self._current < len(self._datas):
            return self._datas[self._current]
        return None

    def currentText(self) -> str:
        if 0 <= self._current < len(self._texts):
            return self._texts[self._current]
        return ""

    def itemText(self, index: int) -> str:
        return self._texts[index] if 0 <= index < len(self._texts) else ""

    def setItemText(self, index: int, text: str) -> None:  # noqa: N802
        if 0 <= index < len(self._texts):
            self._texts[index] = text
            self.updateGeometry()
            self.update()

    def findData(self, data: object) -> int:  # noqa: N802
        try:
            return self._datas.index(data)
        except ValueError:
            return -1

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        if not (0 <= index < len(self._texts)) or index == self._current:
            return
        self._current = index
        target = self._seg_rect(index).x()
        # 动画只在外部赋值（非初始化）时播放；初始化直接落位
        if self._anim is not None and self.isVisible():
            self._anim.stop()
            self._anim.setStartValue(self._hl_x)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._hl_x = target
        self.update()
        self.currentIndexChanged.emit(index)

    # -- 动画属性 -----------------------------------------------------------
    def _get_highlight_pos(self) -> float:
        return self._hl_x

    def _set_highlight_pos(self, x: float) -> None:
        self._hl_x = x
        self.update()

    highlightPos = Property(float, _get_highlight_pos, _set_highlight_pos)

    # -- 事件 ---------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            index = self._segment_at(event.position().x())
            if index >= 0:
                self.setCurrentIndex(index)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        index = self._segment_at(event.position().x())
        if index != self._hovered:
            self._hovered = index
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hovered != -1:
            self._hovered = -1
            self.update()
        super().leaveEvent(event)

    # -- 绘制 ---------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        t = theme.current_theme()
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            # 轨道
            track = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
            radius = (self.height() - 2) / 2
            painter.setPen(QPen(QColor(t.border), 1.0))
            painter.setBrush(QColor(t.bg_secondary))
            painter.drawRoundedRect(track, radius, radius)

            enabled = self.isEnabled()
            seg = self._seg_width()
            if self._current >= 0:
                # 高亮胶囊（accent 底，随 highlightPos 滑动）
                pill = QRectF(
                    self._hl_x,
                    self.PAD,
                    seg,
                    self.height() - self.PAD * 2,
                )
                pill_radius = pill.height() / 2
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(t.accent if enabled else t.border_strong))
                painter.drawRoundedRect(pill, pill_radius, pill_radius)

            # 文案：选中项反白，未选中二级色（悬停提亮）
            font = painter.font()
            for index, text in enumerate(self._texts):
                rect = self._seg_rect(index)
                if index == self._current:
                    color = t.text_inverse if enabled else t.bg_secondary
                    font.setBold(True)
                else:
                    if not enabled:
                        color = t.text_tertiary
                    elif index == self._hovered:
                        color = t.text_primary
                    else:
                        color = t.text_secondary
                    font.setBold(False)
                painter.setFont(font)
                painter.setPen(QColor(color))
                painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
        finally:
            painter.end()


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

        # 柔和投影：浮层与窗口之间的「空气感」
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(15, 23, 42, 60))
        self.setGraphicsEffect(shadow)

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

    def retranslate(self, close_tooltip: str) -> None:
        """语言切换时刷新固定文案（关闭按钮的悬浮提示）。"""
        self._close_btn.setToolTip(close_tooltip)

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


__all__ = [
    "CheckBoxHeader",
    "EdgeFade",
    "EditableChipDelegate",
    "PillToggle",
    "ToastNotification",
]
