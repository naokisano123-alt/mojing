"""OpenAI 兼容 API 基础地址（base_url）校验与规范化。"""

from __future__ import annotations

import urllib.parse

from app.model_gateway.errors import GatewayError


def validate_and_normalize_base_url(raw_url: str) -> str:
    """校验并规范化用户传入的 API 根地址。

    规则要求：
    - 仅允许 http 与 https 协议；
    - 必须包含有效的主机名（允许本地 loopback 与局域网私有地址）；
    - 禁止包含 userinfo（用户名/密码）、query 查询参数与 fragment 片段标识；
    - 规范化去除末尾多余斜杠，仅用于后续直接拼接 /chat/completions。
    """
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise GatewayError(
            code="INVALID_BASE_URL",
            message="API 基础地址不能为空。",
            status=422,
        )

    clean_url = raw_url.strip()
    try:
        parsed = urllib.parse.urlsplit(clean_url)
        host, port = parsed.hostname, parsed.port
        if (not host or (port is not None and not 1 <= port <= 65535)
                or any(c.isspace() or ord(c) < 32 for c in clean_url)
                or chr(92) in clean_url):
            raise ValueError()
    except ValueError:
        raise GatewayError('INVALID_BASE_URL', 'API 地址的主机或端口无效。', status=422) from None

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise GatewayError(
            code="INVALID_BASE_URL",
            message="API 基础地址协议必须为 http 或 https。",
            status=422,
        )

    if not parsed.netloc:
        raise GatewayError(
            code="INVALID_BASE_URL",
            message="API 基础地址缺少有效主机名。",
            status=422,
        )

    if '@' in parsed.netloc:
        raise GatewayError(
            code="INVALID_BASE_URL",
            message="API 基础地址禁止包含用户信息（用户名或密码）。",
            status=422,
        )

    if parsed.query:
        raise GatewayError(
            code="INVALID_BASE_URL",
            message="API 基础地址禁止包含查询参数（query）。",
            status=422,
        )

    if parsed.fragment:
        raise GatewayError(
            code="INVALID_BASE_URL",
            message="API 基础地址禁止包含片段标识（fragment）。",
            status=422,
        )

    # 去除 path 末尾斜杠，保留规范路径
    normalized_path = parsed.path.rstrip("/")
    return f"{scheme}://{parsed.netloc}{normalized_path}"
