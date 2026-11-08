# 墨境第七轮集成报告（R7-D，M2-A 可靠性）

- **分支**：`feat/r7-integration`（从 R7 任务快照 `b812c47` 起）
- **R6 基线**：`f379293`（feat/r6-integration，尚未合 main）
- **契约**：`docs/contracts/M2_A_API.md` v1.0

## 1. R7 审阅项的处置

| ID | 问题 | 状态 | 证据 |
| --- | --- | --- | --- |
| R7-01 | 客户端断开不取消生成任务 | **已修（DSH）** | 路由注入 `Request`，`_await_with_disconnect` 竞争生成任务与断开监听；断开/超时都取消并 `await` 清理；新增真实 ASGI `http.disconnect` 用例，断言上游 stub 收到 `CancelledError` |
| R7-02 | 缺会话返回 404，契约写 409 | **已修（DSH）** | 改为 409 `MODEL_SESSION_EXPIRED`，同步修改固化 404 的旧用例 |
| R7-03 | 未检查 `finish_reason` | 待 Li（R7-L） | — |
| R7-04 | 面板 connect/test/delete 缺世代与卸载保护 | 待 Marcin（R7-M） | — |
| R7-05 | 上下文全量 fetchall | 待 Ma（R7-A） | — |
| R7-06 | entry 来源只切页签、不定位条目 | 待接线（等 Marcin 的 `initialEntryId`） | — |
| R7-07 | 缺真实 store→MockTransport→核心 全栈闭环 | **已补（DSH）** | `tests/test_narration_fullstack.py` 6 条：成功、伪造引用 502、401、429、504、预览前后全表快照不变 |
| R7-08 | `urlsplit` 坏 IPv6 可能抛 ValueError | 待 Li（R7-L） | — |

## 2. 本轮新增/修改

| 文件 | 改动 |
| --- | --- |
| `server/app/main.py` | 断开感知等待器；`test_model_session` 同样受 60s + 断开边界；会话缺失 409；本地变量改名避免与 `Request` 冲突 |
| `server/tests/test_narration_api.py` | 404→409；新增 ASGI 断开取消用例 |
| `server/tests/test_narration_fullstack.py` | 新增全栈 mock 闭环（真实 `ModelSessionStore` + `httpx.MockTransport` + 真实 `build_context` + `generate_draft`） |

## 3. 实际回归

| 命令 | 结果 |
| --- | --- |
| `pytest` | **243 passed**（236 + 1 断开 + 6 全栈） |
| `npm test` | 62 passed |
| `npm run test:ui` | 通过（组件与装配用例保留） |
| `npm run build` | 通过 |

真实服务商未调用；Live 用 MockTransport 全栈验证，不依赖真实 Key。

## 4. 待三包

R7-M / R7-L / R7-A 三包到位后按独立 import 分支核验合并，再补：`finish_reason` 校验、URL 边界、上下文有界读取、面板异步生命周期、entry 精确定位。

## 5. 未做

- 安卓真机复测
- 线上部署
- 真实付费服务商连通测试（需用户 Key）
