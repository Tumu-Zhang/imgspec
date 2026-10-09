"""自检：确认当前环境（尤其是打包成 exe 之后）真的能完成一次转换。

打包会丢掉东西 —— 图像编解码器、PDF 引擎、Office 自动化所需的组件都可能缺。
只看到窗口打开并不代表能干活，所以这里跑一遍完整的真实链路：

    生成测试位图 -> 生成测试 PDF -> 按规格转换 -> 读回校验像素/DPI/压缩标记

再加上 PowerPoint 可用性探测（缺了不算失败，只如实报告）。

用法：
    python -m imgspec.selftest
    图片转换器.exe --selftest
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

from imgspec.model import OutputFormat, OutputSpec, SizeMode, TiffCompression, Unit
from imgspec.pipeline import convert

# 8.5 cm @ 300 dpi = 1003.94 px -> 1004；1000x800 的源按比例得到 803
EXPECTED_PX = (1004, 803)
TIFF_TAG_COMPRESSION = 259
TIFF_LZW = 5


def _make_source_png(path: Path) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (1000, 800), (240, 244, 246))
    draw = ImageDraw.Draw(image)
    for x in range(0, 1000, 50):
        draw.line([(x, 0), (x, 800)], fill=(14, 111, 118), width=2)
    for y in range(0, 800, 50):
        draw.line([(0, y), (1000, y)], fill=(200, 208, 210), width=1)
    image.save(path)


def _make_source_pdf(path: Path) -> None:
    import fitz

    doc = fitz.open()
    for index in range(2):
        page = doc.new_page(width=8.5 * 72, height=11 * 72)
        page.insert_text((72, 120), f"selftest page {index + 1}", fontsize=20)
    doc.save(path)
    doc.close()


def run_selftest(
    work_dir: Path | None = None, extra_files: list[Path] | None = None
) -> tuple[bool, str]:
    """跑一次完整转换。返回 (是否通过, 报告文本)。

    extra_files 是额外的真实文件：每个都会按内置规格真正转一遍。
    用来验证 PPT 这类需要 Office 的链路，或者用户想确认自己手上那份能不能处理。
    """
    lines: list[str] = ["图片转换器 自检", "=" * 46]
    ok = True

    def step(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        mark = "通过" if passed else "失败"
        lines.append(f"[{mark}] {label}" + (f" —— {detail}" if detail else ""))
        if not passed:
            ok = False

    base = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="imgspec_selftest_"))
    base.mkdir(parents=True, exist_ok=True)
    out_dir = base / "converted"

    # --- 依赖就位 -----------------------------------------------------
    try:
        import fitz
        from PIL import Image

        step("图像库 Pillow", True, f"Pillow {Image.__version__}")
        step("PDF 引擎 PyMuPDF", True, f"PyMuPDF {fitz.VersionBind}")
    except Exception as exc:  # noqa: BLE001
        step("依赖导入", False, f"{type(exc).__name__}: {exc}")
        return ok, "\n".join(lines)

    try:
        import numpy

        step("数值库 numpy", True, numpy.__version__)
    except Exception as exc:  # noqa: BLE001
        step("数值库 numpy", False, str(exc))

    # --- 真实转换：位图 -> TIFF ---------------------------------------
    try:
        source_png = base / "selftest_source.png"
        _make_source_png(source_png)
        step("生成测试位图", True, source_png.name)
    except Exception as exc:  # noqa: BLE001
        step("生成测试位图", False, f"{type(exc).__name__}: {exc}")
        return ok, "\n".join(lines)

    try:
        spec = OutputSpec(
            fmt=OutputFormat.TIFF,
            size_mode=SizeMode.PHYSICAL,
            phys_unit=Unit.CM,
            phys_width=8.5,
            dpi=300,
            tiff_compression=TiffCompression.LZW,
            output_dir=out_dir,
        )
        report = convert([source_png], spec)
        result = report.results[0]
        step("转换位图 -> TIFF", result.ok, result.error or result.summary)
    except Exception:  # noqa: BLE001
        step("转换位图 -> TIFF", False, traceback.format_exc(limit=2).strip())
        return ok, "\n".join(lines)

    if result.ok:
        try:
            from PIL import Image

            with Image.open(result.out_path) as produced:
                size = produced.size
                dpi = produced.info.get("dpi")
                compression = int(produced.tag_v2.get(TIFF_TAG_COMPRESSION, 1))
            step("校验像素尺寸", size == EXPECTED_PX, f"{size[0]}x{size[1]}（期望 1004x803）")
            step(
                "校验 DPI 元数据",
                dpi == (300.0, 300.0),
                f"{dpi}",
            )
            step("校验 TIFF LZW 压缩", compression == TIFF_LZW, f"tag259={compression}")
        except Exception as exc:  # noqa: BLE001
            step("读回产物校验", False, f"{type(exc).__name__}: {exc}")

    # --- 真实转换：PDF -> PNG -----------------------------------------
    try:
        source_pdf = base / "selftest_source.pdf"
        _make_source_pdf(source_pdf)
        pdf_spec = OutputSpec(
            fmt=OutputFormat.PNG,
            size_mode=SizeMode.PIXELS,
            px_width=900,
            dpi=300,
            output_dir=out_dir,
        )
        pdf_report = convert([source_pdf], pdf_spec)
        step(
            "转换 PDF -> PNG（逐页）",
            pdf_report.ok_count == 2,
            f"成功 {pdf_report.ok_count} 页 / 失败 {pdf_report.fail_count} 页",
        )
    except Exception as exc:  # noqa: BLE001
        step("转换 PDF -> PNG（逐页）", False, f"{type(exc).__name__}: {exc}")

    # --- PowerPoint（缺了不算失败，如实报告）---------------------------
    try:
        from imgspec import office

        available, note = office.is_available()
        lines.append(
            f"[{'通过' if available else '跳过'}] PowerPoint 自动化 —— {note}"
        )
        if not available:
            lines.append("      幻灯片输入需要本机 PowerPoint；PDF 与图片不受影响。")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"[跳过] PowerPoint 自动化 —— {type(exc).__name__}: {exc}")

    # --- 额外的真实文件：每个都真跑一遍 --------------------------------
    # 主要用于验证幻灯片链路（它依赖 Office，是最容易在打包后出问题的一环），
    # 也可以让用户拿自己手上的文件确认能不能处理。
    if extra_files:
        lines.append("-" * 46)
        for extra in extra_files:
            extra = Path(extra)
            if not extra.exists():
                step(f"真实文件 {extra.name}", False, "文件不存在")
                continue
            try:
                extra_report = convert(
                    [extra],
                    OutputSpec(
                        fmt=OutputFormat.TIFF,
                        size_mode=SizeMode.PHYSICAL,
                        phys_unit=Unit.CM,
                        phys_width=8.5,
                        dpi=300,
                        output_dir=out_dir / "extra",
                    ),
                )
                failures = [item for item in extra_report.results if not item.ok]
                detail = f"成功 {extra_report.ok_count} 页 / 失败 {len(failures)} 页"
                if failures:
                    detail += "；首个错误：" + str(failures[0].error)
                elif extra_report.results:
                    detail += "；" + extra_report.results[0].summary
                step(f"真实文件 {extra.name}", not failures and extra_report.ok_count > 0, detail)
                for item in extra_report.results:
                    for warning in item.warnings:
                        lines.append(f"        提示：{warning}")
            except Exception as exc:  # noqa: BLE001
                step(f"真实文件 {extra.name}", False, f"{type(exc).__name__}: {exc}")

    lines.append("=" * 46)
    lines.append("结论：" + ("全部通过，可以正常使用。" if ok else "存在失败项，请查看上面标为失败的行。"))
    lines.append(f"工作目录：{base}")
    return ok, "\n".join(lines)


def main() -> int:
    # 形如 `图片转换器.exe --selftest D:\deck.pptx` 时，把文件当成额外的真实用例
    extra = [
        Path(arg)
        for arg in sys.argv[1:]
        if not arg.startswith("-") and arg != "--selftest"
    ]
    ok, report = run_selftest(extra_files=extra or None)

    # 打包成 exe 后是 --windowed，标准输出可能不可用（sys.stdout 为 None），
    # 所以报告先落盘，再尝试往控制台打印。
    target = Path(tempfile.gettempdir()) / "imgspec_selftest_report.txt"
    try:
        target.write_text(report, encoding="utf-8")
    except Exception:  # noqa: BLE001
        target = None

    if sys.stdout is not None:
        try:
            print(report)
            if target is not None:
                print(f"\n报告已写入：{target}")
        except Exception:  # noqa: BLE001 - 控制台不可用不影响自检结论
            pass

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
