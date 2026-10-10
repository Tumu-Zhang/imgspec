"""图片转换器 —— 科研投稿图片规格化工具。

把 PPT / PDF / 常见栅格图统一规格化为期刊要求的 DPI、物理尺寸与文件格式。
全过程为纯几何与编码变换（矢量重渲染 + 重采样 + 元数据写入），
不做任何 AI 重绘或图像生成，结果与 Photoshop 的
"图像大小 + 另存为" 等价。
"""

from imgspec.model import (
    OutputFormat,
    OutputSpec,
    SizeMode,
    TargetGeometry,
    Unit,
    resolve_geometry,
)

__all__ = [
    "OutputFormat",
    "OutputSpec",
    "SizeMode",
    "TargetGeometry",
    "Unit",
    "resolve_geometry",
]

__version__ = "1.1.0"
