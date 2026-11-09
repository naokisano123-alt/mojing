# 墨境｜M3 第一轮（Wave 1）世界事件账本、AI 初始局势与因果校验交付报告

- **任务角色**：`Gemini Marcin`
- **功能代码基线（code_baseline）**：`9388278f8a334f457e92ed820cc5b2456c76dfd8`
- **任务书快照（snapshot_commit）**：`20ba6c6fab40301252925acdae1d62471cc520ee`（PR #23 合并后的主分支快照）
- **契约与任务依据**：
  - `docs/collaboration/M3_WAVE1.md`
  - `docs/collaboration/M3_WAVE1_GEMINI_MARCIN.md`
  - `docs/contracts/M3_WAVE1_API.md`（版本 `v1`）
  - `docs/M3_DESIGN_DRAFT.md`、`docs/reports/codex-m3-architecture-review.md`
- **独占业务与测试路径**：
  - `server/app/m3/events/__init__.py`
  - `server/app/m3/events/types.py`
  - `server/app/m3/events/errors.py`
  - `server/app/m3/events/schema.py`
  - `server/app/m3/events/validator.py`
  - `server/app/m3/events/service.py`
  - `server/app/m3/events/ai.py`
  - `server/app/m3/events/fixtures.py`
  - `server/app/m3/events/integration_fixture.json`（可直接供集成测试读取的北境贸易输入与期望输出包）
  - `server/tests/m3/events/__init__.py`
  - `server/tests/m3/events/test_validator_and_ledger.py`
  - `server/tests/m3/events/test_ai_and_causal_chain.py`
  - `server/tests/m3/events/test_snapshot_and_branches.py`
  - `docs/reports/gemini-marcin-m3-wave1.md`（本报告）
- **共享文件零修改声明**：严格遵守协作规则第 1、4 条，未修改 `server/app/db.py`、`server/app/main.py`、`server/app/adventures/**`、`server/app/world_transfer.py`、`server/app/model_gateway/**`、前端公共文件、依赖锁文件、Android 工程或 CI 配置；未引入任何新增第三方依赖。

---

## 1. 交付能力与契约对照

| 契约 / 任务书要求 | 对应实现入口 | 实现说明 |
| --- | --- | --- |
| **纯计算提议校验器** `validate_proposal(payload, context)` | `app.m3.events.validate_proposal` | 纯内存计算，无 I/O；拦截未知字段、跨实例引用、过期版本、伪造来源、任意 JSON 路径写入、时间戳覆写、时间倒退、终态修改、自引用与多跳因果环（三色 DFS）、种子主线数量越界（3～6 条）与活跃临时事件上限。 |
| **原子应用入口** `apply_proposal(conn, instance_id, proposal)` | `app.m3.events.apply_proposal` | 接收外部打开的 `sqlite3.Connection`，内部使用 `SAVEPOINT` 保证单份提议部分非法时整体回滚无写入，且绝不擅自调用 `conn.commit()`；由服务端统一分配 `event_id`、`consequence_id`、`revision` 与实际提交时间。 |
| **到期事件分页读取** | `app.m3.events.list_due_events`（别名 `get_due_events`） | 仅筛选指定 `instance_id` 下非终态且 `next_update_minute <= world_minute` 的到期事件，按 `(next_update_minute ASC, created_minute ASC, event_id ASC)` 稳定排序，同时支持 `offset` 与不透明游标 `cursor` 分页。 |
| **追加式版本修订历史** | `app.m3.events.list_event_revisions` | 每次创建或更新均向 `world_event_revisions` 追加一条不可变快照，记录版本号、操作类型、游戏分钟与变更原因。 |
| **AI 初始局势与事件推进** | `FakeEventModelClient`、`OpenAIEventModelClient`、`generate_initial_situation`、`generate_event_advancement` | 定义受限输入 `EventSeedRequest` 与 `EventAdvanceRequest`（仅传入到期事件、局部相关事实与授权 Canon 资料，防全量塞入与提示词注入），网络与反序列化严格留在 SQLite 写事务外。 |
| **北境贸易因果链与重大转折前置条件** | `run_northern_trade_causal_chain`、`trace_causality`、`register_precondition` | 完整落地「邻国提高关税 -> 商队改道 -> 本地供应减少 & 商人调整采购与挂牌价」四步演进，每条后果绑定 `source_event_id` 与 `source_event_revision`；对战争（`war`）、政权更替（`regime_change`）等重大转折强制检查 `omen -> escalation -> turning_point -> aftermath` 阶段演进与已登记前置条件。 |
| **可移植快照与多分支恢复** `snapshot` / `restore` | `app.m3.events.snapshot`、`app.m3.events.restore` | 导出含 `format_version="m3.events.v1"` 的纯数据快照（不含凭据与任务租约）；支持从同一检查点恢复两个独立实例并对同一 `event_id` 作不同方向推进，支持跨库迁移 `source_id_map` 重映射。 |

