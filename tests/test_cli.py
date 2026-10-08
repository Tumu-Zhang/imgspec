"""命令行入口测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_pdf, make_raster, read_size
from imgspec.cli import main


class TestDryRun:
    def test_previews_without_writing(self, tmp_path, capsys):
        src = make_raster(tmp_path / "fig.png", (1000, 800))
        code = main([str(src), "--width", "8.5", "--unit", "cm", "--dpi", "300", "--dry-run"])
        out = capsys.readouterr().out
        assert code == 0
        assert "1004 x 803 px @ 300 dpi" in out
        assert not (tmp_path / "converted").exists()

    def test_reports_missing_file(self, tmp_path, capsys):
        code = main([str(tmp_path / "nope.png"), "--width", "5", "--dry-run"])
        assert code == 1
        assert "文件不存在" in capsys.readouterr().out


class TestConversion:
    def test_basic_conversion(self, tmp_path, capsys):
        src = make_raster(tmp_path / "fig.png", (1000, 800))
        out_dir = tmp_path / "out"
        code = main([
            str(src), "--width", "8.5", "--unit", "cm", "--dpi", "300",
            "--format", "tiff", "--out", str(out_dir),
        ])
        assert code == 0
        produced = out_dir / "fig.tif"
        assert produced.exists()
        assert read_size(produced) == (1004, 803)
        assert "完成" in capsys.readouterr().out

    def test_pixel_mode(self, tmp_path, capsys):
        src = make_raster(tmp_path / "fig.png", (800, 600))
        out_dir = tmp_path / "out"
        code = main([
            str(src), "--px-width", "1200", "--px-height", "900", "--dpi", "600",
            "--out", str(out_dir),
        ])
        assert code == 0
        assert read_size(out_dir / "fig.tif") == (1200, 900)

    def test_directory_input_expands(self, tmp_path, capsys):
        make_raster(tmp_path / "a.png", (400, 300))
        make_raster(tmp_path / "sub" / "b.png", (400, 300))
        (tmp_path / "ignore.txt").write_text("x", encoding="utf-8")
        out_dir = tmp_path / "out"
        code = main([str(tmp_path), "--width", "5", "--out", str(out_dir)])
        assert code == 0
        assert (out_dir / "a.tif").exists()
        assert (out_dir / "b.tif").exists()

    def test_page_selection(self, tmp_path, capsys):
        src = make_pdf(tmp_path / "doc.pdf", pages=4)
        out_dir = tmp_path / "out"
        code = main([str(src), "--width", "8.5", "--pages", "2-3", "--out", str(out_dir)])
        assert code == 0
        assert sorted(p.name for p in out_dir.iterdir()) == ["doc_p02.tif", "doc_p03.tif"]

    def test_volume_limit_reported(self, tmp_path, capsys):
        src = make_raster(tmp_path / "fig.png", (600, 400))
        out_dir = tmp_path / "out"
        code = main([
            str(src), "--width", "8.5", "--dpi", "300", "--limit", "20",
            "--out", str(out_dir),
        ])
        assert code == 0
        assert (out_dir / "fig.tif").stat().st_size <= 20 * 1024 * 1024

    def test_quiet_suppresses_per_item_lines(self, tmp_path, capsys):
        src = make_raster(tmp_path / "fig.png", (400, 300))
        code = main([str(src), "--width", "5", "--out", str(tmp_path / "o"), "--quiet"])
        out = capsys.readouterr().out
        assert code == 0
        assert "✓" not in out


class TestArgumentErrors:
    def test_missing_size(self, capsys):
        code = main(["whatever.png"])
        assert code == 1
        assert "参数错误" in capsys.readouterr().err

    def test_bad_unit(self, capsys):
        # argparse 对非法的枚举取值会直接 SystemExit(2)
        with pytest.raises(SystemExit) as exc:
            main(["a.png", "--width", "5", "--unit", "furlong"])
        assert exc.value.code == 2

    def test_no_inputs(self):
        with pytest.raises(SystemExit) as exc:
            main(["--width", "5"])
        assert exc.value.code == 2

    def test_no_upscale_flag(self, tmp_path, capsys):
        src = make_raster(tmp_path / "small.png", (300, 200))
        out_dir = tmp_path / "out"
        code = main([
            str(src), "--px-width", "2000", "--no-upscale", "--out", str(out_dir),
        ])
        assert code == 0
        assert read_size(out_dir / "small.tif") == (300, 200)
        assert "未放大" in capsys.readouterr().out


class TestSlidesError:
    def test_missing_powerpoint_is_reported(self, tmp_path, capsys, monkeypatch):
        """幻灯片缺 PowerPoint 时应报出可读错误，而不是静默跳过或崩溃。"""
        from imgspec import office

        def unavailable(*_args, **_kwargs):
            raise office.OfficeUnavailableError(
                "未检测到可用的 PowerPoint：请确认已安装 Microsoft PowerPoint"
            )

        monkeypatch.setattr(office, "PowerPointSession", unavailable)
        path: Path = tmp_path / "deck.ppt"
        path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1stub")

        code = main([str(path), "--width", "8.5", "--out", str(tmp_path / "o")])
        out = capsys.readouterr().out
        assert code == 1
        assert "PowerPoint" in out
        assert "失败 1 项" in out
