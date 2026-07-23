"""素材拆解模块。

负责将导入的原始文本通过 LLM 拆解为结构化碎片（MaterialFragment）。
"""

from inkmind.materials.decomposer import MaterialDecomposer

__all__ = [
    "MaterialDecomposer",
]
