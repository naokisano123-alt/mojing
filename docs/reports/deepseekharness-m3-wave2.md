# M3 第二轮 · DeepSeekHarness 集成报告

- **集成分支**：`feat/m3-wave2-combined`（基于 `feat/m3-wave2-wiring` + 三个 import 分支）
- **快照**：`d333af8`（PR #25）
- **契约**：`docs/contracts/M3_WAVE2_API.md`

## 1. 三包核验

| 包 | 文件数 | 声明基线 | 校验方式 | 结果 |
| --- | --- | --- | --- | --- |
| gemini-marcin-m3-wave2-8a95587 | 10 | 8a95587 / 快照 d333af8 | 包内无 manifest.json；按报告清单逐文件比对 | ✅ 清单一致 |
| gemini-li-m3-wave2-d333af8 | 12 | 8a95587 / 快照 d333af8 | 同上 | ✅ 清单一致 |
| gemini-ma-m3-wave2 | 20 | 8a95587 / 快照 d333af8 | 同上 | ✅ 清单一致 |

**偏差记录**：三个 ZIP 都没有 `manifest.json`（本轮规则要求逐路径哈希），只附了交付说明。我按说明中的文件清单核对，未发现越界文件；建议下一轮要求 manifest。

三包各自 import 分支、作者保留：`import/m3-wave2-marcin` `809254a`、`import/m3-wave2-li` `6bcdd25`、`import/m3-wave2-ma` `7de3750`。

## 2. 公共接线（DSH）

| 项 | 实现 |
| --- | --- |
| 控制面 | `pulse_preview.register_routes` 的 status / connect / enable / activity **全部委托给 Li 的 `pulse.control`**（`get_control_status` / `connect_pulse_session` / `set_pulse_enabled` / `set_pulse_activity`），不再维护第二套语义 |
| 默认关闭 | 开关默认 off；未绑定会话启用返回 409 `MODEL_SESSION_UNBOUND`；只读状态不续期租约或会话 |
| 组合回调 | worker 回调改为分发：模拟旅程沿用首轮无费用演示；正式旅程使用 `build_combined_pulse_callbacks`（events `read_snapshot`/`validate_proposal`/`apply_proposal` + knowledge 同名函数） |
| 异步生命周期 | 启停路由改为 async，`worker.start()` 在事件循环内启动；关闭时取消排队/在途任务 |
| 手机入口 | 冒险记录页新增「世界脉动」折叠区，展开才挂载 Ma 的 `WorldPulsePanel`（关闭时不发请求） |
| 复用会话 | `create_app(narration_store=…)` 注入时可用；未注入返回明确错误，等后续惰性接入 |

## 3. 集成修正（integration_fix）

- 我的控制面测试：错误码从自造的 `PULSE_NOT_CONNECTED` 改为 Li 的 `MODEL_SESSION_UNBOUND`；启用前先发活动租约（否则状态是 `offline`，符合契约）
- 首轮 `test_pulse_preview` 的状态断言从整字典相等改为子集（状态字段加性扩展）
- status 状态允许 `ready`/`working`（worker 循环在跑时为 working）

## 4. 验证

| 项 | 结果 |
| --- | --- |
| 三包各自测试 | events 23、pulse 25、knowledge 15（本地全绿） |
| 前端（Ma 新增） | world-knowledge 5 + world-pulse 9，合计 UI **59 passed** |
| 后端全量（组合分支） | **405 passed** |
| 生产构建 | 通过 |
| CI | 见提交对应 run |

## 5. 未验证 / 待办

- **真实模型付费调用**：本轮无 Key，Live 路径只完成接线与 Fake/Mock 覆盖；需用户配置后验收
- 未做完整 HTTP 「连接→启用→Seed→Advance→消息→叙事取用」的 Live 全链路（受限于无真实会话；模块级 Fake harness 由三包各自覆盖）
- 复用叙事会话需要惰性 store 注入
- 安卓真机、线上部署未验证
- 三包缺 manifest，下一轮需补
