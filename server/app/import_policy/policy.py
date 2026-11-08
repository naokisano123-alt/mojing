"""文档导入限额纯策略实现。

契约依据：
- docs/contracts/M1_R3_API.md §2.1 (逐格式限额，关闭 B1/B2)
- docs/collaboration/ROUND3.md
- docs/collaboration/R3_GEMINI_LI.md

特性：
1. 纯函数设计：所有规则由参数与传入配置驱动，不直接读取环境变量，易于单元测试与依赖注入。
2. 逐格式明确限额：TXT/MD 默认为 2 MiB，PDF/DOCX 默认为 8 MiB。
3. 严格规格化：统一转换扩展名至小写并保留前缀点号（如 'PDF' -> '.pdf'）。
4. 上传守门辅助：提供精确的 limit + 1 字节计算，便于主路由实施“先判格式，再限量读取”。
"""

from __future__ import annotations

from typing import Any

from .types import (
    DEFAULT_FALLBACK_MAX_BYTES,
    DEFAULT_MAX_BYTES_BY_FORMAT,
    ImportPolicyConfig,
)


def normalize_suffix(suffix: str | None) -> str:
    """规范化文件后缀：去除空白、转为小写并补齐前导点号。
    
    例如:
        'pdf' -> '.pdf'
        '.PDF' -> '.pdf'
        '  .docx  ' -> '.docx'
        None / '' -> ''
    """
    if not suffix or not isinstance(suffix, str):
        return ""
    clean = suffix.strip().lower()
    if not clean:
        return ""
    if not clean.startswith("."):
        clean = f".{clean}"
    return clean


def _resolve_config_map(
    config: ImportPolicyConfig | dict[str, Any] | None,
) -> tuple[dict[str, int], int, tuple[str, ...]]:
    """从可能为 ImportPolicyConfig 或 dict 的配置对象中提取策略参数。"""
    if config is None:
        return dict(DEFAULT_MAX_BYTES_BY_FORMAT), DEFAULT_FALLBACK_MAX_BYTES, (".txt", ".md", ".pdf", ".docx")

    if isinstance(config, ImportPolicyConfig):
        return (
            dict(config.max_bytes_by_format),
            config.fallback_max_bytes,
            config.allowed_suffixes,
        )

    if isinstance(config, dict):
        custom_map = config.get("max_bytes_by_format") or config.get("max_bytes") or {}
        merged_map = dict(DEFAULT_MAX_BYTES_BY_FORMAT)
        if isinstance(custom_map, dict):
            for k, v in custom_map.items():
                if isinstance(v, int) and v > 0:
                    merged_map[normalize_suffix(k)] = v

        fallback = config.get("fallback_max_bytes", DEFAULT_FALLBACK_MAX_BYTES)
        allowed = config.get("allowed_suffixes")
        if not allowed or not isinstance(allowed, (list, tuple)):
            allowed = tuple(merged_map.keys())
        else:
            allowed = tuple(normalize_suffix(s) for s in allowed)

        return merged_map, fallback, allowed

    return dict(DEFAULT_MAX_BYTES_BY_FORMAT), DEFAULT_FALLBACK_MAX_BYTES, (".txt", ".md", ".pdf", ".docx")


def get_max_bytes_for_suffix(
    suffix: str | None,
    config: ImportPolicyConfig | dict[str, Any] | None = None,
) -> int:
    """获取指定文件格式对应的最大字节上限。
    
    参数:
        suffix: 文件扩展名（如 '.pdf', '.txt', 'docx'）。
        config: 可选配置对象（支持 ImportPolicyConfig 或 dict）。
        
    返回:
        允许上传的最大文件字节数。未匹配格式将回退至 fallback 上限。
    """
    clean_suffix = normalize_suffix(suffix)
    format_map, fallback, _ = _resolve_config_map(config)
    return format_map.get(clean_suffix, fallback)


def get_upload_read_limit(
    suffix: str | None,
    config: ImportPolicyConfig | dict[str, Any] | None = None,
) -> int:
    """计算用于主路由防护读取的字节数上限（即 limit + 1）。
    
    用于在文件流读取时传入 file.read(limit + 1)，一旦读取达到该长度，
    即可确证文件超限，直接截断并返回 413，无需继续读取后续全量文件流。
    """
    max_bytes = get_max_bytes_for_suffix(suffix, config)
    return max_bytes + 1


def is_supported_suffix(
    suffix: str | None,
    config: ImportPolicyConfig | dict[str, Any] | None = None,
) -> bool:
    """判断指定扩展名是否为系统允许导入的格式。"""
    clean_suffix = normalize_suffix(suffix)
    if not clean_suffix:
        return False
    _, _, allowed_suffixes = _resolve_config_map(config)
    return clean_suffix in allowed_suffixes


def get_capabilities_max_bytes(
    config: ImportPolicyConfig | dict[str, Any] | None = None,
) -> dict[str, int]:
    """返回符合 R3 契约规范的逐格式能力表字典。
    
    形如:
    {
        ".txt": 2097152,
        ".md": 2097152,
        ".pdf": 8388608,
        ".docx": 8388608
    }
    """
    format_map, _, _ = _resolve_config_map(config)
    # 保证包含核心四格式
    result: dict[str, int] = {}
    for ext in [".txt", ".md", ".pdf", ".docx"]:
        result[ext] = format_map.get(ext, DEFAULT_MAX_BYTES_BY_FORMAT[ext])
    return result
