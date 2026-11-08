"""文档提取引擎模块导出。

对外部调用方（如 DSH 集成层）提供稳定接口：
- `extract_document`: 统一提取函数
- `ParsedDocument`: 结构化解析输出
- `ParseLimits`: 资源限制与安全配额
- `ParseError`: 统一解析异常
"""

from .extractor import extract_document
from .types import ParsedDocument, ParseError, ParseLimits

__all__ = [
    "extract_document",
    "ParsedDocument",
    "ParseLimits",
    "ParseError",
]
