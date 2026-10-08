"""界面主题：浅色/深色双主题、Fluent (Windows 11) 样式表与自绘图标。

设计方向
--------
Windows 11 Fluent 体系：柔和的分层背景（窗口灰 + 纯白/深灰卡片）、
克制的系统蓝强调色、更大的圆角与更淡的边框，控件反馈以背景微变为主。
内容优先，文件队列是视觉中心，参数栏退后但始终可见。

主题切换
--------
通过 theme.set_theme("light" | "dark") 切换当前主题，再用
theme.build_stylesheet(theme.current_theme().name) 重新设置 QApplication 的样式表。
界面代码中需要动态颜色时请使用 theme.current_theme() 返回的 Theme 实例，
而不要直接引用本模块底部的 LIGHT 常量。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
)

# ----------------------------------------------------------------------
# 间距：4px 基础网格
# ----------------------------------------------------------------------
SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 24
SPACE_2XL = 32

# ----------------------------------------------------------------------
# 圆角：Win11 Fluent 卡片 8px、控件 5px
# ----------------------------------------------------------------------
RADIUS_CARD = 8
RADIUS_CTRL = 5

# ----------------------------------------------------------------------
# 字号阶梯
# ----------------------------------------------------------------------
FONT_XL = 13   # 规格条主读数
FONT_LG = 11   # 窗口/分组标题
FONT_MD = 10   # 正文、标签、输入框、按钮
FONT_SM = 9    # 次要信息：表头、日志、计数、状态、提示

# ----------------------------------------------------------------------
# 字体
# ----------------------------------------------------------------------
UI_FAMILIES = (
    "Microsoft YaHei",
    "Microsoft YaHei UI",
    "PingFang SC",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "Segoe UI",
)
MONO_FAMILIES = (
    "Cascadia Mono",
    "Cascadia Code",
    "Consolas",
    "JetBrains Mono",
    "DejaVu Sans Mono",
    "Courier New",
)


@lru_cache(maxsize=1)
def _installed_families() -> frozenset[str]:
    try:
        return frozenset(QFontDatabase.families())
    except Exception:  # noqa: BLE001 - 无 GUI 环境下退化为默认字体
        return frozenset()


def _pick(candidates: tuple[str, ...], fallback: str) -> str:
    installed = _installed_families()
    for name in candidates:
        if name in installed:
            return name
    return fallback


@lru_cache(maxsize=1)
def ui_family() -> str:
    """界面字体：中文可读性优先。"""
    return _pick(UI_FAMILIES, "sans-serif")


@lru_cache(maxsize=1)
def mono_family() -> str:
    """等宽字体：尺寸、DPI、体积这些数字需要列对齐才有用。"""
    return _pick(MONO_FAMILIES, "monospace")


def ui_font(size: int = FONT_MD, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont(ui_family(), size)
    font.setWeight(weight)
    return font


def mono_font(size: int = FONT_MD, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont(mono_family(), size)
    font.setWeight(weight)
    return font


# ----------------------------------------------------------------------
# 主题数据类
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Theme:
    name: str

    # 背景
    bg_primary: str      # 窗口/台面
    bg_secondary: str    # 卡片、输入框、列表
    bg_tertiary: str     # 工具栏、表头
    bg_hover: str        # 悬停
    bg_sunk: str         # 禁用、进度槽、凹陷区

    # 边框
    border: str
    border_strong: str

    # 文字
    text_primary: str
    text_secondary: str
    text_tertiary: str
    text_inverse: str    # 深色按钮上的白字

    # 强调
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_wash: str
    accent_line: str

    # 语义
    success: str
    success_wash: str
    warning: str
    warning_wash: str
    danger: str
    danger_wash: str
    info: str
    info_wash: str

    # 规格条（深色块）
    strip: str
    strip_ink: str
    strip_ink_soft: str


LIGHT = Theme(
    name="light",
    bg_primary="#f3f3f3",
    bg_secondary="#ffffff",
    bg_tertiary="#f9f9f9",
    bg_hover="#f0f0f0",
    bg_sunk="#ebebeb",
    border="#e5e5e5",
    border_strong="#c7c7c7",
    text_primary="#1b1b1b",
    text_secondary="#5d5d5d",
    text_tertiary="#8a8a8a",
    text_inverse="#ffffff",
    accent="#0067c0",
    accent_hover="#1975c5",
    accent_pressed="#004e8c",
    accent_wash="#e5f1fb",
    accent_line="#0067c0",
    success="#0f7b0f",
    success_wash="#dff6dd",
    warning="#9d5d00",
    warning_wash="#fff4ce",
    danger="#c42b1c",
    danger_wash="#fde7e9",
    info="#0067c0",
    info_wash="#e5f1fb",
    strip="#1b1b1b",
    strip_ink="#f5f5f5",
    strip_ink_soft="#a3a3a3",
)

DARK = Theme(
    name="dark",
    bg_primary="#202020",
    bg_secondary="#2d2d2d",
    bg_tertiary="#282828",
    bg_hover="#383838",
    bg_sunk="#191919",
    border="#3d3d3d",
    border_strong="#555555",
    text_primary="#f2f2f2",
    text_secondary="#c5c5c5",
    text_tertiary="#8a8a8a",
    text_inverse="#1b1b1b",
    accent="#4cc2ff",
    accent_hover="#69cfff",
    accent_pressed="#8fd6ff",
    accent_wash="#153b52",
    accent_line="#4cc2ff",
    success="#6ccb5f",
    success_wash="#14331d",
    warning="#e8c547",
    warning_wash="#3a3312",
    danger="#ff99a4",
    danger_wash="#3d1d1f",
    info="#4cc2ff",
    info_wash="#153b52",
    strip="#141414",
    strip_ink="#f2f2f2",
    strip_ink_soft="#8a8a8a",
)

_current_theme_name: str = "light"


def set_theme(name: str) -> None:
    """切换当前主题名。"""
    global _current_theme_name
    if name not in ("light", "dark"):
        raise ValueError(f"未知的主题名：{name}")
    _current_theme_name = name


def current_theme() -> Theme:
    """返回当前生效的主题实例。"""
    return DARK if _current_theme_name == "dark" else LIGHT


# ----------------------------------------------------------------------
# 向后兼容：旧代码直接导入的常量（保持浅色主题值）
# ----------------------------------------------------------------------
BENCH = LIGHT.bg_primary
SURFACE = LIGHT.bg_secondary
SURFACE_SUNK = LIGHT.bg_sunk
SURFACE_HOVER = LIGHT.bg_hover
INK = LIGHT.text_primary
INK_SOFT = LIGHT.text_secondary
INK_FAINT = LIGHT.text_tertiary
RULE = LIGHT.border
RULE_STRONG = LIGHT.border_strong
ACCENT = LIGHT.accent
ACCENT_DEEP = LIGHT.accent_hover
ACCENT_WASH = LIGHT.accent_wash
ACCENT_LINE = LIGHT.accent_line
STRIP = LIGHT.strip
STRIP_INK = LIGHT.strip_ink
STRIP_INK_SOFT = LIGHT.strip_ink_soft
CAUTION = LIGHT.warning
CAUTION_WASH = LIGHT.warning_wash
DANGER = LIGHT.danger
DANGER_WASH = LIGHT.danger_wash
OK = LIGHT.success
OK_WASH = LIGHT.success_wash

LOG_COLORS = {
    "ok": OK,
    "info": INK,
    "warn": CAUTION,
    "error": DANGER,
}


# ----------------------------------------------------------------------
# 自绘图标：蓝色渐变底 + 白色相片卡（山与太阳）+ 裁切角标
# ----------------------------------------------------------------------
# 品牌色固定，不随界面主题变化 —— 任务栏、标题栏、文件夹里看到的
# 始终是同一枚标记，利于辨识。
BRAND_TILE_TOP = "#2f80e8"       # 渐变底：顶部亮蓝
BRAND_TILE_BOTTOM = "#0a4ca8"    # 渐变底：底部深蓝
BRAND_CARD = "#ffffff"           # 相片卡
BRAND_MOUNTAIN = "#0e5bb5"       # 近山
BRAND_MOUNTAIN_FAR = "#5aa4ef"   # 远山
BRAND_SUN = "#6fd3ff"            # 太阳
BRAND_CROP = "#6fd3ff"           # 裁切角标


def icon_pixmap(size: int, theme_name: str | None = None) -> QPixmap:
    """应用图标：蓝色渐变圆角方底，中央白色相片卡（山与太阳），
    左上/右下一对青色裁切角标 —— 「把图片裁到投稿规格」的意象。

    小尺寸自动做减法（<32px 略去角标与太阳），保证 16px 下依然清晰可读。
    theme_name 参数保留以兼容旧调用；品牌标记本身不随主题变化。
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 渐变圆角方底
        inset = size * 0.075
        tile = QRectF(inset, inset, size - inset * 2, size - inset * 2)
        radius = size * 0.225
        gradient = QLinearGradient(tile.left(), tile.top(), tile.left(), tile.bottom())
        gradient.setColorAt(0.0, QColor(BRAND_TILE_TOP))
        gradient.setColorAt(1.0, QColor(BRAND_TILE_BOTTOM))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(tile, radius, radius)

        # 白色相片卡
        card = QRectF(size * 0.24, size * 0.24, size * 0.52, size * 0.52)
        painter.setBrush(QColor(BRAND_CARD))
        painter.drawRoundedRect(card, size * 0.09, size * 0.09)

        # 远山 + 近山（横向内收，避免碰到卡片圆角）
        painter.setBrush(QColor(BRAND_MOUNTAIN_FAR))
        far = painter_path()
        if far is not None:
            far.moveTo(size * 0.34, size * 0.76)
            far.lineTo(size * 0.50, size * 0.47)
            far.lineTo(size * 0.66, size * 0.76)
            far.closeSubpath()
            painter.drawPath(far)

        painter.setBrush(QColor(BRAND_MOUNTAIN))
        near = painter_path()
        if near is not None:
            near.moveTo(size * 0.26, size * 0.76)
            near.lineTo(size * 0.37, size * 0.56)
            near.lineTo(size * 0.48, size * 0.76)
            near.closeSubpath()
            painter.drawPath(near)

        # 小尺寸（16/20/24）只保留「相片 + 山」的主体，细节元素省略
        if size >= 32:
            # 太阳
            painter.setBrush(QColor(BRAND_SUN))
            painter.drawEllipse(
                QPointF(size * 0.615, size * 0.375), size * 0.05, size * 0.05
            )

            # 左上 / 右下裁切角标：取景框意象，轻微悬于卡片角外
            pen = QPen(QColor(BRAND_CROP))
            pen.setWidthF(size * 0.045)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            leg = size * 0.08
            for cx, cy, dx, dy in (
                (size * 0.14, size * 0.14, 1, 1),   # 左上
                (size * 0.86, size * 0.86, -1, -1),  # 右下
            ):
                painter.drawLine(QPointF(cx, cy), QPointF(cx + leg * dx, cy))
                painter.drawLine(QPointF(cx, cy), QPointF(cx, cy + leg * dy))
    finally:
        painter.end()
    return pixmap


