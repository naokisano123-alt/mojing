"""文档导入格式与限额策略模块（R3-L / R5-L 交付）。

导出内容：
- 常量：DEFAULT_MAX_BYTES_BY_FORMAT, DEFAULT_FALLBACK_MAX_BYTES, DEFAULT_LEGACY_WARNING_CODE
- 配置类型：ImportPolicyConfig, CapabilitiesMaxBytes
- 纯策略函数：
    normalize_suffix,
    get_max_bytes_for_suffix,
    get_upload_read_limit,
    is_supported_suffix,
    get_capabilities_max_bytes,
    normalize_warning_records
"""

from __future__ import annotations

from .policy import (
    get_capabilities_max_bytes,
    get_max_bytes_for_suffix,
    get_upload_read_limit,
    is_supported_suffix,
    normalize_suffix,
)
from .types import (
    DEFAULT_FALLBACK_MAX_BYTES,
    DEFAULT_MAX_BYTES_BY_FORMAT,
    CapabilitiesMaxBytes,
    ImportPolicyConfig,
)
from .warnings import (
    DEFAULT_LEGACY_WARNING_CODE,
    normalize_warning_records,
)

__all__ = [
    "DEFAULT_MAX_BYTES_BY_FORMAT",
    "DEFAULT_FALLBACK_MAX_BYTES",
    "DEFAULT_LEGACY_WARNING_CODE",
    "CapabilitiesMaxBytes",
    "ImportPolicyConfig",
    "normalize_suffix",
    "get_max_bytes_for_suffix",
    "get_upload_read_limit",
    "is_supported_suffix",
    "get_capabilities_max_bytes",
    "normalize_warning_records",
]