---

## 2. 数据库表结构与实例隔离设计

在 `server/app/m3/events/schema.py` 中通过 `ensure_events_schema(conn)` 幂等创建以下表（逐条 `conn.execute` 执行 DDL，避免 `executescript` 隐式提交外层事务）：

1. **`world_events`**：联合主键 `PRIMARY KEY (instance_id, event_id)`，外键引用 `world_instances(id)`。采用 `(instance_id, event_id)` 复合主键使检查点恢复分支或导入新实例时，领域对象 `event_id` 可在新实例命名空间内安全复用且与原实例严格隔离。
2. **`world_event_revisions`**：联合主键 `PRIMARY KEY (instance_id, event_id, revision)`，保存不可变历史版本快照。
3. **`world_event_consequences`**：联合主键 `PRIMARY KEY (instance_id, consequence_id)`，保存受限领域（`trade_route`、`regional_supply`、`market_price`、`npc_inventory`、`shop_status`、`faction_relation`、`npc_schedule`、`politics`、`security`）的可追溯后果及 `source_event_id`、`source_event_revision`。
4. **`world_event_preconditions`**：联合主键 `PRIMARY KEY (instance_id, precondition_id)`，登记战争、政权更替等重大转折所需的前置条件。

---

## 3. 测试验证与四种证据区分

### 3.1 实际执行的测试命令与结果

在当前沙箱环境（Python `3.11.2`）下实际执行：

```bash
PYTHONPATH=server python3 -m unittest discover -s server/tests/m3/events -t server -v
```

- **实跑结果**：`Ran 16 tests in 0.053s — OK`（16 项测试全部通过，0 失败，0 错误）。
- **逐项覆盖清单**：
  1. `test_seed_thread_count_boundaries`：初始种子数量 `<3`（2 条）与 `>6`（7 条）拒绝，3 条与 6 条通过。
  2. `test_source_fabrication_rejected`：伪造 `chunk:forged_999`、非法前缀、初始主线空来源均抛出 `422 SOURCE_FABRICATED`。
  3. `test_stale_revision_conflict_409`：使用过期 `expected_event_revision` 更新抛出 `409 EVENT_REVISION_CONFLICT`。
  4. `test_cross_instance_reference_rejected`：跨实例更新事件、跨实例引用父事件、跨实例读取均抛出 `422 CROSS_INSTANCE_REFERENCE`。
  5. `test_self_reference_and_causal_cycle_rejected`：自引用（`A->A`）与多跳因果环（`A->C->B->A`）均抛出 `422 CAUSAL_SELF_REFERENCE` / `CAUSAL_CYCLE_DETECTED`。
  6. `test_legal_completion_spawns_derived_incident_and_terminal_protection`：事件合法 `completed` 后衍生临时事件，且终态事件再次修改抛出 `422 EVENT_TERMINAL_STATE`。
  7. `test_atomic_rollback_when_partial_proposal_invalid_and_preserve_outer_tx`：多操作提议第 3 项非法时，前 2 项通过 `SAVEPOINT` 完整回滚无残留写入，且外层 `conn.in_transaction` 保持未提交。
  8. `test_reject_player_ledger_arbitrary_path_and_future_backfill`：拦截玩家金钱/背包修改（`PLAYER_LEDGER_PROTECTED`）、任意 JSON 路径（`ARBITRARY_PATH_FORBIDDEN`）、时间戳覆写（`TIMESTAMP_OVERRIDE_FORBIDDEN`）及非法 `next_update_minute`。
  9. `test_due_events_pagination_and_revision_history`：验证到期事件 `offset` 与 `cursor` 分页，以及 `rev 1 -> 2 -> 3` 追加式修订历史。
  10. `test_incident_limit_enforced`：验证未完成临时事件上限 `INCIDENT_LIMIT_EXCEEDED`。
  11. `test_fake_model_seed_and_advance_lifecycle`：验证 `FakeEventModelClient` 生成 5 条初始主线与推进到期事件。
  12. `test_openai_event_model_client_with_mock_transport`：使用 `httpx.MockTransport` 验证 OpenAI 兼容响应解析、代码块剥离、usage 提取、伪造来源拦截、非 JSON 报错与 API Key 脱敏。
  13. `test_northern_trade_causal_chain_traceability_and_fixture_file`：验证北境贸易四步因果链、6 条可追溯后果及 `integration_fixture.json` 一致性。
  14. `test_major_turnpoint_stages_and_preconditions`：验证战争（`war`）重大转折不可跳阶段生成或推进、必须先通过 `register_precondition` 登记前置条件。
  15. `test_restore_two_branches_from_same_checkpoint_and_diverge`：从同一检查点快照恢复 `inst_branch_a` 与 `inst_branch_b` 两个实例并对同一事件作不同推进，验证原实例与两分支完全隔离，且存档后原实例的新变更不混入旧快照。
  16. `test_snapshot_restore_source_id_remapping_and_corrupted_rollback`：验证跨库导入 `source_id_map` 映射及损坏快照恢复失败时的 `SAVEPOINT` 原子回滚。