def painter_path():
    """延迟导入 QPainterPath，避免某些无 GUI 环境报错。"""
    try:
        from PySide6.QtGui import QPainterPath
        return QPainterPath()
    except Exception:  # noqa: BLE001
        return None


def app_icon(theme_name: str | None = None) -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(icon_pixmap(size, theme_name))
    return icon


# ----------------------------------------------------------------------
# 样式表
# ----------------------------------------------------------------------
def build_stylesheet(theme_name: str | None = None) -> str:
    """整份 QSS。传入主题名即可切换浅色/深色外观。"""
    t = DARK if theme_name == "dark" else LIGHT
    mono = mono_family()
    ui = ui_family()

    return f"""
/* ---------- 基座 ---------- */
QWidget {{
    background: {t.bg_primary};
    color: {t.text_primary};
    font-family: "{ui}";
    font-size: {FONT_MD}pt;
}}
QMainWindow, QDialog {{
    background: {t.bg_primary};
}}

/* 文字类控件不绘制自己的背景。
   上面那条 QWidget 规则会把窗口灰底刷到每个子控件上，卡片（白底）里的
   标签与勾选项于是变成一块块灰斑，卡片失去整体感 —— 这里显式抹掉。 */
QLabel, QCheckBox, QRadioButton {{
    background: transparent;
}}

/* ---------- 工作区卡片（文件队列 / 日志） ---------- */
QFrame#card {{
    background: {t.bg_secondary};
    border: 1px solid {t.border};
    border-radius: {RADIUS_CARD}px;
}}
QLabel#cardTitle {{
    background: transparent;
    color: {t.text_primary};
    font-size: {FONT_MD}pt;
    font-weight: 600;
}}
QPushButton#ghostBtn {{
    background: transparent;
    border: none;
    border-radius: {RADIUS_CTRL}px;
    padding: 4px 10px;
    color: {t.text_secondary};
}}
QPushButton#ghostBtn:hover {{
    background: {t.bg_hover};
    color: {t.text_primary};
}}
QPushButton#ghostBtn:pressed {{
    background: {t.bg_sunk};
}}
QPushButton#ghostBtn:disabled {{
    color: {t.text_tertiary};
    background: transparent;
}}

/* ---------- 分组卡片：标题在卡片内部（Fluent 卡片式） ---------- */
QGroupBox {{
    background: {t.bg_secondary};
    border: 1px solid {t.border};
    border-radius: {RADIUS_CARD}px;
    margin-top: {SPACE_SM}px;
    padding-top: 26px;
    padding-bottom: {SPACE_MD}px;
    padding-left: {SPACE_MD}px;
    padding-right: {SPACE_MD}px;
}}
QGroupBox::title {{
    subcontrol-origin: padding;
    subcontrol-position: top left;
    left: {SPACE_MD + 2}px;
    top: 5px;
    background: transparent;
    color: {t.text_primary};
    font-size: {FONT_MD}pt;
    font-weight: 600;
}}

/* ---------- 规格条（反转色块，视觉锚点） ---------- */
QFrame#specStrip {{
    background: {t.strip};
    border: none;
    border-radius: {RADIUS_CARD}px;
}}
QFrame#specStrip QLabel {{
    background: transparent;
    color: {t.strip_ink};
    font-family: "{mono}";
    font-size: {FONT_XL}pt;
}}
QFrame#specAccent {{
    background: {t.accent_line};
    border: none;
    border-radius: 2px;
}}

/* ---------- 参数栏容器 ---------- */
QScrollArea#sidePanel {{
    background: transparent;
    border: none;
}}
QScrollArea#sidePanel > QWidget > QWidget {{
    background: transparent;
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {t.bg_secondary};
    border: 1px solid {t.border};
    border-bottom: 1px solid {t.border_strong};
    border-radius: {RADIUS_CTRL}px;
    padding: 6px 16px;
    color: {t.text_primary};
}}
QPushButton:hover {{
    background: {t.bg_hover};
}}
QPushButton:pressed {{
    background: {t.bg_sunk};
    color: {t.text_secondary};
}}
QPushButton:focus {{
    border-color: {t.accent};
}}
QPushButton:disabled {{
    background: {t.bg_sunk};
    border-color: {t.border};
    color: {t.text_tertiary};
}}
QPushButton#primary {{
    background: {t.accent};
    border: 1px solid {t.accent};
    color: {t.text_inverse};
    font-weight: 600;
    padding: 8px 24px;
}}
QPushButton#primary:hover {{
    background: {t.accent_hover};
    border-color: {t.accent_hover};
}}
QPushButton#primary:pressed {{
    background: {t.accent_pressed};
    border-color: {t.accent_pressed};
}}
QPushButton#primary:disabled {{
    background: {t.bg_sunk};
    border-color: {t.border};
    color: {t.text_tertiary};
}}
QPushButton#danger {{
    background: {t.danger_wash};
    border-color: {t.danger};
    color: {t.danger};
}}
QPushButton#danger:hover {{
    background: {t.danger};
    border-color: {t.danger};
    color: {t.text_inverse};
}}

/* ---------- 输入控件：底部焦点线是 Fluent 的标志性反馈 ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {t.bg_secondary};
    border: 1px solid {t.border};
    border-bottom: 1px solid {t.border_strong};
    border-radius: {RADIUS_CTRL}px;
    padding: 5px 9px;
    color: {t.text_primary};
    selection-background-color: {t.accent_wash};
    selection-color: {t.text_primary};
    min-height: 20px;
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
    background: {t.bg_tertiary};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {t.accent};
    border-bottom: 2px solid {t.accent};
    padding: 5px 9px 4px 9px;
    background: {t.bg_secondary};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background: {t.bg_sunk};
    border-color: {t.border};
    color: {t.text_tertiary};
}}
QComboBox::drop-down {{
    border: none;
    width: 26px;
}}
/* 自定义了 drop-down 之后 Qt 不再绘制默认箭头，必须显式画一个三角 */
QComboBox::down-arrow {{
    width: 0px;
    height: 0px;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {t.text_secondary};
}}
QComboBox::down-arrow:disabled {{
    border-top-color: {t.text_tertiary};
}}
QComboBox::down-arrow:hover {{
    border-top-color: {t.accent};
}}
QComboBox QAbstractItemView {{
    background: {t.bg_secondary};
    border: 1px solid {t.border};
    border-radius: {RADIUS_CTRL}px;
    selection-background-color: {t.accent_wash};
    selection-color: {t.text_primary};
    outline: none;
    padding: 4px;
}}

/* ---------- 勾选与单选 ---------- */
QCheckBox, QRadioButton {{
    spacing: {SPACE_SM}px;
    color: {t.text_primary};
    padding: 2px 0px;
}}
QCheckBox:disabled, QRadioButton:disabled {{ color: {t.text_tertiary}; }}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 4px;
}}
QCheckBox::indicator:unchecked,
QRadioButton::indicator:unchecked {{
    border: 1px solid {t.border_strong};
    background: {t.bg_secondary};
}}
QCheckBox::indicator:hover,
QRadioButton::indicator:hover {{
    border-color: {t.accent};
}}
QCheckBox::indicator:checked {{
    border: 1px solid {t.accent};
    background: {t.accent};
}}
QRadioButton::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 8px;
}}
QRadioButton::indicator:checked {{
    border: 1px solid {t.accent};
    background: {t.accent};
}}

/* ---------- 表格 ---------- */
QTableWidget {{
    background: {t.bg_secondary};
    alternate-background-color: {t.bg_tertiary};
    border: 1px solid {t.border};
    border-radius: {RADIUS_CARD}px;
    gridline-color: transparent;
    selection-background-color: {t.accent_wash};
    selection-color: {t.text_primary};
    outline: none;
}}
QTableWidget::item {{
    padding: 7px 10px;
    border: none;
}}
QTableWidget::item:selected {{
    background: {t.accent_wash};
    color: {t.text_primary};
}}
QTableWidget::item:hover {{
    background: {t.bg_hover};
}}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {t.bg_tertiary};
    border: none;
    border-bottom: 1px solid {t.border};
    padding: 9px 10px;
    color: {t.text_secondary};
    font-size: {FONT_SM}pt;
    font-weight: 600;
}}
QTableCornerButton::section {{
    background: {t.bg_tertiary};
    border: none;
}}

/* ---------- 日志 ---------- */
QPlainTextEdit {{
    background: {t.bg_secondary};
    border: 1px solid {t.border};
    border-radius: {RADIUS_CARD}px;
    padding: 10px;
    font-family: "{mono}";
    font-size: {FONT_SM}pt;
    color: {t.text_primary};
    selection-background-color: {t.accent_wash};
    selection-color: {t.text_primary};
}}

/* ---------- 进度：带百分比文字 ---------- */
QProgressBar {{
    background: {t.bg_sunk};
    border: none;
    border-radius: 8px;
    height: 16px;
    text-align: center;
    color: {t.text_secondary};
    font-size: {FONT_SM}pt;
}}
QProgressBar::chunk {{
    background: {t.accent};
    border-radius: 8px;
}}

/* ---------- 滚动条 ---------- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: {t.border_strong};
    border-radius: 4px;
    min-height: 36px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{ background: {t.text_tertiary}; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0px;
}}
QScrollBar::handle:horizontal {{
    background: {t.border_strong};
    border-radius: 4px;
    min-width: 36px;
    margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{ background: {t.text_tertiary}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------- 分割条：细而干净 ---------- */
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:vertical {{
    height: 6px;
}}
QSplitter::handle:horizontal {{
    width: 6px;
}}
QSplitter::handle:hover {{
    background: {t.accent_wash};
}}

/* ---------- 空状态 / 拖拽提示 ---------- */
QFrame#dropHint {{
    background: {t.bg_secondary};
    border: 2px dashed {t.border_strong};
    border-radius: {RADIUS_CARD}px;
}}
QFrame#dropHint QLabel {{
    background: transparent;
    color: {t.text_secondary};
    font-size: {FONT_SM}pt;
}}
QFrame#dropHint QLabel#dropHintTitle {{
    color: {t.text_primary};
    font-size: {FONT_LG}pt;
    font-weight: 600;
}}
QFrame#dropHint QLabel#dropHintSub {{
    color: {t.text_tertiary};
    font-size: {FONT_SM}pt;
}}
QFrame#dropHintActive {{
    background: {t.accent_wash};
    border: 2px dashed {t.accent};
    border-radius: {RADIUS_CARD}px;
}}
QFrame#dropHintActive QLabel {{
    background: transparent;
    color: {t.accent};
}}
QFrame#dropHintActive QLabel#dropHintTitle,
QFrame#dropHintActive QLabel#dropHintSub {{
    color: {t.accent};
}}

/* ---------- 状态与计数 ---------- */
QLabel#statusLabel {{
    color: {t.text_secondary};
    font-size: {FONT_SM}pt;
}}
QLabel#countLabel {{
    color: {t.text_tertiary};
    font-size: {FONT_SM}pt;
}}
QLabel#outputPath {{
    font-family: "{mono}";
    font-size: {FONT_SM}pt;
    color: {t.text_primary};
}}
QLabel#hintLabel {{
    color: {t.text_tertiary};
    font-size: {FONT_SM}pt;
    font-style: italic;
}}

/* ---------- Toast 完成通知 ---------- */
QFrame#toast {{
    background: {t.bg_secondary};
    border: 1px solid {t.border};
    border-radius: {RADIUS_CARD}px;
}}
QFrame#toast QLabel {{
    background: transparent;
    color: {t.text_primary};
}}
QFrame#toast QLabel#toastTitle {{
    font-weight: 600;
}}
QFrame#toast QLabel#toastBody {{
    color: {t.text_secondary};
    font-size: {FONT_SM}pt;
}}
QPushButton#toastClose {{
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 2px 6px;
    color: {t.text_tertiary};
    font-weight: 700;
}}
QPushButton#toastClose:hover {{
    background: {t.bg_hover};
    color: {t.text_primary};
}}
QPushButton#toastAction {{
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 4px 8px;
    color: {t.accent};
    font-weight: 600;
}}
QPushButton#toastAction:hover {{
    background: {t.accent_wash};
}}

/* ---------- 提示气泡 ---------- */
QToolTip {{
    background: {t.strip};
    color: {t.strip_ink};
    border: none;
    border-radius: 4px;
    padding: 6px 9px;
    font-size: {FONT_SM}pt;
}}

/* ---------- 右键菜单 ---------- */
QMenu {{
    background: {t.bg_secondary};
    border: 1px solid {t.border};
    border-radius: 6px;
    padding: 4px;
}}
QMenu::item {{
    padding: 7px 24px 7px 12px;
    border-radius: 4px;
    color: {t.text_primary};
}}
QMenu::item:selected {{
    background: {t.accent_wash};
    color: {t.text_primary};
}}
QMenu::item:disabled {{
    color: {t.text_tertiary};
}}
QMenu::separator {{
    height: 1px;
    background: {t.border};
    margin: 4px 10px;
}}

/* ---------- 对话框 ---------- */
QMessageBox {{
    background: {t.bg_primary};
}}
QMessageBox QLabel {{
    color: {t.text_primary};
}}
"""


__all__ = [
    "DARK",
    "LIGHT",
    "FONT_LG",
    "RADIUS_CARD",
    "RADIUS_CTRL",
    "FONT_MD",
    "FONT_SM",
    "FONT_XL",
    "SPACE_2XL",
    "SPACE_LG",
    "SPACE_MD",
    "SPACE_SM",
    "SPACE_XL",
    "SPACE_XS",
    "Theme",
    "app_icon",
    "build_stylesheet",
    "current_theme",
    "icon_pixmap",
    "mono_family",
    "mono_font",
    "set_theme",
    "ui_family",
    "ui_font",
]
