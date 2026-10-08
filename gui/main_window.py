"""主窗口：拖拽队列 + 参数面板 + 实时尺寸预览 + 后台批量转换。"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThread, QUrl
from PySide6.QtGui import (
    QAction,
    QCursor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui import theme
from gui.widgets import ToastNotification
from gui.worker import ConversionWorker
from imgspec import ingest
from imgspec.ingest import SourceInfo
from imgspec.model import (
    ConflictPolicy,
    OutputFormat,
    OutputSpec,
    SizeMode,
    SpecError,
    TiffCompression,
    Unit,
)
from imgspec.pipeline import ConversionReport, TaskResult, default_output_dir

COL_NAME, COL_KIND, COL_SOURCE, COL_TARGET, COL_STATUS = range(5)

DOCUMENT_SUFFIXES = ".pdf .svg .ppt .pptx .pptm .pps .ppsx"
IMAGE_SUFFIXES = (
    ".png .jpg .jpeg .jpe .tif .tiff .bmp .gif .webp .jfif .jp2 .ppm .pgm .tga"
)
SUPPORTED_SUFFIXES = frozenset((DOCUMENT_SUFFIXES + " " + IMAGE_SUFFIXES).split())

# 关窗时仍在跑的转换线程：断开与窗口的父子关系后在这里留个引用，
# 让 Python 继续持有它们直到自己收工，避免被析构导致崩溃。
_orphaned_jobs: list[object] = []


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("图片转换器")
        self.setWindowIcon(theme.app_icon(theme.current_theme().name))
        self.resize(1360, 920)
        self.setMinimumSize(1080, 720)
        self.setAcceptDrops(True)

        self._rows: list[Path] = []
        self._sources: dict[str, SourceInfo] = {}
        self._thread: QThread | None = None
        self._worker: ConversionWorker | None = None
        self._busy: bool = False
        self._last_output_dir: Path | None = None
        self._last_report: ConversionReport | None = None

        self._build_ui()
        self._load_settings()
        self._refresh_preview()

    # ==================================================================
    # 界面搭建
    # ==================================================================
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG)
        root.setSpacing(theme.SPACE_MD)

        # 窗口标题栏已承载应用名，界面内不再重复标题；
        # 主题切换与「添加文件」收进文件卡片顶栏（见 _build_file_table）。

        # 左右布局：左栏是文件队列 + 日志（工作内容），右栏是固定参数面板
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)

        left = QSplitter(Qt.Orientation.Vertical)
        left.setChildrenCollapsible(False)
        left.addWidget(self._build_file_table())
        left.addWidget(self._build_log_panel())
        left.setStretchFactor(0, 3)
        left.setStretchFactor(1, 2)

        body.addWidget(left)
        body.addWidget(self._build_side_panel())
        body.setStretchFactor(0, 1)
        body.setStretchFactor(1, 0)
        root.addWidget(body, 1)

        # 完成通知浮层（非模态，位于窗口右下角）
        self.toast = ToastNotification(self)

    def _build_spec_strip(self) -> QWidget:
        """规格条：等宽读数 + 左端强调线。"""
        strip = QFrame()
        strip.setObjectName("specStrip")
        layout = QHBoxLayout(strip)
        layout.setContentsMargins(theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_LG, theme.SPACE_MD)
        layout.setSpacing(theme.SPACE_MD)

        accent = QFrame()
        accent.setObjectName("specAccent")
        accent.setFixedWidth(3)

        self.preview_label = QLabel()
        self.preview_label.setWordWrap(True)
        self.preview_label.setTextFormat(Qt.TextFormat.RichText)
        self.preview_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        layout.addWidget(accent)
        layout.addWidget(self.preview_label, 1)
        return strip

    def _build_drop_hint(self) -> QWidget:
        """空状态卡片：品牌图标 + 一句话说明 + 选择文件按钮。"""
        frame = QFrame()
        frame.setObjectName("dropHint")
        frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(theme.SPACE_2XL, theme.SPACE_XL, theme.SPACE_2XL, theme.SPACE_XL)
        layout.setSpacing(theme.SPACE_SM)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_label = QLabel()
        mark = theme.icon_pixmap(96 * 2)
        mark.setDevicePixelRatio(2.0)
        icon_label.setPixmap(mark)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.drop_hint_title = QLabel("把文件拖到这里")
        self.drop_hint_title.setObjectName("dropHintTitle")
        self.drop_hint_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.drop_hint_sub = QLabel(
            "科研投稿图片规格化 · 支持 PNG、JPG、TIFF、PDF、PPT 等"
        )
        self.drop_hint_sub.setObjectName("dropHintSub")
        self.drop_hint_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.empty_add_btn = QPushButton("选择文件")
        self.empty_add_btn.setToolTip("选择要转换的图片、PDF 或 PPT")
        self.empty_add_btn.clicked.connect(self._choose_files)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.empty_add_btn)
        button_row.addStretch(1)

        layout.addWidget(icon_label)
        layout.addWidget(self.drop_hint_title)
        layout.addWidget(self.drop_hint_sub)
        layout.addSpacing(theme.SPACE_SM)
        layout.addLayout(button_row)

        self.drop_hint = frame
        return frame

    def _build_file_table(self) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        root = QVBoxLayout(card)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.SPACE_SM)

        # 卡片顶栏：分区标题 + 窗口级动作（主题切换、添加文件）
        header = QHBoxLayout()
        header.setContentsMargins(theme.SPACE_MD, theme.SPACE_SM, theme.SPACE_MD, 0)
        header.setSpacing(theme.SPACE_SM)
        title = QLabel("待处理文件")
        title.setObjectName("cardTitle")
        header.addWidget(title)
        header.addStretch(1)

        self.theme_btn = QPushButton("深色" if theme.current_theme().name == "light" else "浅色")
        self.theme_btn.setObjectName("ghostBtn")
        self.theme_btn.setToolTip("切换浅色/深色主题")
        self.theme_btn.clicked.connect(self._toggle_theme)
        header.addWidget(self.theme_btn)

        self.add_btn = QPushButton("添加文件")
        self.add_btn.setToolTip("选择要转换的图片、PDF 或 PPT")
        self.add_btn.clicked.connect(self._choose_files)
        header.addWidget(self.add_btn)
        root.addLayout(header)

        body = QVBoxLayout()
        body.setContentsMargins(theme.SPACE_MD, 0, theme.SPACE_MD, theme.SPACE_MD)
        body.setSpacing(theme.SPACE_MD)

        # 空状态提示（无文件时显示）
        self.empty_state = self._build_drop_hint()
        body.addWidget(self.empty_state, 1)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["文件名", "类型", "源信息", "输出尺寸", "状态"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.table.setShowGrid(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        table_header = self.table.horizontalHeader()
        table_header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for column in (COL_KIND, COL_STATUS):
            table_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for column in (COL_SOURCE, COL_TARGET):
            table_header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(column, 220)

        body.addWidget(self.table)

        buttons = QHBoxLayout()
        self.remove_btn = QPushButton("从清单移除")
        self.remove_btn.setToolTip("将选中的文件从处理列表移除（不会删除源文件）")
        self.remove_btn.clicked.connect(self.remove_selected)
        self.clear_btn = QPushButton("清空列表")
        self.clear_btn.setToolTip("清空所有待处理文件")
        self.clear_btn.clicked.connect(self.clear_all)
        buttons.addWidget(self.remove_btn)
        buttons.addWidget(self.clear_btn)
        buttons.addStretch(1)
        self.count_label = QLabel("0 个文件")
        self.count_label.setObjectName("countLabel")
        buttons.addWidget(self.count_label)
        body.addLayout(buttons)

        root.addLayout(body)

        shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.table)
        shortcut.activated.connect(self.remove_selected)
        return card

    def _build_log_panel(self) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        root = QVBoxLayout(card)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.SPACE_SM)

        header = QHBoxLayout()
        header.setContentsMargins(theme.SPACE_MD, theme.SPACE_SM, theme.SPACE_MD, 0)
        header.setSpacing(theme.SPACE_SM)
        title = QLabel("转换日志")
        title.setObjectName("cardTitle")
        header.addWidget(title)
        header.addStretch(1)
        root.addLayout(header)

        body = QVBoxLayout()
        body.setContentsMargins(theme.SPACE_MD, 0, theme.SPACE_MD, theme.SPACE_MD)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("转换结果、降质提示与错误都会出现在这里。")
        body.addWidget(self.log_view)
        root.addLayout(body)
        return card

    def _build_side_panel(self) -> QWidget:
        """右侧参数栏：预览条置顶，参数与动作集中在一列，动线不跳跃。

        动作区固定在栏底、不随参数区滚动 —— 主按钮在任何窗口高度下都可见。
        """
        panel = QWidget()
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.SPACE_SM)

        scroll = QScrollArea()
        scroll.setObjectName("sidePanel")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, theme.SPACE_XS, 0)
        layout.setSpacing(theme.SPACE_SM)

        layout.addWidget(self._build_spec_strip())
        layout.addWidget(self._build_format_group())
        layout.addWidget(self._build_size_group())
        layout.addWidget(self._build_encoding_group())
        layout.addWidget(self._build_output_group())
        layout.addStretch(1)

        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        outer.addWidget(self._build_action_bar())

        panel.setMinimumWidth(372)
        panel.setMaximumWidth(430)
        return panel

    def _form(self, box: QWidget) -> QFormLayout:
        """统一表单排布：标签右对齐。卡片内边距由 QSS 提供，这里不再叠加。"""
        form = QFormLayout(box)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(theme.SPACE_MD)
        form.setVerticalSpacing(theme.SPACE_SM)
        form.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        return form

    def _build_format_group(self) -> QWidget:
        box = QGroupBox("输出格式")
        form = self._form(box)
        self.fmt_combo = QComboBox()
        for fmt in OutputFormat:
            self.fmt_combo.addItem(fmt.label, fmt.value)
        self.fmt_combo.setCurrentIndex(0)
        self.fmt_combo.setMaximumWidth(330)
        self.fmt_combo.currentIndexChanged.connect(self._refresh_preview)
        form.addRow("格式", self.fmt_combo)
        return box

    def _build_size_group(self) -> QWidget:
        box = QGroupBox("尺寸与分辨率")
        form = self._form(box)

        mode_row = QHBoxLayout()
        self.mode_phys = QRadioButton("物理尺寸")
        self.mode_px = QRadioButton("像素尺寸")
        self.mode_phys.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.mode_phys)
        group.addButton(self.mode_px)
        mode_row.addWidget(self.mode_phys)
        mode_row.addWidget(self.mode_px)
        mode_row.addStretch(1)
        form.addRow("按", mode_row)

        self.phys_width = self._number_edit("8.5")
        self.phys_height = self._number_edit("", "留空则按源图比例自动计算")
        self.unit_combo = QComboBox()
        for unit in Unit:
            self.unit_combo.addItem(unit.label, unit.value)
        self.unit_combo.setCurrentIndex(1)  # cm
        self.unit_combo.setMaximumWidth(110)
        phys_row = QHBoxLayout()
        phys_row.setSpacing(theme.SPACE_SM)
        phys_row.addWidget(self.phys_width)
        phys_row.addWidget(QLabel("×"))
        phys_row.addWidget(self.phys_height)
        phys_row.addWidget(self.unit_combo)
        phys_row.addStretch(1)
        form.addRow("物理尺寸", phys_row)

        self.px_width = self._number_edit("", "例如 2550")
        self.px_height = self._number_edit("", "留空则按源图比例自动计算")
        px_row = QHBoxLayout()
        px_row.setSpacing(theme.SPACE_SM)
        px_row.addWidget(self.px_width)
        px_row.addWidget(QLabel("×"))
        px_row.addWidget(self.px_height)
        px_row.addWidget(QLabel("px"))
        px_row.addStretch(1)
        form.addRow("像素尺寸", px_row)

        self.dpi_combo = QComboBox()
        self.dpi_combo.setEditable(True)
        for value in ("72", "150", "300", "600", "1200"):
            self.dpi_combo.addItem(value)
        self.dpi_combo.setCurrentText("300")
        self.dpi_combo.setMaximumWidth(150)
        self.dpi_combo.currentTextChanged.connect(self._refresh_preview)
        form.addRow("输出 DPI", self.dpi_combo)

        self.aspect_check = QCheckBox("保持宽高比（不拉伸变形）")
        self.aspect_check.setChecked(True)
        form.addRow("", self.aspect_check)

        self.upscale_check = QCheckBox("不放大：源像素不足时保持原始像素并提示")
        form.addRow("", self.upscale_check)

        for widget in (self.phys_width, self.phys_height, self.px_width, self.px_height):
            widget.textChanged.connect(self._refresh_preview)
        self.unit_combo.currentIndexChanged.connect(self._refresh_preview)
        self.mode_phys.toggled.connect(self._on_mode_changed)
        self.aspect_check.toggled.connect(self._refresh_preview)

        self._enable_size_inputs()
        return box

    def _build_encoding_group(self) -> QWidget:
        box = QGroupBox("编码与体积")
        form = self._form(box)

        self.tiff_combo = QComboBox()
        for compression, label in (
            (TiffCompression.LZW, "LZW（无损，通用）"),
            (TiffCompression.DEFLATE, "Deflate（无损，体积略小）"),
            (TiffCompression.NONE, "不压缩（体积最大）"),
        ):
            self.tiff_combo.addItem(label, compression.value)
        self.tiff_combo.setMaximumWidth(330)
        form.addRow("TIFF 压缩", self.tiff_combo)

        self.jpeg_quality = QComboBox()
        self.jpeg_quality.setEditable(True)
        for value in ("100", "95", "90", "80", "70"):
            self.jpeg_quality.addItem(value)
        self.jpeg_quality.setCurrentText("95")
        self.jpeg_quality.setMaximumWidth(150)
        form.addRow("JPEG 起始质量", self.jpeg_quality)

        self.limit_edit = self._number_edit("", "留空表示不限制")
        limit_row = QHBoxLayout()
        limit_row.setSpacing(theme.SPACE_SM)
        limit_row.addWidget(self.limit_edit)
        limit_row.addWidget(QLabel("MB / 每张"))
        limit_row.addStretch(1)
        form.addRow("体积上限", limit_row)

        self.lossy_check = QCheckBox("无损格式压不进上限时，允许自动改为有损")
        self.lossy_check.setChecked(True)
        form.addRow("", self.lossy_check)
        return box

    def _build_output_group(self) -> QWidget:
        """输出与命名：右栏中的分组，字段纵向排列。"""
        box = QGroupBox("输出与命名")
        form = self._form(box)

        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("留空 = 源文件同级的 converted 文件夹")
        self.out_edit.setToolTip(
            "留空：输出到每个源文件自己所在目录下的 converted 文件夹\n"
            "填路径：所有图都输出到该目录（不存在会自动创建）"
        )
        out_row = QHBoxLayout()
        out_row.setSpacing(theme.SPACE_SM)
        out_row.addWidget(self.out_edit, 1)
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._choose_output_dir)
        out_row.addWidget(browse)
        form.addRow("输出到", out_row)

        self.name_edit = QLineEdit("{stem}")
        self.name_edit.setToolTip(
            "输出文件名（不含扩展名），可用变量：\n"
            "  {stem}   源文件名\n"
            "  {page}   页号，多页文档用\n"
            "  {index}  同 {page}\n"
            "例：{stem}_300dpi"
        )
        form.addRow("命名", self.name_edit)

        self.pages_edit = QLineEdit()
        self.pages_edit.setPlaceholderText("全部")
        self.pages_edit.setMaximumWidth(120)
        self.pages_edit.setToolTip("PDF / PPT 取哪些页，如 1,3-5；留空表示全部")
        form.addRow("页码", self.pages_edit)

        self.conflict_combo = QComboBox()
        for policy, label in (
            (ConflictPolicy.RENAME, "自动改名（推荐）"),
            (ConflictPolicy.OVERWRITE, "覆盖同名文件"),
            (ConflictPolicy.SKIP, "跳过不处理"),
        ):
            self.conflict_combo.addItem(label, policy.value)
        self.conflict_combo.setToolTip(
            "自动改名：若 fig.tif 已存在，就输出 fig_1.tif、fig_2.tif…\n"
            "既不会失败，也不会覆盖你已有的文件"
        )
        form.addRow("重名", self.conflict_combo)

        return box

    def _build_action_bar(self) -> QWidget:
        """动作区：主按钮置顶，进度与状态紧随其后。"""
        bar = QWidget()
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(0, theme.SPACE_XS, theme.SPACE_XS, 0)
        layout.setSpacing(theme.SPACE_SM)

        self.start_btn = QPushButton("全部转换")
        self.start_btn.setObjectName("primary")
        self.start_btn.setMinimumHeight(40)
        self.start_btn.setToolTip("转换列表中的所有文件")
        self.start_btn.clicked.connect(self._start)

        self.start_selected_btn = QPushButton("转换选中")
        self.start_selected_btn.setToolTip("仅转换当前选中的文件")
        self.start_selected_btn.clicked.connect(self._export_selected)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)

        row = QHBoxLayout()
        row.setSpacing(theme.SPACE_SM)
        row.addWidget(self.start_selected_btn, 1)
        row.addWidget(self.cancel_btn, 1)

        self.open_dir_btn = QPushButton("打开输出文件夹")
        self.open_dir_btn.setEnabled(False)
        self.open_dir_btn.clicked.connect(self._open_output_dir)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p%")

        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)

        layout.addWidget(self.start_btn)
        layout.addLayout(row)
        layout.addWidget(self.open_dir_btn)
        layout.addWidget(self.progress)
        layout.addWidget(self.status_label)
        return bar

    @staticmethod
    def _number_edit(placeholder: str = "", tooltip: str = "") -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        if tooltip:
            edit.setToolTip(tooltip)
        edit.setMinimumWidth(64)
        edit.setMaximumWidth(120)
        return edit

    # ==================================================================
    # 主题切换
    # ==================================================================
    def _toggle_theme(self) -> None:
        new_name = "dark" if theme.current_theme().name == "light" else "light"
        theme.set_theme(new_name)

        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.build_stylesheet(new_name))
            app.setWindowIcon(theme.app_icon(new_name))

        self.setWindowIcon(theme.app_icon(new_name))
        self.theme_btn.setText("浅色" if new_name == "dark" else "深色")
        self._refresh_preview()
        self._save_settings()

    # ==================================================================
    # 右键菜单
    # ==================================================================
    def _show_context_menu(self, position) -> None:
        indexes = self.table.selectedIndexes()
        if not indexes:
            return

        menu = QMenu(self)
        export_action = QAction("转换选中", self)
        export_action.triggered.connect(self._export_selected)
        menu.addAction(export_action)

        reveal_action = QAction("在文件夹中查看", self)
        reveal_action.triggered.connect(self._reveal_selected)
        menu.addAction(reveal_action)

        menu.addSeparator()

        remove_action = QAction("从清单移除", self)
        remove_action.triggered.connect(self.remove_selected)
        menu.addAction(remove_action)

        menu.exec(QCursor.pos())

    def _reveal_selected(self) -> None:
        rows = {index.row() for index in self.table.selectedIndexes()}
        for row in rows:
            if 0 <= row < len(self._rows):
                path = self._rows[row]
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
                break

    # ==================================================================
    # 拖拽与文件列表
    # ==================================================================
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - Qt 命名
        if event.mimeData().hasUrls():
            self._set_drop_active(True)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._set_drop_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt 命名
        self._set_drop_active(False)
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.add_paths(paths)
            event.acceptProposedAction()

    def _set_drop_active(self, active: bool) -> None:
        self.drop_hint.setObjectName("dropHintActive" if active else "dropHint")
        self.drop_hint.style().unpolish(self.drop_hint)
        self.drop_hint.style().polish(self.drop_hint)

    def add_paths(self, paths: list[Path]) -> None:
        added = 0
        for path in paths:
            if path.is_dir():
                for child in sorted(path.rglob("*")):
                    if child.is_file() and self._add_file(child):
                        added += 1
            elif self._add_file(path):
                added += 1
        if added:
            self._append_log(f"已加入 {added} 个文件", "info")
        self._refresh_preview()

    def _add_file(self, path: Path) -> bool:
        key = str(path)
        if key in self._sources:
            return False
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            self._append_log(f"跳过不支持的文件：{path.name}", "warn")
            return False

        info = ingest.probe(path)
        self._sources[key] = info
        self._rows.append(path)

        row = self.table.rowCount()
        self.table.insertRow(row)
        self._set_cell(row, COL_NAME, path.name, tooltip=key)
        self._set_cell(row, COL_KIND, info.kind_label)
        self._set_cell(row, COL_SOURCE, info.describe_source())
        self._set_cell(row, COL_TARGET, "—")
        self._set_cell(row, COL_STATUS, "待处理" if not info.error else "无法读取")
        if info.error:
            self._append_log(f"{path.name}：{info.error}", "error")
        for note in info.notes:
            self._append_log(f"{path.name}：{note}", "warn")
        return True

    def remove_selected(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            path = self._rows.pop(row)
            self._sources.pop(str(path), None)
            self.table.removeRow(row)
        self._refresh_preview()

    def clear_all(self) -> None:
        self._rows.clear()
        self._sources.clear()
        self.table.setRowCount(0)
        self.log_view.clear()
        self.progress.setValue(0)
        self.status_label.setText("就绪")
        self.open_dir_btn.setEnabled(False)
        self._refresh_preview()

    def _set_cell(
        self,
        row: int,
        column: int,
        text: str,
        *,
        tooltip: str = "",
        bold: bool = False,
    ) -> None:
        item = QTableWidgetItem(text)
        if tooltip:
            item.setToolTip(tooltip)
        if bold:
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        self.table.setItem(row, column, item)

    def _update_empty_state(self) -> None:
        has_rows = len(self._rows) > 0
        self.empty_state.setVisible(not has_rows)
        self.table.setVisible(has_rows)
        # 空列表时移除/清空是无效按钮，置灰；转换进行中则维持 _set_busy 的禁用态
        if not self._busy:
            self.remove_btn.setEnabled(has_rows)
            self.clear_btn.setEnabled(has_rows)

    # ==================================================================
    # 规格与预览
    # ==================================================================
    def _enable_size_inputs(self) -> None:
        physical = self.mode_phys.isChecked()
        for widget in (self.phys_width, self.phys_height, self.unit_combo):
            widget.setEnabled(physical)
        for widget in (self.px_width, self.px_height):
            widget.setEnabled(not physical)

    def _on_mode_changed(self) -> None:
        self._enable_size_inputs()
        self._refresh_preview()

    @staticmethod
    def _read_number(edit: QLineEdit) -> str:
        return edit.text().strip()

    def _build_spec(self) -> OutputSpec:
        """从界面读出规格。任何不合法的地方都会抛出 SpecError。"""
        def as_float(edit: QLineEdit) -> float | None:
            text = self._read_number(edit)
            if not text:
                return None
            return float(text)

        def as_int(edit: QLineEdit) -> int | None:
            text = self._read_number(edit)
            if not text:
                return None
            return int(float(text))

        pixels = self.mode_px.isChecked()

        try:
            dpi = int(float(self.dpi_combo.currentText().strip() or "300"))
        except ValueError as exc:
            raise SpecError(f"DPI 需要是数字：{self.dpi_combo.currentText()}") from exc

        try:
            quality = int(float(self.jpeg_quality.currentText().strip() or "95"))
        except ValueError as exc:
            raise SpecError(f"JPEG 质量需要是数字：{self.jpeg_quality.currentText()}") from exc

        limit_text = self._read_number(self.limit_edit)
        try:
            max_bytes = int(float(limit_text) * 1024 * 1024) if limit_text else None
        except ValueError as exc:
            raise SpecError(f"体积上限需要是数字（MB）：{limit_text}") from exc

        try:
            phys_width = None if pixels else as_float(self.phys_width)
            phys_height = None if pixels else as_float(self.phys_height)
        except ValueError as exc:
            raise SpecError("物理尺寸需要是数字") from exc

        try:
            px_width = as_int(self.px_width) if pixels else None
            px_height = as_int(self.px_height) if pixels else None
        except ValueError as exc:
            raise SpecError("像素尺寸需要是整数") from exc

        out_text = self.out_edit.text().strip()

        spec = OutputSpec(
            fmt=OutputFormat(self.fmt_combo.currentData()),
            size_mode=SizeMode.PIXELS if pixels else SizeMode.PHYSICAL,
            phys_unit=Unit(self.unit_combo.currentData()),
            phys_width=phys_width,
            phys_height=phys_height,
            px_width=px_width,
            px_height=px_height,
            dpi=dpi,
            keep_aspect=self.aspect_check.isChecked(),
            allow_upscale=not self.upscale_check.isChecked(),
            tiff_compression=TiffCompression(self.tiff_combo.currentData()),
            jpeg_quality=quality,
            max_bytes=max_bytes,
            lossy_fallback=self.lossy_check.isChecked(),
            output_dir=Path(out_text) if out_text else None,
            name_template=self.name_edit.text().strip() or "{stem}",
            page_range=self.pages_edit.text().strip() or None,
            on_conflict=ConflictPolicy(self.conflict_combo.currentData()),
        )
        spec.validate()
        return spec

    def _refresh_preview(self) -> None:
        try:
            spec = self._build_spec()
            error = None
        except (SpecError, ValueError) as exc:
            spec = None
            error = str(exc)

        for row, path in enumerate(self._rows):
            text = "—"
            if spec is not None:
                info = self._sources.get(str(path))
                aspect = info.aspect if info else None
                try:
                    text = spec.resolve(aspect).describe()
                except SpecError:
                    text = "—"
            self._set_cell(row, COL_TARGET, text)

        self.count_label.setText(f"{len(self._rows)} 个文件")
        self._update_empty_state()
        self._render_spec_strip(spec, error)

    def _render_spec_strip(self, spec: OutputSpec | None, error: str | None) -> None:
        t = theme.current_theme()
        if error:
            self.preview_label.setText(
                f'<span style="color:{t.warning}; font-size:{theme.FONT_MD}pt;">'
                f"参数待修正：{html.escape(error)}</span>"
            )
            return

        if spec is None:  # pragma: no cover - error 分支已覆盖
            return

        if not self._rows:
            self.preview_label.setText(
                f'<span style="color:{t.strip_ink_soft};'
                f' font-size:{theme.FONT_SM}pt; font-style:italic;">'
                "添加文件后，此处显示输出规格预览"
                "</span>"
            )
            return

        first = self._sources.get(str(self._rows[0]))
        aspect = first.aspect if first else None
        try:
            geometry = spec.resolve(aspect)
        except SpecError as exc:
            self.preview_label.setText(
                f'<span style="color:{t.warning}; font-size:{theme.FONT_MD}pt;">'
                f"{html.escape(str(exc))}</span>"
            )
            return

        width_px, height_px = geometry.pixel_size
        unit = spec.phys_unit
        phys = (
            f"{geometry.width_in * unit.per_inch:.2f} × "
            f"{geometry.height_in * unit.per_inch:.2f} {unit.value}"
        )
        # 压缩方式在下方「编码与体积」分组里即可见，规格条只保留关键读数，
        # 否则窄栏下三列等宽文字会溢出被裁
        fmt_text = spec.fmt.value.upper()
        size_text = (
            f"≤ {spec.max_bytes / 1024 / 1024:.1f} MB" if spec.max_bytes else ""
        )
        queue_text = f"队列 {len(self._rows)} 个文件" if len(self._rows) > 1 else ""

        def cell(text: str, *, lead: bool = False, soft: bool = False) -> str:
            if soft:
                color, weight = t.strip_ink_soft, "400"
                size, style = theme.FONT_SM, "italic"
            else:
                color = t.strip_ink
                weight = "700" if lead else "400"
                size, style = theme.FONT_XL, "normal"
            return (
                f'<td style="padding-right:16px; color:{color}; font-weight:{weight};'
                f' font-size:{size}pt; font-style:{style};">{text}</td>'
            )

        self.preview_label.setText(
            "<table cellspacing='0' cellpadding='0'>"
            f"<tr>{cell(f'{width_px} × {height_px} px', lead=True)}"
            f"{cell(f'{geometry.dpi} dpi')}{cell(fmt_text)}</tr>"
            f"<tr>{cell(phys, soft=True)}{cell(size_text, soft=True)}"
            f"{cell(queue_text, soft=True)}</tr>"
            "</table>"
        )

    # ==================================================================
    # 转换
    # ==================================================================
    def _start(self) -> None:
        self._run_conversion(list(self._rows))

    def _export_selected(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "未选择文件", "请先选中要转换的文件。")
            return
        files = [self._rows[row] for row in rows if 0 <= row < len(self._rows)]
        self._run_conversion(files)

    def _run_conversion(self, files: list[Path]) -> None:
        if not files:
            QMessageBox.information(self, "没有文件", "请先拖入或添加要转换的文件。")
            return
        try:
            spec = self._build_spec()
        except (SpecError, ValueError) as exc:
            QMessageBox.warning(self, "参数有误", str(exc))
            return

        self._last_output_dir = spec.output_dir or default_output_dir(files[0].parent)
        self.log_view.clear()
        self._set_busy(True)
        self.progress.setRange(0, len(files))
        self.progress.setValue(0)

        self._thread = QThread(self)
        self._worker = ConversionWorker(files, spec)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.status_label.setText("正在取消…")

    def _on_progress(self, done: int, total: int, current: str) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.status_label.setText(f"{done}/{total}  {current}")

    def _on_finished(self, report: ConversionReport) -> None:
        self._last_report = report
        outcomes: dict[str, list[bool]] = {}
        for result in report.results:
            outcomes.setdefault(str(result.source), []).append(result.ok)

        for row, path in enumerate(self._rows):
            flags = outcomes.get(str(path))
            if not flags:
                label, bold = "待处理", False
            elif all(flags):
                label, bold = "完成", True
            elif any(flags):
                label, bold = "部分失败", True
            else:
                label, bold = "失败", True
            self._set_cell(row, COL_STATUS, label, bold=bold)

        warnings = 0
        for result in report.results:
            if result.ok:
                page = f"（第 {result.page} 页）" if result.page and result.page > 1 else ""
                self._append_log(f"完成：{result.source.name}{page} → {result.out_path.name}", "ok")
                self._append_log(f"      {result.summary}", "info")
            else:
                self._append_log(f"失败：{result.source.name} — {result.error}", "error")
            for warning in result.warnings:
                warnings += 1
                self._append_log(f"提示：{warning}", "warn")

        summary = (
            f"成功 {report.ok_count} 项，失败 {report.fail_count} 项，"
            f"提示 {warnings} 条"
        )
        self.status_label.setText(summary)
        self._append_log(summary, "ok" if report.fail_count == 0 else "warn")
        self.open_dir_btn.setEnabled(self._last_output_dir is not None)
        self.progress.setValue(self.progress.maximum())
        self._set_busy(False)
        self._show_completion_toast(report)

    def _on_failed(self, message: str) -> None:
        self._append_log(f"转换中断：{message}", "error")
        self.status_label.setText("转换中断")
        self._set_busy(False)

    def _on_thread_finished(self) -> None:
        if self._thread is not None:
            self._thread.deleteLater()
        self._thread = None
        self._worker = None

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        has_rows = len(self._rows) > 0
        self.start_btn.setEnabled(not busy)
        self.start_selected_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        self.add_btn.setEnabled(not busy)
        self.remove_btn.setEnabled(not busy and has_rows)
        self.clear_btn.setEnabled(not busy and has_rows)
        self.theme_btn.setEnabled(not busy)

    def _open_output_dir(self) -> None:
        if self._last_output_dir is None:
            return
        self._last_output_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_output_dir)))

    # ==================================================================
    # 完成通知（非模态 Toast，不打断继续操作）
    # ==================================================================
    def _show_completion_toast(self, report: ConversionReport) -> None:
        ok_count = report.ok_count
        fail_count = report.fail_count

        # 输出体积分布
        size_buckets: dict[str, int] = {}
        for result in report.results:
            if result.ok and result.out_path.exists():
                try:
                    mb = result.out_path.stat().st_size / (1024 * 1024)
                    if mb < 1:
                        key = "< 1 MB"
                    elif mb < 5:
                        key = "1–5 MB"
                    elif mb < 10:
                        key = "5–10 MB"
                    else:
                        key = "> 10 MB"
                    size_buckets[key] = size_buckets.get(key, 0) + 1
                except OSError:
                    pass

        lines: list[str] = []
        if size_buckets:
            distribution = "，".join(
                f"{key} {size_buckets[key]} 项"
                for key in ("< 1 MB", "1–5 MB", "5–10 MB", "> 10 MB")
                if size_buckets.get(key)
            )
            lines.append(f"体积分布：{distribution}")
        if self._last_output_dir:
            lines.append(f"输出目录：{self._last_output_dir}")

        if fail_count == 0:
            title = f"转换完成，成功 {ok_count} 项"
            level, auto_close = "ok", 6000
        else:
            title = f"完成：成功 {ok_count} 项，失败 {fail_count} 项"
            level, auto_close = "warn", 0  # 有失败时驻留，由用户手动关闭

        self.toast.show_message(
            title,
            "\n".join(lines),
            level=level,
            action_text="打开输出文件夹" if self._last_output_dir else "",
            on_action=self._open_output_dir,
            auto_close_ms=auto_close,
        )

    # ==================================================================
    # 辅助
    # ==================================================================
    def _choose_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择要转换的文件",
            "",
            f"所有支持的文件 (*{DOCUMENT_SUFFIXES.replace(' ', ' *')} *{IMAGE_SUFFIXES.replace(' ', ' *')})"
            ";;文档与矢量图 (*.pdf *.svg *.ppt *.pptx *.pptm *.pps *.ppsx)"
            ";;图片 (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp)"
            ";;所有文件 (*)",
        )
        if files:
            self.add_paths([Path(f) for f in files])

    def _choose_output_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if directory:
            self.out_edit.setText(directory)

    def _append_log(self, message: str, level: str = "info") -> None:
        t = theme.current_theme()
        color = {
            "ok": t.success,
            "info": t.text_primary,
            "warn": t.warning,
            "error": t.danger,
        }.get(level, t.text_primary)
        safe = html.escape(message)
        self.log_view.appendHtml(f'<span style="color:{color};">{safe}</span>')

    # ==================================================================
    # 设置持久化
    # ==================================================================
    def _settings(self) -> QSettings:
        return QSettings("imgspec", "ImageSpecTool")

    def _load_settings(self) -> None:
        s = self._settings()
        geometry = s.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

        # 主题：没有保存过偏好时沿用当前生效的主题（app.py 启动时探测到的系统主题），
        # 不要回退到 "light" —— 否则深色系统的新用户首次启动会被强制切成浅色
        saved_theme = s.value("theme", theme.current_theme().name)
        if saved_theme in ("light", "dark"):
            theme.set_theme(saved_theme)
            self.theme_btn.setText("浅色" if saved_theme == "dark" else "深色")
            app = QApplication.instance()
            if app is not None:
                app.setStyleSheet(theme.build_stylesheet(saved_theme))
                app.setWindowIcon(theme.app_icon(saved_theme))
            self.setWindowIcon(theme.app_icon(saved_theme))

        index = self.fmt_combo.findData(s.value("fmt", OutputFormat.TIFF.value))
        if index >= 0:
            self.fmt_combo.setCurrentIndex(index)

        if s.value("mode", SizeMode.PHYSICAL.value) == SizeMode.PIXELS.value:
            self.mode_px.setChecked(True)
        self.phys_width.setText(s.value("phys_width", "8.5"))
        self.phys_height.setText(s.value("phys_height", ""))
        unit_index = self.unit_combo.findData(s.value("unit", Unit.CM.value))
        if unit_index >= 0:
            self.unit_combo.setCurrentIndex(unit_index)
        self.px_width.setText(s.value("px_width", ""))
        self.px_height.setText(s.value("px_height", ""))
        self.dpi_combo.setCurrentText(s.value("dpi", "300"))
        self.aspect_check.setChecked(s.value("keep_aspect", True, type=bool))
        self.upscale_check.setChecked(s.value("no_upscale", False, type=bool))

        compression_index = self.tiff_combo.findData(
            s.value("tiff_compression", TiffCompression.LZW.value)
        )
        if compression_index >= 0:
            self.tiff_combo.setCurrentIndex(compression_index)
        self.jpeg_quality.setCurrentText(s.value("jpeg_quality", "95"))
        self.limit_edit.setText(s.value("max_mb", ""))
        self.lossy_check.setChecked(s.value("lossy_fallback", True, type=bool))

        self.out_edit.setText(s.value("out_dir", ""))
        self.name_edit.setText(s.value("name_template", "{stem}"))
        self.pages_edit.setText(s.value("pages", ""))
        policy_index = self.conflict_combo.findData(
            s.value("on_conflict", ConflictPolicy.RENAME.value)
        )
        if policy_index >= 0:
            self.conflict_combo.setCurrentIndex(policy_index)

        self._on_mode_changed()

    def _save_settings(self) -> None:
        s = self._settings()
        s.setValue("window/geometry", self.saveGeometry())
        s.setValue("theme", theme.current_theme().name)
        s.setValue("fmt", self.fmt_combo.currentData())
        s.setValue("mode", SizeMode.PIXELS.value if self.mode_px.isChecked() else SizeMode.PHYSICAL.value)
        s.setValue("phys_width", self.phys_width.text())
        s.setValue("phys_height", self.phys_height.text())
        s.setValue("unit", self.unit_combo.currentData())
        s.setValue("px_width", self.px_width.text())
        s.setValue("px_height", self.px_height.text())
        s.setValue("dpi", self.dpi_combo.currentText())
        s.setValue("keep_aspect", self.aspect_check.isChecked())
        s.setValue("no_upscale", self.upscale_check.isChecked())
        s.setValue("tiff_compression", self.tiff_combo.currentData())
        s.setValue("jpeg_quality", self.jpeg_quality.currentText())
        s.setValue("max_mb", self.limit_edit.text())
        s.setValue("lossy_fallback", self.lossy_check.isChecked())
        s.setValue("out_dir", self.out_edit.text())
        s.setValue("name_template", self.name_edit.text())
        s.setValue("pages", self.pages_edit.text())
        s.setValue("on_conflict", self.conflict_combo.currentData())

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._worker is not None:
            self._worker.cancel()
        self._save_settings()

        thread, worker = self._thread, self._worker
        if thread is not None and thread.isRunning():
            if not thread.wait(15_000):
                _orphaned_jobs.extend([thread, worker])
                thread.setParent(None)

        super().closeEvent(event)
