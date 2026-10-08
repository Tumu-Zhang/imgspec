"""输入文件探测：识别类别、页数、宽高比与源分辨率。

这一层保持"只读且不启动 Office"，保证界面拖入文件时立刻有反馈。
真正的 PowerPoint 自动化留到转换阶段（见 office.py）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image, UnidentifiedImageError

EMU_PER_INCH = 914400


class SourceKind(str, Enum):
    RASTER = "raster"      # 单帧栅格图
    PDF = "pdf"            # 矢量/混合页面文档
    SVG = "svg"            # 矢量图，由 MuPDF 直接渲染
    SLIDES = "slides"      # PPT / PPTX
    UNSUPPORTED = "unsupported"


RASTER_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".jpe", ".tif", ".tiff", ".bmp",
    ".gif", ".webp", ".jfif", ".jp2", ".ppm", ".pgm", ".tga", ".dib",
}
SLIDE_SUFFIXES = {".ppt", ".pptx", ".pptm", ".pps", ".ppsx", ".pot", ".potx"}
PDF_SUFFIXES = {".pdf"}
# MuPDF 自己能解析 SVG（实测可直接栅格化），所以不需要外部渲染器
SVG_SUFFIXES = {".svg"}
# 需要外部渲染器才能栅格化的格式，本机没有装，给出明确指引而不是静默失败
NEEDS_EXTERNAL = {
    ".eps": "EPS 需要 Ghostscript（或先在 Illustrator 中另存为 PDF/SVG）",
    ".ai": "Illustrator 文件请先在 Illustrator 中另存为 PDF 或 SVG",
    ".cdr": "CorelDRAW 文件请先导出为 PDF 或 TIFF",
    ".wmf": "WMF 需要外部转换器",
    ".emf": "EMF 需要外部转换器",
    ".psd": "PSD 请先另存为 TIFF 或 PNG（避免只读合并层的歧义）",
    ".raw": "RAW 请先导出为 TIFF",
}
MAX_RASTER_PIXELS = 600_000_000  # PIL 解压炸弹阈值，允许超大科研图


@dataclass
class SourceInfo:
    """一个输入文件的可展示元信息。"""

    path: Path
    kind: SourceKind
    page_count: int = 1
    aspect: float | None = None             # 宽/高
    pixel_size: tuple[int, int] | None = None
    dpi: tuple[float, float] | None = None
    page_sizes_in: list[tuple[float, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def kind_label(self) -> str:
        return {
            SourceKind.RASTER: "图片",
            SourceKind.PDF: "PDF",
            SourceKind.SVG: "矢量图",
            SourceKind.SLIDES: "幻灯片",
            SourceKind.UNSUPPORTED: "不支持",
        }[self.kind]

    @property
    def source_dpi_text(self) -> str:
        if self.dpi is None:
            return "无 DPI 信息"
        x, y = self.dpi
        if abs(x - y) < 0.01:
            return f"{x:.0f} dpi"
        return f"{x:.0f} x {y:.0f} dpi"

    def describe_source(self) -> str:
        if self.kind is SourceKind.RASTER and self.pixel_size:
            return f"{self.pixel_size[0]} x {self.pixel_size[1]} px / {self.source_dpi_text}"
        if self.kind in (SourceKind.PDF, SourceKind.SVG) and self.page_sizes_in:
            w, h = self.page_sizes_in[0]
            if self.kind is SourceKind.SVG:
                return f"矢量图 / {w * 25.4:.0f} x {h * 25.4:.0f} mm"
            return f"{self.page_count} 页 / 首页 {w * 25.4:.0f} x {h * 25.4:.0f} mm"
        if self.kind is SourceKind.SLIDES:
            if self.aspect:
                return f"{self.page_count} 页 / 宽高比 {self.aspect:.2f}"
            return f"{self.page_count} 页"
        return "—"


def classify(path: Path) -> SourceKind:
    suffix = path.suffix.lower()
    if suffix in PDF_SUFFIXES:
        return SourceKind.PDF
    if suffix in SVG_SUFFIXES:
        return SourceKind.SVG
    if suffix in SLIDE_SUFFIXES:
        return SourceKind.SLIDES
    if suffix in RASTER_SUFFIXES:
        return SourceKind.RASTER
    return SourceKind.UNSUPPORTED


def probe(path: Path | str) -> SourceInfo:
    """探测单个文件。永不抛异常，失败信息放在 SourceInfo.error 里。"""
    path = Path(path)
    kind = classify(path)

    if not path.exists():
        return SourceInfo(path, SourceKind.UNSUPPORTED, error="文件不存在")
    if kind is SourceKind.UNSUPPORTED:
        suffix = path.suffix.lower()
        hint = NEEDS_EXTERNAL.get(suffix)
        message = f"不支持的文件类型：{suffix or '（无扩展名）'}"
        if hint:
            message += f"。{hint}"
        return SourceInfo(path, SourceKind.UNSUPPORTED, error=message)

    try:
        if kind in (SourceKind.PDF, SourceKind.SVG):
            # SVG 交给同一个函数：MuPDF 能直接解析它，页数与尺寸都能读出来
            return _probe_pdf(path)
        if kind is SourceKind.SLIDES:
            return _probe_slides(path)
        return _probe_raster(path)
    except Exception as exc:  # noqa: BLE001 - 探测失败不应中断批量流程
        return SourceInfo(path, kind, error=f"无法读取：{exc}")


def _probe_pdf(path: Path) -> SourceInfo:
    info = SourceInfo(path, classify(path))
    with fitz.open(path) as doc:
        if doc.needs_pass:
            info.error = "PDF 已加密，请先解除密码"
            return info
        info.page_count = doc.page_count
        for page in doc:
            rect = page.rect
            info.page_sizes_in.append((rect.width / 72.0, rect.height / 72.0))
    if info.page_sizes_in:
        w, h = info.page_sizes_in[0]
        info.aspect = w / h if h else None
        if len(set(info.page_sizes_in)) > 1:
            info.notes.append("各页尺寸不一致，等比缩放将以各页自身比例为准")
    if not info.page_count:
        info.error = "PDF 不含任何页面"
    return info


def _probe_slides(path: Path) -> SourceInfo:
    info = SourceInfo(path, SourceKind.SLIDES)
    if path.suffix.lower() == ".pptx" or path.suffix.lower() == ".pptm":
        try:
            from pptx import Presentation

            prs = Presentation(str(path))
            info.page_count = len(prs.slides)
            # python-pptx 的 EMU 长度对象
            width_in = prs.slide_width / EMU_PER_INCH
            height_in = prs.slide_height / EMU_PER_INCH
            info.page_sizes_in = [(width_in, height_in)] * info.page_count
            info.aspect = width_in / height_in if height_in else None
            if not info.page_count:
                info.error = "演示文稿不含任何幻灯片"
            return info
        except Exception as exc:  # noqa: BLE001
            info.notes.append(f"python-pptx 读取失败（{exc}），改由 PowerPoint 在转换时读取")
            return info
    # 旧版 .ppt 只能靠 PowerPoint 本体读取，转换阶段再补
    info.notes.append("旧版 .ppt：页数与比例将在转换时由 PowerPoint 读取")
    info.page_count = 0
    return info


def _probe_raster(path: Path) -> SourceInfo:
    info = SourceInfo(path, SourceKind.RASTER)
    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_RASTER_PIXELS
    try:
        with Image.open(path) as img:
            info.pixel_size = img.size
            info.aspect = img.size[0] / img.size[1] if img.size[1] else None
            dpi = img.info.get("dpi")
            if dpi:
                try:
                    x, y = float(dpi[0]), float(dpi[1])
                except (TypeError, ValueError, IndexError):
                    x = y = 0.0
                # 有些工具会写 dpi=(0, 0)；那不是可用信息，按"无 DPI"处理，
                # 否则界面上会出现"0 dpi"这种误导性显示。
                if x > 0 and y > 0:
                    info.dpi = (x, y)
            info.page_count = getattr(img, "n_frames", 1)
            if info.page_count > 1:
                info.notes.append(f"多帧文件，仅处理第 1 帧（共 {info.page_count} 帧）")
                info.page_count = 1
            if img.mode in ("P", "LA", "RGBA"):
                info.notes.append(f"含透明通道（{img.mode}），输出 {path.suffix} 时会合成白底")
            if info.dpi is None:
                info.notes.append("源文件无 DPI 元数据，将直接按像素重采样")
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit
    return info


def probe_all(paths) -> list[SourceInfo]:
    return [probe(p) for p in paths]


__all__ = [
    "SourceInfo",
    "SourceKind",
    "classify",
    "probe",
    "probe_all",
    "NEEDS_EXTERNAL",
    "UnidentifiedImageError",
]