### 3.2 四种证据区分与待集成项说明

| 证据类别 | 当前状态 | 详细说明 |
| --- | --- | --- |
| **Fake 证据** | **已实跑通过** | `FakeEventModelClient` 及北境贸易 fixture 在内存 SQLite 下完整跑通种子生成、到期推进、衍生临时事件、因果追溯与多分支恢复。 |
| **Mock 证据** | **已实跑通过** | `OpenAIEventModelClient` 通过 `httpx.MockTransport` 完成 OpenAI 兼容协议响应解析、错误码映射、伪造来源拦截与凭据脱敏验证。 |
| **真实服务商证据** | **未运行（待用户配置开启）** | 沙箱无外网且按安全规范不在仓库与自动化测试中内置真实 API Key；待集成后由用户主动配置模型连接开启。 |
| **安卓真机 / 跨模块全量回归** | **待 DeepSeekHarness 集成后运行** | 当前沙箱未预装 `pytest` 可执行脚本（通过标准库 `unittest` 完整运行本模块 16 项测试；测试类全部继承 `unittest.TestCase` / `IsolatedAsyncioTestCase`，与 `pytest` 100% 兼容）。跨模块端到端闭环（M3.0 底座 + M3.1 事件 + M3.2 队列 + M3.3 知识投影）及 Android APK 构建待 DeepSeekHarness 合入各分支后在 CI 统一执行。 |

---

## 4. 公共接线需求（供 DeepSeekHarness / Gemini Li / Gemini Ma 对接）

1. **数据库初始化（`server/app/db.py`）**：
   在 `init_db` 中调用 `from .m3.events.schema import ensure_events_schema; ensure_events_schema(conn)`。
2. **实例创建与后台种子/推进任务对接（`server/app/m3/core/` & `server/app/m3/pulse/`）**：
   - 事务外：使用 `generate_initial_situation(seed_req, model_client)` 或 `generate_event_advancement(adv_req, model_client)` 获取已通过 `validate_proposal` 校验的 `ValidatedEventProposal`；
   - 短写事务内：在核对实例版本与队列租约令牌后，调用 `apply_proposal(conn, instance_id, validated_proposal, world_minute=target_world_minute)`。
3. **检查点保存、分支恢复与文件迁移（`server/app/adventures/checkpoints.py` & `server/app/world_transfer.py`）**：
   - 保存检查点或导出世界存档时：调用 `events_snap = snapshot(conn, instance_id)` 存入实例快照载荷；
   - 恢复检查点分支或导入存档新建实例时：在同一写事务内调用 `restore(conn, new_instance_id, events_snap, source_id_map=...)`。
4. **集成测试直接消费数据包**：
   - 可直接调用 `from app.m3.events import run_northern_trade_causal_chain, get_northern_trade_integration_bundle`，或直接读取 `server/app/m3/events/integration_fixture.json`。
