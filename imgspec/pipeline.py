"""转换编排：把一批输入文件按同一份规格输出成期刊可用的图片。

每个输入文件可能产生多个输出项（多页 PDF / 多页幻灯片 -> 每页一张图），
所以任务粒度是"源文件 + 页号"。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from imgspec import encode, ingest, office, render
from imgspec.encode import EncodeResult, VolumeLimitUnreachable
from imgspec.ingest import SourceInfo, SourceKind
from imgspec.model import (
    ConflictPolicy,
    OutputSpec,
    SpecError,
    TargetGeometry,
    fit_within,
    parse_page_range,
)

ProgressCallback = Callable[["ProgressEvent"], None]
CancelCheck = Callable[[], bool]


@dataclass
class ProgressEvent:
    completed_files: int
    total_files: int
    current: str
    page: str = ""


@dataclass
class TaskResult:
    """一个输出项的结果（成功或失败）。"""

    source: Path
    page: int | None
    out_path: Path | None
    ok: bool
    geometry: TargetGeometry | None = None
    num_bytes: int = 0
    encode: EncodeResult | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    elapsed: float = 0.0
    source_kind: SourceKind | None = None

    @property
    def summary(self) -> str:
        if not self.ok:
            return f"失败：{self.error}"
        parts = []
        if self.geometry:
            parts.append(self.geometry.describe())
        if self.encode:
            parts.append(self.encode.describe())
        return " / ".join(parts)


@dataclass
class ConversionReport:
    results: list[TaskResult] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if not r.ok)

    @property
    def warning_count(self) -> int:
        return sum(1 for r in self.results if r.warnings)

    @property
    def success(self) -> bool:
        return self.fail_count == 0 and self.ok_count > 0


# ----------------------------------------------------------------------
# 输出路径
# ----------------------------------------------------------------------
def default_output_dir(source_dir: Path) -> Path:
    """不指定输出目录时，落在源文件同级的 converted 子目录，避免污染原始素材。"""
    return Path(source_dir) / "converted"


def _resolve_output_dir(spec: OutputSpec, source: Path) -> Path:
    return Path(spec.output_dir) if spec.output_dir else default_output_dir(source.parent)


def build_output_name(spec: OutputSpec, source: Path, page: int, multi_page: bool) -> str:
    template = spec.name_template or "{stem}"
    if multi_page and "{page" not in template and "{index" not in template:
        template = template + "_p{page:02d}"
    try:
        base = template.format(stem=source.stem, page=page, index=page)
    except (KeyError, IndexError, ValueError) as exc:
        raise SpecError(f"命名模板不合法：{template} —— {exc}") from exc
    return f"{base}{spec.fmt.extension}"


def resolve_conflict(path: Path, policy: ConflictPolicy) -> Path | None:
    """按冲突策略决定最终路径；SKIP 时返回 None。"""
    if not path.exists():
        return path
    if policy is ConflictPolicy.OVERWRITE:
        return path
    if policy is ConflictPolicy.SKIP:
        return None
    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


# ----------------------------------------------------------------------
# 转换器
# ----------------------------------------------------------------------
class Converter:
    """按一份 OutputSpec 批量转换。

    可复用的长生命周期对象：PowerPoint 会话按需惰性开启，并在所有幻灯片
    处理完之前保持存活，避免每个文件都重启一次 Office。
    """

    def __init__(
        self,
        spec: OutputSpec,
        *,
        progress: ProgressCallback | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> None:
        self.spec = spec
        self.progress = progress
        self.should_cancel = should_cancel or (lambda: False)
        self._ppt: office.PowerPointSession | None = None

    # -- 公共入口 -------------------------------------------------------
    def run(self, paths: Iterable[Path | str]) -> ConversionReport:
        sources = [Path(p) for p in paths]
        report = ConversionReport()
        total = len(sources)

        try:
            for index, source in enumerate(sources):
                if self.should_cancel():
                    break
                self._emit(index, total, source.name)
                report.results.extend(self.convert_one(source))
        finally:
            self._close_ppt()

        return report

    def convert_one(self, source: Path) -> list[TaskResult]:
        started = time.perf_counter()
        info = ingest.probe(source)

        if info.error:
            return [
                TaskResult(
                    source=source, page=None, out_path=None, ok=False,
                    error=info.error, source_kind=info.kind,
                    elapsed=time.perf_counter() - started,
                )
            ]

        try:
            self.spec.validate()
        except SpecError as exc:
            return [
                TaskResult(
                    source=source, page=None, out_path=None, ok=False,
                    error=f"输出规格有误：{exc}", source_kind=info.kind,
                )
            ]

        try:
            if info.kind is SourceKind.RASTER:
                results = self._convert_raster(source, info)
            elif info.kind in (SourceKind.PDF, SourceKind.SVG):
                # SVG 与 PDF 走同一条矢量渲染路径（MuPDF 两者都能直接解析）
                results = self._convert_pdf(source, info, None)
            elif info.kind is SourceKind.SLIDES:
                results = self._convert_slides(source, info)
            else:  # pragma: no cover - probe 已拦截
                raise RuntimeError(f"不支持的文件类型：{source.suffix}")
        except (VolumeLimitUnreachable, office.OfficeUnavailableError, SpecError) as exc:
            return [
                TaskResult(
                    source=source, page=None, out_path=None, ok=False,
                    error=str(exc), source_kind=info.kind,
                    elapsed=time.perf_counter() - started,
                )
            ]
        except Exception as exc:  # noqa: BLE001 - 单个文件失败不应中断整批
            return [
                TaskResult(
                    source=source, page=None, out_path=None, ok=False,
                    error=f"{type(exc).__name__}: {exc}", source_kind=info.kind,
                    elapsed=time.perf_counter() - started,
                )
            ]

        elapsed = time.perf_counter() - started
        for result in results:
            result.elapsed = elapsed
            result.source_kind = info.kind
        return results

    # -- 分支实现 -------------------------------------------------------
    def _convert_raster(self, source: Path, info: SourceInfo) -> list[TaskResult]:
        loaded = render.load_raster(source)
        aspect = loaded.image.size[0] / loaded.image.size[1]
        geometry = self.spec.resolve(aspect)
        geometry, scale_warnings = fit_within(
            geometry, loaded.image.size, allow_upscale=self.spec.allow_upscale
        )

        target_px = geometry.pixel_size
        image, resize_warnings = render.resize_raster(loaded.image, target_px)

        out_path = self._plan_output(source, page=1, multi_page=False)
        if out_path is None:
            return [TaskResult(
                source=source, page=1, out_path=None, ok=False,
                error="同名文件已存在，按「跳过」策略未输出", warnings=loaded.warnings,
            )]

        result = encode.encode_to_file(image, out_path, self.spec)
        return [TaskResult(
            source=source, page=1, out_path=out_path, ok=True,
            geometry=geometry, num_bytes=result.num_bytes, encode=result,
            warnings=loaded.warnings + scale_warnings + resize_warnings + result.warnings,
        )]

    def _convert_pdf(
        self, source: Path, info: SourceInfo, pdf_path: Path | None
    ) -> list[TaskResult]:
        actual_pdf = pdf_path or source
        results: list[TaskResult] = []

        with render.PdfPageRenderer(actual_pdf) as doc:
            pages = parse_page_range(self.spec.page_range, doc.page_count)
            # 页号后缀取决于「源文档是不是多页」，而不是「这次选中了几页」：
            # 从 10 页 PDF 里只取第 3 页，输出叫 doc_p03.tif 才不会被误认成第 1 页。
            multi = doc.page_count > 1

            for page_no in pages:
                if self.should_cancel():
                    break
                index = page_no - 1
                aspect = doc.page_aspect(index)
                geometry = self.spec.resolve(aspect)
                warnings: list[str] = []

                if self.spec.check_embedded_dpi:
                    embedded = doc.embedded_image_dpi(index)
                    if embedded is not None and embedded < geometry.dpi * 0.95:
                        warnings.append(
                            f"第 {page_no} 页内嵌位图的有效分辨率仅约 {embedded:.0f} dpi，"
                            f"低于目标 {geometry.dpi} dpi。"
                            "放大不会产生新细节，建议改为用矢量源或提高原始截屏分辨率。"
                        )

                image, render_warnings = doc.render_page(index, geometry)
                warnings.extend(render_warnings)

                out_path = self._plan_output(source, page=page_no, multi_page=multi)
                if out_path is None:
                    results.append(TaskResult(
                        source=source, page=page_no, out_path=None, ok=False,
                        error="同名文件已存在，按「跳过」策略未输出", warnings=warnings,
                    ))
                    continue

                encoded = encode.encode_to_file(image, out_path, self.spec)
                results.append(TaskResult(
                    source=source, page=page_no, out_path=out_path, ok=True,
                    geometry=geometry, num_bytes=encoded.num_bytes, encode=encoded,
                    warnings=warnings + encoded.warnings,
                ))
        return results

    def _convert_slides(self, source: Path, info: SourceInfo) -> list[TaskResult]:
        session = self._ppt_session()
        pdf_path = session.export_pdf(source)
        return self._convert_pdf(source, info, pdf_path)

    # -- 内部工具 -------------------------------------------------------
    def _ppt_session(self) -> office.PowerPointSession:
        if self._ppt is None:
            self._ppt = office.PowerPointSession()
            self._ppt.__enter__()
        return self._ppt

    def _close_ppt(self) -> None:
        if self._ppt is not None:
            self._ppt.__exit__(None, None, None)
            self._ppt = None

    def _plan_output(self, source: Path, *, page: int, multi_page: bool) -> Path | None:
        out_dir = _resolve_output_dir(self.spec, source)
        name = build_output_name(self.spec, source, page, multi_page)
        return resolve_conflict(out_dir / name, self.spec.on_conflict)

    def _emit(self, index: int, total: int, current: str, page: str = "") -> None:
        if self.progress:
            self.progress(ProgressEvent(index, total, current, page))


def convert(
    paths: Iterable[Path | str],
    spec: OutputSpec,
    *,
    progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> ConversionReport:
    """一次性转换的便捷入口。"""
    return Converter(spec, progress=progress, should_cancel=should_cancel).run(paths)


__all__ = [
    "ConversionReport",
    "Converter",
    "ProgressEvent",
    "TaskResult",
    "build_output_name",
    "convert",
    "default_output_dir",
    "resolve_conflict",
]
