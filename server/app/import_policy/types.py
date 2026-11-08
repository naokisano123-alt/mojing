"""文档导入限额与格式策略类型定义。

契约依据：
- docs/contracts/M1_R3_API.md §2.1 (逐格式限额)
- docs/collaboration/ROUND3.md
- docs/collaboration/R3_GEMINI_LI.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict


# 系统默认逐格式字节上限 (2 MiB 针对纯文本，8 MiB 针对二进制富文本)
DEFAULT_MAX_BYTES_BY_FORMAT: dict[str, int] = {
    ".txt": 2 * 1024 * 1024,
    ".md": 2 * 1024 * 1024,
    ".pdf": 8 * 1024 * 1024,
    ".docx": 8 * 1024 * 1024,
}

DEFAULT_FALLBACK_MAX_BYTES: int = 2 * 1024 * 1024


class CapabilitiesMaxBytes(TypedDict):
    """用于 /api/v1/capabilities 输出的逐格式字节上限结构。"""

    txt: int
    md: int
    pdf: int
    docx: int


@dataclass(frozen=True)
class ImportPolicyConfig:
    """导入限额策略配置类。
    
    支持调用方（如 config.py 或 main.py）以参数形式注入，
    模块内绝不直接读取 os.environ。
    """

    max_bytes_by_format: dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_MAX_BYTES_BY_FORMAT)
    )
    fallback_max_bytes: int = DEFAULT_FALLBACK_MAX_BYTES
    allowed_suffixes: tuple[str, ...] = (".txt", ".md", ".pdf", ".docx")
