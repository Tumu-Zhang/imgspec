"""端到端管线测试：从源文件到最终落盘图片的完整链路。"""

from __future__ import annotations

import pytest
from PIL import Image

from conftest import (
    PNG_DPI_TOLERANCE,
    TIFF_LZW,
    make_pdf,
    make_raster,
    read_dpi,
    read_size,
    read_tiff_compression,
)
from imgspec.model import (
    ConflictPolicy,
    OutputFormat,
    OutputSpec,
    SizeMode,
    TiffCompression,
    Unit,
)
from imgspec.pipeline import convert, default_output_dir


def base_spec(out_dir, **kwargs) -> OutputSpec:
    params = dict(
        fmt=OutputFormat.TIFF,
        size_mode=SizeMode.PHYSICAL,
        phys_unit=Unit.CM,
        phys_width=8.5,
        dpi=300,
        output_dir=out_dir,
    )
    params.update(kwargs)
    return OutputSpec(**params)


class TestRasterConversion:
    def test_png_to_tiff_physical_size(self, tmp_path, workdir):
        src = make_raster(tmp_path / "fig1.png", (1000, 800))
        report = convert([src], base_spec(workdir))
        assert report.success, [r.error for r in report.results]

        out = report.results[0].out_path
        assert out.suffix == ".tif"
        assert read_size(out) == (1004, 803)
        assert read_dpi(out) == (300.0, 300.0)

    def test_tiff_is_lzw_compressed(self, tmp_path, workdir):
        src = make_raster(tmp_path / "fig.png", (600, 400))
        report = convert([src], base_spec(workdir, tiff_compression=TiffCompression.LZW))
        assert read_tiff_compression(report.results[0].out_path) == TIFF_LZW

    def test_pixel_mode_produces_exact_size(self, tmp_path, workdir):
        """源比例与目标框一致时，像素模式给出精确尺寸。"""
        src = make_raster(tmp_path / "fig.png", (800, 600))
        spec = base_spec(
            workdir, size_mode=SizeMode.PIXELS, px_width=1200, px_height=900, dpi=600
        )
        report = convert([src], spec)
        out = report.results[0].out_path
        assert read_size(out) == (1200, 900)
        assert read_dpi(out) == (600.0, 600.0)

    def test_pixel_box_fits_within_when_aspect_differs(self, tmp_path, workdir):
        """源比例与目标框不一致时应等比内接，而不是拉伸变形。"""
        src = make_raster(tmp_path / "fig.png", (1000, 800))  # 比例 1.25
        spec = base_spec(
            workdir, size_mode=SizeMode.PIXELS, px_width=1200, px_height=900, dpi=600
        )
        report = convert([src], spec)
        # 高度顶满 900，宽度 = 900 * 1.25 = 1125，小于框宽 1200
        assert read_size(report.results[0].out_path) == (1125, 900)

    def test_jpeg_output(self, tmp_path, workdir):
        src = make_raster(tmp_path / "fig.png", (800, 600))
        report = convert([src], base_spec(workdir, fmt=OutputFormat.JPEG, jpeg_quality=90))
        out = report.results[0].out_path
        assert out.suffix == ".jpg"
        with Image.open(out) as img:
            assert img.format == "JPEG"
        assert read_dpi(out) == (300.0, 300.0)

    def test_png_output(self, tmp_path, workdir):
        src = make_raster(tmp_path / "fig.png", (800, 600))
        report = convert([src], base_spec(workdir, fmt=OutputFormat.PNG))
        out = report.results[0].out_path
        with Image.open(out) as img:
            assert img.format == "PNG"
        # PNG 的 pHYs 以「像素/米」存储，300dpi 只能近似表示，这是格式固有精度
        dx, dy = read_dpi(out)
        assert dx == pytest.approx(300.0, abs=PNG_DPI_TOLERANCE)
        assert dy == pytest.approx(300.0, abs=PNG_DPI_TOLERANCE)

    def test_pdf_output(self, tmp_path, workdir):
        src = make_raster(tmp_path / "fig.png", (800, 600))
        report = convert([src], base_spec(workdir, fmt=OutputFormat.PDF))
        out = report.results[0].out_path
        assert out.suffix == ".pdf"
        assert out.stat().st_size > 0

    def test_sixteen_bit_tiff_preserved(self, tmp_path, workdir):
        """16 位科学图像在 TIFF 输出下不能被静默截断到 8 位。"""
        src = make_raster(tmp_path / "deep.tif", (400, 300), mode="I;16")
        report = convert([src], base_spec(workdir, fmt=OutputFormat.TIFF))
        with Image.open(report.results[0].out_path) as img:
            assert img.mode in ("I;16", "I;16B", "I;16L", "I;16N")
            assert img.size == (1004, 753)

    def test_sixteen_bit_to_jpeg_warns_about_bit_depth(self, tmp_path, workdir):
        src = make_raster(tmp_path / "deep.tif", (400, 300), mode="I;16")
        report = convert([src], base_spec(workdir, fmt=OutputFormat.JPEG))
        result = report.results[0]
        assert result.ok
        assert any("16 位" in w for w in result.warnings)
        with Image.open(result.out_path) as img:
            assert img.mode == "L"

    def test_no_upscale_keeps_source_pixels(self, tmp_path, workdir):
        src = make_raster(tmp_path / "small.png", (200, 160))
        report = convert([src], base_spec(workdir, allow_upscale=False))
        result = report.results[0]
        assert read_size(result.out_path) == (200, 160)
        assert any("未放大" in w for w in result.warnings)


