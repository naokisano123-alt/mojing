# 墨境｜M3 第二轮（Wave 2）真实模型事件推演与权威校验交付报告

- **任务角色**：`Gemini Marcin`
- **功能代码基线（code_baseline）**：[`8a95587a778e3849385bc413632d402ecf09972f`](https://github.com/naokisano123-alt/mojing/commit/8a95587a778e3849385bc413632d402ecf09972f)（PR #24 合入 `main`）
- **任务书快照（snapshot_commit）**：[`d333af8757212894223b02de75eb2915dc40a4af`](https://github.com/naokisano123-alt/mojing/archive/d333af8757212894223b02de75eb2915dc40a4af.zip)（PR #25 合入 `main`）
- **契约与任务书依据**：
  - [`docs/collaboration/M3_WAVE2.md`](../collaboration/M3_WAVE2.md)
  - [`docs/collaboration/M3_WAVE2_GEMINI_MARCIN.md`](../collaboration/M3_WAVE2_GEMINI_MARCIN.md)
  - [`docs/contracts/M3_WAVE2_API.md`](../contracts/M3_WAVE2_API.md)
  - [`docs/M3_DESIGN_DRAFT.md`](../M3_DESIGN_DRAFT.md)、[`docs/reports/codex-m3-wave1-integration.md`](codex-m3-wave1-integration.md)

## 1. 交付文件清单与变更声明

本交付包仅包含 `server/app/m3/events/`、`server/tests/m3/events/` 与本报告中的改动与新增文件，未触碰任何共享入口、数据库主初始化、前端代码、Android 工程、依赖锁或 CI 配置。

| 文件路径 | 操作类型 | 说明 |
| --- | --- | --- |
| `server/app/m3/events/__init__.py` | 修改（Modify） | 导出 `read_snapshot`、`select_canon_sources`、`build_pulse_event_prompts`、`PULSE_EVENT_OUTPUT_SCHEMA`、`PROMPT_EXAMPLES`、`run_wave2_mock_causal_chain` 等 Wave 2 接口。 |
| `server/app/m3/events/errors.py` | 修改（Modify） | 新增 `WorldRevisionConflictError`（`409 WORLD_REVISION_CONFLICT`）与 `PulseSourceRequiredError`（`409/422 PULSE_SOURCE_REQUIRED`）。 |
| `server/app/m3/events/types.py` | 修改（Modify） | 扩展 `CanonSourceItem`、`ProposalValidationContext`、`ValidatedEventProposal`、`EventSeedRequest`、`EventAdvanceRequest`，增加 Canon 挑选上限/字符预算常量及 `mode`/`kind`、`revision`/`world_revision` 双向属性映射。 |
| `server/app/m3/events/validator.py` | 修改（Modify） | 支持 `PulseWorker` 上下文及 `read_snapshot` 嵌套结构，新增 `mode`/`kind`、`world_revision`/`base_world_revision`、`world_minute`/`target_world_minute` 回显核对、存量临时事件叠加计数及传播封套安全校验。 |
| `server/app/m3/events/service.py` | 修改（Modify） | 实现 `select_canon_sources`、`read_snapshot`（只读快照、最小因果邻域、空/超前跳过、无资料阻塞）与支持 4 参数 `job` 的 `apply_proposal` 事务内实时复核。 |
| `server/app/m3/events/ai.py` | 修改（Modify） | 交付 `PULSE_EVENT_OUTPUT_SCHEMA`、`build_pulse_event_prompts`、`PROMPT_EXAMPLES`、`build_fake_pulse_generator`，并使 `FakeEventModelClient` 从用户世界真实 Canon 动态生成初始主线（不套用静态示例标题）。 |
| `server/app/m3/events/fixtures.py` | 修改（Modify） | 新增真实世界表初始化 `seed_northern_trade_world_canon`、Mock 回包序列 `build_wave2_mock_model_responses` 与四步端到端执行器 `run_wave2_mock_causal_chain`。 |
| `server/app/m3/events/wave2_mock_causal_chain_fixture.json` | 新增（Add） | 「Seed -> 邻国加税 -> 商队改道 -> 本地供给变化」Mock 回包四步链的可重放输入、快照、Mock 响应与期望输出快照包。 |
| `server/tests/m3/events/test_wave2_pulse_and_mock_chain.py` | 新增（Add） | 覆盖 M3 Wave 2 全部 7 大验收场景的自动化单元测试。 |
| `docs/reports/gemini-marcin-m3-wave2.md` | 新增（Add） | 本交付与测试报告。 |

- **删除或重命名文件**：无（0 删除，0 重命名）。

## 2. 核心实现与契约对照

### 2.1 `PulseWorker` 三回调与 `mode`/`kind`、`revision`/`world_revision` 映射

| 回调边界 | 实现入口 | 适配与契约行为 |
| --- | --- | --- |
| **取快照** | `app.m3.events.read_snapshot(conn, instance_id, job=None, ...)` | 只读查询 `world_instances` 已确认的 `world_minute` 与 `revision`，对外同时映射 `world_revision` / `base_world_revision` / `revision` 及 `kind` / `mode`。空资料或无到期事件时返回 `status`（`blocked` / `skipped`）、`reason_code` 与中文 `reason_message`。 |
| **纯校验** | `app.m3.events.validate_proposal(proposal, context)` | 在 SQLite 写事务外运行；直接兼容 `ProposalValidationContext`、旧版字典、`read_snapshot` 返回字典以及 `PulseWorker` 传入的 `{"instance_id", "kind", "base_world_revision", "target_world_minute", "snapshot"}` 结构。 |
| **原子应用** | `app.m3.events.apply_proposal(conn, instance_id, validated, job=None, ...)` | 兼容 3 参数直接调用与 `PulseWorker` 4 参数 `(conn, instance_id, validated, job)` 调用；在 `SAVEPOINT` 内重新核对数据库当前 `world_instances.revision`、已确认 `world_minute`、事件 `revision`、跨实例引用、因果环与临时事件上限，任一失败完整回滚。 |

### 2.2 Seed Canon 稳定挑选、字符预算与动态主线生成

1. **稳定挑选顺序（`select_canon_sources`）**：
   - **已确认设定条目（`entry:<id>`）**：仅选取 `lore_entries` 中 `deleted_at IS NULL AND status = 'confirmed'` 的记录（自动过滤 `draft` 与软删除条目），按 `(LORE_ENTRY_TYPE_PRIORITY[type], id ASC)` 排序（优先级依次为：`world_rule/rule/magic` -> `geography/location/region` -> `faction/organization/country/politics` -> `economy/trade/resource` -> `history/culture` -> `character/npc/person` -> 其他）。
   - **就绪文档分块（`chunk:<id>`）**：仅选取 `chunks JOIN documents` 中 `d.deleted_at IS NULL AND d.status = 'ready'` 的记录，按 `(c.chunk_index ASC, d.created_at ASC, d.id ASC, c.id ASC)` 跨文档轮询排序，优先覆盖每份导入资料的首章与前序分块。
2. **明确配额与字符预算**：
   - Seed 默认上限：设定条目最多 `DEFAULT_SEED_MAX_ENTRIES = 6` 条（单条最多 `600` UTF-16 单位），文档分块最多 `DEFAULT_SEED_MAX_CHUNKS = 6` 块（单块最多 `800` UTF-16 单位），总字符预算 `DEFAULT_SEED_CHAR_BUDGET = 4800` UTF-16 单位。超长节选按 UTF-16 边界安全截断并标记 `truncated=True`。
3. **无授权资料阻塞与零静态示例污染**：
   - 当世界无任何已确认设定条目或有效文档分块时，`read_snapshot` 返回 `status="blocked", blocked=True, skip=True, reason_code="PULSE_SOURCE_REQUIRED"` 及可展示的中文提示；调用生成或校验会抛出 `PulseSourceRequiredError`。
   - 提示词与 `FakeEventModelClient` 均严格从用户世界实际 Canon 的标题与正文中提取主题、地点与参与方生成 3～6 条活跃主线，绝不把「霜木镇」「旅人交换线索」等静态示例标题套进用户的世界。

### 2.3 Advance 到期活跃事件、最小因果邻域与跳过条件

1. **只传到期活跃事件**：仅查询 `status = 'active' AND next_update_minute IS NOT NULL AND next_update_minute <= confirmed_world_minute` 的事件，按 `(next_update_minute ASC, created_minute ASC, event_id ASC)` 排序，单次上限 `DEFAULT_ADVANCE_MAX_DUE_EVENTS = 6`。
2. **最小因果邻域（`causal_neighborhood`）**：仅抽取同实例内与到期事件存在 1 跳直接父子因果关联的事件，以及本实例近期已实际发生演进（`updated_minute > created_minute` 或 `revision > 1` 或已产生后果）的潜在上游事件，上限 `DEFAULT_ADVANCE_MAX_CAUSAL_NEIGHBORS = 8`；排除未演进的远期无关事件、停滞事件与跨实例事件。
3. **允许的 Canon ID 与节选**：优先加载到期事件、因果邻域及已满足前置条件绑定的 `source_ids`，再按稳定顺序补充至最多 `4` 条设定条目、`4` 个分块，总预算 `3200` UTF-16 单位。
4. **跳过条件**：若 `job.target_world_minute > confirmed_world_minute` 或无到期活跃事件，返回 `status="skipped", skip=True` 并区分 `DUE_TIME_EXCEEDS_CONFIRMED_MINUTE`（附 `earliest_next_update_minute`）与 `NO_DUE_EVENTS`。

### 2.4 严格输出结构、提示词与六类非法拦截

- **输出 Schema 与提示词**：在 `app.m3.events.ai` 中导出 `PULSE_EVENT_OUTPUT_SCHEMA`、`build_pulse_event_prompts(request_or_snapshot)` 与 `PROMPT_EXAMPLES`。
- **六类安全校验与事务内二次复核**：
  1. **伪造 Canon**：非 `chunk:`/`entry:` 前缀或不在本次 `allowed_source_ids` 内的来源抛出 `422 SOURCE_FABRICATED`；
  2. **跨实例 ID**：顶层 `instance_id`、操作 `event_id`/`cause_event_ids` 或 `transmissions` 引用其他实例抛出 `422 CROSS_INSTANCE_REFERENCE`；
  3. **错误旧版本**：事件 `expected_event_revision` 不符抛出 `409 EVENT_REVISION_CONFLICT`；世界 `base_world_revision` / `world_revision` 不符抛出 `409 WORLD_REVISION_CONFLICT`；
  4. **因果环**：自引用或三色 DFS 检测到多跳环路抛出 `422 CAUSAL_SELF_REFERENCE` / `CAUSAL_CYCLE_DETECTED`；
  5. **玩家账本字段与任意路径**：包含 `coins`、`inventory`、`player_*` 等受保护键或靶向玩家账本抛出 `422 PLAYER_LEDGER_PROTECTED`；包含 `$set`、`json_path`、`.`、`/` 等路径操作抛出 `422 ARBITRARY_PATH_FORBIDDEN`；
  6. **伪造时间**：回显的 `world_minute`/`target_world_minute` 与已确认分钟不一致、覆写 `created_minute`/`updated_minute`、或非终态事件 `next_update_minute <= world_minute` 抛出 `422 TIMESTAMP_OVERRIDE_FORBIDDEN` / `INVALID_NEXT_UPDATE_MINUTE`。
- **新事件 ID 与传播引用边界**：`create` 操作严禁模型传入 `event_id`（`SERVER_ID_FORBIDDEN`），由 `apply_proposal` 统一生成；Seed 阶段禁止夹带非空 `transmissions`（`SEED_TRANSMISSIONS_FORBIDDEN`），Advance 阶段禁止引用本批新建临时事件 ID（`TEMP_EVENT_REFERENCE_FORBIDDEN`），合法存量事件传播交由 Ma 的知识模块核验。

## 3. 测试验证与证据记录

### 3.1 独立测试命令与实际结果

在沙箱环境（Python `3.11.2`）下实际执行：

```bash
PYTHONPATH=server python3 -m unittest discover -s server/tests/m3/events -t server -v
```

- **实际输出**：`Ran 23 tests in 0.124s — OK`（23 项单元测试全部通过，含首轮 16 项回归测试 + 本轮新增 7 项核心验收测试）。
- **与 `m3/pulse` 联跑结果**：
  ```bash
  PYTHONPATH=server python3 -m unittest discover -s server/tests/m3/pulse -t server -v
  ```
  `Ran 18 tests in 0.095s — OK`。

### 3.2 新增测试覆盖明细（`test_wave2_pulse_and_mock_chain.py`）

1. `test_seed_canon_selection_budget_blocking_and_no_static_titles`：验证空资料世界返回 `PULSE_SOURCE_REQUIRED` 阻塞原因；验证设定条目按类型优先级、分块按 `chunk_index` 稳定挑选及字符预算截断；验证自定义世界（「苍穹浮岛群」）动态生成主线且不含任何静态示例标题。
2. `test_advance_due_events_minimal_causal_neighborhood_and_skip_conditions`：验证未到期/超前时间跳过，验证 Advance 快照仅含到期活跃事件与最小因果邻域（排除未演进无关远期事件与停滞事件），并验证快照外存量活跃临时事件上限叠加拦截。
3. `test_strict_output_schema_prompts_and_six_security_guards_plus_db_recheck`：验证 `PULSE_EVENT_OUTPUT_SCHEMA`、`build_pulse_event_prompts` 及伪造 Canon、跨实例、旧版本、因果环、玩家账本、伪造时间六大拦截与 `apply_proposal` 数据库状态二次复核。
4. `test_wave2_mock_http_causal_chain_and_concurrent_conflict_atomicity`：使用 `httpx.MockTransport` 跑通「邻国加税 → 商队改道 → 本地供给变化」四步链，核对每步来源、版本、时间与 6 条可追溯后果，验证多操作并发冲突下 `SAVEPOINT` 完整回滚无半笔状态，并校验 `wave2_mock_causal_chain_fixture.json` 可重放一致性。
5. `test_pulse_worker_direct_wiring_end_to_end`：将 `read_snapshot`、`validate_proposal`、`apply_proposal` 直接注入 `PulseWorker` 跑通 Seed 与 Advance 完整闭环。
6. `test_transmission_envelope_guards_and_server_generated_ids`：验证 Seed 禁传 `transmissions`、Advance 禁引新建临时事件 ID、以及新事件 ID 由程序生成。
7. `test_mode_kind_and_revision_world_revision_bidirectional_mapping`：验证 `mode`/`kind` 与 `revision`/`world_revision`/`base_world_revision` 双向映射与冲突拦截。

### 3.3 证据分类记录

| 证据类别 | 状态 | 说明 |
| --- | --- | --- |
| **Fake 证据** | **已实跑通过** | `FakeEventModelClient` 与 `build_fake_pulse_generator` 在内存与临时文件 SQLite 下通过全部 Seed/Advance 与自定义世界测试。 |
| **Mock 证据** | **已实跑通过** | `OpenAIEventModelClient` 配合 `httpx.MockTransport` 跑通「邻国加税 → 商队改道 → 本地供给变化」四步因果链及错误拦截测试。 |
| **APK 打包证据** | **待集成方 CI 执行** | 本包仅修改后端 `server/app/m3/events/`，Android APK 打包由 DeepSeekHarness 在合入集成分支后通过 GitHub Actions 统一验证。 |
| **真实服务商证据** | **未运行（待用户开启）** | 遵守安全规范不在仓库或测试中内置真实 API Key，沙箱无外网请求；待集成后由用户在前端主动连接并开启脉动验收。 |
| **安卓真机证据** | **待集成后验收** | 待公共接线 PR 合入并产出 APK 后在安卓设备验收。 |

## 4. 建议公共接线方式与最小调用示例（供 DeepSeekHarness / Li / Ma 对接）

### 4.1 组合 `PulseWorker` 三回调与提示词构建

```python
from app.m3 import events, knowledge
from app.m3.pulse.worker import PulseWorker


def read_combined_snapshot(conn, instance_id: str, job: dict) -> dict:
    # 直接使用 events.read_snapshot 获取含 Canon 预算节选、到期事件、最小因果邻域与 allowed_event_ids 的快照
    return events.read_snapshot(conn, instance_id, job)


def validate_combined_proposal(proposal: dict, context: dict) -> dict:
    # 1. 事件模块纯校验（校验顶层身份/时间/版本回显、operations 因果与来源、以及 transmissions 封套安全）
    validated_events = events.validate_proposal(proposal, context)
    # 2. 若存在 transmissions，交由 Ma 的知识模块校验传播结构与 allowed_event_ids 引用
    raw_transmissions = list(validated_events.transmissions)
    return {
        "events_proposal": validated_events,
        "transmissions": raw_transmissions,
    }


def apply_combined_proposal(conn, instance_id: str, validated: dict, job: dict) -> dict:
    # 在 PulseWorker 的短写事务 SAVEPOINT 内先后应用权威事件与知识传播；任一抛错自动整体回滚
    ev_res = events.apply_proposal(conn, instance_id, validated["events_proposal"], job)
    tx_list = validated.get("transmissions") or []
    tx_res = (
        knowledge.record_transmission(conn, instance_id, {"transmissions": tx_list})
        if tx_list
        else []
    )
    return {
        "applied_operations": ev_res["applied_operations"],
        "created_event_ids": ev_res["created_event_ids"],
        "updated_event_ids": ev_res["updated_event_ids"],
        "applied_transmissions": len(tx_res),
    }
```

### 4.2 在真实模型客户端中复用事件域提示词与跳过判断

- 当 `snap = events.read_snapshot(conn, instance_id, job)` 返回 `snap["skip"] is True`（如 `status == "skipped"` 无到期事件，或 `status == "blocked"` 缺授权资料）时，Worker 应在阶段 1 直接跳过或标记阻塞，不发起外部付费模型请求。
- 当 `snap["status"] == "ready"` 时，可调用 `prompts = events.build_pulse_event_prompts(snap)` 获取 `prompts["system_prompt"]`、`prompts["user_prompt"]` 与 `prompts["output_schema"]` 发送给模型服务商。
