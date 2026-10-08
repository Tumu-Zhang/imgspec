"""界面层测试（离屏运行）。

验证拖拽入队、参数解析、实时预览与后台转换的完整闭环，
不依赖真实显示器，也不启动 Office。
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from conftest import make_pdf, make_raster, read_dpi, read_size  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


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
        assert "2 个文件" in window.count_label.text()

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
        assert window.table.item(0, 1).text() == "矢量图"

    def test_remove_selected(self, window, tmp_path):
        make_raster(tmp_path / "a.png", (100, 100))
        make_raster(tmp_path / "b.png", (100, 100))
        window.add_paths([tmp_path / "a.png", tmp_path / "b.png"])
        window.table.selectRow(0)
        window.remove_selected()
        assert window.table.rowCount() == 1

    def test_directory_drop_expands(self, window, tmp_path):
        make_raster(tmp_path / "inner" / "a.png", (200, 200))
        window.add_paths([tmp_path])
        assert window.table.rowCount() == 1


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
        assert "1004 x 803 px" in window.table.item(0, 3).text()

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


class TestConversionFlow:
    def test_raster_end_to_end(self, qapp, window, tmp_path):
        make_raster(tmp_path / "fig.png", (1000, 800))
        window.add_paths([tmp_path / "fig.png"])
        window.mode_phys.setChecked(True)
        window.phys_width.setText("8.5")
        window.phys_height.setText("")
        window.unit_combo.setCurrentIndex(1)
        window.dpi_combo.setCurrentText("300")
        window.out_edit.setText(str(tmp_path / "out"))

        window._start()
        wait_for_conversion(qapp, window)

        produced = tmp_path / "out" / "fig.tif"
        assert produced.exists()
        assert read_size(produced) == (1004, 803)
        assert read_dpi(produced) == (300.0, 300.0)
        assert window.table.item(0, 4).text() == "完成"
        assert "成功 1 项" in window.status_label.text()
        assert window.open_dir_btn.isEnabled()

    def test_pdf_page_range_end_to_end(self, qapp, window, tmp_path):
        make_pdf(tmp_path / "doc.pdf", pages=4)
        window.add_paths([tmp_path / "doc.pdf"])
        window.phys_width.setText("8.5")
        window.unit_combo.setCurrentIndex(1)
        window.pages_edit.setText("2-3")
        window.out_edit.setText(str(tmp_path / "out"))

        window._start()
        wait_for_conversion(qapp, window)

        names = sorted(p.name for p in (tmp_path / "out").iterdir())
        assert names == ["doc_p02.tif", "doc_p03.tif"]

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
        window.out_edit.setText(str(tmp_path / "out"))
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
        window.out_edit.setText(str(tmp_path / "out"))
        window._start()
        wait_for_conversion(qapp, window)

        statuses = [window.table.item(row, 4).text() for row in range(2)]
        assert sorted(statuses) == ["失败", "完成"], statuses

    def test_partial_failure_is_labeled(self, qapp, window, tmp_path):
        """多页源里只有部分页成功时，状态不能显示成「完成」。"""
        src = make_pdf(tmp_path / "doc.pdf", pages=3)
        out = tmp_path / "out"
        out.mkdir()
        # 占住第 2 页的输出名，让它在「跳过」策略下失败
        (out / "doc_p02.tif").write_bytes(b"occupied")

        window.add_paths([src])
        window.phys_width.setText("5")
        window.out_edit.setText(str(out))
        window.conflict_combo.setCurrentIndex(window.conflict_combo.findData("skip"))
        window._start()
        wait_for_conversion(qapp, window)

        assert window.table.item(0, 4).text() == "部分失败"
        assert "失败 1 项" in window.status_label.text()


class TestCloseDuringConversion:
    def test_closing_while_running_is_safe(self, qapp, window, tmp_path):
        """转换过程中关窗，不能让 QThread 在运行中被销毁。"""
        for index in range(2):
            make_raster(tmp_path / f"f{index}.png", (1200, 900))
        window.add_paths(sorted(tmp_path.glob("*.png")))
        window.phys_width.setText("30")     # 放大到 30 cm，明显更慢
        window.dpi_combo.setCurrentText("600")
        window.out_edit.setText(str(tmp_path / "out"))

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