class TestPdfConversion:
    def test_multipage_pdf_exports_every_page(self, tmp_path, workdir):
        src = make_pdf(tmp_path / "doc.pdf", pages=3)
        report = convert([src], base_spec(workdir))
        assert report.ok_count == 3
        names = sorted(r.out_path.name for r in report.results)
        assert names == ["doc_p01.tif", "doc_p02.tif", "doc_p03.tif"]

    def test_page_range_selects_subset(self, tmp_path, workdir):
        src = make_pdf(tmp_path / "doc.pdf", pages=5)
        report = convert([src], base_spec(workdir, page_range="2,4-5"))
        assert report.ok_count == 3
        names = sorted(r.out_path.name for r in report.results)
        assert names == ["doc_p02.tif", "doc_p04.tif", "doc_p05.tif"]

    def test_single_page_from_multipage_keeps_page_suffix(self, tmp_path, workdir):
        """从多页 PDF 里只取一页时，文件名仍要带页号。

        否则 doc.tif 会被误认成第 1 页，而它其实是第 3 页。
        """
        src = make_pdf(tmp_path / "doc.pdf", pages=5)
        report = convert([src], base_spec(workdir, page_range="3"))
        assert report.ok_count == 1
        assert report.results[0].out_path.name == "doc_p03.tif"

    def test_page_override_is_per_file(self, tmp_path, workdir):
        """page_overrides 让同一批文件各自带页码（GUI 输入区逐文件设置）。"""
        doc_a = make_pdf(tmp_path / "a.pdf", pages=5)
        doc_b = make_pdf(tmp_path / "b.pdf", pages=5)
        report = convert(
            [doc_a, doc_b],
            base_spec(workdir),
            page_overrides={str(doc_a): "1,3", str(doc_b): None},
        )
        assert report.ok_count == 7  # a 取 2 页 + b 全部 5 页
        names = sorted(r.out_path.name for r in report.results)
        assert names == [
            "a_p01.tif", "a_p03.tif",
            "b_p01.tif", "b_p02.tif", "b_p03.tif", "b_p04.tif", "b_p05.tif",
        ]
        # 覆盖只影响指定文件，spec 本身不被污染
        assert convert([doc_a], base_spec(workdir, page_range="2")).ok_count == 1

    def test_vector_page_renders_at_target_size(self, tmp_path, workdir):
        src = make_pdf(tmp_path / "doc.pdf", pages=1, size_in=(8.5, 11.0))
        spec = base_spec(workdir, phys_unit=Unit.INCH, phys_width=4.0, dpi=300)
        report = convert([src], spec)
        assert read_size(report.results[0].out_path) == (1200, 1553)
        assert read_dpi(report.results[0].out_path) == (300.0, 300.0)

    def test_single_page_pdf_has_no_page_suffix(self, tmp_path, workdir):
        src = make_pdf(tmp_path / "one.pdf", pages=1)
        report = convert([src], base_spec(workdir))
        assert report.results[0].out_path.name == "one.tif"

    def test_svg_renders_through_the_vector_path(self, tmp_path, workdir):
        """SVG 与 PDF 共用矢量渲染路径：MuPDF 能直接解析，无需外部工具。"""
        src = tmp_path / "figure.svg"
        src.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300">'
            '<rect width="400" height="300" fill="#ffffff"/>'
            '<line x1="0" y1="0" x2="400" y2="300" stroke="#000" stroke-width="6"/>'
            "</svg>",
            encoding="utf-8",
        )
        spec = base_spec(workdir, phys_unit=Unit.INCH, phys_width=4.0, dpi=300)
        report = convert([src], spec)
        assert report.ok_count == 1, report.results[0].error
        assert read_size(report.results[0].out_path) == (1200, 900)
        assert report.results[0].out_path.name == "figure.tif"

    def test_name_template(self, tmp_path, workdir):
        src = make_pdf(tmp_path / "doc.pdf", pages=2)
        report = convert([src], base_spec(workdir, name_template="{stem}_300dpi"))
        names = sorted(r.out_path.name for r in report.results)
        assert names == ["doc_300dpi_p01.tif", "doc_300dpi_p02.tif"]

    def test_name_template_with_explicit_page(self, tmp_path, workdir):
        src = make_pdf(tmp_path / "doc.pdf", pages=2)
        report = convert([src], base_spec(workdir, name_template="{stem}-fig{page}"))
        names = sorted(r.out_path.name for r in report.results)
        assert names == ["doc-fig1.tif", "doc-fig2.tif"]


