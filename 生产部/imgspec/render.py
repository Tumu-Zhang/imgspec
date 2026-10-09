"""渲染层：把 PDF 页与栅格图统一成 PIL 位图。

设计要点
--------
矢量页不做"先渲染再重采样"，而是直接用矩阵把目标像素尺寸算进渲染矩阵，
让 MuPDF 在最终分辨率上做抗锯齿。这样 600 dpi 输出得到的线条是原生锐利的，
而不是从 72 dpi 插值放大的。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
from PIL import Image

from imgspec.model import TargetGeometry

Image.MAX_IMAGE_PIXELS = 600_000_000

# 这些是 16 位灰度/彩色模式，重采样与编码需要特殊照顾
SIXTEEN_BIT_MODES = {"I;16", "I;16B", "I;16L", "I;16N", "I"}


@dataclass
class LoadedImage:
    """已从磁盘完整载入的栅格图。"""

    path: Path
    image: Image.Image
    warnings: list[str] = field(default_factory=list)

    @property
    def is_16bit(self) -> bool:
        return self.image.mode in SIXTEEN_BIT_MODES

    @property
    def pixel_size(self) -> tuple[int, int]:
        return self.image.size


# ----------------------------------------------------------------------
# 栅格图
# ----------------------------------------------------------------------
def load_raster(path: Path | str) -> LoadedImage:
    """读入栅格图，必要时合成白底。"""
    path = Path(path)
    warnings: list[str] = []
    img = Image.open(path)
    try:
        img.load()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"图片数据损坏或不完整：{path.name} —— {exc}") from exc

    if getattr(img, "n_frames", 1) > 1:
        img.seek(0)
        img = img.copy()
        warnings.append("多帧文件仅取第 1 帧")

    mode = img.mode
    if mode in ("RGBA", "LA", "PA"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        alpha = img.convert("RGBA")
        background.paste(alpha, mask=alpha.split()[-1])
        img = background
        warnings.append("源图含透明通道，已合成到白色背景")
    elif mode == "P":
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
        warnings.append("索引色调色板图已转换为 RGB")
    elif mode == "CMYK":
        img = img.convert("RGB")
        warnings.append("CMYK 源图已转换为 RGB（多数期刊电子版要求 RGB）")
    elif mode == "1":
        img = img.convert("L")
    elif mode in ("L", "RGB", "I;16", "I;16B", "I;16L", "I;16N", "I", "F"):
        pass
    else:
        img = img.convert("RGB")
        warnings.append(f"源图色彩模式 {mode} 已转换为 RGB")

    return LoadedImage(path=path, image=img, warnings=warnings)


def _resample_filter() -> int:
    return Image.Resampling.LANCZOS


def resize_raster(image: Image.Image, target_px: tuple[int, int]) -> tuple[Image.Image, list[str]]:
    """把栅格图重采样到目标像素尺寸。

    Pillow 对 16 位模式的重采样支持有限，这里统一先升成 float32 再插值，
    避免高位深图像被静默截断到 8 位。
    """
    warnings: list[str] = []
    if image.size == tuple(target_px):
        return image, warnings

    if image.mode in SIXTEEN_BIT_MODES:
        arr = np.asarray(image)
        if arr.dtype != np.uint16:
            arr = arr.astype(np.float32)
            arr = np.clip(arr, 0, 65535).astype(np.uint16)
        float_img = Image.fromarray(arr.astype(np.float32), mode="F")
        resized = float_img.resize(tuple(target_px), _resample_filter())
        out = np.clip(np.asarray(resized), 0, 65535).round().astype(np.uint16)
        # 不传 mode=，让 Pillow 从 uint16 自动推断为 I;16（显式 mode= 已被弃用）
        return Image.fromarray(out), warnings

    return image.resize(tuple(target_px), _resample_filter()), warnings


# ----------------------------------------------------------------------
# PDF / 矢量页
# ----------------------------------------------------------------------
class PdfPageRenderer:
    """按页渲染 PDF，可复用同一个文档句柄。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._doc = fitz.open(self.path)
        if self._doc.needs_pass:
            self._doc.close()
            raise RuntimeError(f"PDF 已加密，无法渲染：{self.path.name}")

    def __enter__(self) -> "PdfPageRenderer":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None  # type: ignore[assignment]

    @property
    def page_count(self) -> int:
        return self._doc.page_count

    def page_aspect(self, index: int) -> float:
        """0 起始页号 -> 宽高比。"""
        rect = self._doc[index].rect
        return rect.width / rect.height if rect.height else 1.0

    def page_size_inches(self, index: int) -> tuple[float, float]:
        rect = self._doc[index].rect
        return rect.width / 72.0, rect.height / 72.0

    def embedded_image_dpi(self, index: int) -> float | None:
        """估算页内嵌位图的最低有效分辨率（dpi）。

        返回 None 表示页面没有可测量的嵌入位图（纯矢量页）。
        这是判断"600 dpi 是不是真的 600 dpi"的依据。
        """
        page = self._doc[index]
        worst: float | None = None
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                rects = page.get_image_rects(xref)
            except Exception:  # noqa: BLE001
                continue
            if not rects:
                continue
            pix_w = None
            try:
                detail = self._doc.extract_image(xref)
                pix_w = detail.get("width")
            except Exception:  # noqa: BLE001
                if xref > 0:
                    pix_w = img_info[2]
            if not pix_w:
                continue
            width_in = max(r.width for r in rects) / 72.0
            if width_in <= 0:
                continue
            effective = pix_w / width_in
            worst = effective if worst is None else min(worst, effective)
        return worst

    def render_page(self, index: int, geometry: TargetGeometry) -> tuple[Image.Image, list[str]]:
        """把第 index 页（0 起始）渲染为目标像素尺寸。"""
        page = self._doc[index]
        rect = page.rect
        if rect.width <= 0 or rect.height <= 0:  # pragma: no cover - 畸形 PDF
            raise RuntimeError(f"PDF 第 {index + 1} 页尺寸异常")

        target_w, target_h = geometry.pixel_size
        # 直接把目标像素折进渲染矩阵，避免二次重采样
        zoom_x = target_w / rect.width
        zoom_y = target_h / rect.height
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(zoom_x, zoom_y),
            colorspace=fitz.csRGB,
            alpha=False,
            annots=True,
        )
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)

        warnings: list[str] = []
        if image.size != (target_w, target_h):
            # MuPDF 会对像素数取整，这里补最后一次精确重采样，保证尺寸严格等于目标
            image = image.resize((target_w, target_h), _resample_filter())
        # 宽高各自独立取整会带来千分之几的比例偏差（物理尺寸 8.5cm 这类值除不尽时必然如此），
        # 只有明显变形才算"非等比拉伸"，否则会是满屏噪音警告。
        if max(zoom_x, zoom_y) > 0 and abs(zoom_x - zoom_y) / max(zoom_x, zoom_y) > 0.005:
            warnings.append(
                f"第 {index + 1} 页被非等比拉伸（{zoom_x:.3f} x {zoom_y:.3f}），"
                "如需保持形状请勾选等比"
            )
        return image, warnings


def estimate_render_dpi(geometry: TargetGeometry, source_size_in: tuple[float, float]) -> float:
    """按较受限的一边估算本次渲染等效 DPI，用于提示信息。"""
    src_w, src_h = source_size_in
    if src_w <= 0 or src_h <= 0:
        return float(geometry.dpi)
    zoom = min(geometry.width_in / src_w, geometry.height_in / src_h)
    return zoom * geometry.dpi


__all__ = [
    "LoadedImage",
    "PdfPageRenderer",
    "SIXTEEN_BIT_MODES",
    "estimate_render_dpi",
    "load_raster",
    "resize_raster",
]
