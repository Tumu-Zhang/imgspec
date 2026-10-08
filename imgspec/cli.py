"""命令行入口。

图形界面适合手动批量处理；命令行适合把同一套规格固化进投稿流程里重复使用。

用法示例
--------
    # 8.5cm 宽、300dpi、TIFF（无损）、输出到 ./converted
    python -m imgspec figure.pdf --width 8.5 --unit cm --dpi 300 --format tiff

    # 只要第 2 页，限制单图 10MB
    python -m imgspec slides.pptx --width 170 --unit mm --dpi 300 --pages 2 --limit 10

    # 精确像素尺寸
    python -m imgspec a.png b.png --px-width 2550 --px-height 3300 --dpi 300

    # 只预览不写盘
    python -m imgspec *.pdf --width 8.5 --unit cm --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from imgspec import ingest
from imgspec.model import (
    ConflictPolicy,
    OutputFormat,
    OutputSpec,
    SizeMode,
    SpecError,
    TiffCompression,
    Unit,
)
from imgspec.pipeline import Converter, ProgressEvent, default_output_dir

EXIT_OK = 0
EXIT_FAILED = 1


def _force_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - 尽力而为
                pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="imgspec",
        description="科研投稿图片规格化：统一 DPI、物理尺寸与文件格式。"
                    "对 PPT/PDF 按目标 DPI 重新矢量渲染，对图片做高质量重采样。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("inputs", nargs="+", help="输入文件或目录（目录会被递归展开）")

    size = parser.add_argument_group("尺寸（物理模式与像素模式二选一）")
    size.add_argument("--width", type=float, help="物理宽度")
    size.add_argument("--height", type=float, help="物理高度")
    size.add_argument("--unit", choices=[u.value for u in Unit], default="cm",
                      help="物理尺寸单位，默认 cm")
    size.add_argument("--px-width", type=int, help="目标像素宽度（使用后进入像素模式）")
    size.add_argument("--px-height", type=int, help="目标像素高度")
    size.add_argument("--no-aspect", action="store_true",
                      help="取消等比：精确拉伸到目标宽高（需同时给出宽和高）")
    size.add_argument("--no-upscale", action="store_true",
                      help="不放大：源像素不足时按原始像素输出并给出警告")

    out = parser.add_argument_group("输出")
    out.add_argument("--dpi", type=int, default=300, help="输出 DPI，默认 300")
    out.add_argument("--format", choices=[f.value for f in OutputFormat], default="tiff",
                     help="输出格式，默认 tiff")
    out.add_argument("--out", type=Path, help="输出目录，默认在源文件同级建 converted/")
    out.add_argument("--name", default="{stem}", help="命名模板，可用 {stem} {page} {index}")
    out.add_argument("--pages", help='PDF/PPT 页码，如 "1,3-5"，默认全部')
    out.add_argument("--on-conflict", choices=[c.value for c in ConflictPolicy],
                     default="rename", help="重名处理，默认 rename")

    enc = parser.add_argument_group("编码")
    enc.add_argument("--tiff-compression", choices=[c.value for c in TiffCompression],
                     default="lzw", help="TIFF 压缩，默认 lzw（无损）")
    enc.add_argument("--jpeg-quality", type=int, default=95, help="JPEG 起始质量，默认 95")
    enc.add_argument("--limit", type=float,
                     help="单张图体积上限（MB）。无损达标不了时会自动降质并给出警告")
    enc.add_argument("--no-lossy-fallback", action="store_true",
                     help="禁止把无损格式降级为有损：压不进上限就报错")
    enc.add_argument("--skip-embedded-dpi-check", action="store_true",
                     help="跳过 PDF 内嵌位图有效分辨率检查")

    misc = parser.add_argument_group("其他")
    misc.add_argument("--dry-run", action="store_true", help="只预览尺寸结果，不写任何文件")
    misc.add_argument("--quiet", action="store_true", help="只输出错误与最终统计")
    return parser


def _expand_inputs(raw: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in raw:
        path = Path(item)
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and ingest.classify(child) is not ingest.SourceKind.UNSUPPORTED:
                    files.append(child)
        else:
            files.append(path)
    return files


def _spec_from_args(args: argparse.Namespace) -> OutputSpec:
    if args.px_width or args.px_height:
        mode = SizeMode.PIXELS
    else:
        mode = SizeMode.PHYSICAL

    return OutputSpec(
        fmt=OutputFormat(args.format),
        size_mode=mode,
        phys_unit=Unit(args.unit),
        phys_width=args.width,
        phys_height=args.height,
        px_width=args.px_width,
        px_height=args.px_height,
        dpi=args.dpi,
        keep_aspect=not args.no_aspect,
        allow_upscale=not args.no_upscale,
        tiff_compression=TiffCompression(args.tiff_compression),
        jpeg_quality=args.jpeg_quality,
        max_bytes=int(args.limit * 1024 * 1024) if args.limit else None,
        lossy_fallback=not args.no_lossy_fallback,
        check_embedded_dpi=not args.skip_embedded_dpi_check,
        output_dir=args.out,
        name_template=args.name,
        page_range=args.pages,
        on_conflict=ConflictPolicy(args.on_conflict),
    )


def _print_dry_run(files: list[Path], spec: OutputSpec) -> int:
    print(f"预览（不写文件）：{len(files)} 个输入\n")
    exit_code = EXIT_OK
    for path in files:
        info = ingest.probe(path)
        print(f"  {path.name}")
        print(f"    源   ：{info.kind_label} / {info.describe_source()}")
        if info.error:
            print(f"    ✗ {info.error}")
            exit_code = EXIT_FAILED
            continue
        if info.notes:
            for note in info.notes:
                print(f"    · {note}")
        try:
            geometry = spec.resolve(info.aspect)
        except SpecError as exc:
            print(f"    ✗ {exc}")
            exit_code = EXIT_FAILED
            continue
        out_dir = spec.output_dir or default_output_dir(path.parent)
        print(f"    输出 ：{geometry.describe()}")
        print(f"    目录 ：{out_dir}")
    return exit_code


def _run_conversion(files: list[Path], spec: OutputSpec, quiet: bool) -> int:
    state = {"last": 0}

    def progress(event: ProgressEvent) -> None:
        if quiet or event.total_files <= 1:
            return
        if event.completed_files != state["last"]:
            print(f"[{event.completed_files}/{event.total_files}] {event.current}")
            state["last"] = event.completed_files

    report = Converter(spec, progress=progress).run(files)

    if not quiet:
        print()
    for result in report.results:
        if quiet and result.ok:
            continue  # quiet 模式只保留失败项与警告
        label = result.out_path.name if result.out_path else result.source.name
        page = f" (第 {result.page} 页)" if result.page and result.page > 1 else ""
        if result.ok:
            print(f"✓ {label}{page}  ->  {result.summary}")
            for warning in result.warnings:
                print(f"    ⚠ {warning}")
        else:
            print(f"✗ {label}{page}  {result.error}")

    print(
        f"\n完成：成功 {report.ok_count} 项，失败 {report.fail_count} 项，"
        f"其中 {report.warning_count} 项带提示"
    )
    return EXIT_OK if report.fail_count == 0 else EXIT_FAILED


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    parser = _build_parser()
    args = parser.parse_args(argv)

    files = _expand_inputs(args.inputs)
    if not files:
        print("没有找到可处理的输入文件", file=sys.stderr)
        return EXIT_FAILED

    try:
        spec = _spec_from_args(args)
        spec.validate()
    except SpecError as exc:
        print(f"参数错误：{exc}", file=sys.stderr)
        return EXIT_FAILED

    if args.dry_run:
        return _print_dry_run(files, spec)

    # 不做 PowerPoint 预检：那需要额外启停一次 Office，而缺 PowerPoint 时转换层
    # 已经会抛出可读的错误（见 office.PowerPointSession），错误路径统一即可。
    return _run_conversion(files, spec, args.quiet)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