class TestOutputPlumbing:
    def test_default_output_dir_is_converted_subfolder(self, tmp_path):
        src = make_raster(tmp_path / "fig.png", (400, 300))
        report = convert([src], OutputSpec(phys_width=5, dpi=300))
        expected_dir = default_output_dir(tmp_path)
        assert report.results[0].out_path.parent == expected_dir
        assert expected_dir.name == "converted"

    def test_conflict_rename_keeps_both(self, tmp_path, workdir):
        src = make_raster(tmp_path / "fig.png", (400, 300))
        spec = base_spec(workdir, on_conflict=ConflictPolicy.RENAME)
        first = convert([src], spec).results[0].out_path
        second = convert([src], spec).results[0].out_path
        assert first != second
        assert second.name == "fig_1.tif"
        assert first.exists() and second.exists()

    def test_conflict_skip(self, tmp_path, workdir):
        src = make_raster(tmp_path / "fig.png", (400, 300))
        spec = base_spec(workdir, on_conflict=ConflictPolicy.SKIP)
        assert convert([src], spec).results[0].ok
        second = convert([src], spec).results[0]
        assert not second.ok
        assert "跳过" in second.error

    def test_conflict_overwrite(self, tmp_path, workdir):
        src = make_raster(tmp_path / "fig.png", (400, 300))
        spec = base_spec(workdir, on_conflict=ConflictPolicy.OVERWRITE)
        first = convert([src], spec).results[0]
        second = convert([src], spec).results[0]
        assert first.out_path == second.out_path


class TestBatchRobustness:
    def test_unsupported_file_reports_error_and_batch_continues(self, tmp_path, workdir):
        good = make_raster(tmp_path / "ok.png", (400, 300))
        bad = tmp_path / "drawing.cdr"
        bad.write_bytes(b"RIFFxxxxCDR")
        missing = tmp_path / "nope.png"

        report = convert([good, bad, missing], base_spec(workdir))
        assert report.ok_count == 1
        assert report.fail_count == 2
        errors = " ".join(r.error or "" for r in report.results)
        assert "CorelDRAW" in errors or "不支持" in errors
        assert "不存在" in errors

    def test_invalid_spec_fails_every_item(self, tmp_path, workdir):
        src = make_raster(tmp_path / "fig.png", (400, 300))
        spec = base_spec(workdir, phys_width=None)
        report = convert([src], spec)
        assert report.fail_count == 1
        assert "输出规格有误" in report.results[0].error

    def test_cancel_after_first_file(self, tmp_path, workdir):
        """取消请求在文件边界生效：当前文件处理完，后续文件不再开始。"""
        srcs = [make_pdf(tmp_path / f"d{i}.pdf", pages=2) for i in range(3)]
        state = {"cancel": False}

        def progress(event):
            if event.completed_files >= 1:
                state["cancel"] = True

        report = convert(
            srcs,
            base_spec(workdir),
            progress=progress,
            should_cancel=lambda: state["cancel"],
        )
        assert report.ok_count == 2  # 只有第 1 个文件的两页
        assert {r.source.name for r in report.results} == {"d0.pdf"}

    def test_progress_callback_reports_files(self, tmp_path, workdir):
        srcs = [make_raster(tmp_path / f"f{i}.png", (200, 200)) for i in range(3)]
        events = []
        convert(srcs, base_spec(workdir), progress=events.append)
        assert [e.completed_files for e in events] == [0, 1, 2]
        assert all(e.total_files == 3 for e in events)


class TestIdempotence:
    def test_rerunning_on_own_output_keeps_geometry(self, tmp_path, workdir):
        """把产物再跑一遍，尺寸与 DPI 必须稳定（可复现）。"""
        src = make_raster(tmp_path / "fig.png", (1000, 800))
        spec = base_spec(workdir, size_mode=SizeMode.PIXELS, px_width=1000, dpi=300)
        first = convert([src], spec).results[0].out_path

        dest = workdir / "round2"
        spec2 = base_spec(dest, size_mode=SizeMode.PIXELS, px_width=1000, dpi=300)
        second = convert([first], spec2).results[0].out_path

        assert read_size(second) == read_size(first)
        assert read_dpi(second) == read_dpi(first)
