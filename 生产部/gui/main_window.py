"""主窗口：文件队列（勾选 + 页码）+ 参数面板 + 实时尺寸预览 + 后台批量转换。

交互结构
--------
顶部：品牌图标 + 应用名与一句话定位 + 语言/主题切换框（靠右上角，同宽等高）。

左侧（工作内容）：
- 待处理文件卡片：勾选列（表头全选）、序号、文件名、页码（仅 PDF/PPT 可编辑，
  可编辑单元格画成 accent 描边 chip + 铅笔符作为引导）、源信息、输出尺寸、状态；
  勾选行会自动成为选中行（编辑/移除的操作目标），「添加文件」在卡片底部按钮行；
- 转换日志卡片：进度条与状态文字内嵌在卡片顶栏，日志字号加大并带图标前缀。

右侧（参数，按归属分组）：
- 输出格式（TIFF/JPEG 的编码选项跟随所选格式动态出现；体积上限与有损降级
  也归入本组）；
- 尺寸与分辨率（勾选「保持宽高比」后宽高双向联动，两框始终都有值）；
- 输出与命名（输出目录、命名模板、重名处理）；
- 动作区只留「开始转换（已勾选数）」「取消」「打开输出文件夹」。

语言切换走 retranslate_ui() 就地刷新全部文案 —— 不重建窗口，因此没有闪烁。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (
    QDateTime,
    QEvent,
    QItemSelectionModel,
    QObject,
    QSettings,
    QStandardPaths,
    Qt,
    QThread,
    QTime,
    QUrl,
)
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
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui import i18n, theme
from gui.maintenance import (
    ISSUES_PAGE,
    UpdateChecker,
    app_version,
    build_log_text,
    environment_lines,
    is_newer,
)
from gui.session_log import SessionLog
from gui.widgets import (
    CheckBoxHeader,
    EdgeFade,
    EditableChipDelegate,
    PillToggle,
    ToastNotification,
)
from gui.worker import ConversionWorker
from imgspec import ingest
from imgspec.ingest import SourceInfo, SourceKind
from imgspec.model import (
    ConflictPolicy,
    OutputFormat,
    OutputSpec,
    SizeMode,
    SpecError,
    TiffCompression,
    Unit,
)
from imgspec.pipeline import ConversionReport, default_output_dir

# 列布局：勾选 | 序号 | 文件名 | 页码 | 源信息 | 输出尺寸 | 状态
COL_CHECK, COL_INDEX, COL_NAME, COL_PAGES, COL_SOURCE, COL_TARGET, COL_STATUS = range(7)

DOCUMENT_SUFFIXES = ".pdf .svg .ppt .pptx .pptm .pps .ppsx"
IMAGE_SUFFIXES = (
    ".png .jpg .jpeg .jpe .tif .tiff .bmp .gif .webp .jfif .jp2 .ppm .pgm .tga"
)
SUPPORTED_SUFFIXES = frozenset((DOCUMENT_SUFFIXES + " " + IMAGE_SUFFIXES).split())

# 页码列只对这些类别开放编辑
PAGED_KINDS = (SourceKind.PDF, SourceKind.SLIDES)

# 页码表达式：数字、逗号（中英文皆可）、连字符与空白；至少要出现一个数字
_PAGES_PATTERN = re.compile(r"^[\d\s,，\-]+$")

KIND_LABEL_KEYS = {
    SourceKind.RASTER: "kind_raster",
    SourceKind.PDF: "kind_pdf",
    SourceKind.SVG: "kind_svg",
    SourceKind.SLIDES: "kind_slides",
    SourceKind.UNSUPPORTED: "kind_unsupported",
}

# 关窗时仍在跑的转换线程：断开与窗口的父子关系后在这里留个引用，
# 让 Python 继续持有它们直到自己收工，避免被析构导致崩溃。
_orphaned_jobs: list[object] = []


@dataclass
class SizeParams:
    """一个文件的尺寸参数快照（行级），同时也是右栏尺寸面板的编辑状态。

    每行文件保存自己的尺寸参数：右栏编辑的是「当前选中行」，
    未选中的文件不受影响 —— 同批文件可以各自设置不同尺寸。
    last_side 记录用户最后编辑的是宽还是高：勾选「保持宽高比」时它
    决定哪条边是硬约束（另一边由各源文件自己的比例决定），
    避免「等比内接」语义下不同比例的源（如竖版 PDF）看起来不跟随参数。
    """

    pixels: bool = False
    phys_width: str = "8.5"
    phys_height: str = ""
    unit: str = Unit.CM.value
    px_width: str = ""
    px_height: str = ""
    dpi: str = "300"
    keep_aspect: bool = True
    no_upscale: bool = False
    last_side: str = "width"  # "width" | "height"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(i18n.t("app_title"))
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
        # 批量填表时挂起 itemChanged，避免勾选/校验逻辑被程序写入触发
        self._loading_rows: bool = False
        # 每行文件的尺寸参数（键 str(path)）；无选中行时编辑的是默认模板
        self._size_params: dict[str, SizeParams] = {}
        self._default_params = SizeParams()
        # 载入行参数到面板时挂起「面板→行参数」回写
        self._loading_panel: bool = False
        # 用户最后编辑的边（"width"/"height"），决定 keep_aspect 时的硬约束边
        self._pending_side: str = "width"
        # 转换结束的摘要（优先于「就绪」状态显示）
        self._status_summary: str | None = None
        # 由 app.py 注入：切换语言后的刷新回调（默认为 None，测试独立运行时
        # 走 retranslate_ui 兜底 —— 见 _on_lang_combo_changed）
        self.on_language_change = None
        # 防止 _fill_missing_side 的程序写入重入 _refresh_preview
        self._filling_side: bool = False
        # 勾选 → 选中 单向联动的重入保护
        self._syncing_check_select: bool = False
        # 日志记录（时间戳, 级别, 消息）：主题切换后按新主题重染，
        # 避免深色下写入的浅色文字残留在浅色界面上（或反之）
        self._log_records: list[tuple[str, str, str]] = []
        # 表单字段标签（form, 标签控件, i18n键）：语言切换统一刷新
        self._form_rows: list[tuple[QFormLayout, QLabel, str]] = []
        # 更新检查线程（一次一个；启动时自动检查可在顶栏菜单里关掉）
        self._update_thread: UpdateChecker | None = None
        self._auto_check: bool = True
        # 会话日志落盘：退出即丢的界面日志在这里留一份本地副本，
        # 上次没正常退出的话，下次启动会归档成 crash-*.log 并提示导出
        self._session_log = SessionLog(self._session_log_dir())
        self._session_log.start(self._session_header())

        self._load_language()
        self._build_ui()
        self._load_settings()
        self._refresh_preview()
        self._notify_previous_crash()

    # ==================================================================
    # 界面搭建
    # ==================================================================
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG)
        root.setSpacing(theme.SPACE_MD)

        # 左右布局：左栏是文件队列 + 日志（工作内容），右栏是高频参数面板。
        # 所有分割条把手统一 12px —— 与卡片间距同一节奏，缝隙不再宽窄不一
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        body.setHandleWidth(theme.SPACE_MD)
        self._body_splitter = body

        left = QSplitter(Qt.Orientation.Vertical)
        left.setChildrenCollapsible(False)
        left.setHandleWidth(theme.SPACE_MD)
        left.addWidget(self._build_file_table())
        left.addWidget(self._build_log_panel())
        left.setStretchFactor(0, 3)
        left.setStretchFactor(1, 2)

        body.addWidget(left)
        body.addWidget(self._build_side_panel())
        body.setStretchFactor(0, 1)
        body.setStretchFactor(1, 0)

        root.addWidget(self._build_top_bar())
        root.addWidget(body, 1)

        # 完成通知浮层（非模态，位于窗口右下角）
        self.toast = ToastNotification(self)

    def _build_top_bar(self) -> QWidget:
        """窗口顶栏：品牌图标 + 应用名/定位，语言/主题切换框靠右（同宽等高）。"""
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(theme.SPACE_XS, 0, theme.SPACE_XS, 0)
        layout.setSpacing(theme.SPACE_MD)

        logo = QLabel()
        mark = theme.icon_pixmap(64)  # 32 逻辑像素 @2x，高分屏下依旧锐利
        mark.setDevicePixelRatio(2.0)
        logo.setPixmap(mark)
        logo.setFixedSize(32, 32)
        layout.addWidget(logo, 0, Qt.AlignmentFlag.AlignVCenter)

        self.title_label = QLabel(i18n.t("app_title"))
        self.title_label.setObjectName("appTitle")
        self.subtitle_label = QLabel(i18n.t("app_subtitle"))
        self.subtitle_label.setObjectName("appSubtitle")
        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(0)
        title_col.addWidget(self.title_label)
        title_col.addWidget(self.subtitle_label)
        layout.addLayout(title_col)
        layout.addStretch(1)

        # 维护功能：日志导出 / 问题反馈 / 检查更新（自动检查开关在该按钮菜单里）
        self.export_log_btn = QPushButton(i18n.t("btn_export_log"))
        self.export_log_btn.setObjectName("ghostBtn")
        self.export_log_btn.setToolTip(i18n.t("export_log_tooltip"))
        self.export_log_btn.clicked.connect(self.export_log)

        self.feedback_btn = QPushButton(i18n.t("btn_feedback"))
        self.feedback_btn.setObjectName("ghostBtn")
        self.feedback_btn.setToolTip(i18n.t("feedback_tooltip"))
        self.feedback_btn.clicked.connect(self.open_feedback)

        # 主按钮 = 立即检查；右侧箭头弹出菜单（含「启动时自动检查更新」开关）
        self.update_btn = QToolButton()
        self.update_btn.setObjectName("ghostBtn")
        self.update_btn.setText(i18n.t("btn_check_update"))
        self.update_btn.setToolTip(i18n.t("update_btn_tooltip"))
        self.update_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.update_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.update_btn.clicked.connect(lambda: self.check_updates(manual=True))

        self.update_menu = QMenu(self.update_btn)
        self.update_now_action = self.update_menu.addAction(i18n.t("btn_check_update"))
        self.update_now_action.triggered.connect(lambda: self.check_updates(manual=True))
        self.auto_check_action = self.update_menu.addAction(i18n.t("update_auto_check"))
        self.auto_check_action.setCheckable(True)
        self.auto_check_action.toggled.connect(self._on_auto_check_toggled)
        self.update_btn.setMenu(self.update_menu)

        layout.addWidget(self.export_log_btn)
        layout.addWidget(self.feedback_btn)
        layout.addWidget(self.update_btn)

        # 语言 / 主题：胶囊开关，点击即切换（高亮块滑动过去，确认感明确）
        self.lang_combo = PillToggle()
        self.lang_combo.addItem(i18n.t("lang_name_zh"), "zh")
        self.lang_combo.addItem(i18n.t("lang_short_en"), "en")
        self.lang_combo.setCurrentIndex(0 if i18n.current_lang() == "zh" else 1)
        self.lang_combo.setToolTip(i18n.t("lang_combo_tooltip"))
        self.lang_combo.currentIndexChanged.connect(self._on_lang_combo_changed)

        self.theme_combo = PillToggle()
        self.theme_combo.addItem(i18n.t("theme_light"), "light")
        self.theme_combo.addItem(i18n.t("theme_dark"), "dark")
        self.theme_combo.setCurrentIndex(
            0 if theme.current_theme().name == "light" else 1
        )
        self.theme_combo.setToolTip(i18n.t("theme_combo_tooltip"))
        self.theme_combo.currentIndexChanged.connect(self._on_theme_combo_changed)

        layout.addWidget(self.lang_combo)
        layout.addWidget(self.theme_combo)
        return bar

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
        mark = theme.icon_pixmap(112 * 2)
        mark.setDevicePixelRatio(2.0)
        icon_label.setPixmap(mark)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.drop_hint_title = QLabel(i18n.t("drop_title"))
        self.drop_hint_title.setObjectName("dropHintTitle")
        self.drop_hint_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.drop_hint_sub = QLabel(i18n.t("drop_sub"))
        self.drop_hint_sub.setObjectName("dropHintSub")
        self.drop_hint_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.empty_add_btn = QPushButton(i18n.t("choose_files"))
        self.empty_add_btn.setObjectName("accentOutline")
        self.empty_add_btn.setToolTip(i18n.t("drop_sub"))
        self.empty_add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
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

        # 卡片顶栏：分区标题（语言/主题切换框已移至窗口顶栏）
        header = QHBoxLayout()
        header.setContentsMargins(theme.SPACE_MD, theme.SPACE_SM, theme.SPACE_MD, 0)
        header.setSpacing(theme.SPACE_SM)
        self.files_card_title = QLabel(i18n.t("card_files"))
        self.files_card_title.setObjectName("cardTitle")
        header.addWidget(self.files_card_title)
        header.addStretch(1)
        root.addLayout(header)

        body = QVBoxLayout()
        body.setContentsMargins(theme.SPACE_MD, 0, theme.SPACE_MD, theme.SPACE_MD)
        body.setSpacing(theme.SPACE_MD)

        # 空状态提示（无文件时显示）
        self.empty_state = self._build_drop_hint()
        body.addWidget(self.empty_state, 1)

        self.table = QTableWidget(0, 7)
        self._install_table_header()
        self.table.verticalHeader().setVisible(False)
        # 行高放宽一档：可编辑 chip 与勾选框都有从容的落位空间
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # 页码列依赖 item 的 ItemIsEditable 标记开放编辑；其余列不可编辑。
        # 双击可编；选中行后再单击 chip 也可直接进编辑（SelectedClicked），
        # 可编辑单元格由 EditableChipDelegate 画出「可输入」的引导外观
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.table.setItemDelegateForColumn(COL_PAGES, EditableChipDelegate(self.table))
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.table.setShowGrid(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.itemChanged.connect(self._on_item_changed)
        # 页码 chip 单击直接进入编辑：点哪个改哪个，不需要双击、没有弹层
        self.table.cellClicked.connect(self._on_table_clicked)
        # 选中行 = 尺寸参数的编辑目标：点选某行后，右栏显示/编辑的就是该行的参数
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        table_header = self.table.horizontalHeader()
        table_header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for column in (COL_CHECK, COL_INDEX, COL_STATUS):
            table_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for column in (COL_PAGES, COL_SOURCE, COL_TARGET):
            table_header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(COL_PAGES, 92)
        self.table.setColumnWidth(COL_SOURCE, 168)
        self.table.setColumnWidth(COL_TARGET, 168)

        body.addWidget(self.table)

        buttons = QHBoxLayout()
        self.add_btn = QPushButton(i18n.t("add_files"))
        self.add_btn.setToolTip(i18n.t("drop_sub"))
        self.add_btn.clicked.connect(self._choose_files)
        buttons.addWidget(self.add_btn)
        self.remove_btn = QPushButton(i18n.t("remove_selected"))
        self.remove_btn.setToolTip(i18n.t("remove_selected_tooltip"))
        self.remove_btn.clicked.connect(self.remove_selected)
        self.clear_btn = QPushButton(i18n.t("clear_all"))
        self.clear_btn.setToolTip(i18n.t("clear_all_tooltip"))
        self.clear_btn.clicked.connect(self.clear_all)
        buttons.addWidget(self.remove_btn)
        buttons.addWidget(self.clear_btn)
        buttons.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("countLabel")
        buttons.addWidget(self.count_label)
        body.addLayout(buttons)

        root.addLayout(body)

        shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.table)
        shortcut.activated.connect(self.remove_selected)
        return card

    def _install_table_header(self) -> None:
        """创建带全选框的表头（仅一次），随后写入首版列名。"""
        header = CheckBoxHeader(self.table)
        header.toggle_requested.connect(self._toggle_select_all)
        self.table.setHorizontalHeader(header)
        self.table.horizontalHeader().setSectionResizeMode(
            COL_CHECK, QHeaderView.ResizeMode.Fixed
        )
        self.table.setColumnWidth(COL_CHECK, 40)
        self.table.horizontalHeader().setFixedHeight(42)
        self._update_table_headers()

    def _update_table_headers(self) -> None:
        """只刷新列名文字（表头实例不变，全选三态得以保留）。"""
        labels = {
            COL_CHECK: "",
            COL_INDEX: "#",
            COL_NAME: i18n.t("col_name"),
            COL_PAGES: i18n.t("col_pages"),
            COL_SOURCE: i18n.t("col_source"),
            COL_TARGET: i18n.t("col_target"),
            COL_STATUS: i18n.t("col_status"),
        }
        for column, text in labels.items():
            self.table.setHorizontalHeaderItem(column, QTableWidgetItem(text))

    def _build_log_panel(self) -> QWidget:
        """日志卡片：进度与状态内嵌顶栏，转换进展始终可见。"""
        card = QFrame()
        card.setObjectName("card")
        root = QVBoxLayout(card)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.SPACE_SM)

        header = QHBoxLayout()
        header.setContentsMargins(theme.SPACE_MD, theme.SPACE_SM, theme.SPACE_MD, 0)
        header.setSpacing(theme.SPACE_MD)
        self.log_card_title = QLabel(i18n.t("card_log"))
        self.log_card_title.setObjectName("cardTitle")
        header.addWidget(self.log_card_title)
        header.addStretch(1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setMaximumWidth(320)
        header.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p%")
        self.progress.setFixedWidth(180)
        self.progress.setVisible(False)  # 空闲时隐藏，转换时出现
        header.addWidget(self.progress)
        root.addLayout(header)

        body = QVBoxLayout()
        body.setContentsMargins(theme.SPACE_MD, 0, theme.SPACE_MD, theme.SPACE_MD)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText(i18n.t("log_placeholder"))
        body.addWidget(self.log_view)
        root.addLayout(body)
        return card

    def _build_side_panel(self) -> QWidget:
        """右侧参数栏：规格条常驻顶部（不随滚动划走），参数区滚动，
        动作区固定在栏底 —— 主按钮在任何窗口高度下都可见。

        滚动区上下边缘叠 20px 背景渐隐遮罩：内容滑出视口时先淡出，
        不再被一条直线拦腰切开。
        """
        panel = QWidget()
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.SPACE_MD)

        # 规格条常驻：放在滚动区外，滚动参数时保持可见
        outer.addWidget(self._build_spec_strip())

        scroll = QScrollArea()
        scroll.setObjectName("sidePanel")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # 横向留 AsNeeded：英文等长文案超出面板宽度时允许滚动查看，不硬裁
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, theme.SPACE_XS, theme.SPACE_XS, theme.SPACE_SM)
        layout.setSpacing(theme.SPACE_MD)

        layout.addWidget(self._build_format_group())
        self.size_group = self._build_size_group()
        layout.addWidget(self.size_group)
        layout.addWidget(self._build_output_naming_group())
        layout.addStretch(1)

        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        outer.addWidget(self._build_action_bar())

        # 边缘渐隐遮罩：挂载在滚动区上（视口外），跟随滚动区尺寸。
        # 颜色由遮罩绘制时实时取当前主题，无需在这里传入或刷新。
        self._side_scroll = scroll
        self._fade_top = EdgeFade(scroll, top=True)
        self._fade_bottom = EdgeFade(scroll, top=False)
        scroll.installEventFilter(self)
        self._reposition_edge_fades()

        panel.setMinimumWidth(372)
        panel.setMaximumWidth(470)
        return panel

    def _reposition_edge_fades(self) -> None:
        """渐隐遮罩贴住滚动区上下边（滚动条出现/消失时随尺寸重排）。"""
        scroll = getattr(self, "_side_scroll", None)
        if scroll is None:
            return
        height = self._fade_top.height()
        self._fade_top.setGeometry(0, 0, scroll.width(), height)
        self._fade_bottom.setGeometry(
            0, scroll.height() - height, scroll.width(), height
        )
        self._fade_top.raise_()
        self._fade_bottom.raise_()

    def eventFilter(self, watched: QObject, event) -> bool:  # noqa: N802 - Qt 命名
        if watched is self._side_scroll and event.type() == QEvent.Type.Resize:
            self._reposition_edge_fades()
        return super().eventFilter(watched, event)

    def _form(self, box: QWidget) -> QFormLayout:
        """统一表单排布：标签右对齐，字段一律拉满整列 —— 不再出现长短不一的框。"""
        form = QFormLayout(box)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(theme.SPACE_MD)
        form.setVerticalSpacing(theme.SPACE_SM)
        form.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        return form

    def _add_row(self, form: QFormLayout, key: str, field) -> None:
        """带本地化字段标签的 addRow。

        标签统一 fieldLabel 样式（二级色，与分组标题、输入内容拉开层级），
        并登记进 _form_rows，语言切换时按 key 就地刷新。
        """
        label = QLabel(i18n.t(key))
        label.setObjectName("fieldLabel")
        form.addRow(label, field)
        self._form_rows.append((form, label, key))

    def _build_format_group(self) -> QWidget:
        """输出格式：编码选项跟随格式出现 —— TIFF 压缩只在 TIFF，JPEG 质量只在 JPEG。"""
        box = QGroupBox(i18n.t("group_format"))
        self.format_group = box
        form = self._format_form = self._form(box)

        self.fmt_combo = QComboBox()
        for fmt in OutputFormat:
            self.fmt_combo.addItem(i18n.t(f"fmt_{fmt.value}"), fmt.value)
        self.fmt_combo.setCurrentIndex(0)
        self.fmt_combo.currentIndexChanged.connect(self._on_format_changed)
        self._add_row(form, "label_format", self.fmt_combo)   # row 0

        self.tiff_combo = QComboBox()
        for compression, key in (
            (TiffCompression.LZW, "tiff_lzw"),
            (TiffCompression.DEFLATE, "tiff_deflate"),
            (TiffCompression.NONE, "tiff_none"),
        ):
            self.tiff_combo.addItem(i18n.t(key), compression.value)
        self.tiff_combo.currentIndexChanged.connect(self._refresh_preview)
        self._add_row(form, "tiff_compression", self.tiff_combo)
        self.row_tiff = 1

        self.jpeg_quality = QComboBox()
        self.jpeg_quality.setEditable(True)
        for value in ("100", "95", "90", "80", "70"):
            self.jpeg_quality.addItem(value)
        self.jpeg_quality.setCurrentText("95")
        self.jpeg_quality.currentTextChanged.connect(self._refresh_preview)
        self._add_row(form, "jpeg_quality", self.jpeg_quality)
        self.row_jpeg = 2

        # 体积上限：与压缩/质量同属「编码与体积」，随格式组常驻
        self.limit_edit = QLineEdit()
        self.limit_edit.setPlaceholderText(i18n.t("limit_placeholder"))
        self.limit_edit.setToolTip(i18n.t("limit_placeholder"))
        self.limit_edit.textChanged.connect(self._refresh_preview)
        limit_row = QHBoxLayout()
        limit_row.setSpacing(theme.SPACE_SM)
        limit_row.addWidget(self.limit_edit, 1)
        limit_row.addWidget(QLabel(i18n.t("limit_unit")))
        self._add_row(form, "label_max_mb", limit_row)   # row 3

        self.lossy_check = QCheckBox(i18n.t("lossy_fallback"))
        self.lossy_check.toggled.connect(self._refresh_preview)
        form.addRow("", self.lossy_check)                # row 4

        self._on_format_changed()
        return box

    def _build_size_group(self) -> QWidget:
        box = QGroupBox(i18n.t("group_size"))
        form = self._size_form = self._form(box)

        mode_row = QHBoxLayout()
        self.mode_phys = QRadioButton(i18n.t("mode_physical"))
        self.mode_px = QRadioButton(i18n.t("mode_pixels"))
        self.mode_phys.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.mode_phys)
        group.addButton(self.mode_px)
        mode_row.addWidget(self.mode_phys)
        mode_row.addWidget(self.mode_px)
        mode_row.addStretch(1)
        self._add_row(form, "label_mode", mode_row)

        self.phys_width = self._number_edit("8.5")
        self.phys_height = self._number_edit("", i18n.t("phys_placeholder"))
        self.unit_combo = QComboBox()
        for unit in Unit:
            self.unit_combo.addItem(i18n.t(f"unit_{unit.value}"), unit.value)
        self.unit_combo.setCurrentIndex(1)  # cm
        self.unit_combo.setMinimumWidth(96)
        phys_row = QHBoxLayout()
        phys_row.setSpacing(theme.SPACE_SM)
        phys_row.addWidget(self.phys_width, 1)
        phys_row.addWidget(QLabel("×"))
        phys_row.addWidget(self.phys_height, 1)
        phys_row.addWidget(self.unit_combo)
        self._add_row(form, "label_physical", phys_row)

        self.px_width = self._number_edit("", i18n.t("px_placeholder"))
        self.px_height = self._number_edit("", i18n.t("phys_placeholder"))
        px_row = QHBoxLayout()
        px_row.setSpacing(theme.SPACE_SM)
        px_row.addWidget(self.px_width, 1)
        px_row.addWidget(QLabel("×"))
        px_row.addWidget(self.px_height, 1)
        px_row.addWidget(QLabel("px"))
        self._add_row(form, "label_pixels", px_row)

        self.dpi_combo = QComboBox()
        self.dpi_combo.setEditable(True)
        for value in ("72", "150", "300", "600", "1200"):
            self.dpi_combo.addItem(value)
        self.dpi_combo.setCurrentText("300")
        self.dpi_combo.currentTextChanged.connect(self._refresh_preview)
        self._add_row(form, "label_dpi", self.dpi_combo)

        self.aspect_check = QCheckBox(i18n.t("aspect_check"))
        self.aspect_check.setChecked(True)
        self.aspect_check.setToolTip(i18n.t("aspect_link_hint"))
        form.addRow("", self.aspect_check)

        self.upscale_check = QCheckBox(i18n.t("upscale_check"))
        form.addRow("", self.upscale_check)

        for widget in (self.phys_width, self.phys_height, self.px_width, self.px_height):
            widget.textChanged.connect(self._refresh_preview)
        self.unit_combo.currentIndexChanged.connect(self._refresh_preview)
        self.mode_phys.toggled.connect(self._on_mode_changed)
        self.aspect_check.toggled.connect(self._refresh_preview)
        self.aspect_check.toggled.connect(self._fill_missing_side)

        # 宽高比联动：只在用户手动输入时触发（textEdited），程序写入不会回环
        self.phys_width.textEdited.connect(self._on_phys_width_edited)
        self.phys_height.textEdited.connect(self._on_phys_height_edited)
        self.px_width.textEdited.connect(self._on_px_width_edited)
        self.px_height.textEdited.connect(self._on_px_height_edited)

        self._enable_size_inputs()
        return box

    def _build_output_naming_group(self) -> QWidget:
        """输出与命名：输出目录、命名模板、重名处理（按归属回归右栏）。"""
        box = QGroupBox(i18n.t("group_output_naming"))
        self.output_group = box
        form = self._output_form = self._form(box)

        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText(i18n.t("out_placeholder"))
        self.out_edit.setToolTip(i18n.t("out_dir_tooltip"))
        # 描边按钮而非幽灵文字：「可以点击」必须一眼可见
        self.browse_btn = QPushButton(i18n.t("browse"))
        self.browse_btn.setMinimumHeight(32)
        self.browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.browse_btn.clicked.connect(self._choose_out_dir)
        out_row = QHBoxLayout()
        out_row.setSpacing(theme.SPACE_SM)
        out_row.addWidget(self.out_edit, 1)
        out_row.addWidget(self.browse_btn)
        self._add_row(form, "label_output_to", out_row)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("{stem}")
        self.name_edit.setToolTip(i18n.t("name_tooltip"))
        self._add_row(form, "label_name_template", self.name_edit)

        self.conflict_combo = QComboBox()
        for policy, key in (
            (ConflictPolicy.RENAME, "conflict_rename"),
            (ConflictPolicy.OVERWRITE, "conflict_overwrite"),
            (ConflictPolicy.SKIP, "conflict_skip"),
        ):
            self.conflict_combo.addItem(i18n.t(key), policy.value)
        self.conflict_combo.setToolTip(i18n.t("conflict_tooltip"))
        self._add_row(form, "label_conflict", self.conflict_combo)

        self.out_edit.textChanged.connect(self._refresh_preview)
        self.name_edit.textChanged.connect(self._refresh_preview)
        self.conflict_combo.currentIndexChanged.connect(self._refresh_preview)
        return box

    def _choose_out_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, i18n.t("fd_pick_dir"))
        if directory:
            self.out_edit.setText(directory)

    def _build_action_bar(self) -> QWidget:
        """动作区：只留转换、取消与打开输出文件夹。"""
        bar = QWidget()
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(0, theme.SPACE_XS, theme.SPACE_XS, 0)
        layout.setSpacing(theme.SPACE_SM)

        self.start_btn = QPushButton(i18n.t("start_convert"))
        self.start_btn.setObjectName("primary")
        self.start_btn.setMinimumHeight(46)
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.setToolTip(i18n.t("start_convert_tooltip"))
        self.start_btn.clicked.connect(self._start)
        self.start_btn.setEnabled(False)

        self.cancel_btn = QPushButton(i18n.t("cancel"))
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)

        self.open_dir_btn = QPushButton(i18n.t("open_output_folder"))
        self.open_dir_btn.setEnabled(False)
        self.open_dir_btn.clicked.connect(self._open_output_dir)

        row = QHBoxLayout()
        row.setSpacing(theme.SPACE_SM)
        row.addWidget(self.cancel_btn, 1)
        row.addWidget(self.open_dir_btn, 1)

        layout.addWidget(self.start_btn)
        layout.addLayout(row)
        return bar

    @staticmethod
    def _number_edit(placeholder: str = "", tooltip: str = "") -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        if tooltip:
            edit.setToolTip(tooltip)
        edit.setMinimumWidth(72)
        edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        return edit

    # ==================================================================
    # 主题与语言（顶栏切换框）
    # ==================================================================
    def _on_theme_combo_changed(self) -> None:
        new_name = str(self.theme_combo.currentData())
        if new_name == theme.current_theme().name:
            return
        self._apply_theme(new_name)
        self._save_settings()

    def _apply_theme(self, name: str) -> None:
        """应用主题的全部副作用：QSS、图标、日志重染、预览刷新。

        用户手动切换与启动时恢复保存主题必须走同一入口 —— 曾经只在
        手动切换里做后续刷新，而启动恢复为了不打扰开关屏蔽了信号，
        主题相关的刷新全部被跳过（渐隐遮罩滞留旧主题色、深色界面
        上下出现白带）。统一入口后两条路径行为一致。
        """
        theme.set_theme(name)

        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.build_stylesheet(name))
            app.setWindowIcon(theme.app_icon(name))

        self.setWindowIcon(theme.app_icon(name))
        self._rerender_log()  # 旧日志按新主题重染，不再残留上一主题的配色
        self._refresh_preview()

    def _on_lang_combo_changed(self) -> None:
        """切换中/英文：就地刷新全部文案，不重建窗口 —— 没有闪烁。"""
        new_lang = str(self.lang_combo.currentData())
        if new_lang == i18n.current_lang():
            return
        if self._busy:
            self._sync_lang_combo()
            QMessageBox.information(
                self, i18n.t("lang_info_title"), i18n.t("lang_busy_block")
            )
            return
        i18n.set_lang(new_lang)
        self._save_settings()
        if self.on_language_change is not None:
            self.on_language_change()
        else:
            self.retranslate_ui()

    def _sync_lang_combo(self) -> None:
        """把语言下拉框回弹到当前语言（busy 时不切换）。"""
        self.lang_combo.blockSignals(True)
        try:
            index = self.lang_combo.findData(i18n.current_lang())
            if index >= 0:
                self.lang_combo.setCurrentIndex(index)
        finally:
            self.lang_combo.blockSignals(False)

    # ==================================================================
    # 就地重译：语言切换零闪烁
    # ==================================================================
    def retranslate_ui(self) -> None:
        """把界面上的全部文案按当前语言刷新一遍。

        窗口、布局、数据全部原样保留，只换文字 —— 切换语言不再有
        「关旧窗开新窗」的闪烁，文件队列与参数状态也天然不丢。
        """
        self.setWindowTitle(i18n.t("app_title"))

        # 顶栏
        self.title_label.setText(i18n.t("app_title"))
        self.subtitle_label.setText(i18n.t("app_subtitle"))
        self._retranslate_combo(self.lang_combo, ("lang_name_zh", "lang_short_en"))
        self._retranslate_combo(self.theme_combo, ("theme_light", "theme_dark"))
        self.lang_combo.setToolTip(i18n.t("lang_combo_tooltip"))
        self.theme_combo.setToolTip(i18n.t("theme_combo_tooltip"))

        # 顶栏维护功能按钮
        self.export_log_btn.setText(i18n.t("btn_export_log"))
        self.export_log_btn.setToolTip(i18n.t("export_log_tooltip"))
        self.feedback_btn.setText(i18n.t("btn_feedback"))
        self.feedback_btn.setToolTip(i18n.t("feedback_tooltip"))
        self.update_btn.setText(i18n.t("btn_check_update"))
        self.update_btn.setToolTip(i18n.t("update_btn_tooltip"))
        self.update_now_action.setText(i18n.t("btn_check_update"))
        self.auto_check_action.setText(i18n.t("update_auto_check"))

        # 卡片标题与表格列名
        self.files_card_title.setText(i18n.t("card_files"))
        self.log_card_title.setText(i18n.t("card_log"))
        self._update_table_headers()

        # 空状态
        self.drop_hint_title.setText(i18n.t("drop_title"))
        self.drop_hint_sub.setText(i18n.t("drop_sub"))
        self.empty_add_btn.setText(i18n.t("choose_files"))
        self.empty_add_btn.setToolTip(i18n.t("drop_sub"))

        # 文件卡片按钮行
        self.add_btn.setText(i18n.t("add_files"))
        self.add_btn.setToolTip(i18n.t("drop_sub"))
        self.remove_btn.setText(i18n.t("remove_selected"))
        self.remove_btn.setToolTip(i18n.t("remove_selected_tooltip"))
        self.clear_btn.setText(i18n.t("clear_all"))
        self.clear_btn.setToolTip(i18n.t("clear_all_tooltip"))

        # 分组标题与表单行标签
        self.format_group.setTitle(i18n.t("group_format"))
        self.output_group.setTitle(i18n.t("group_output_naming"))
        self._update_size_group_title()
        for _form, label, key in self._form_rows:
            label.setText(i18n.t(key))

        # 单选 / 勾选
        self.mode_phys.setText(i18n.t("mode_physical"))
        self.mode_px.setText(i18n.t("mode_pixels"))
        self.aspect_check.setText(i18n.t("aspect_check"))
        self.aspect_check.setToolTip(i18n.t("aspect_link_hint"))
        self.upscale_check.setText(i18n.t("upscale_check"))
        self.lossy_check.setText(i18n.t("lossy_fallback"))

        # 下拉框条目（保留当前选中，只换文字）
        self._retranslate_combo_by_data(self.fmt_combo, "fmt_{value}")
        self._retranslate_combo_by_data(self.tiff_combo, "tiff_{value}")
        self._retranslate_combo_by_data(self.unit_combo, "unit_{value}")
        self._retranslate_combo_by_data(self.conflict_combo, "conflict_{value}")
        self.conflict_combo.setToolTip(i18n.t("conflict_tooltip"))

        # 占位与提示
        self.phys_height.setPlaceholderText(i18n.t("phys_placeholder"))
        self.phys_height.setToolTip(i18n.t("phys_placeholder"))
        self.px_width.setPlaceholderText(i18n.t("px_placeholder"))
        self.px_width.setToolTip(i18n.t("px_placeholder"))
        self.px_height.setPlaceholderText(i18n.t("phys_placeholder"))
        self.px_height.setToolTip(i18n.t("phys_placeholder"))
        self.limit_edit.setPlaceholderText(i18n.t("limit_placeholder"))
        self.limit_edit.setToolTip(i18n.t("limit_placeholder"))
        self.out_edit.setPlaceholderText(i18n.t("out_placeholder"))
        self.out_edit.setToolTip(i18n.t("out_dir_tooltip"))
        self.name_edit.setToolTip(i18n.t("name_tooltip"))
        self.browse_btn.setText(i18n.t("browse"))

        # 动作区
        self.cancel_btn.setText(i18n.t("cancel"))
        self.open_dir_btn.setText(i18n.t("open_output_folder"))
        self.start_btn.setToolTip(i18n.t("start_convert_tooltip"))

        # 日志与通知
        self.log_view.setPlaceholderText(i18n.t("log_placeholder"))
        self.toast.retranslate(i18n.t("toast_close"))

        # 表格逐行：页码（"全部"是本地化显示）、源信息、状态（UserRole 存键）
        self._loading_rows = True
        try:
            for row, path in enumerate(self._rows):
                info = self._sources.get(str(path))
                pages_item = self.table.item(row, COL_PAGES)
                if (
                    pages_item is not None
                    and pages_item.flags() & Qt.ItemFlag.ItemIsEditable
                ):
                    saved = pages_item.data(Qt.ItemDataRole.UserRole) or ""
                    pages_item.setText(i18n.t("pages_all") if saved == "" else saved)
                    pages_item.setToolTip(i18n.t("pages_tooltip"))
                source_item = self.table.item(row, COL_SOURCE)
                if source_item is not None and info is not None:
                    source_item.setText(self._describe_source(info))
                status_item = self.table.item(row, COL_STATUS)
                if status_item is not None:
                    key = status_item.data(Qt.ItemDataRole.UserRole)
                    if key:
                        status_item.setText(i18n.t(key))
        finally:
            self._loading_rows = False

        # 目标列、规格条、计数/状态/主按钮、尺寸组标题统一重算
        self._refresh_preview()

    @staticmethod
    def _retranslate_combo(combo, keys: tuple[str, ...]) -> None:
        combo.blockSignals(True)
        try:
            for index, key in enumerate(keys):
                if index < combo.count():
                    combo.setItemText(index, i18n.t(key))
        finally:
            combo.blockSignals(False)

    @staticmethod
    def _retranslate_combo_by_data(combo: QComboBox, key_pattern: str) -> None:
        combo.blockSignals(True)
        try:
            for index in range(combo.count()):
                data = combo.itemData(index)
                if data is not None:
                    combo.setItemText(index, i18n.t(key_pattern.format(value=data)))
        finally:
            combo.blockSignals(False)

    # ==================================================================
    # 右键菜单
    # ==================================================================
    def _show_context_menu(self, position) -> None:
        indexes = self.table.selectedIndexes()
        if not indexes:
            return

        menu = QMenu(self)
        export_action = QAction(i18n.t("convert_selected"), self)
        export_action.triggered.connect(self._export_selected)
        menu.addAction(export_action)

        reveal_action = QAction(i18n.t("ctx_reveal"), self)
        reveal_action.triggered.connect(self._reveal_selected)
        menu.addAction(reveal_action)

        menu.addSeparator()

        remove_action = QAction(i18n.t("remove_selected"), self)
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
            self._append_log("log_added", "info", n=added)
        self._refresh_preview()
        # 首批文件进来后自动选中第一行，让右栏立即进入「编辑该行」状态；
        # 必须在补齐空边之前选中，补齐值才能写进该行的参数快照
        if added and self._selected_row() is None and self.table.rowCount() > 0:
            self.table.selectRow(0)
        # 新文件带来源比例：勾选宽高比且只有一边有值时，补齐空边
        self._fill_missing_side()

    def _add_file(self, path: Path) -> bool:
        key = str(path)
        if key in self._sources:
            return False
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            self._append_log("log_skipped", "warn", name=path.name)
            return False

        info = ingest.probe(path)
        self._sources[key] = info
        self._rows.append(path)
        # 新文件快照当前编辑上下文作为自己的尺寸参数
        self._size_params[key] = self._read_panel_params()

        row = self.table.rowCount()
        self.table.insertRow(row)
        self._populate_row(row, path, info)
        if info.error:
            self._append_log("log_failed_item", "error", name=path.name, error=info.error)
        for note in info.notes:
            self._append_log("log_warning_item", "warn", warning=note)
        return True

    def _populate_row(self, row: int, path: Path, info: SourceInfo) -> None:
        """填一行：默认勾选；页码列仅对 PDF/PPT 开放。"""
        check_item = QTableWidgetItem()
        check_item.setFlags(
            Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
        )
        check_item.setCheckState(Qt.CheckState.Checked)

        index_item = self._plain_item(str(row + 1))
        name_item = self._plain_item(path.name, tooltip=str(path))

        pages_item = self._plain_item(i18n.t("pages_all"), tooltip=i18n.t("pages_tooltip"))
        if info.kind in PAGED_KINDS and not info.error:
            pages_item.setFlags(pages_item.flags() | Qt.ItemFlag.ItemIsEditable)
            pages_item.setData(Qt.ItemDataRole.UserRole, "")  # "" = 全部页
        else:
            pages_item.setText("—")

        source_item = self._plain_item(self._describe_source(info))
        target_item = self._plain_item("—")
        status_item = self._plain_item("")

        self._loading_rows = True
        try:
            self.table.setItem(row, COL_CHECK, check_item)
            self.table.setItem(row, COL_INDEX, index_item)
            self.table.setItem(row, COL_NAME, name_item)
            self.table.setItem(row, COL_PAGES, pages_item)
            self.table.setItem(row, COL_SOURCE, source_item)
            self.table.setItem(row, COL_TARGET, target_item)
            self.table.setItem(row, COL_STATUS, status_item)
        finally:
            self._loading_rows = False
        self._set_status_item(
            row,
            "row_unreadable" if info.error else "row_pending",
            bold=bool(info.error),
        )

    def _set_status_item(self, row: int, key: str, *, bold: bool = False) -> None:
        """状态列：UserRole 存 i18n 键（语言切换时重译），UserRole+1 存加粗标记。"""
        item = self.table.item(row, COL_STATUS)
        if item is None:
            item = QTableWidgetItem()
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, COL_STATUS, item)
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setData(Qt.ItemDataRole.UserRole + 1, bool(bold))
        item.setText(i18n.t(key))
        font = item.font()
        font.setBold(bold)
        item.setFont(font)

    @staticmethod
    def _plain_item(text: str, *, tooltip: str = "", bold: bool = False) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if tooltip:
            item.setToolTip(tooltip)
        if bold:
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        return item

    def _describe_source(self, info: SourceInfo) -> str:
        """本地化的源信息（类型 + 关键读数），替代 imgspec 的中文版描述。"""
        kind = i18n.t(KIND_LABEL_KEYS[info.kind])
        if info.kind is SourceKind.RASTER and info.pixel_size:
            w, h = info.pixel_size
            if info.dpi is None:
                dpi_text = i18n.t("log_no_dpi")
            else:
                x, y = info.dpi
                dpi_text = f"{x:.0f} dpi" if abs(x - y) < 0.01 else f"{x:.0f} x {y:.0f} dpi"
            return f"{kind} · {w} x {h} px / {dpi_text}"
        if info.kind in (SourceKind.PDF, SourceKind.SVG) and info.page_sizes_in:
            w, h = info.page_sizes_in[0]
            first = i18n.t("log_first_page", w=w * 25.4, h=h * 25.4)
            if info.kind is SourceKind.SVG:
                return f"{kind} · {w * 25.4:.0f} x {h * 25.4:.0f} mm"
            return f"{kind} · {i18n.t('log_source_page', n=info.page_count)} / {first}"
        if info.kind is SourceKind.SLIDES:
            if info.page_count:
                pages = i18n.t("log_source_page", n=info.page_count)
                if info.aspect:
                    return f"{kind} · {pages} / {i18n.t('log_aspect', a=info.aspect)}"
                return f"{kind} · {pages}"
            return f"{kind} · …"
        return kind

    def _renumber_rows(self) -> None:
        self._loading_rows = True
        try:
            for row in range(self.table.rowCount()):
                item = self.table.item(row, COL_INDEX)
                if item is not None:
                    item.setText(str(row + 1))
        finally:
            self._loading_rows = False

    def remove_selected(self) -> None:
        """移除操作目标：优先移除选中行；没有选中时回退到已勾选的行。

        与「勾选 → 选中」联动配合，保证勾了就能移除、点了也能移除。
        """
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            rows = sorted(self._checked_rows(), reverse=True)
        for row in rows:
            path = self._rows.pop(row)
            self._sources.pop(str(path), None)
            self._size_params.pop(str(path), None)
            self.table.removeRow(row)
        self._renumber_rows()
        # 选中行已被移除时回到默认模板（清空选择会触发 _on_selection_changed）
        if self._selected_row() is None:
            self._load_row_into_panel(None)
        self._refresh_preview()

    def clear_all(self) -> None:
        self._rows.clear()
        self._sources.clear()
        self._size_params.clear()
        self.table.setRowCount(0)
        self.log_view.clear()
        self._log_records.clear()
        self.progress.setValue(0)
        self.open_dir_btn.setEnabled(False)
        self._status_summary = None
        self._load_row_into_panel(None)
        self._refresh_preview()

    # ------------------------------------------------------------------
    # 勾选与页码
    # ------------------------------------------------------------------
    def _checked_rows(self) -> list[int]:
        rows = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_CHECK)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                rows.append(row)
        return rows

    def _checked_paths(self) -> list[Path]:
        return [self._rows[row] for row in self._checked_rows() if 0 <= row < len(self._rows)]

    def _toggle_select_all(self) -> None:
        header = self.table.horizontalHeader()
        new_state = (
            Qt.CheckState.Unchecked
            if header.check_state == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        self._loading_rows = True
        try:
            for row in range(self.table.rowCount()):
                item = self.table.item(row, COL_CHECK)
                if item is not None:
                    item.setCheckState(new_state)
        finally:
            self._loading_rows = False
        # 全选/全不选同样保持「勾选 → 选中」的一致观感
        self._syncing_check_select = True
        try:
            if new_state == Qt.CheckState.Checked:
                self.table.selectAll()
            else:
                self.table.clearSelection()
        finally:
            self._syncing_check_select = False
        self._update_selection_ui()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading_rows:
            return
        column = item.column()
        if column == COL_CHECK:
            self._sync_check_to_selection(item)
            self._update_selection_ui()
        elif column == COL_PAGES:
            self._validate_pages_item(item)

    def _on_table_clicked(self, row: int, column: int) -> None:
        """页码列单击即编辑：chip 是就地内嵌的输入框，不是弹出的对话框。"""
        if column != COL_PAGES:
            return
        item = self.table.item(row, COL_PAGES)
        if item is not None and item.flags() & Qt.ItemFlag.ItemIsEditable:
            self.table.editItem(item)

    def _sync_check_to_selection(self, item: QTableWidgetItem) -> None:
        """勾选 → 选中 单向联动：被勾选的行同时成为编辑/移除的操作目标，
        取消勾选则退出选中 —— 「勾了就能操作」，不再出现勾选与选中两张皮。
        （反方向不联动：点击选中某行不会改动任何勾选，避免浏览时误改转换范围。）
        """
        if self._syncing_check_select:
            return
        row = item.row()
        if not (0 <= row < self.table.rowCount()):
            return
        checked = item.checkState() == Qt.CheckState.Checked
        self._syncing_check_select = True
        try:
            model = self.table.model()
            flag = (
                QItemSelectionModel.SelectionFlag.Select
                if checked
                else QItemSelectionModel.SelectionFlag.Deselect
            )
            self.table.selectionModel().select(
                model.index(row, 0),
                flag | QItemSelectionModel.SelectionFlag.Rows,
            )
        finally:
            self._syncing_check_select = False

    def _validate_pages_item(self, item: QTableWidgetItem) -> None:
        """页码表达式校验：合法则保存，非法则还原并提示。"""
        row = item.row()
        text = item.text().strip()
        saved = item.data(Qt.ItemDataRole.UserRole) or ""

        def restore(value: str) -> None:
            self._loading_rows = True
            try:
                item.setText(i18n.t("pages_all") if value == "" else value)
            finally:
                self._loading_rows = False

        if text == "" or text == i18n.t("pages_all"):
            item.setData(Qt.ItemDataRole.UserRole, "")
            restore("")
            return

        if _PAGES_PATTERN.match(text) and re.search(r"\d", text):
            item.setData(Qt.ItemDataRole.UserRole, text)
            return

        path = self._rows[row] if 0 <= row < len(self._rows) else None
        self._append_log(
            "pages_invalid",
            "warn",
            name=path.name if path else "?",
            text=text,
            old=i18n.t("pages_all") if saved == "" else saved,
        )
        restore(saved)

    def _pages_override_for(self, path: Path) -> str | None:
        """读取某文件的页码覆盖；None 表示跟随「全部页」。"""
        try:
            row = self._rows.index(path)
        except ValueError:
            return None
        item = self.table.item(row, COL_PAGES)
        if item is None:
            return None
        saved = item.data(Qt.ItemDataRole.UserRole) or ""
        return saved or None

    # ------------------------------------------------------------------
    # 选择状态联动（表头三态 / 主按钮 / 计数与状态文字）
    # ------------------------------------------------------------------
    def _update_selection_ui(self) -> None:
        total = len(self._rows)
        checked = len(self._checked_rows())

        header = self.table.horizontalHeader()
        if total == 0 or checked == 0:
            header.set_check_state(Qt.CheckState.Unchecked)
        elif checked == total:
            header.set_check_state(Qt.CheckState.Checked)
        else:
            header.set_check_state(Qt.CheckState.PartiallyChecked)

        if self._busy:
            self.start_btn.setEnabled(False)
            self.start_btn.setText(i18n.t("start_convert"))
        else:
            self.start_btn.setEnabled(checked > 0)
            self.start_btn.setText(
                i18n.t("start_convert_n", n=checked) if checked else i18n.t("start_convert")
            )

        if total == 0:
            self.count_label.setText(i18n.t("count_files", n=0))
        else:
            self.count_label.setText(i18n.t("count_with_checked", n=total, m=checked))

        if not self._busy:
            self.remove_btn.setEnabled(total > 0)
            self.clear_btn.setEnabled(total > 0)
            if self._status_summary:
                self.status_label.setText(self._status_summary)
            elif total == 0:
                self.status_label.setText(i18n.t("status_idle_empty"))
            else:
                self.status_label.setText(i18n.t("status_idle", n=checked, total=total))

    def _update_empty_state(self) -> None:
        has_rows = len(self._rows) > 0
        self.empty_state.setVisible(not has_rows)
        self.table.setVisible(has_rows)

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
        # 切换物理/像素模式后，若当前模式只有一边有值且勾选了宽高比，补齐空边
        self._fill_missing_side()

    def _link_aspect(self, edited: QLineEdit, other: QLineEdit, *, pixel: bool) -> None:
        """保持宽高比时，把用户刚输入的一边按源比例换算到另一边。

        aspect = 宽 / 高：编辑宽时另一边 = 宽 / aspect；编辑高时另一边 = 高 × aspect。
        源比例取编辑目标（选中行）—— 正在编辑哪个文件，就按哪个文件的比例联动。
        """
        if not self.aspect_check.isChecked():
            return
        aspect = self._editing_aspect()
        if not aspect or aspect <= 0:
            return
        text = edited.text().strip()
        try:
            value = float(text)
        except ValueError:
            return
        if value <= 0:
            return
        # edited 是宽（编辑宽 -> 高 = 宽/aspect），否则 edited 是高（宽 = 高*aspect）
        result = value / aspect if edited is self.phys_width or edited is self.px_width else value * aspect
        if pixel:
            other.setText(str(max(1, round(result))))
        else:
            other.setText(f"{result:.2f}")

    def _on_phys_width_edited(self) -> None:
        self._mark_side("width")
        self._link_aspect(self.phys_width, self.phys_height, pixel=False)

    def _on_phys_height_edited(self) -> None:
        self._mark_side("height")
        self._link_aspect(self.phys_height, self.phys_width, pixel=False)

    def _on_px_width_edited(self) -> None:
        self._mark_side("width")
        self._link_aspect(self.px_width, self.px_height, pixel=True)

    def _on_px_height_edited(self) -> None:
        self._mark_side("height")
        self._link_aspect(self.px_height, self.px_width, pixel=True)

    def _fill_missing_side(self) -> None:
        """勾选「保持宽高比」时，宽高两框都应有值：空的一边按源比例补齐。

        只补空边、不动已有值；程序写入用 blockSignals 防止重入预览刷新。
        源比例取编辑目标（选中行优先，其次第一行）。
        """
        if self._loading_panel or self._filling_side or not self.aspect_check.isChecked():
            return
        aspect = self._editing_aspect()
        if not aspect or aspect <= 0:
            return
        pixel = not self.mode_phys.isChecked()
        if pixel:
            width_edit, height_edit = self.px_width, self.px_height
        else:
            width_edit, height_edit = self.phys_width, self.phys_height

        def fmt(value: float) -> str:
            return str(max(1, round(value))) if pixel else f"{value:.2f}"

        def to_float(text: str) -> float | None:
            try:
                value = float(text)
            except ValueError:
                return None
            return value if value > 0 else None

        width = to_float(width_edit.text().strip())
        height = to_float(height_edit.text().strip())

        if width and not height:
            self._set_side_text(height_edit, fmt(width / aspect))
        elif height and not width:
            self._set_side_text(width_edit, fmt(height * aspect))
        else:
            return
        # 补齐值也要进入行参数快照（blockSignals 绕过了 textChanged 的常规回写）
        self._capture_size_params()

    @staticmethod
    def _set_side_text(edit: QLineEdit, text: str) -> None:
        """宽高联动框的程序写入：屏蔽 textChanged，避免触发预览重入。"""
        edit.blockSignals(True)
        try:
            edit.setText(text)
        finally:
            edit.blockSignals(False)

    # -- 格式切换：编码行跟随格式 ----------------------------------------
    def _on_format_changed(self, *_args) -> None:
        if not hasattr(self, "tiff_combo") or not hasattr(self, "_format_form"):
            return
        fmt = OutputFormat(self.fmt_combo.currentData())
        self._format_form.setRowVisible(self.row_tiff, fmt is OutputFormat.TIFF)
        self._format_form.setRowVisible(self.row_jpeg, fmt is OutputFormat.JPEG)
        # UI 还没搭完时不刷新（start_btn 是最后创建的控件之一）
        if hasattr(self, "start_btn"):
            self._refresh_preview()

    @staticmethod
    def _read_number(edit: QLineEdit) -> str:
        return edit.text().strip()

    def _build_spec(self, params: SizeParams | None = None) -> OutputSpec:
        """从界面或行级参数快照读出规格。任何不合法的地方都会抛出 SpecError。

        页码范围不属于规格：每个 PDF/PPT 文件的页码在输入区单独设置，
        转换时经 page_overrides 传入管线。

        勾选「保持宽高比」时只把用户最后编辑的那条边当作硬约束：
        另一边在规格里留空，由每个源文件自己的比例决定 —— 这样
        「改宽度」时同批所有文件的输出宽度都精确等于设定值，
        竖版 PDF 之类的异比例源不会被「等比内接」框住。
        """
        p = params if params is not None else self._read_panel_params()

        def as_float(text: str) -> float | None:
            text = text.strip()
            return float(text) if text else None

        def as_int(text: str) -> int | None:
            text = text.strip()
            return int(float(text)) if text else None

        pixels = p.pixels

        try:
            dpi = int(float(p.dpi.strip() or "300"))
        except ValueError as exc:
            raise SpecError(f"DPI 需要是数字：{p.dpi}") from exc

        try:
            quality = int(float(self.jpeg_quality.currentText().strip() or "95"))
        except ValueError as exc:
            raise SpecError(f"JPEG 质量需要是数字：{self.jpeg_quality.currentText()}") from exc

        limit_text = (self.max_mb or "").strip()
        try:
            max_bytes = int(float(limit_text) * 1024 * 1024) if limit_text else None
        except ValueError as exc:
            raise SpecError(f"体积上限需要是数字（MB）：{limit_text}") from exc

        try:
            phys_width = as_float(p.phys_width) if not pixels else None
            phys_height = as_float(p.phys_height) if not pixels else None
        except ValueError as exc:
            raise SpecError("物理尺寸需要是数字") from exc

        try:
            px_width = as_int(p.px_width) if pixels else None
            px_height = as_int(p.px_height) if pixels else None
        except ValueError as exc:
            raise SpecError("像素尺寸需要是整数") from exc

        keep_aspect = p.keep_aspect
        if keep_aspect:
            # 单边硬约束：另一边交给每个源文件自己的比例（两框里显示的联动值只是预览）
            if p.last_side == "height":
                phys_width = None
                px_width = None
            else:
                phys_height = None
                px_height = None

        spec = OutputSpec(
            fmt=OutputFormat(self.fmt_combo.currentData()),
            size_mode=SizeMode.PIXELS if pixels else SizeMode.PHYSICAL,
            phys_unit=Unit(p.unit),
            phys_width=phys_width,
            phys_height=phys_height,
            px_width=px_width,
            px_height=px_height,
            dpi=dpi,
            keep_aspect=keep_aspect,
            allow_upscale=not p.no_upscale,
            tiff_compression=TiffCompression(self.tiff_combo.currentData()),
            jpeg_quality=quality,
            max_bytes=max_bytes,
            lossy_fallback=self.lossy_fallback,
            output_dir=Path(self.out_dir) if self.out_dir.strip() else None,
            name_template=self.name_template.strip() or "{stem}",
            page_range=None,
            on_conflict=self.conflict_policy,
        )
        spec.validate()
        return spec

    def _refresh_preview(self) -> None:
        # 面板当前值写回编辑目标（选中行；无选中行时写默认模板）
        self._capture_size_params()
        for row, path in enumerate(self._rows):
            text = "—"
            params = self._size_params.get(str(path))
            info = self._sources.get(str(path))
            aspect = info.aspect if info else None
            if aspect:
                try:
                    text = self._build_spec(params).resolve(aspect).describe()
                except (SpecError, ValueError):
                    text = "—"
            self._set_cell(row, COL_TARGET, text)

        self._update_empty_state()
        self._update_selection_ui()
        self._render_spec_strip()

    # ------------------------------------------------------------------
    # 行级尺寸参数：选中行驱动右栏面板
    # ------------------------------------------------------------------
    def _selected_row(self) -> int | None:
        """当前编辑目标：选中的第一行；无选中返回 None（编辑默认模板）。"""
        rows = {index.row() for index in self.table.selectedIndexes()}
        return min(rows) if rows else None

    def _read_panel_params(self) -> SizeParams:
        """从右栏控件读出一份尺寸参数快照。"""
        return SizeParams(
            pixels=self.mode_px.isChecked(),
            phys_width=self.phys_width.text().strip(),
            phys_height=self.phys_height.text().strip(),
            unit=str(self.unit_combo.currentData()),
            px_width=self.px_width.text().strip(),
            px_height=self.px_height.text().strip(),
            dpi=self.dpi_combo.currentText().strip(),
            keep_aspect=self.aspect_check.isChecked(),
            no_upscale=self.upscale_check.isChecked(),
            last_side=self._pending_side,
        )

    def _capture_size_params(self) -> None:
        """把控件值写回编辑目标。

        单个选中行：只写该行（单张个性化）；多选：写所有选中行（批量统一改）；
        无选中：写默认模板（新文件沿用）。
        """
        if self._loading_panel:
            return
        params = self._read_panel_params()
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        rows = [row for row in rows if 0 <= row < len(self._rows)]
        if rows:
            for row in rows:
                self._size_params[str(self._rows[row])] = SizeParams(**vars(params))
        else:
            self._default_params = params

    def _load_row_into_panel(self, row: int | None) -> None:
        """把某行（或默认模板）的参数载入右栏控件，屏蔽回写与联动。"""
        if row is not None and 0 <= row < len(self._rows):
            params = self._size_params.get(
                str(self._rows[row]), self._default_params
            )
        else:
            params = self._default_params

        self._loading_panel = True
        try:
            self._pending_side = params.last_side
            (self.mode_px if params.pixels else self.mode_phys).setChecked(
                params.pixels
            )
            self.phys_width.setText(params.phys_width)
            self.phys_height.setText(params.phys_height)
            unit_index = self.unit_combo.findData(params.unit)
            if unit_index >= 0:
                self.unit_combo.setCurrentIndex(unit_index)
            self.px_width.setText(params.px_width)
            self.px_height.setText(params.px_height)
            self.dpi_combo.setCurrentText(params.dpi)
            self.aspect_check.setChecked(params.keep_aspect)
            self.upscale_check.setChecked(params.no_upscale)
        finally:
            self._loading_panel = False
        self._enable_size_inputs()
        self._update_size_group_title()
        self._refresh_preview()

    def _on_selection_changed(self) -> None:
        """编辑目标切换：把控件切到新选中行的参数。"""
        self._load_row_into_panel(self._selected_row())

    def _mark_side(self, side: str) -> None:
        """记录用户最后编辑的边，并同步到编辑目标。"""
        self._pending_side = side
        row = self._selected_row()
        if row is not None and 0 <= row < len(self._rows):
            params = self._size_params.setdefault(str(self._rows[row]), self._read_panel_params())
            params.last_side = side
        else:
            self._default_params.last_side = side

    def _update_size_group_title(self) -> None:
        """尺寸组标题保持纯净：只写组名，不做任何「编辑目标」的后缀解释。"""
        self.size_group.setTitle(i18n.t("group_size"))

    def _set_cell(self, row: int, column: int, text: str) -> None:
        """更新预览/状态列文本。这些列不接 itemChanged 逻辑，无递归风险。"""
        item = self.table.item(row, column)
        if item is None:
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, column, item)
        else:
            item.setText(text)

    def _render_spec_strip(self) -> None:
        _bg, strip_ink, strip_soft = theme.strip_palette()
        t = theme.current_theme()
        try:
            spec = self._build_spec(self._size_params.get(self._strip_spec_key()))
            error = None
        except (SpecError, ValueError) as exc:
            spec = None
            error = str(exc)

        if error:
            self.preview_label.setText(
                f'<span style="color:{t.warning}; font-size:{theme.FONT_MD}pt;">'
                f"{html.escape(i18n.t('spec_error', error=error))}</span>"
            )
            return

        if spec is None:  # pragma: no cover - error 分支已覆盖
            return

        if not self._rows:
            self.preview_label.setText(
                f'<span style="color:{strip_soft};'
                f' font-size:{theme.FONT_SM}pt; font-style:italic;">'
                f"{html.escape(i18n.t('spec_hint_empty'))}"
                "</span>"
            )
            return

        aspect = self._editing_aspect()
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
        fmt_text = spec.fmt.value.upper()
        size_text = (
            f"≤ {spec.max_bytes / 1024 / 1024:.1f} MB" if spec.max_bytes else ""
        )
        queue_text = (
            i18n.t("spec_queue", n=len(self._rows)) if len(self._rows) > 1 else ""
        )

        def cell(text: str, *, lead: bool = False, soft: bool = False) -> str:
            if soft:
                color, weight = strip_soft, "400"
                size, style = theme.FONT_SM, "italic"
            else:
                color = strip_ink
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

    def _strip_spec_key(self) -> str | None:
        """规格条展示哪一行的规格：优先选中行，其次第一行。"""
        row = self._selected_row()
        if row is None or not (0 <= row < len(self._rows)):
            row = 0 if self._rows else None
        return str(self._rows[row]) if row is not None else None

    def _editing_aspect(self) -> float | None:
        """规格条/联动使用的源比例：优先选中行，其次第一行。"""
        if not self._rows:
            return None
        row = self._selected_row()
        if row is None or not (0 <= row < len(self._rows)):
            row = 0
        info = self._sources.get(str(self._rows[row]))
        return info.aspect if info else None

    # ==================================================================
    # 转换
    # ==================================================================
    def _start(self) -> None:
        files = self._checked_paths()
        if not files:
            QMessageBox.information(
                self,
                i18n.t("msg_no_selection_title"),
                i18n.t("msg_no_selection"),
            )
            return
        self._run_conversion(files)

    def _export_selected(self) -> None:
        """右键菜单「转换选中」：忽略勾选，直接转当前选中的行。"""
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        if not rows:
            QMessageBox.information(
                self,
                i18n.t("msg_no_selection_title"),
                i18n.t("msg_no_selection"),
            )
            return
        files = [self._rows[row] for row in rows if 0 <= row < len(self._rows)]
        self._run_conversion(files)

    def _run_conversion(self, files: list[Path]) -> None:
        if not files:
            QMessageBox.information(
                self, i18n.t("msg_no_files_title"), i18n.t("msg_no_files")
            )
            return
        try:
            spec = self._build_spec()
        except (SpecError, ValueError) as exc:
            QMessageBox.warning(self, i18n.t("msg_invalid_title"), str(exc))
            return

        self._last_output_dir = spec.output_dir or default_output_dir(files[0].parent)
        self.log_view.clear()
        self._log_records.clear()
        self._status_summary = None
        self._set_busy(True)
        self.progress.setRange(0, len(files))
        self.progress.setValue(0)

        # 每个文件自己的页码范围（仅 PDF/PPT）与尺寸参数，键为 str(路径)
        page_overrides = {str(path): self._pages_override_for(path) for path in files}
        spec_overrides = {
            str(path): self._build_spec(self._size_params[str(path)])
            for path in files
            if str(path) in self._size_params
        }

        self._thread = QThread(self)
        self._worker = ConversionWorker(
            files, spec, page_overrides=page_overrides, spec_overrides=spec_overrides
        )
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
            self.status_label.setText(i18n.t("status_cancelling"))

    def _on_progress(self, done: int, total: int, current: str) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.status_label.setText(
            i18n.t("status_converting", name=current, done=done, total=total)
        )

    def _on_finished(self, report: ConversionReport) -> None:
        self._last_report = report
        outcomes: dict[str, list[bool]] = {}
        for result in report.results:
            outcomes.setdefault(str(result.source), []).append(result.ok)

        for row, path in enumerate(self._rows):
            flags = outcomes.get(str(path))
            if not flags:
                key, bold = "row_pending", False
            elif all(flags):
                key, bold = "row_done", True
            elif any(flags):
                key, bold = "row_partial", True
            else:
                key, bold = "row_failed", True
            self._set_status_item(row, key, bold=bold)

        warnings = 0
        for result in report.results:
            if result.ok:
                page = (
                    i18n.t("log_page_suffix", page=result.page)
                    if result.page and result.page > 1
                    else ""
                )
                self._append_log(
                    "log_done_item",
                    "ok",
                    name=result.source.name,
                    page=page,
                    out=result.out_path.name,
                )
                self._append_log_raw(f"      ↳ {result.summary}", "info")
            else:
                self._append_log(
                    "log_failed_item", "error", name=result.source.name, error=result.error
                )
            for warning in result.warnings:
                warnings += 1
                self._append_log("log_warning_item", "warn", warning=warning)

        self._status_summary = i18n.t(
            "status_summary", ok=report.ok_count, fail=report.fail_count
        )
        self.status_label.setText(self._status_summary)
        self._append_log(
            "log_summary",
            "ok" if report.fail_count == 0 else "warn",
            ok=report.ok_count,
            fail=report.fail_count,
            warn=warnings,
        )
        self.open_dir_btn.setEnabled(self._last_output_dir is not None)
        self.progress.setValue(self.progress.maximum())
        self._set_busy(False)
        self._show_completion_toast(report)

    def _on_failed(self, message: str) -> None:
        self._append_log("log_interrupted", "error", message=message)
        self._status_summary = i18n.t("status_aborted")
        self.status_label.setText(self._status_summary)
        self._set_busy(False)

    def _on_thread_finished(self) -> None:
        if self._thread is not None:
            self._thread.deleteLater()
        self._thread = None
        self._worker = None

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.cancel_btn.setEnabled(busy)
        self.add_btn.setEnabled(not busy)
        self.theme_combo.setEnabled(not busy)
        self.lang_combo.setEnabled(not busy)
        self.progress.setVisible(busy)
        if not busy:
            self.progress.setValue(0)
        self._update_selection_ui()

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
                i18n.t("toast_dist_entry", key=key, n=size_buckets[key])
                for key in ("< 1 MB", "1–5 MB", "5–10 MB", "> 10 MB")
                if size_buckets.get(key)
            )
            lines.append(i18n.t("toast_size_dist", dist=distribution))
        if self._last_output_dir:
            lines.append(i18n.t("toast_out_dir", dir=self._last_output_dir))

        if fail_count == 0:
            title = i18n.t("toast_done_ok", ok=ok_count)
            level, auto_close = "ok", 6000
        else:
            title = i18n.t("toast_done_mixed", ok=ok_count, fail=fail_count)
            level, auto_close = "warn", 0  # 有失败时驻留，由用户手动关闭

        self.toast.show_message(
            title,
            "\n".join(lines),
            level=level,
            action_text=i18n.t("open_output_folder") if self._last_output_dir else "",
            on_action=self._open_output_dir,
            auto_close_ms=auto_close,
        )

    # ==================================================================
    # 维护功能：日志导出 / 问题反馈 / 检查更新
    # ==================================================================
    def _open_url(self, url: str) -> None:
        """用系统默认程序打开链接。

        单独抽成一个方法（而不是到处直接调 QDesktopServices）：测试里可以
        整体替换掉它 —— QDesktopServices.openUrl 是 C++ 静态方法，patch 不掉，
        测试一旦漏掉就会真的拉起浏览器。
        """
        QDesktopServices.openUrl(QUrl(url))

    def export_log(self) -> None:
        """把本次会话的转换日志导出成 txt（头部含版本与系统信息）。"""
        stamp = QDateTime.currentDateTime().toString("yyyyMMdd_HHmmss")
        text = build_log_text(
            list(self._log_records),
            generated_at=QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss"),
            note=i18n.t("log_privacy_note"),
        )
        self._save_log_text(
            text, i18n.t("export_log_title"), Path.home() / f"imgspec_log_{stamp}.txt"
        )

    def export_crash_log(self) -> None:
        """导出上次异常退出会话的日志（本地归档文件，用户自选保存位置）。"""
        text = self._session_log.crash_text()
        if text is None:
            self._append_log("crash_log_missing", "warn")
            return
        stamp = QDateTime.currentDateTime().toString("yyyyMMdd_HHmmss")
        self._save_log_text(
            text, i18n.t("crash_export_title"), Path.home() / f"imgspec_crash_{stamp}.txt"
        )

    def _save_log_text(self, text: str, title: str, default_path: Path) -> None:
        """导出日志文本的公共落盘流程（本次会话日志与上次崩溃日志共用）。"""
        path, _ = QFileDialog.getSaveFileName(self, title, str(default_path), "*.txt")
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            self._append_log_raw(i18n.t("log_export_failed", err=exc), "error")
            return
        self._append_log_raw(i18n.t("log_exported", path=path), "ok")
        folder_url = QUrl.fromLocalFile(str(Path(path).parent)).toString()
        self.toast.show_message(
            i18n.t("export_log_done"),
            path,
            level="ok",
            action_text=i18n.t("open_containing_folder"),
            on_action=lambda: self._open_url(folder_url),
            auto_close_ms=6000,
        )

    # ------------------------------------------------------------------
    # 会话日志：本地留档 + 上次异常退出的提示与导出
    # ------------------------------------------------------------------
    def _session_log_dir(self) -> Path:
        """会话日志目录（默认 %LOCALAPPDATA%\\imgspec\\...\\logs）。

        单独抽成方法是为了测试能把它指到临时目录 —— 否则跑测试会往
        用户真实的 AppData 里写日志。
        """
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppLocalDataLocation
        )
        return Path(base or Path.home() / ".imgspec") / "logs"

    def _session_header(self) -> str:
        lines = ["图片转换器 会话日志", "=" * 44]
        lines.extend(environment_lines())
        lines.append(f"启动：{QDateTime.currentDateTime().toString('yyyy-MM-dd HH:mm:ss')}")
        lines.append("=" * 44)
        return "\n".join(lines)

    def _notify_previous_crash(self) -> None:
        """上次没有正常退出：提示用户可把那次日志导出发给维护者。"""
        crash = self._session_log.previous_crash
        if crash is None:
            return
        self._append_log("crash_detected_log", "warn", name=crash.name)
        self.toast.show_message(
            i18n.t("crash_detected_title"),
            i18n.t("crash_detected_body"),
            level="warn",
            action_text=i18n.t("crash_export_action"),
            on_action=self.export_crash_log,
            auto_close_ms=0,
        )

    def open_feedback(self) -> None:
        """打开 GitHub 反馈页（Issue 模板会引导填写版本与复现步骤）。"""
        self._open_url(ISSUES_PAGE)
        self._append_log("feedback_opened")

    def check_updates(self, manual: bool = False) -> None:
        """检查更新。

        manual=False（启动时自动检查）完全静默：失败不打扰，只有发现新版才提示；
        manual=True 时把过程与结果写进日志/浮层。
        """
        if self._update_thread is not None and self._update_thread.isRunning():
            if manual:
                self._append_log("update_checking")
            return
        if manual:
            self._append_log("update_checking")
        self._update_thread = UpdateChecker(self)
        self._update_thread.checked.connect(
            lambda release: self._on_update_checked(release, manual)
        )
        self._update_thread.start()

    def _on_update_checked(self, release: object, manual: bool) -> None:
        """更新检查回到界面线程。release 为 None 表示失败/无法访问。"""
        if not isinstance(release, dict):
            if manual:
                self._append_log("update_check_failed", "warn")
                self.toast.show_message(
                    i18n.t("update_failed_title"),
                    i18n.t("update_failed_body"),
                    level="warn",
                    auto_close_ms=0,
                )
            return

        tag = str(release.get("tag") or "")
        url = str(release.get("url") or "")
        if is_newer(tag, app_version()):
            self._append_log("update_found", "ok", ver=tag, cur=app_version())
            self.toast.show_message(
                i18n.t("update_found_title", ver=tag),
                i18n.t("update_found_body", cur=app_version()),
                level="ok",
                action_text=i18n.t("update_open_page"),
                on_action=lambda: self._open_url(url),
                auto_close_ms=0,
            )
        elif manual:
            self._append_log("update_is_latest", "ok", ver=app_version())
            self.toast.show_message(
                i18n.t("update_latest_title"),
                i18n.t("update_latest_body", ver=app_version()),
                level="ok",
                auto_close_ms=5000,
            )

    def _on_auto_check_toggled(self, checked: bool) -> None:
        self._auto_check = bool(checked)
        self._settings().setValue("update_check_on_start", self._auto_check)

    def check_updates_on_start(self) -> None:
        """启动后的静默检查（由 app.py 定时触发；测试直接建窗不会触发）。"""
        if self._auto_check:
            self.check_updates(manual=False)

    # ==================================================================
    # 辅助
    # ==================================================================
    def _choose_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            i18n.t("fd_title"),
            "",
            f"{i18n.t('fd_all_supported')} (*{DOCUMENT_SUFFIXES.replace(' ', ' *')} *{IMAGE_SUFFIXES.replace(' ', ' *')})"
            f";;{i18n.t('fd_documents')} (*.pdf *.svg *.ppt *.pptx *.pptm *.pps *.ppsx)"
            f";;{i18n.t('fd_images')} (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp)"
            f";;{i18n.t('fd_all_files')} (*)",
        )
        if files:
            self.add_paths([Path(f) for f in files])

    def _append_log(self, key: str, level: str = "info", **kwargs) -> None:
        self._append_log_raw(i18n.t(key, **kwargs), level)

    def _append_log_raw(self, message: str, level: str = "info") -> None:
        """写一条日志：记录进 _log_records（供主题切换后重染）再上屏，同时落盘。"""
        stamp = QTime.currentTime().toString("[HH:mm]")
        self._log_records.append((stamp, level, message))
        if len(self._log_records) > 2000:  # 封顶，长跑不胀内存
            self._log_records = self._log_records[-2000:]
        self._session_log.append_record(stamp, level, message)
        self._append_log_line(stamp, level, message)

    def _append_log_line(self, stamp: str, level: str, message: str) -> None:
        """真正上屏一行：时间戳 + 图标前缀 + 按级别着色（取当前主题色）。"""
        t = theme.current_theme()
        color = {
            "ok": t.success,
            "info": t.text_primary,
            "warn": t.warning,
            "error": t.danger,
        }.get(level, t.text_primary)
        icon = {"ok": "✓", "warn": "⚠", "error": "✗", "info": "·"}.get(level, "·")
        safe = html.escape(message)
        self.log_view.appendHtml(
            f'<span style="color:{t.text_tertiary};">{stamp}</span> '
            f'<span style="color:{color};">{icon} {safe}</span>'
        )

    def _rerender_log(self) -> None:
        """按当前主题重染全部日志（主题切换后调用）。"""
        records = list(self._log_records)
        self.log_view.clear()
        for stamp, level, message in records:
            self._append_log_line(stamp, level, message)

    # ==================================================================
    # 右栏设置的 property 包装：读写直接落在控件上
    # ==================================================================
    @property
    def out_dir(self) -> str:
        return self.out_edit.text().strip()

    @out_dir.setter
    def out_dir(self, value: str) -> None:
        self.out_edit.setText(str(value))

    @property
    def name_template(self) -> str:
        return self.name_edit.text().strip() or "{stem}"

    @name_template.setter
    def name_template(self, value: str) -> None:
        self.name_edit.setText(str(value) if str(value).strip() else "{stem}")

    @property
    def conflict_policy(self) -> ConflictPolicy:
        return ConflictPolicy(str(self.conflict_combo.currentData()))

    @conflict_policy.setter
    def conflict_policy(self, value: ConflictPolicy) -> None:
        index = self.conflict_combo.findData(value.value)
        if index >= 0:
            self.conflict_combo.setCurrentIndex(index)

    @property
    def max_mb(self) -> str:
        return self.limit_edit.text().strip()

    @max_mb.setter
    def max_mb(self, value) -> None:
        self.limit_edit.setText(str(value))

    @property
    def lossy_fallback(self) -> bool:
        return self.lossy_check.isChecked()

    @lossy_fallback.setter
    def lossy_fallback(self, value: bool) -> None:
        self.lossy_check.setChecked(bool(value))

    # ==================================================================
    # 设置持久化
    # ==================================================================
    def _settings(self) -> QSettings:
        return QSettings("imgspec", "ImageSpecTool")

    def _load_language(self) -> None:
        """读取语言偏好。必须在 _build_ui 之前调用（所有文案都依赖它）。"""
        s = self._settings()
        lang = s.value("lang", "zh")
        i18n.set_lang(lang if lang in i18n.LANGS else "zh")

    def _load_settings(self) -> None:
        s = self._settings()
        geometry = s.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        self._clamp_geometry_to_screen()

        # 主题：没有保存过偏好时沿用当前生效的主题（app.py 启动时探测到的系统主题），
        # 不要回退到 "light" —— 否则深色系统的新用户首次启动会被强制切成浅色。
        # 恢复走 _apply_theme（与手动切换同一入口），保证 QSS、日志、预览等
        # 主题相关状态全部同步；手动设置开关时屏蔽信号，避免重复触发。
        saved_theme = s.value("theme", theme.current_theme().name)
        if saved_theme in ("light", "dark"):
            index = self.theme_combo.findData(saved_theme)
            if index >= 0:
                self.theme_combo.blockSignals(True)
                self.theme_combo.setCurrentIndex(index)
                self.theme_combo.blockSignals(False)
            self._apply_theme(saved_theme)

        # 启动时是否自动检查更新（默认开；顶栏「检查更新」菜单可关）
        self._auto_check = s.value("update_check_on_start", True, type=bool)
        self.auto_check_action.blockSignals(True)
        self.auto_check_action.setChecked(self._auto_check)
        self.auto_check_action.blockSignals(False)

        index = self.fmt_combo.findData(s.value("fmt", OutputFormat.TIFF.value))
        if index >= 0:
            self.fmt_combo.setCurrentIndex(index)

        # 尺寸参数模板：恢复到默认模板并同步到面板控件
        self._default_params = SizeParams(
            pixels=s.value("mode", SizeMode.PHYSICAL.value) == SizeMode.PIXELS.value,
            phys_width=str(s.value("phys_width", "8.5")),
            phys_height=str(s.value("phys_height", "")),
            unit=str(s.value("unit", Unit.CM.value)),
            px_width=str(s.value("px_width", "")),
            px_height=str(s.value("px_height", "")),
            dpi=str(s.value("dpi", "300")),
            keep_aspect=s.value("keep_aspect", True, type=bool),
            no_upscale=s.value("no_upscale", False, type=bool),
            last_side=str(s.value("last_side", "width")),
        )
        self._load_row_into_panel(None)

        compression_index = self.tiff_combo.findData(
            s.value("tiff_compression", TiffCompression.LZW.value)
        )
        if compression_index >= 0:
            self.tiff_combo.setCurrentIndex(compression_index)
        self.jpeg_quality.setCurrentText(s.value("jpeg_quality", "95"))

        # 输出与命名 / 体积（经 property 写入右栏控件）
        self.out_dir = s.value("out_dir", "") or ""
        self.name_template = s.value("name_template", "{stem}") or "{stem}"
        policy = s.value("on_conflict", ConflictPolicy.RENAME.value)
        try:
            self.conflict_policy = ConflictPolicy(policy)
        except ValueError:
            self.conflict_policy = ConflictPolicy.RENAME
        self.max_mb = s.value("max_mb", "") or ""
        self.lossy_fallback = s.value("lossy_fallback", True, type=bool)

        self._on_mode_changed()

    def _clamp_geometry_to_screen(self) -> None:
        """恢复的窗口几何超出当前屏幕（换屏/DPI 变化）时收缩回可用区域，
        避免出现「窗口比屏幕宽、splitter 布局异常」的观感。"""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        frame = self.frameGeometry()
        width = min(frame.width(), int(avail.width() * 0.95))
        height = min(frame.height(), int(avail.height() * 0.95))
        if frame.width() > avail.width() or frame.height() > avail.height():
            self.resize(max(self.minimumWidth(), width - 16), max(self.minimumHeight(), height - 16))

        # 右栏固定宽度，其余给左栏 —— 不依赖 sizeHint 的初始猜测
        total = max(self._body_splitter.width(), width - 48)
        self._body_splitter.setSizes([max(560, total - 470), 470])

    def _save_settings(self) -> None:
        s = self._settings()
        s.setValue("window/geometry", self.saveGeometry())
        s.setValue("theme", theme.current_theme().name)
        s.setValue("lang", i18n.current_lang())
        s.setValue("fmt", self.fmt_combo.currentData())
        # 尺寸参数只保存默认模板（新文件的出厂设置），行级参数跟随文件列表、不跨会话
        p = self._default_params
        s.setValue("mode", SizeMode.PIXELS.value if p.pixels else SizeMode.PHYSICAL.value)
        s.setValue("phys_width", p.phys_width)
        s.setValue("phys_height", p.phys_height)
        s.setValue("unit", p.unit)
        s.setValue("px_width", p.px_width)
        s.setValue("px_height", p.px_height)
        s.setValue("dpi", p.dpi)
        s.setValue("keep_aspect", p.keep_aspect)
        s.setValue("no_upscale", p.no_upscale)
        s.setValue("last_side", p.last_side)
        s.setValue("tiff_compression", self.tiff_combo.currentData())
        s.setValue("jpeg_quality", self.jpeg_quality.currentText())
        s.setValue("out_dir", self.out_dir)
        s.setValue("name_template", self.name_template)
        s.setValue("on_conflict", self.conflict_policy.value)
        s.setValue("max_mb", self.max_mb)
        s.setValue("lossy_fallback", self.lossy_fallback)
        s.setValue("update_check_on_start", self._auto_check)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._worker is not None:
            self._worker.cancel()
        self._save_settings()
        # 写「正常退出」标记：下次启动据此判断上次是干净退出还是崩溃
        self._session_log.finish()

        thread, worker = self._thread, self._worker
        if thread is not None and thread.isRunning():
            if not thread.wait(15_000):
                _orphaned_jobs.extend([thread, worker])
                thread.setParent(None)

        super().closeEvent(event)
