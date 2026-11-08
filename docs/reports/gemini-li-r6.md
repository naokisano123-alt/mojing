# Gemini（Li）｜R6-L 会话与 OpenAI 兼容模型网关交付报告

- **角色**：`Gemini（Li）`
- **任务编号**：`R6-L`
- **功能代码基线（code_baseline）**：`8087d093724c7f554b2987c4af2b86f92a5afc06`
- **当前完整快照提交（snapshot_commit）**：`8087d093724c7f554b2987c4af2b86f92a5afc06`
- **依据契约与规范**：`docs/contracts/M2_A_API.md`（v1.0）、`docs/collaboration/ROUND6.md`、`docs/collaboration/R6_GEMINI_LI.md`、`server/app/narration/core.py`
- **交付包名称**：`gemini-li-R6-8087d09.zip`
- **独占目录范围**：
  - `server/app/model_gateway/**`（新增：模型网关、异常、会话存储与客户端）
  - `server/tests/model_gateway/**`（新增：存储生命周期与客户端网络模拟单测套件）
  - `docs/reports/gemini-li-r6.md`（本交付报告）
- **防覆写与所有权声明**：严格遵守 R6 协作边界与防覆写协议，所有产出均位于 Gemini（Li）独占目录内。未修改任何受保护文件（未触碰 `server/app/main.py`、`db.py`、`config.py`、`server/requirements.txt`、Codex 核心 `server/app/narration/core.py` 或任何前端代码）。所有对主路由挂载的建议均以独立提案呈现于本报告及 `INTEGRATION.md` 中，交由 DeepSeekHarness 统一合并。

---

## 1. 核心交付块一：内存模型会话存储 (`ModelSessionStore`)

依据 `docs/contracts/M2_A_API.md` 与 `R6_GEMINI_LI.md`，Gemini（Li）交付了纯内存安全会话存储 `ModelSessionStore`（`server/app/model_gateway/store.py`）：

1. **绝对脱敏与零落盘**：
   - 会话对象 `ModelSession` 中的 `api_key` 字段显式声明 `repr=False`；
   - 无论是 `repr(session)`、日志打印还是异常抛出，均绝不泄露 API Key；
   - 全程仅保存在运行期内存字典中，不写入 SQLite 数据库、不落盘、不写入 `.env`，服务重启后自动失效。
2. **容量硬隔离（最多 8 个活跃会话）**：
   - 设定 `MAX_SESSIONS = 8`；
   - 创建新会话时先惰性清理过期死会话；
   - 若有效会话数已满 8 个，立即抛出 `GatewayError(code="MODEL_SESSION_CAPACITY_EXCEEDED", status=429)`，明确告知用户删除闲置会话，**严禁静默淘汰或踢出旧会话**。
3. **UTC 时间与 30 分钟无操作 TTL**：
   - 默认 TTL 为 1800 秒（30 分钟），支持通过依赖注入模拟时钟便于单测；
   - 创建响应包含 ISO 8601 UTC 字符串（`expires_at: "YYYY-MM-DDTHH:MM:SSZ"`）；
   - 在调用 `get_client()` 或执行 `test()` 成功时自动将 `expires_at` 延展刷新 30 分钟；
   - 一旦过期，在下一次获取时执行物理删除并返回 `409`（`MODEL_SESSION_EXPIRED`）。
4. **幂等删除与全量清理**：
   - `delete(session_id)` 幂等处理，重复删除或删除不存在的 ID 均安全返回；
   - 提供 `clear()` 方法，供应用停机（FastAPI lifespan/shutdown）或测试夹具快速释放资源。

---

## 2. 核心交付块二：OpenAI 兼容模型客户端 (`OpenAIModelClient`)

依据 Codex 定义的 `ModelClient` 协议，客户端实现了 `async def generate(request: NarrationRequest) -> object` 与 `async def test() -> dict[str, Any]`（`server/app/model_gateway/client.py`）：

