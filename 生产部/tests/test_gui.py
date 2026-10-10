"""界面层测试（离屏运行）。

验证拖拽入队、勾选/页码编辑、参数解析、宽高比联动、实时预览、
设置弹窗与后台转换的完整闭环。不依赖真实显示器，也不启动 Office。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from conftest import make_pdf, make_raster, read_dpi, read_size  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

# 列布局与 gui.main_window 保持一致
COL_CHECK, COL_INDEX, COL_NAME, COL_PAGES, COL_SOURCE, COL_TARGET, COL_STATUS = range(7)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    """每次拿到一个干净的窗口，配置读写隔离到临时目录。

    注意不能只 patch `QSettings.fileName`：`QSettings("imgspec", "ImageSpecTool")`
    在构造时就已经定了存储位置（Windows 上是注册表），`fileName()` 只是查询。
    真正的情形是：用户跑过软件后设置被写进注册表，测试再读到它，结果别人的设置
    把测试搞红了。所以这里直接替换窗口读取配置的入口，让它落到临时 ini 文件。
    """
    from PySide6.QtCore import QSettings

    import gui.main_window as main_window_module

    settings_file = tmp_path / "settings.ini"

    def isolated_settings(_self):
        return QSettings(str(settings_file), QSettings.Format.IniFormat)

    monkeypatch.setattr(
        main_window_module.MainWindow, "_settings", isolated_settings
    )

    win = main_window_module.MainWindow()
    win.clear_all()
    yield win
    win.close()


def wait_for_conversion(qapp, window, timeout: float = 60.0) -> None:
    """转起事件循环直到后台 worker 收工。"""
    deadline = time.time() + timeout
    while window._worker is not None and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    assert window._worker is None, "转换线程未在超时内结束"


class TestFileQueue:
    def test_add_and_count(self, window, tmp_path):
        make_raster(tmp_path / "a.png", (400, 300))
        make_raster(tmp_path / "b.tif", (400, 300))
        window.add_paths([tmp_path / "a.png", tmp_path / "b.tif"])
        assert window.table.rowCount() == 2
        assert "2" in window.count_label.text()

    def test_new_files_checked_by_default(self, window, tmp_path):
        make_raster(tmp_path / "a.png", (400, 300))
        window.add_paths([tmp_path / "a.png"])
        assert window.table.item(0, COL_CHECK).checkState() == Qt.CheckState.Checked
        assert window.start_btn.isEnabled()

    def test_duplicate_added_once(self, window, tmp_path):
        src = make_raster(tmp_path / "a.png", (400, 300))
        window.add_paths([src, src])
        assert window.table.rowCount() == 1

    def test_unsupported_file_skipped(self, window, tmp_path):
        bad = tmp_path / "drawing.cdr"
        bad.write_bytes(b"RIFFxxxxCDR")
        window.add_paths([bad])
        assert window.table.rowCount() == 0
        assert "跳过" in window.log_view.toPlainText()

    def test_svg_accepted(self, window, tmp_path):
        good = tmp_path / "figure.svg"
        good.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300">'
            '<rect width="400" height="300" fill="#fff"/></svg>',
            encoding="utf-8",
        )
        window.add_paths([good])
        assert window.table.rowCount() == 1
        assert "矢量图" in window.table.item(0, COL_SOURCE).text()

    def test_row_index_renumbered_after_remove(self, window, tmp_path):
        make_raster(tmp_path / "a.png", (100, 100))
        make_raster(tmp_path / "b.png", (100, 100))
        make_raster(tmp_path / "c.png", (100, 100))
        window.add_paths([tmp_path / "a.png", tmp_path / "b.png", tmp_path / "c.png"])
        window.table.selectRow(0)
        window.remove_selected()
        assert [window.table.item(r, COL_INDEX).text() for r in range(2)] == ["1", "2"]

    def test_directory_drop_expands(self, window, tmp_path):
        make_raster(tmp_path / "inner" / "a.png", (200, 200))
        window.add_paths([tmp_path])
        assert window.table.rowCount() == 1


class TestSelection:
    def test_header_toggle_and_partial_state(self, window, tmp_path):
        make_raster(tmp_path / "a.png", (100, 100))
        make_raster(tmp_path / "b.png", (100, 100))
        window.add_paths([tmp_path / "a.png", tmp_path / "b.png"])

        header = window.table.horizontalHeader()
        assert header.check_state == Qt.CheckState.Checked
        assert "（2）" in window.start_btn.text()

        window.table.item(0, COL_CHECK).setCheckState(Qt.CheckState.Unchecked)
        assert header.check_state == Qt.CheckState.PartiallyChecked
        assert "（1）" in window.start_btn.text()
        assert window.start_btn.isEnabled()

        window.table.item(1, COL_CHECK).setCheckState(Qt.CheckState.Unchecked)
        assert header.check_state == Qt.CheckState.Unchecked
        assert not window.start_btn.isEnabled()

        window._toggle_select_all()
        assert header.check_state == Qt.CheckState.Checked
        assert window.start_btn.isEnabled()

    def test_start_blocked_without_checked(self, qapp, window, tmp_path, monkeypatch):
        shown = {}

        def fake_info(*args, **kwargs):
            shown["called"] = True

        monkeypatch.setattr(
            "gui.main_window.QMessageBox.information", fake_info, raising=False
        )
        make_raster(tmp_path / "a.png", (100, 100))
        window.add_paths([tmp_path / "a.png"])
        window.table.item(0, COL_CHECK).setCheckState(Qt.CheckState.Unchecked)
        window._start()
        assert shown.get("called")
        assert window._worker is None


class TestPagesColumn:
    def test_pages_editable_only_for_paged_kinds(self, window, tmp_path):
        make_raster(tmp_path / "a.png", (100, 100))
        make_pdf(tmp_path / "doc.pdf", pages=3)
        window.add_paths([tmp_path / "a.png", tmp_path / "doc.pdf"])

        raster_item = window.table.item(0, COL_PAGES)
        pdf_item = window.table.item(1, COL_PAGES)
        assert raster_item.text() == "—"
        assert not raster_item.flags() & Qt.ItemFlag.ItemIsEditable
        assert pdf_item.text() == "全部"
        assert pdf_item.flags() & Qt.ItemFlag.ItemIsEditable

    def test_page_range_used_in_conversion(self, qapp, window, tmp_path):
        make_pdf(tmp_path / "doc.pdf", pages=4)
        window.add_paths([tmp_path / "doc.pdf"])
        window.phys_width.setText("8.5")
        window.unit_combo.setCurrentIndex(1)
        window.table.item(0, COL_PAGES).setText("2-3")
        window.out_dir = str(tmp_path / "out")

        window._start()
        wait_for_conversion(qapp, window)

        names = sorted(p.name for p in (tmp_path / "out").iterdir())
        assert names == ["doc_p02.tif", "doc_p03.tif"]

    def test_invalid_page_range_restored(self, window, tmp_path):
        make_pdf(tmp_path / "doc.pdf", pages=3)
        window.add_paths([tmp_path / "doc.pdf"])
        item = window.table.item(0, COL_PAGES)
        item.setText("2")
        assert item.text() == "2"
        item.setText("oops")
        assert item.text() == "2"  # 非法输入还原为上一个合法值


class TestAspectLinking:
    def test_width_edit_updates_height(self, window, tmp_path):
        make_raster(tmp_path / "fig.png", (400, 300))  # aspect 1.333
        window.add_paths([tmp_path / "fig.png"])
        window.mode_phys.setChecked(True)
        window.aspect_check.setChecked(True)
        window.phys_width.setText("12")
        window.phys_width.textEdited.emit("12")
        assert window.phys_height.text() == "9.00"

    def test_height_edit_updates_width(self, window, tmp_path):
        make_raster(tmp_path / "fig.png", (400, 300))
        window.add_paths([tmp_path / "fig.png"])
        window.aspect_check.setChecked(True)
        window.phys_height.setText("3.75")
        window.phys_height.textEdited.emit("3.75")
        assert window.phys_width.text() == "5.00"

    def test_pixel_mode_linking(self, window, tmp_path):
        make_raster(tmp_path / "fig.png", (400, 300))
        window.add_paths([tmp_path / "fig.png"])
        window.mode_px.setChecked(True)
        window.aspect_check.setChecked(True)
        window.px_width.setText("1000")
        window.px_width.textEdited.emit("1000")
        assert window.px_height.text() == "750"

    def test_no_linking_when_aspect_disabled(self, window, tmp_path):
        make_raster(tmp_path / "fig.png", (400, 300))
        window.add_paths([tmp_path / "fig.png"])
        window.aspect_check.setChecked(False)
        window.phys_height.setText("")          # 先清空，排除加文件时补齐的值
        window.phys_width.setText("12")
        window.phys_width.textEdited.emit("12")
        assert window.phys_height.text() == ""

    def test_programmatic_set_does_not_link(self, window, tmp_path):
        """程序写入（如恢复上次设置）不应触发联动。"""
        make_raster(tmp_path / "fig.png", (400, 300))
        window.add_paths([tmp_path / "fig.png"])
        window.aspect_check.setChecked(False)
        window.aspect_check.setChecked(True)    # toggled(True) 触发补齐：高 = 8.5/aspect
        window.phys_width.setText("12")         # 程序写入，不得把高联动成 12/aspect
        assert window.phys_height.text() == f"{8.5 / (400 / 300):.2f}"

    def test_missing_side_filled_when_checked(self, window, tmp_path):
        """勾选宽高比后，只有一边有值时另一边应立即按源比例补齐。"""
        make_raster(tmp_path / "fig.png", (400, 300))
        window.add_paths([tmp_path / "fig.png"])
        window.mode_phys.setChecked(True)
        window.aspect_check.setChecked(True)
        window.phys_width.setText("8.5")
        window.phys_height.setText("")
        window._fill_missing_side()
        assert window.phys_height.text() == f"{8.5 / (400 / 300):.2f}"

    def test_missing_px_side_filled(self, window, tmp_path):
        """像素模式下空的一边同样补齐（取整）。"""
        make_raster(tmp_path / "fig.png", (400, 300))
        window.add_paths([tmp_path / "fig.png"])
        window.mode_px.setChecked(True)
        window.aspect_check.setChecked(True)
        window.px_width.setText("800")
        window.px_height.setText("")
        window._fill_missing_side()
        assert window.px_height.text() == "600"

    def test_fill_runs_after_adding_file(self, window, tmp_path):
        """加文件后（默认只填了宽），勾选着宽高比时空的高应被补齐。"""
        make_raster(tmp_path / "fig.png", (400, 300))
        window.add_paths([tmp_path / "fig.png"])
        assert window.phys_height.text() == f"{8.5 / (400 / 300):.2f}"

    def test_fill_widens_when_only_height_given(self, window, tmp_path):
        """只有高有值时，按比例补宽。"""
        make_raster(tmp_path / "fig.png", (400, 300))
        window.add_paths([tmp_path / "fig.png"])
        window.mode_phys.setChecked(True)
        window.aspect_check.setChecked(True)
        window.phys_width.setText("")
        window.phys_height.setText("6")
        window._fill_missing_side()
        assert window.phys_width.text() == f"{6 * (400 / 300):.2f}"

    def test_both_sides_present_not_overwritten(self, window, tmp_path):
        """两边都有值时补齐逻辑不改动任何一侧。"""
        make_raster(tmp_path / "fig.png", (400, 300))
        window.add_paths([tmp_path / "fig.png"])
        window.mode_phys.setChecked(True)
        window.aspect_check.setChecked(True)
        window.phys_width.setText("10")
        window.phys_height.setText("5")
        window._fill_missing_side()
        assert window.phys_width.text() == "10"
        assert window.phys_height.text() == "5"


class TestPerFileSizes:
    """行级尺寸参数：选中行驱动右栏，同批文件可各自设置不同尺寸。"""

    def test_editing_selected_row_only(self, window, tmp_path):
        make_raster(tmp_path / "a.png", (400, 300))
        make_raster(tmp_path / "b.png", (800, 600))
        window.add_paths([tmp_path / "a.png", tmp_path / "b.png"])
        window.table.selectRow(0)
        window.phys_width.setText("10")
        window.phys_width.textEdited.emit("10")

        assert window._size_params[str(tmp_path / "a.png")].phys_width == "10"
        # 未选中的 b 行保持自己加入时的快照，不被波及
        assert window._size_params[str(tmp_path / "b.png")].phys_width == "8.5"

    def test_preview_column_uses_row_params(self, window, tmp_path):
        """输出尺寸列按每行自己的参数解析（10cm -> 1181px，8.5cm -> 1004px）。"""
        make_raster(tmp_path / "a.png", (400, 300))
        make_raster(tmp_path / "b.png", (800, 600))
        window.add_paths([tmp_path / "a.png", tmp_path / "b.png"])
        window.table.selectRow(0)
        window.phys_width.setText("10")
        window.phys_width.textEdited.emit("10")

        assert "1181" in window.table.item(0, COL_TARGET).text()
        assert "1004" in window.table.item(1, COL_TARGET).text()

    def test_multi_select_edits_all_selected(self, window, tmp_path):
        """多选行时修改参数批量应用到全部选中行。"""
        from PySide6.QtCore import QItemSelectionModel

        make_raster(tmp_path / "a.png", (400, 300))
        make_raster(tmp_path / "b.png", (800, 600))
        make_raster(tmp_path / "c.png", (400, 300))
        window.add_paths(
            [tmp_path / "a.png", tmp_path / "b.png", tmp_path / "c.png"]
        )
        window.table.selectRow(0)
        model = window.table.model()
        window.table.selectionModel().select(
            model.index(1, 0),
            QItemSelectionModel.SelectionFlag.Select
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        window.phys_width.setText("5")
        window.phys_width.textEdited.emit("5")

        assert window._size_params[str(tmp_path / "a.png")].phys_width == "5"
        assert window._size_params[str(tmp_path / "b.png")].phys_width == "5"
        # 未选中的 c 行不受影响
        assert window._size_params[str(tmp_path / "c.png")].phys_width == "8.5"

    def test_width_constraint_same_for_all_aspects(self, window, tmp_path):
        """保持宽高比时改宽度：不同比例的源（横图 + 竖版 PDF）输出宽度一致。

        跨比例的统一约束需要多选后批量修改 —— 这里同时验证两条语义。
        """
        from PySide6.QtCore import QItemSelectionModel

        land = make_raster(tmp_path / "land.png", (400, 300))       # aspect 1.333
        doc = make_pdf(tmp_path / "doc.pdf", pages=1)               # aspect 0.773（竖版）
        window.add_paths([doc, land])
        window.table.selectRow(0)
        model = window.table.model()
        window.table.selectionModel().select(
            model.index(1, 0),
            QItemSelectionModel.SelectionFlag.Select
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        window.phys_width.setText("8.5")
        window.phys_width.textEdited.emit("8.5")

        widths = []
        for row in range(2):
            text = window.table.item(row, COL_TARGET).text()
            widths.append(int(text.split(" ")[0]))
        assert widths == [1004, 1004]

    def test_single_row_height_edit_leaves_other_row(self, window, tmp_path):
        """单选行编辑高度：只影响该行（单张个性化），另一行保持自己的约束。"""
        land = make_raster(tmp_path / "land.png", (400, 300))
        doc = make_pdf(tmp_path / "doc.pdf", pages=1)
        window.add_paths([doc, land])
        window.table.selectRow(0)
        window.phys_height.setText("6")
        window.phys_height.textEdited.emit("6")

        row0 = window.table.item(0, COL_TARGET).text()
        row1 = window.table.item(1, COL_TARGET).text()
        assert int(row0.split(" x ")[1].split(" ")[0]) == 709   # 高 6cm @300dpi
        assert row1.startswith("1004 ")                          # 行 1 仍宽约束 8.5cm

    def test_conversion_uses_per_file_size(self, qapp, window, tmp_path):
        """端到端：两行不同宽度，输出像素尺寸各自跟随。"""
        make_raster(tmp_path / "a.png", (400, 300))
        make_raster(tmp_path / "b.png", (400, 300))
        window.add_paths([tmp_path / "a.png", tmp_path / "b.png"])
        window.table.selectRow(0)
        window.phys_width.setText("4")
        window.phys_width.textEdited.emit("4")
        window.table.selectRow(1)
        window.phys_width.setText("2")
        window.phys_width.textEdited.emit("2")
        window.out_dir = str(tmp_path / "out")

        window._start()
        wait_for_conversion(qapp, window)

        a = read_size(tmp_path / "out" / "a.tif")
        b = read_size(tmp_path / "out" / "b.tif")
        assert a[0] == 472  # 4 cm @ 300 dpi
        assert b[0] == 236  # 2 cm @ 300 dpi

    def test_group_title_has_no_hint_suffix(self, window, tmp_path):
        """尺寸组标题只写组名，不带「编辑目标/作用于之后」之类的后缀解释。"""
        from gui import i18n

        make_raster(tmp_path / "fig.png", (400, 300))
        window.add_paths([tmp_path / "fig.png"])
        assert window.size_group.title() == i18n.t("group_size")
        window.table.clearSelection()
        assert window.size_group.title() == i18n.t("group_size")

    def test_pages_single_click_opens_editor(self, window, tmp_path):
        """页码 chip 单击直接进入就地编辑（不是弹出对话框）。"""
        from PySide6.QtWidgets import QAbstractItemView

        make_raster(tmp_path / "a.png", (100, 100))
        make_pdf(tmp_path / "doc.pdf", pages=3)
        window.add_paths([tmp_path / "a.png", tmp_path / "doc.pdf"])

        # 不可编辑的单元格：普通图片行页码 "—"、文件名列 —— 点击不开编辑器
        window.table.cellClicked.emit(0, COL_PAGES)
        assert window.table.state() != QAbstractItemView.State.EditingState
        window.table.cellClicked.emit(0, COL_NAME)
        assert window.table.state() != QAbstractItemView.State.EditingState

        # PDF 行的页码 chip：单击即就地编辑
        window.table.cellClicked.emit(1, COL_PAGES)
        assert window.table.state() == QAbstractItemView.State.EditingState
        window.table.closePersistentEditor(window.table.item(1, COL_PAGES))

    def test_pages_editor_seamless(self, window, tmp_path):
        """就地编辑器与 chip 同形：无框、objectName=pagesEditor、进入即全选。"""
        from PySide6.QtWidgets import QLineEdit, QStyleOptionViewItem

        make_pdf(tmp_path / "doc.pdf", pages=3)
        window.add_paths([tmp_path / "doc.pdf"])
        delegate = window.table.itemDelegateForColumn(COL_PAGES)
        index = window.table.model().index(0, COL_PAGES)

        editor = delegate.createEditor(window.table, QStyleOptionViewItem(), index)
        assert isinstance(editor, QLineEdit)
        assert editor.objectName() == "pagesEditor"
        assert not editor.hasFrame()

        delegate.setEditorData(editor, index)
        assert editor.selectedText() == "全部"  # 进入编辑即全选，输入即替换
        editor.deleteLater()


class TestFormatRows:
    def test_encoding_rows_follow_format(self, window):
        from imgspec.model import OutputFormat

        form = window._format_form
        window.fmt_combo.setCurrentIndex(
            window.fmt_combo.findData(OutputFormat.TIFF.value)
        )
        assert form.isRowVisible(window.row_tiff)
        assert not form.isRowVisible(window.row_jpeg)

        window.fmt_combo.setCurrentIndex(
            window.fmt_combo.findData(OutputFormat.JPEG.value)
        )
        assert not form.isRowVisible(window.row_tiff)
        assert form.isRowVisible(window.row_jpeg)

        window.fmt_combo.setCurrentIndex(
            window.fmt_combo.findData(OutputFormat.PNG.value)
        )
        assert not form.isRowVisible(window.row_tiff)
        assert not form.isRowVisible(window.row_jpeg)


class TestPreview:
    def test_preview_matches_spec(self, window, tmp_path):
        make_raster(tmp_path / "fig.png", (1000, 800))
        window.add_paths([tmp_path / "fig.png"])
        window.mode_phys.setChecked(True)
        window.phys_width.setText("8.5")
        window.phys_height.setText("")
        window.unit_combo.setCurrentIndex(1)  # cm
        window.dpi_combo.setCurrentText("300")
        window._refresh_preview()
        assert "1004 × 803 px" in window.preview_label.text()
        assert "1004 x 803 px" in window.table.item(0, COL_TARGET).text()

    def test_pixel_mode_switches_inputs(self, window, tmp_path):
        window.mode_px.setChecked(True)
        assert window.px_width.isEnabled()
        assert not window.phys_width.isEnabled()
        window.mode_phys.setChecked(True)
        assert window.phys_width.isEnabled()
        assert not window.px_width.isEnabled()

    def test_invalid_spec_shows_error(self, window, tmp_path):
        make_raster(tmp_path / "fig.png", (400, 300))
        window.add_paths([tmp_path / "fig.png"])
        window.mode_phys.setChecked(True)
        window.phys_width.setText("")
        window.phys_height.setText("")
        window._refresh_preview()
        assert "参数待修正" in window.preview_label.text()

    def test_dpi_change_updates_preview(self, window, tmp_path):
        make_raster(tmp_path / "fig.png", (1000, 800))
        window.add_paths([tmp_path / "fig.png"])
        window.phys_width.setText("8.5")
        window.dpi_combo.setCurrentText("300")
        window._refresh_preview()
        assert "1004 × 803 px" in window.preview_label.text()
        window.dpi_combo.setCurrentText("600")
        window._refresh_preview()
        assert "2008 × 1606 px" in window.preview_label.text()


class TestOutputNaming:
    """「输出与命名」组回归右栏后的读写与规格传递。"""

    def test_output_settings_read_write(self, window, tmp_path):
        from imgspec.model import ConflictPolicy

        window.out_dir = str(tmp_path / "out")
        window.name_template = "{stem}_x"
        window.conflict_policy = ConflictPolicy.SKIP
        window.max_mb = "2"
        window.lossy_fallback = False

        assert window.out_dir == str(tmp_path / "out")
        assert window.name_template == "{stem}_x"
        assert window.conflict_policy is ConflictPolicy.SKIP
        assert window.max_mb == "2"
        assert window.lossy_fallback is False

    def test_blank_name_template_falls_back_to_stem(self, window):
        window.name_template = "   "
        assert window.name_template == "{stem}"
        assert window.name_edit.text() == "{stem}"

    def test_settings_reach_spec(self, window, tmp_path):
        from imgspec.model import ConflictPolicy

        window.out_dir = str(tmp_path / "out")
        window.max_mb = "2.5"
        window.conflict_policy = ConflictPolicy.SKIP
        window.lossy_fallback = False

        spec = window._build_spec()
        assert spec.max_bytes == int(2.5 * 1024 * 1024)
        assert spec.output_dir == tmp_path / "out"
        assert spec.on_conflict is ConflictPolicy.SKIP
        assert spec.lossy_fallback is False

    def test_settings_persist_across_restart(self, qapp, window, tmp_path, monkeypatch):
        """设置写回 QSettings 后，新窗口应读回相同值。"""
        import gui.main_window as main_window_module

        window.out_dir = str(tmp_path / "out")
        window.max_mb = "3"
        window._save_settings()

        win2 = main_window_module.MainWindow()
        try:
            assert win2.out_dir == str(tmp_path / "out")
            assert win2.max_mb == "3"
        finally:
            win2.close()


class TestConversionFlow:
    def test_raster_end_to_end(self, qapp, window, tmp_path):
        make_raster(tmp_path / "fig.png", (1000, 800))
        window.add_paths([tmp_path / "fig.png"])
        window.mode_phys.setChecked(True)
        window.phys_width.setText("8.5")
        window.phys_height.setText("")
        window.unit_combo.setCurrentIndex(1)
        window.dpi_combo.setCurrentText("300")
        window.out_dir = str(tmp_path / "out")

        window._start()
        wait_for_conversion(qapp, window)

        produced = tmp_path / "out" / "fig.tif"
        assert produced.exists()
        assert read_size(produced) == (1004, 803)
        assert read_dpi(produced) == (300.0, 300.0)
        assert window.table.item(0, COL_STATUS).text() == "完成"
        assert "成功 1 项" in window.status_label.text()
        assert window.open_dir_btn.isEnabled()

    def test_missing_size_blocks_start(self, qapp, window, tmp_path, monkeypatch):
        """参数不合法时应弹窗提示而不是启动线程。"""
        shown = {}

        def fake_warning(*args, **kwargs):
            shown["called"] = True

        monkeypatch.setattr(
            "gui.main_window.QMessageBox.warning", fake_warning, raising=False
        )
        make_raster(tmp_path / "fig.png", (400, 300))
        window.add_paths([tmp_path / "fig.png"])
        window.phys_width.setText("")
        window.phys_height.setText("")
        window._start()
        assert shown.get("called")
        assert window._worker is None

    def test_failure_is_reported_not_crashed(self, qapp, window, tmp_path):
        """坏文件应记成失败项，界面保持可用。"""
        bad = tmp_path / "fake.png"
        bad.write_bytes(b"not a png")
        window.add_paths([bad])
        window.phys_width.setText("5")
        window.out_dir = str(tmp_path / "out")
        window._start()
        wait_for_conversion(qapp, window)
        assert "失败 1 项" in window.status_label.text()
        assert window.start_btn.isEnabled()


class TestStatusAggregation:
    def test_same_name_in_different_folders_keeps_separate_status(self, qapp, window, tmp_path):
        """不同目录下的同名文件必须各显示各的状态，不能互相覆盖。"""
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        make_raster(tmp_path / "a" / "fig.png", (400, 300))
        (tmp_path / "b" / "fig.png").write_bytes(b"not a png")  # 同名，但损坏

        window.add_paths([tmp_path / "a" / "fig.png", tmp_path / "b" / "fig.png"])
        window.phys_width.setText("5")
        window.out_dir = str(tmp_path / "out")
        window._start()
        wait_for_conversion(qapp, window)

        statuses = [window.table.item(row, COL_STATUS).text() for row in range(2)]
        assert sorted(statuses) == ["失败", "完成"], statuses

    def test_partial_failure_is_labeled(self, qapp, window, tmp_path):
        """多页源里只有部分页成功时，状态不能显示成「完成」。"""
        from imgspec.model import ConflictPolicy

        src = make_pdf(tmp_path / "doc.pdf", pages=3)
        out = tmp_path / "out"
        out.mkdir()
        # 占住第 2 页的输出名，让它在「跳过」策略下失败
        (out / "doc_p02.tif").write_bytes(b"occupied")

        window.add_paths([src])
        window.phys_width.setText("5")
        window.out_dir = str(out)
        window.conflict_policy = ConflictPolicy.SKIP
        window._start()
        wait_for_conversion(qapp, window)

        assert window.table.item(0, COL_STATUS).text() == "部分失败"
        assert "失败 1 项" in window.status_label.text()


class TestLanguage:
    def test_lang_combo_retranslates_in_place(self, qapp, window):
        """切换语言后文案就地刷新：同一窗口对象、无重建、无闪烁。"""
        from gui import i18n

        try:
            assert window.lang_combo.currentText() == "中文"
            assert window.add_btn.text() == "添加文件"
            index_en = window.lang_combo.findData("en")
            assert index_en >= 0
            window.lang_combo.setCurrentIndex(index_en)
            assert i18n.current_lang() == "en"
            assert window.add_btn.text() == "Add Files"          # 就地刷新
            assert window.remove_btn.text() == "Remove Selected"
            assert window.files_card_title.text() == "Files"
            assert "Output Format" in window.format_group.title()
            # 表单行标签与占位符同样跟随
            assert window.phys_height.placeholderText() == "Empty = follow source aspect"
        finally:
            i18n.set_lang("zh")

    def test_lang_combo_blocked_while_busy(self, qapp, window, monkeypatch):
        """转换进行中不允许切换语言，下拉框回弹到当前语言。"""
        from gui import i18n

        shown = {}

        def fake_info(*args, **kwargs):
            shown["called"] = True

        monkeypatch.setattr(
            "gui.main_window.QMessageBox.information", fake_info, raising=False
        )
        window._busy = True
        try:
            window.lang_combo.setCurrentIndex(window.lang_combo.findData("en"))
            assert shown.get("called")
            assert i18n.current_lang() == "zh"
            assert window.lang_combo.currentText() == "中文"
        finally:
            window._busy = False
            i18n.set_lang("zh")

    def test_theme_combo_switches_theme(self, qapp, window):
        from gui import theme as theme_module

        try:
            window.theme_combo.setCurrentIndex(window.theme_combo.findData("dark"))
            assert theme_module.current_theme().name == "dark"
        finally:
            window.theme_combo.setCurrentIndex(window.theme_combo.findData("light"))
            assert theme_module.current_theme().name == "light"

    def test_language_round_trip_same_window(self, qapp, monkeypatch, tmp_path):
        """回归：连续两次切换语言都必须生效，且始终是同一个窗口对象。

        旧实现靠关旧窗开新窗换语言（有闪烁，还曾因回调未重新注入导致第二次
        切换死锁）；现在是就地重译，同一窗口往返切换文案都必须跟上。
        """
        import gui.main_window as main_window_module
        from gui import i18n
        from gui.main_window import MainWindow

        from PySide6.QtCore import QSettings

        settings_file = tmp_path / "lang_roundtrip.ini"
        monkeypatch.setattr(
            main_window_module.MainWindow,
            "_settings",
            lambda _self: QSettings(str(settings_file), QSettings.Format.IniFormat),
        )

        win = MainWindow()
        try:
            win.lang_combo.setCurrentIndex(win.lang_combo.findData("en"))
            assert i18n.current_lang() == "en"
            assert win.add_btn.text() == "Add Files"

            win.lang_combo.setCurrentIndex(win.lang_combo.findData("zh"))
            assert i18n.current_lang() == "zh"
            assert win.add_btn.text() == "添加文件"
            assert win.lang_combo.currentText() == "中文"
            assert win.files_card_title.text() == "待处理文件"
        finally:
            win.close()
            i18n.set_lang("zh")

    def test_retranslate_preserves_row_state(self, qapp, window, tmp_path):
        """语言切换不丢数据：页码表达式、状态、勾选都原样保留。"""
        from gui import i18n

        make_pdf(tmp_path / "doc.pdf", pages=4)
        window.add_paths([tmp_path / "doc.pdf"])
        item = window.table.item(0, COL_PAGES)
        item.setText("2-3")
        assert item.text() == "2-3"

        try:
            window.lang_combo.setCurrentIndex(window.lang_combo.findData("en"))
            assert window.table.item(0, COL_PAGES).text() == "2-3"   # 表达式保留
            assert window.table.item(0, COL_STATUS).text() == "Pending"
            item.setText("")                                          # 清空 → All
            assert window.table.item(0, COL_PAGES).text() == "All"

            window.lang_combo.setCurrentIndex(window.lang_combo.findData("zh"))
            assert window.table.item(0, COL_PAGES).text() == "全部"
            assert window.table.item(0, COL_STATUS).text() == "待处理"
            assert window.table.item(0, COL_CHECK).checkState() == Qt.CheckState.Checked
        finally:
            i18n.set_lang("zh")

    def test_strip_palette_follows_theme(self, qapp):
        """规格条配色跟随主题：浅色下不再是一整条黑块。"""
        from gui import theme as theme_module

        theme_module.set_theme("light")
        bg, ink, _soft = theme_module.strip_palette()
        assert bg == theme_module.LIGHT.accent_wash
        assert ink == theme_module.LIGHT.text_primary

        theme_module.set_theme("dark")
        bg, ink, _soft = theme_module.strip_palette()
        assert bg == theme_module.DARK.strip
        assert ink == theme_module.DARK.strip_ink
        theme_module.set_theme("light")

    def test_log_recolored_on_theme_switch(self, qapp, window, tmp_path):
        """深色下写入的日志，切到浅色后必须按浅色重染（不残留近白文字）。"""
        from gui import theme as theme_module

        make_raster(tmp_path / "a.png", (100, 100))
        window.theme_combo.setCurrentIndex(window.theme_combo.findData("dark"))
        window.add_paths([tmp_path / "a.png"])
        dark_ink = theme_module.DARK.text_primary
        assert dark_ink in window.log_view.document().toHtml()

        window.theme_combo.setCurrentIndex(window.theme_combo.findData("light"))
        html_now = window.log_view.document().toHtml()
        light_ink = theme_module.LIGHT.text_primary
        assert light_ink in html_now
        assert dark_ink not in html_now  # 旧主题色已重染

    def test_theme_switch_preserves_log_content(self, qapp, window, tmp_path):
        from gui import theme as theme_module  # noqa: F401

        make_raster(tmp_path / "a.png", (100, 100))
        window.add_paths([tmp_path / "a.png"])
        before = window.log_view.toPlainText()
        window.theme_combo.setCurrentIndex(window.theme_combo.findData("dark"))
        assert window.log_view.toPlainText() == before
        window.theme_combo.setCurrentIndex(window.theme_combo.findData("light"))


class TestThemeRestoreAndFades:
    """主题恢复与渐隐遮罩（2026-10-09 R7 白边 bug 回归）。

    白边根因：启动时 _load_settings 恢复保存主题会屏蔽开关信号，
    当年只有手动切换的回调里才刷新渐隐遮罩颜色 —— 「系统浅色启动
    + 设置里保存深色」时遮罩滞留近白色，深色界面上下出现白带。
    现在遮罩绘制时实时取当前主题色，恢复与切换共用 _apply_theme。
    """

    @pytest.fixture
    def dark_restored_window(self, qapp, tmp_path, monkeypatch):
        """模拟「系统浅色启动、设置里保存了深色」的窗口。"""
        from PySide6.QtCore import QSettings

        import gui.main_window as main_window_module
        from gui import theme as theme_module

        settings_file = tmp_path / "settings.ini"
        seed = QSettings(str(settings_file), QSettings.Format.IniFormat)
        seed.setValue("theme", "dark")
        seed.setValue("lang", "zh")
        seed.sync()

        def isolated_settings(_self):
            return QSettings(str(settings_file), QSettings.Format.IniFormat)

        monkeypatch.setattr(
            main_window_module.MainWindow, "_settings", isolated_settings
        )
        theme_module.set_theme("light")  # 模拟系统浅色

        win = main_window_module.MainWindow()
        yield win
        win.close()
        # 还原全局状态，避免污染同模块后续测试
        theme_module.set_theme("light")
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme_module.build_stylesheet("light"))

    def test_restored_dark_theme_applied(self, dark_restored_window):
        """保存的深色主题在启动恢复后必须全局生效。"""
        from gui import theme as theme_module

        assert theme_module.current_theme().name == "dark"
        combo_data = dark_restored_window.theme_combo.currentData()
        assert combo_data == "dark"

    def test_fade_color_follows_restored_theme(self, qapp, dark_restored_window):
        """渐隐遮罩边缘必须与深色窗口底色一致（白色残留即回归）。"""
        from gui import theme as theme_module

        qapp.processEvents()
        expect = theme_module.DARK.bg_primary  # #17191d
        er, eg, eb = (
            int(expect[1:3], 16),
            int(expect[3:5], 16),
            int(expect[5:7], 16),
        )
        for fade in (dark_restored_window._fade_top, dark_restored_window._fade_bottom):
            color = fade.grab().toImage().pixelColor(5, 1)
            assert abs(color.red() - er) < 15
            assert abs(color.green() - eg) < 15
            assert abs(color.blue() - eb) < 15

    def test_group_title_bold_rule(self, qapp):
        """分组标题加粗必须写在 QGroupBox 本体规则上。

        Qt 对 ::title 子控件的 font-weight 支持不完整（6.11 实测被
        静默忽略），写回 ::title 会退回「看起来没加粗」的状态。
        渲染效果由离屏探针截图人工核验；此处守住 QSS 结构不回退。
        （注：QSS 字体不会回写 widget.font()，无法用 QFontInfo 断言。）
        """
        from gui import theme as theme_module

        for name in ("light", "dark"):
            qss = theme_module.build_stylesheet(name)
            start = qss.index("QGroupBox {")
            block = qss[start:qss.index("}", start)]
            assert "font-weight: 700" in block
            assert "font-size:" in block


class TestPillToggle:
    """胶囊开关：QComboBox 兼容 API 与信号行为。"""

    def test_api_compat(self, qapp):
        from gui.widgets import PillToggle

        toggle = PillToggle()
        toggle.addItem("中文", "zh")
        toggle.addItem("EN", "en")
        assert toggle.count() == 2
        assert toggle.currentIndex() == 0
        assert toggle.currentData() == "zh"
        assert toggle.currentText() == "中文"
        assert toggle.findData("en") == 1
        assert toggle.findData("fr") == -1

        seen = []
        toggle.currentIndexChanged.connect(seen.append)
        toggle.setCurrentIndex(1)
        assert toggle.currentData() == "en"
        assert seen == [1]

        toggle.setItemText(1, "English")
        assert toggle.currentText() == "English"

    def test_blocked_signals_silent(self, qapp):
        from gui.widgets import PillToggle

        toggle = PillToggle()
        toggle.addItem("浅色", "light")
        toggle.addItem("深色", "dark")
        seen = []
        toggle.currentIndexChanged.connect(seen.append)
        toggle.blockSignals(True)
        toggle.setCurrentIndex(1)
        toggle.blockSignals(False)
        assert seen == []
        assert toggle.currentData() == "dark"


class TestCheckSelectSync:
    """勾选 → 选中 单向联动与移除回退（勾了就能操作）。"""

    def test_checking_row_selects_it(self, window, tmp_path):
        make_raster(tmp_path / "a.png", (100, 100))
        make_raster(tmp_path / "b.png", (100, 100))
        window.add_paths([tmp_path / "a.png", tmp_path / "b.png"])

        window.table.item(1, COL_CHECK).setCheckState(Qt.CheckState.Unchecked)
        window.table.clearSelection()
        window.table.item(1, COL_CHECK).setCheckState(Qt.CheckState.Checked)
        selected = {index.row() for index in window.table.selectedIndexes()}
        assert 1 in selected

    def test_unchecking_row_deselects_it(self, window, tmp_path):
        make_raster(tmp_path / "a.png", (100, 100))
        window.add_paths([tmp_path / "a.png"])
        assert 0 in {index.row() for index in window.table.selectedIndexes()}

        window.table.item(0, COL_CHECK).setCheckState(Qt.CheckState.Unchecked)
        assert not window.table.selectedIndexes()

    def test_selecting_does_not_change_check(self, window, tmp_path):
        """反向不联动：点击选中不改变勾选状态（浏览不等于纳入转换）。"""
        make_raster(tmp_path / "a.png", (100, 100))
        make_raster(tmp_path / "b.png", (100, 100))
        window.add_paths([tmp_path / "a.png", tmp_path / "b.png"])
        window.table.item(1, COL_CHECK).setCheckState(Qt.CheckState.Unchecked)

        window.table.selectRow(1)
        assert window.table.item(1, COL_CHECK).checkState() == Qt.CheckState.Unchecked
        assert window.table.item(0, COL_CHECK).checkState() == Qt.CheckState.Checked

    def test_header_toggle_syncs_selection(self, window, tmp_path):
        make_raster(tmp_path / "a.png", (100, 100))
        make_raster(tmp_path / "b.png", (100, 100))
        window.add_paths([tmp_path / "a.png", tmp_path / "b.png"])

        window._toggle_select_all()   # 全不选
        assert not window.table.selectedIndexes()
        window._toggle_select_all()   # 全选
        assert len({index.row() for index in window.table.selectedIndexes()}) == 2

    def test_remove_falls_back_to_checked(self, window, tmp_path):
        """无选中时「移除选中」移除已勾选的行。"""
        make_raster(tmp_path / "a.png", (100, 100))
        make_raster(tmp_path / "b.png", (100, 100))
        window.add_paths([tmp_path / "a.png", tmp_path / "b.png"])

        window.table.item(1, COL_CHECK).setCheckState(Qt.CheckState.Unchecked)
        window.table.clearSelection()
        window.remove_selected()
        assert window.table.rowCount() == 1
        assert window.table.item(0, COL_NAME).text() == "b.png"

    def test_pages_column_has_editable_delegate(self, window):
        from gui.widgets import EditableChipDelegate

        assert isinstance(
            window.table.itemDelegateForColumn(COL_PAGES), EditableChipDelegate
        )


class TestCloseDuringConversion:
    def test_closing_while_running_is_safe(self, qapp, window, tmp_path):
        """转换过程中关窗，不能让 QThread 在运行中被销毁。"""
        for index in range(2):
            make_raster(tmp_path / f"f{index}.png", (1200, 900))
        window.add_paths(sorted(tmp_path.glob("*.png")))
        window.phys_width.setText("30")     # 放大到 30 cm，明显更慢
        window.dpi_combo.setCurrentText("600")
        window.out_dir = str(tmp_path / "out")

        window._start()
        assert window._thread is not None

        window.close()                      # 立刻关窗，线程多半还在跑
        assert not window.isVisible()

        # 线程最终应自行收工，不留悬挂线程
        deadline = time.time() + 30
        while time.time() < deadline:
            qapp.processEvents()
            thread = window._thread
            if thread is None or not thread.isRunning():
                break
            time.sleep(0.02)
        qapp.processEvents()


class TestMaintenanceFeatures:
    """维护功能：日志导出、反馈入口、检查更新（测试不触网）。"""

    def test_log_export_writes_environment_and_records(self, window, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QFileDialog

        target = tmp_path / "session-log.txt"
        monkeypatch.setattr(
            QFileDialog,
            "getSaveFileName",
            staticmethod(lambda *args, **kwargs: (str(target), "*.txt")),
        )

        window._append_log_raw("测试日志行", "info")
        window.export_log()

        text = target.read_text(encoding="utf-8")
        assert "图片转换器 · 转换日志" in text
        assert "版本：" in text  # 头部环境信息，供 Issue 附言
        assert "[INFO] 测试日志行" in text

    def test_log_export_cancelled_writes_nothing(self, window, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QFileDialog

        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", ""))
        )
        window.export_log()  # 取消不应抛异常
        assert not list(tmp_path.glob("*.txt"))

    def test_maintenance_buttons_present(self, window):
        assert window.export_log_btn.isEnabled()
        assert window.feedback_btn.isEnabled()
        assert window.update_btn.isEnabled()
        assert window.auto_check_action.isCheckable()
        assert window.export_log_btn.toolTip()

    def test_feedback_opens_issues_page(self, window, monkeypatch):
        opened: list[str] = []
        # 走窗口自己的 _open_url（QDesktopServices.openUrl 是 C++ 静态方法，
        # patch 不掉，漏掉就会真的拉起浏览器）
        monkeypatch.setattr(window, "_open_url", lambda url: opened.append(url))
        window.open_feedback()
        assert opened and "issues/new/choose" in opened[0]

    def test_toast_actions_reuse_open_url(self, window, monkeypatch):
        """导出日志/发现新版时，Toast 上的动作也走 _open_url（不真开浏览器）。"""
        from PySide6.QtWidgets import QFileDialog

        opened: list[str] = []
        monkeypatch.setattr(window, "_open_url", lambda url: opened.append(url))

        target = Path(window._settings().fileName()).parent / "exported.txt"
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "*.txt"))
        )
        window.export_log()
        assert not window.toast.isHidden()  # 窗口未 show，isVisible() 恒为 False
        window.toast._invoke_action()  # 触发「打开所在文件夹」
        assert opened and opened[0].startswith("file:")

        window._on_update_checked({"tag": "v9.9.9", "url": "https://example.com/rel"}, manual=True)
        window.toast._invoke_action()  # 触发「打开下载页」
        assert opened[-1] == "https://example.com/rel"

    def test_retranslate_updates_maintenance_labels(self, window):
        from gui import i18n

        i18n.set_lang("en")
        try:
            window.retranslate_ui()
            assert window.export_log_btn.text() == i18n.t("btn_export_log")
            assert window.feedback_btn.text() == i18n.t("btn_feedback")
            assert window.update_btn.text() == i18n.t("btn_check_update")
            assert window.auto_check_action.text() == i18n.t("update_auto_check")
        finally:
            i18n.set_lang("zh")
            window.retranslate_ui()

    def test_auto_check_default_on_and_persists(self, qapp, window):
        import gui.main_window as main_window_module

        assert window.auto_check_action.isChecked() is True  # 默认开启

        window.auto_check_action.setChecked(False)  # 立即写回设置
        assert window._settings().value("update_check_on_start", True, type=bool) is False

        win2 = main_window_module.MainWindow()
        try:
            assert win2.auto_check_action.isChecked() is False
        finally:
            win2.close()

    def test_auto_check_skipped_when_disabled(self, window, monkeypatch):
        called: list[bool] = []
        monkeypatch.setattr(window, "check_updates", lambda manual=False: called.append(manual))
        window.auto_check_action.setChecked(False)
        window.check_updates_on_start()
        assert called == []

        window.auto_check_action.setChecked(True)
        window.check_updates_on_start()
        assert called == [False]  # 静默（manual=False）

    def test_update_result_newer_logs_and_toasts(self, window):
        window._on_update_checked(
            {"tag": "v9.9.9", "url": "https://example.com/rel"}, manual=True
        )
        assert any("9.9.9" in message for _s, _l, message in window._log_records)
        assert "9.9.9" in window.toast._title.text()

    def test_update_result_failure_is_silent_on_auto_check(self, window):
        before = len(window._log_records)
        window._on_update_checked(None, manual=False)  # 自动检查失败不打扰
        assert len(window._log_records) == before
        assert window.toast.isHidden()

    def test_update_result_failure_reports_on_manual_check(self, window):
        from gui import i18n

        before = len(window._log_records)
        window._on_update_checked(None, manual=True)
        assert len(window._log_records) == before + 1
        assert window.toast._title.text() == i18n.t("update_failed_title")
        assert not window.toast.isHidden()
