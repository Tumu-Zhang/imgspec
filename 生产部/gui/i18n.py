"""界面双语支持：中文 / English。

设计
----
- 界面层字符串一律通过 ``t("key")`` 取值，不直接写死中文；
- imgspec 核心层（管线错误、探测提示）暂不翻译，日志中的底层消息保持原文；
- 切换语言走 ``set_lang()`` 后重建主窗口（见 app.py 的重建回调），
  不做逐控件 retranslate —— 窗口重建的成本远低于维护两套映射。
"""

from __future__ import annotations

LANGS = ("zh", "en")

_STRINGS: dict[str, dict[str, str]] = {
    # ------------------------------------------------------------------
    # 窗口与卡片
    # ------------------------------------------------------------------
    "app_title": {"zh": "图片转换器", "en": "Image Converter"},
    "app_subtitle": {
        "zh": "科研投稿图片规格化工具",
        "en": "Journal-ready image specs",
    },
    "card_files": {"zh": "待处理文件", "en": "Files"},
    "card_log": {"zh": "转换日志", "en": "Log"},
    "add_files": {"zh": "添加文件", "en": "Add Files"},
    "choose_files": {"zh": "选择文件", "en": "Choose Files"},
    "drop_title": {"zh": "把文件拖到这里", "en": "Drop files here"},
    "drop_sub": {
        "zh": "科研投稿图片规格化 · 支持 PNG、JPG、TIFF、PDF、PPT 等",
        "en": "Journal-ready image specs · PNG, JPG, TIFF, PDF, PPT & more",
    },
    "lang_name_zh": {"zh": "中文", "en": "中文"},
    "lang_name_en": {"zh": "English", "en": "English"},
    "lang_short_en": {"zh": "EN", "en": "EN"},
    "lang_combo_tooltip": {"zh": "界面语言", "en": "Interface language"},
    "theme_combo_tooltip": {"zh": "配色主题", "en": "Color theme"},
    "theme_light": {"zh": "浅色", "en": "Light"},
    "theme_dark": {"zh": "深色", "en": "Dark"},
    "remove_selected": {"zh": "移除选中", "en": "Remove Selected"},
    "remove_selected_tooltip": {
        "zh": "将选中的文件从处理列表移除；未选中时移除已勾选的文件（不会删除源文件）",
        "en": "Remove the selected files; if none are selected, remove the checked ones (source files are kept)",
    },
    "clear_all": {"zh": "清空列表", "en": "Clear All"},
    "clear_all_tooltip": {"zh": "清空所有待处理文件", "en": "Remove all pending files"},
    "count_files": {"zh": "{n} 个文件", "en": "{n} file(s)"},
    "count_with_checked": {"zh": "{n} 个文件 · 已勾选 {m}", "en": "{n} file(s) · {m} checked"},
    "status_idle_empty": {"zh": "添加文件后即可转换", "en": "Add files to get started"},
    "status_idle": {"zh": "就绪 · 已勾选 {n}/{total} 个文件", "en": "Ready · {n}/{total} checked"},
    "status_converting": {"zh": "正在转换 {name}（{done}/{total}）", "en": "Converting {name} ({done}/{total})"},
    "status_summary": {
        "zh": "完成：成功 {ok} 项，失败 {fail} 项",
        "en": "Finished: {ok} succeeded, {fail} failed",
    },
    "status_cancelling": {"zh": "正在取消…", "en": "Cancelling…"},
    "status_aborted": {"zh": "转换中断", "en": "Conversion aborted"},
    "lang_busy_block": {
        "zh": "转换进行中，完成后再切换语言。",
        "en": "A conversion is running; switch language after it finishes.",
    },
    "lang_info_title": {"zh": "语言", "en": "Language"},

    # ------------------------------------------------------------------
    # 表格
    # ------------------------------------------------------------------
    "col_name": {"zh": "文件名", "en": "Name"},
    "col_pages": {"zh": "页码", "en": "Pages"},
    "col_source": {"zh": "源信息", "en": "Source"},
    "col_target": {"zh": "输出尺寸", "en": "Output Size"},
    "col_status": {"zh": "状态", "en": "Status"},
    "pages_all": {"zh": "全部", "en": "All"},
    "pages_tooltip": {
        "zh": "仅 PDF / PPT：转换哪些页，如 1,3-5。双击修改，留空或“全部”表示所有页。",
        "en": "PDF / PPT only: pages to convert, e.g. 1,3-5. Double-click to edit; empty or \"All\" means every page.",
    },
    "pages_invalid": {
        "zh": "{name}：页码“{text}”无法识别，已还原为 {old}",
        "en": "{name}: unrecognized page range \"{text}\", restored to {old}",
    },
    "kind_raster": {"zh": "图片", "en": "Image"},
    "kind_pdf": {"zh": "PDF", "en": "PDF"},
    "kind_svg": {"zh": "矢量图", "en": "Vector"},
    "kind_slides": {"zh": "幻灯片", "en": "Slides"},
    "kind_unsupported": {"zh": "不支持", "en": "Unsupported"},
    "row_pending": {"zh": "待处理", "en": "Pending"},
    "row_unreadable": {"zh": "无法读取", "en": "Unreadable"},
    "row_done": {"zh": "完成", "en": "Done"},
    "row_partial": {"zh": "部分失败", "en": "Partial"},
    "row_failed": {"zh": "失败", "en": "Failed"},

    # ------------------------------------------------------------------
    # 规格条
    # ------------------------------------------------------------------
    "spec_error": {"zh": "参数待修正：{error}", "en": "Needs fixing: {error}"},
    "spec_hint_empty": {
        "zh": "添加文件后，此处显示输出规格预览",
        "en": "Add files to preview the output spec here",
    },
    "spec_queue": {"zh": "队列 {n} 个文件", "en": "{n} files queued"},

    # ------------------------------------------------------------------
    # 完成通知（Toast）
    # ------------------------------------------------------------------
    "toast_done_ok": {"zh": "转换完成，成功 {ok} 项", "en": "Done: {ok} converted"},
    "toast_done_mixed": {
        "zh": "完成：成功 {ok} 项，失败 {fail} 项",
        "en": "Finished: {ok} succeeded, {fail} failed",
    },
    "toast_size_dist": {"zh": "体积分布：{dist}", "en": "Size spread: {dist}"},
    "toast_dist_entry": {"zh": "{key} {n} 项", "en": "{key}: {n}"},
    "toast_out_dir": {"zh": "输出目录：{dir}", "en": "Output: {dir}"},
    "toast_close": {"zh": "关闭", "en": "Close"},

    # ------------------------------------------------------------------
    # 维护功能：日志导出 / 问题反馈 / 检查更新
    # ------------------------------------------------------------------
    "btn_export_log": {"zh": "导出日志", "en": "Export log"},
    "export_log_title": {"zh": "保存转换日志", "en": "Save log"},
    "export_log_tooltip": {
        "zh": "把本次会话的转换日志存成 txt（含版本与系统信息，便于反馈问题）",
        "en": "Save this session's log as txt (includes version and system info)",
    },
    "export_log_done": {"zh": "日志已导出", "en": "Log exported"},
    "log_exported": {"zh": "日志已导出：{path}", "en": "Log exported: {path}"},
    "log_export_failed": {"zh": "日志导出失败：{err}", "en": "Failed to export log: {err}"},
    "open_containing_folder": {"zh": "打开所在文件夹", "en": "Open Folder"},
    "btn_feedback": {"zh": "反馈", "en": "Feedback"},
    "feedback_tooltip": {
        "zh": "到 GitHub 提交问题或建议（模板会引导你填写，可附上导出的日志）",
        "en": "Report issues or ideas on GitHub (attach the exported log if useful)",
    },
    "feedback_opened": {
        "zh": "已打开反馈页；建议先用「导出日志」导出日志，再附到 Issue 里",
        "en": "Feedback page opened; export the log first if you want to attach it",
    },
    "btn_check_update": {"zh": "检查更新", "en": "Check updates"},
    "update_btn_tooltip": {
        "zh": "检查是否有新版本；右键箭头可关闭启动时自动检查",
        "en": "Check for a newer release; the arrow menu can turn off startup checks",
    },
    "update_auto_check": {"zh": "启动时自动检查更新", "en": "Check at startup"},
    "update_checking": {"zh": "正在检查更新…", "en": "Checking for updates..."},
    "update_is_latest": {"zh": "已是最新版本（{ver}）", "en": "Already up to date ({ver})"},
    "update_latest_title": {"zh": "已是最新版本", "en": "Up to date"},
    "update_latest_body": {
        "zh": "当前版本 {ver} 就是最新版。",
        "en": "You are on the latest version, {ver}.",
    },
    "update_found": {
        "zh": "发现新版本 {ver}（当前 {cur}）",
        "en": "New release {ver} available (current {cur})",
    },
    "update_found_title": {"zh": "发现新版本 {ver}", "en": "New release {ver}"},
    "update_found_body": {
        "zh": "当前版本 {cur}。打开下载页获取新版压缩包。",
        "en": "Current version {cur}. Open the download page to get it.",
    },
    "update_open_page": {"zh": "打开下载页", "en": "Open Download Page"},
    "update_check_failed": {
        "zh": "检查更新失败（网络不可用或无法访问 GitHub）",
        "en": "Update check failed (offline or GitHub unreachable)",
    },
    "update_failed_title": {"zh": "检查更新失败", "en": "Update check failed"},
    "update_failed_body": {
        "zh": "未能连接 GitHub，稍后再试即可 —— 不影响正常使用。",
        "en": "Could not reach GitHub. Try again later - normal use is unaffected.",
    },

    # ------------------------------------------------------------------
    # 右栏：输出格式
    # ------------------------------------------------------------------
    "group_format": {"zh": "输出格式", "en": "Output Format"},
    "label_format": {"zh": "格式", "en": "Format"},
    "fmt_tiff": {"zh": "TIFF（无损，投稿首选）", "en": "TIFF (lossless, journal standard)"},
    "fmt_png": {"zh": "PNG（无损，带透明通道）", "en": "PNG (lossless, keeps transparency)"},
    "fmt_jpeg": {"zh": "JPEG（有损，体积小）", "en": "JPEG (lossy, small files)"},
    "fmt_pdf": {"zh": "PDF（单页图，矢量可缩放）", "en": "PDF (single-page, zoomable)"},

    # ------------------------------------------------------------------
    # 右栏：尺寸与分辨率
    # ------------------------------------------------------------------
    "group_size": {"zh": "尺寸与分辨率", "en": "Size && Resolution"},
    "label_mode": {"zh": "按", "en": "Mode"},
    "mode_physical": {"zh": "物理尺寸", "en": "Physical"},
    "mode_pixels": {"zh": "像素尺寸", "en": "Pixels"},
    "label_physical": {"zh": "物理尺寸", "en": "Physical size"},
    "label_pixels": {"zh": "像素尺寸", "en": "Pixel size"},
    "phys_placeholder": {"zh": "留空则按源图比例自动计算", "en": "Empty = follow source aspect"},
    "px_placeholder": {"zh": "例如 2550", "en": "e.g. 2550"},
    "label_dpi": {"zh": "输出 DPI", "en": "DPI"},
    "aspect_check": {"zh": "保持宽高比（不拉伸变形）", "en": "Keep aspect ratio (no distortion)"},
    "aspect_link_hint": {
        "zh": "勾选后修改宽或高，另一边会按源图比例自动联动",
        "en": "When checked, editing width or height updates the other side by the source aspect",
    },
    "upscale_check": {
        "zh": "不放大：源像素不足时保持原始像素并提示",
        "en": "No upscaling: warn if source is smaller",
    },
    "unit_mm": {"zh": "毫米", "en": "mm"},
    "unit_cm": {"zh": "厘米", "en": "cm"},
    "unit_inch": {"zh": "英寸", "en": "inch"},

    # ------------------------------------------------------------------
    # 右栏：编码（跟随格式动态显示）
    # ------------------------------------------------------------------
    "tiff_compression": {"zh": "TIFF 压缩", "en": "TIFF compression"},
    "tiff_lzw": {"zh": "LZW（无损，通用）", "en": "LZW (lossless, universal)"},
    "tiff_deflate": {"zh": "Deflate（无损，体积略小）", "en": "Deflate (lossless, smaller)"},
    "tiff_none": {"zh": "不压缩（体积最大）", "en": "No compression (largest)"},
    "jpeg_quality": {"zh": "JPEG 质量", "en": "JPEG quality"},

    # ------------------------------------------------------------------
    # 右栏：体积上限（随输出格式组展示）
    # ------------------------------------------------------------------
    "label_max_mb": {"zh": "每张上限", "en": "Size Limit"},
    "limit_unit": {"zh": "MB", "en": "MB"},
    "limit_placeholder": {"zh": "不限制", "en": "No limit"},
    "lossy_fallback": {
        "zh": "无损格式压不进上限时，允许自动改为有损",
        "en": "Allow lossy fallback if over the limit",
    },

    # ------------------------------------------------------------------
    # 右栏：输出与命名
    # ------------------------------------------------------------------
    "group_output_naming": {"zh": "输出与命名", "en": "Output && Naming"},
    "label_output_to": {"zh": "输出到", "en": "Output to"},
    "out_placeholder": {
        "zh": "留空 = 源文件同级的 converted 文件夹",
        "en": "Empty = \"converted\" folder next to sources",
    },
    "out_dir_tooltip": {
        "zh": "留空：输出到每个源文件自己所在目录下的 converted 文件夹\n填路径：所有图都输出到该目录（不存在会自动创建）",
        "en": "Empty: output to a \"converted\" folder beside each source file\nA path: everything goes there (created automatically)",
    },
    "browse": {"zh": "浏览…", "en": "Browse…"},
    "label_name_template": {"zh": "命名模板", "en": "Name Template"},
    "name_tooltip": {
        "zh": "输出文件名（不含扩展名），可用变量：\n  {stem}   源文件名\n  {page}   页号，多页文档用\n  {index}  同 {page}\n例：{stem}_300dpi",
        "en": "Output file name (without extension). Variables:\n  {stem}   source file name\n  {page}   page number, multi-page documents\n  {index}  same as {page}\nExample: {stem}_300dpi",
    },
    "label_conflict": {"zh": "重名处理", "en": "On Conflict"},
    "conflict_rename": {"zh": "自动改名（推荐）", "en": "Auto-rename (recommended)"},
    "conflict_overwrite": {"zh": "覆盖同名文件", "en": "Overwrite existing files"},
    "conflict_skip": {"zh": "跳过不处理", "en": "Skip"},
    "conflict_tooltip": {
        "zh": "自动改名：若 fig.tif 已存在，就输出 fig_1.tif、fig_2.tif…\n既不会失败，也不会覆盖你已有的文件",
        "en": "Auto-rename: if fig.tif exists, writes fig_1.tif, fig_2.tif…\nNever fails and never overwrites your files",
    },

    # ------------------------------------------------------------------
    # 动作区
    # ------------------------------------------------------------------
    "start_convert": {"zh": "开始转换", "en": "Convert"},
    "start_convert_n": {"zh": "开始转换（{n}）", "en": "Convert ({n})"},
    "start_convert_tooltip": {
        "zh": "转换清单中所有已勾选的文件（在左侧列表勾选）",
        "en": "Convert all checked files in the list (tick them on the left)",
    },
    "convert_selected": {"zh": "转换选中", "en": "Convert Selected"},
    "cancel": {"zh": "取消", "en": "Cancel"},
    "open_output_folder": {"zh": "打开输出文件夹", "en": "Open Output Folder"},
    "msg_no_selection_title": {"zh": "没有勾选文件", "en": "Nothing Checked"},
    "msg_no_selection": {
        "zh": "请先在左侧列表勾选要转换的文件。",
        "en": "Tick the files to convert in the list on the left first.",
    },
    "msg_no_files_title": {"zh": "没有文件", "en": "Nothing to Convert"},
    "msg_no_files": {
        "zh": "请先拖入或添加要转换的文件。",
        "en": "Drag in or add files to convert first.",
    },
    "msg_invalid_title": {"zh": "参数有误", "en": "Invalid Settings"},

    # ------------------------------------------------------------------
    # 日志
    # ------------------------------------------------------------------
    "log_placeholder": {
        "zh": "转换结果、降质提示与错误都会出现在这里。",
        "en": "Results, quality warnings and errors will appear here.",
    },
    "log_added": {"zh": "已加入 {n} 个文件", "en": "Added {n} file(s)"},
    "log_skipped": {"zh": "跳过不支持的文件：{name}", "en": "Skipped unsupported file: {name}"},
    "log_done_item": {"zh": "完成：{name}{page} → {out}", "en": "Done: {name}{page} → {out}"},
    "log_page_suffix": {"zh": "（第 {page} 页）", "en": " (page {page})"},
    "log_failed_item": {"zh": "失败：{name} — {error}", "en": "Failed: {name} — {error}"},
    "log_warning_item": {"zh": "提示：{warning}", "en": "Note: {warning}"},
    "log_summary": {
        "zh": "成功 {ok} 项，失败 {fail} 项，提示 {warn} 条",
        "en": "{ok} succeeded, {fail} failed, {warn} note(s)",
    },
    "log_interrupted": {"zh": "转换中断：{message}", "en": "Conversion aborted: {message}"},
    "log_source_page": {"zh": "{n} 页", "en": "{n} pages"},
    "log_first_page": {"zh": "首页 {w:.0f} × {h:.0f} mm", "en": "page 1 {w:.0f} × {h:.0f} mm"},
    "log_aspect": {"zh": "宽高比 {a:.2f}", "en": "aspect {a:.2f}"},
    "log_no_dpi": {"zh": "无 DPI 信息", "en": "no DPI metadata"},
    "log_vector": {"zh": "矢量图", "en": "vector"},

    # ------------------------------------------------------------------
    # 文件对话框
    # ------------------------------------------------------------------
    "fd_title": {"zh": "选择要转换的文件", "en": "Choose files to convert"},
    "fd_all_supported": {"zh": "所有支持的文件", "en": "All supported files"},
    "fd_documents": {"zh": "文档与矢量图", "en": "Documents & vector"},
    "fd_images": {"zh": "图片", "en": "Images"},
    "fd_all_files": {"zh": "所有文件", "en": "All files"},
    "fd_pick_dir": {"zh": "选择输出目录", "en": "Choose output folder"},

    # ------------------------------------------------------------------
    # 右键菜单
    # ------------------------------------------------------------------
    "ctx_reveal": {"zh": "在文件夹中查看", "en": "Show in Folder"},
}


def current_lang() -> str:
    """返回当前语言代码（zh / en）。"""
    return _lang


_lang: str = "zh"


def set_lang(lang: str) -> None:
    """设置当前语言；未知代码回退中文。"""
    global _lang
    _lang = lang if lang in LANGS else "zh"


def t(key: str, **kwargs) -> str:
    """按 key 取当前语言的文本；缺 key 或缺译文时回退中文，再退到 key 本身。"""
    entry = _STRINGS.get(key)
    if entry is None:
        return key
    text = entry.get(_lang) or entry.get("zh") or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


__all__ = ["LANGS", "current_lang", "set_lang", "t"]