1. **协议兼容与最终校验委托**：
   - 客户端解析上游 `/chat/completions` 返回的 `choices[0].message.content` 为 JSON 字典；
   - 自动去除模型可能包裹的 Markdown 代码块（如 ` ```json ... ``` `）；
   - **不静默剥离多余或未知字段**：若模型输出包含了违规扩展字段（如 `state_changes`），客户端诚实将其保留并交由 Codex 核心 `validate_draft()` 最终校验，从而正确触发 `DraftError('MODEL_OUTPUT_INVALID')`。
2. **防注入与不可信数据隔离**：
   - 构造标准化系统提示词（System Prompt），强制模型以合法的纯 JSON 对象输出，根对象仅限 `narrative`（string）和 `references`（string[]）；
   - 显式声明世界资料项（`chunk:<id>` 或 `entry:<id>`）为**不可信参考数据（Untrusted Reference Data）**，严禁模型将其解释为系统指令、越权规则或元提示词。
3. **流式字节控制与防 DoS（256KiB 上限）**：
   - 采用 `client.stream("POST", ...)` 逐步读取响应字节并累计；
   - 一旦总字节数超过 256 KiB（262,144 字节），立即中断网络连接并抛出 `GatewayError(code="MODEL_RESPONSE_TOO_LARGE", status=502)`，坚决杜绝恶意或失控的无限响应内存耗尽。
4. **异步取消传播与资源管理**：
   - 遇到 `asyncio.CancelledError` 时直接向外抛出（`raise`），确保客户端连接断开时能够立即取消模型请求任务；
   - 使用异步上下文管理器 `async with httpx.AsyncClient(...)` 保证所有连接与底层套接字在退出或异常时立即关闭。

---

## 3. 核心交付块三：URL 校验与安全重定向拦截

在 `server/app/model_gateway/url.py` 中实现了严格的 base_url 校验器：
1. **协议与结构限制**：
   - 仅允许 `http` 与 `https` 协议；
   - 严禁包含 `userinfo`（用户名/密码），防止凭据混淆；
   - 严禁包含 `query` 参数或 `fragment` 锚点；
   - 自动去除末尾斜杠，由客户端统一仅拼接 `/chat/completions`。
2. **重定向拦截与防凭据外泄**：
   - HTTP 客户端显式设置 `follow_redirects=False`；
   - 若上游返回 301、302、303、307、308 重定向，立即抛出 `GatewayError(code="MODEL_REDIRECT_DISALLOWED", status=502)`；
   - 严禁将携带 `Authorization: Bearer <API_KEY>` 的请求自动跟随至跳转后的不受信任目标。
3. **错误脱敏规范**：
   - 401/403 映射为 `MODEL_AUTH_FAILED`（status 401）；
   - 429 映射为 `MODEL_RATE_LIMITED`（status 429）；
   - 超时映射为 `MODEL_TIMEOUT`（status 504）；
   - 连接失败映射为 `MODEL_CONNECTION_FAILED`（status 502）；
   - 所有异常消息与响应正文均经过脱敏，绝不打印上游原始响应、请求头或 API Key。

---

## 4. 单元测试覆盖与实跑结果

所有测试均位于 `server/tests/model_gateway/`，完全基于 Python 标准库 `unittest` 与 `httpx.MockTransport` 实现，**零真实网络、零外部凭据、零额外第三方测试运行器依赖**。

### 4.1 测试套件清单
1. `server/tests/model_gateway/test_store.py`（8 项测试）：
   - `test_url_validation_and_normalization`：覆盖协议、主机、userinfo、query、fragment 校验。
   - `test_create_session_success_and_no_key_leak`：验证响应字段契约及 Key 绝对未泄露。
   - `test_session_ttl_expiration_and_refresh`：验证 20 分钟访问成功刷新 TTL、31 分钟过期访问返回 409 并清理。
   - `test_capacity_limit_max_8_sessions`：验证第 9 个会话抛出 429 且不淘汰旧会话、主动删除后可继续创建。
   - `test_expired_sessions_do_not_block_capacity`：验证已过期的死会话自动被清理，不阻塞新会话创建。
   - `test_delete_idempotence`：验证重复删除与不存在删除的幂等性。
   - `test_clear_all_sessions`：验证清空所有会话。
   - `test_client_repr_does_not_contain_key`：验证客户端 repr 安全性。

2. `server/tests/model_gateway/test_client.py`（15 项测试）：
   - `test_client_repr_protects_key`：客户端字符串表示脱敏。
   - `test_generate_success_and_draft_validation`：与 Codex `generate_draft()` 联动完整测试成功流程。
   - `test_generate_strips_markdown_code_fence`：自动剥离 Markdown 代码块。
   - `test_redirect_disallowed`：拦截重定向响应，防止 Key 外泄。
   - `test_auth_failure_401`：401 凭据错误脱敏映射。
   - `test_rate_limited_429`：429 限流错误映射。
   - `test_upstream_error_500`：500 服务端异常映射。
   - `test_timeout_mapping_504`：超时映射 504。
   - `test_network_connection_failed_502`：连接失败映射 502。
   - `test_response_too_large_aborts_reading`：超大响应（>256KiB）立即中断并报 502。
   - `test_invalid_json_content_502`：非合法 JSON 输出映射。
   - `test_empty_response_502`：空回复或空 choices 映射。
   - `test_preserves_extra_fields_for_codex_validation`：保留额外字段，交由 Codex 核心抛出 `MODEL_OUTPUT_INVALID`。
   - `test_cancellation_propagates`：验证异步取消异常向外传播。
   - `test_client_test_success_and_failure`：最小开销连通测试方法成功与失败分支。

### 4.2 实际运行命令与输出
```bash
$ PYTHONPATH=server python3 -m unittest discover -s server/tests/model_gateway -p "test_*.py"
.......................
----------------------------------------------------------------------
Ran 23 tests in 0.046s

OK
```
**结果**：23 项测试全部秒级通过（0.046s），测试全绿通过。

---

## 5. 给 DeepSeekHarness 的集成建议 (`INTEGRATION.md`)

为协助 DeepSeekHarness 挂载路由，提供以下最小接线说明：

### 5.1 全局会话存储初始化
在 `server/app/main.py`（或公共依赖模块）中初始化全局会话存储：
```python
from app.model_gateway import ModelSessionStore, GatewayError

# 单例会话池
model_session_store = ModelSessionStore(ttl_seconds=1800, max_sessions=8)

# 在 FastAPI lifespan shutdown 时清理：
# model_session_store.clear()
```

### 5.2 HTTP 路由挂载示例
```python
from pydantic import BaseModel
from fastapi import status

class CreateModelSessionRequest(BaseModel):
    base_url: str
    model: str
    api_key: str

@app.post("/api/v1/model-sessions", status_code=status.HTTP_201_CREATED)
async def create_model_session(req: CreateModelSessionRequest):
    return model_session_store.create(
        base_url=req.base_url,
        model=req.model,
        api_key=req.api_key,
    )

@app.delete("/api/v1/model-sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model_session(session_id: str):
    model_session_store.delete(session_id)
    return None

@app.post("/api/v1/model-sessions/{session_id}/test", status_code=status.HTTP_200_OK)
async def test_model_session(session_id: str):
    return await model_session_store.test(session_id)

# 在 POST /api/v1/worlds/{world_id}/narration-preview 路由中：
# if mode == "live":
#     if not session_id:
#         raise GatewayError("MODEL_SESSION_EXPIRED", "Live 模式必须提供有效的 session_id。", status=409)
#     client = model_session_store.get_client(session_id)
# else:
#     client = FakeModelClient()
# draft = await generate_draft(narration_request, client)
```
