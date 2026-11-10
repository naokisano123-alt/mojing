# 墨境｜M3 第三轮（Wave 3）NPC 自主行动、行程与模拟深度交付报告

- **任务角色**：`Gemini Marcin`
- **共同仓库快照（base_sha）**：[`22dfaaae63b715708321f2cd532c2b99774bdc95`](https://github.com/naokisano123-alt/mojing/archive/22dfaaae63b715708321f2cd532c2b99774bdc95.zip)
- **契约与任务书依据**：
  - [`docs/collaboration/M3_WAVE3.md`](../collaboration/M3_WAVE3.md)
  - [`docs/collaboration/M3_WAVE3_GEMINI_MARCIN.md`](../collaboration/M3_WAVE3_GEMINI_MARCIN.md)
  - [`docs/M3_DESIGN_DRAFT.md`](../M3_DESIGN_DRAFT.md)、[`docs/contracts/M3_WAVE2_API.md`](../contracts/M3_WAVE2_API.md)、[`docs/reports/codex-m3-wave2-completion.md`](codex-m3-wave2-completion.md)

## 1. 交付文件清单与独占边界声明

本交付包严格限定在独占目录 `server/app/m3/npcs/`、`server/tests/m3/npcs/` 与本报告 `docs/reports/gemini-marcin-m3-wave3.md` 内，未修改任何公共入口、数据库主初始化、前端代码、Android 工程或其它助手的独占目录。

| 文件路径 | 操作类型 | 说明 |
| --- | --- | --- |
| `server/app/m3/npcs/__init__.py` | 新增（`add`） | 模块公共导出入口（四大核心接口、可接触性查询、提示词构建、模型客户端与北境贸易 Fixture）。 |
| `server/app/m3/npcs/errors.py` | 新增（`add`） | 遵循 `ApiError` 规范的 NPC 异常体系（404/409/422/502），含版本冲突、未到期、重复任务、无根据因果、跨实例及账本/知情保护异常。 |
| `server/app/m3/npcs/types.py` | 新增（`add`） | NPC 领域数据结构、四种动作类型、三档模拟深度常量、可测量预算 `DepthBudget`、升降档记录 `NpcDepthEvaluation` 与校验上下文定义。 |
| `server/app/m3/npcs/schema.py` | 新增（`add`） | SQLite 表结构（`m3_npcs`、`m3_npc_tasks`、`m3_npc_action_log`、`m3_npc_applied_jobs` 及独立测试桩表）与不隐式提交事务的 `ensure_npcs_schema(conn)`。 |
| `server/app/m3/npcs/validator.py` | 新增（`add`） | 事务外纯函数校验器 `validate_proposal` 与三档动态模拟深度评估器 `evaluate_npc_depths`。 |
| `server/app/m3/npcs/service.py` | 新增（`add`） | 只读选取 `read_snapshot`、`SAVEPOINT` 事务内原子应用 `apply_proposal`、快照 `snapshot`/`restore`、`NpcsAdapter` 及供 Ma 接入的可接触性核验接口 `check_npc_accessibility` / `list_contactable_npcs`。 |
| `server/app/m3/npcs/ai.py` | 新增（`add`） | `PULSE_NPC_OUTPUT_SCHEMA`、`build_pulse_npc_prompts`、`FakeNpcModelClient` 与支持 `httpx.MockTransport` 的 `OpenAINpcModelClient`。 |
| `server/app/m3/npcs/fixtures.py` | 新增（`add`） | 北境贸易 10 名 NPC 初始化、同一局势差异化行动、多步 Mock 响应构建与端到端模拟运行器 `run_northern_trade_npc_simulation`。 |
| `server/app/m3/npcs/wave3_northern_trade_npc_fixture.json` | 新增（`add`） | 北境贸易 NPC 自主行动四步链的可重放快照与预期输出数据包。 |
| `server/tests/m3/npcs/__init__.py` | 新增（`add`） | 测试包初始化文件。 |
| `server/tests/m3/npcs/test_depth_and_selection.py` | 新增（`add`） | 覆盖三档模拟深度判定、升降档理由、可测量数量/字符预算及无到期跳过（无到期不变更）的单元测试。 |
| `server/tests/m3/npcs/test_validator_and_actions.py` | 新增（`add`） | 覆盖四种动作类型、时间/因果约束、重复任务拦截、未到期拦截、伪造来源/跨实例拦截及玩家账本/知情保护的单元测试。 |
| `server/tests/m3/npcs/test_northern_trade_and_branches.py` | 新增（`add`） | 覆盖北境贸易双 NPC 差异化安排、HTTP Mock 四步链、幂等重放、分支隔离、`SAVEPOINT` 版本冲突回滚、可接触性边界与跨模块表联动的单元测试。 |
| `docs/reports/gemini-marcin-m3-wave3.md` | 新增（`add`） | 本交付与测试报告。 |

- **删除或重命名文件**：无（0 删除，0 重命名）。

## 2. 核心实现与契约对照

### 2.1 NPC 持久状态与四种受约束动作

1. **持久状态字段（`m3_npcs`）**：
   - **实例内稳定标识与身份**：`(instance_id, npc_id)` 复合主键，含 `name`、`role`（同时暴露 `identity` 别名）、`faction`、`home_location`；
   - **地点与行程状态**：`location`、`origin_location`、`destination_location`、`availability`（`available / in_transit / busy / off_duty`）、`shift_name`；
   - **正在做的事与任务关联**：`current_activity`、`current_action_kind`（同时暴露 `action_kind`）、`counterpart_npc_id`、`conversation_topic`、`current_task_id`、`action_meta`；
   - **可验证的动机与因果链**：`motivation`、`goal`、`cause_event_ids`（引用实例内已存在事件）、`cause_fact_ids`（引用该 NPC 已获知事实）、`source_ids`（引用已授权 Canon 来源）；
   - **时间与乐观并发版本**：`created_minute`、`updated_minute`、`last_action_minute`、`next_due_minute`（同时暴露 `next_update_minute`）、`revision`。
2. **四种动作类型与时间/因果约束**：
   - `travel`（出行）：校验 `from_location` 与 NPC 当前实际地点一致，`to_location` 非空且不同于起点；持续时间 `duration_minutes >= 15`，应用后记录 `origin_location` 与 `destination_location` 并将状态标为 `in_transit`，原所在地不再可接触；
   - `conversation_plan`（交谈安排）：校验 `target_npc_id` 为同实例内已存在的其它 NPC（严禁指向自身或 `player`），要求非空 `conversation_topic` 且 `duration_minutes >= 10`；
   - `shift_change`（换班）：校验 `to_shift` / `shift_name` 非空，拦截与当前班次及状态完全一致的无变化重复换班（`DUPLICATE_SHIFT_CHANGE`），`duration_minutes >= 15`；
   - `task_progress`（任务进度）：校验 `task_id`、`task_stage` 与 `task_progress_percent`（`0..100`），在 `m3_npc_tasks` 中持久化进度；严格拦截已完成任务二次提交（`TASK_ALREADY_COMPLETED`）、进度倒退（`TASK_PROGRESS_REGRESSION`）以及阶段与进度均无变化的重复提交（`DUPLICATE_NPC_TASK`）。

### 2.2 动态三档模拟深度、可测量预算与升降档理由

`evaluate_npc_depths` 与 `read_snapshot` 按相关性将实例内 NPC 动态划分为三档：

| 深度档位 | 判定条件与原因码（`reason_codes`） | 默认配额与字符预算（`DepthBudget`） | 最小演进间隔 |
| --- | --- | --- | --- |
| **近景（`foreground`）** | 与玩家同处当前地点（`CO_LOCATED_WITH_PLAYER`）或为玩家当前关注焦点（`PLAYER_FOCUS_TARGET`），叠加本地事件参与度（`LOCAL_PLAYER_LOCATION_EVENT` / `ACTIVE_DUE_EVENT_PARTICIPANT`） | 每次脉动最多 `3` 人，单人最多 `420` UTF-16 单位 | `15` 分钟 |
| **关联（`associated`）** | 虽不在玩家当前地点，但参与到期/活跃事件（`ACTIVE_DUE_EVENT_PARTICIPANT` / `ACTIVE_EVENT_PARTICIPANT`）、受到期事件因果驱动（`DUE_EVENT_CAUSAL_MOTIVATION`）或与近景 NPC 存在直接交谈协作关联（`LINKED_TO_FOREGROUND_NPC`） | 每次脉动最多 `3` 人，单人最多 `240` UTF-16 单位 | `30` 分钟 |
| **背景（`background`）** | 位于远方且当前无到期事件或近景关联（`DISTANT_NO_ACTIVE_LINK`） | 每次脉动最多 `1` 人，单人最多 `120` UTF-16 单位 | `120` 分钟 |

- **可审计升降档理由**：每次评估对比 NPC 原 `depth_tier` 与新 `current_tier`，输出 `transition`（`promoted` / `demoted` / `unchanged`）、`reason_codes` 及中文 `reason_summary`（例如韩掌柜离开霜木镇前往白河城后，自动记录 `降档(近景->关联)：LEFT_PLAYER_LOCATION、ACTIVE_DUE_EVENT_PARTICIPANT`）。
- **可测量预算截断**：总人数受 `max_total_npcs`（默认 `6`）与总字符预算 `total_char_budget`（默认 `2200` UTF-16 单位）双重约束；超出配额或未到期的 NPC 均明确标记 `selected_for_simulation=False` 及 `excluded_reason`（如 `NPC_NOT_DUE`、`TIER_FOREGROUND_COUNT_BUDGET_EXCEEDED`、`TOTAL_COUNT_BUDGET_EXCEEDED`、`TOTAL_CHAR_BUDGET_EXCEEDED`）。

### 2.3 四个标准接口与玩家账本/知情保护边界

| 接口 | 入口签名 | 核心行为与边界 |
| --- | --- | --- |
| **只读选取** | `read_snapshot(conn, instance_id, job=None, ...)` | 只读查询已确认 `world_minute` 与 `world_revision`；若 `target_world_minute > confirmed_world_minute` 或当前无到期活跃 NPC，返回 `skip=True, status='skipped', reason_code='NO_DUE_NPCS'`（无到期不变更）。自动发现同库 `world_events` 与 `m3_knowledge_facts` 作为合法因果池。 |
| **纯校验** | `validate_proposal(proposal, context)` | 在 SQLite 事务外纯计算运行；递归拦截 `coins/inventory/stamina/player_state` 等玩家个人账本字段（`422 PLAYER_LEDGER_PROTECTED`）、直接写玩家知情事实或将 `player` 设为后台交谈目标（`422 PLAYER_KNOWLEDGE_PROTECTED`）、以及 `$set` / `.` / `/` 等任意路径写入（`422 ARBITRARY_PATH_FORBIDDEN`）。 |
| **事务内应用** | `apply_proposal(conn, instance_id, proposal, job=None, ...)` | 接收调用方已打开的 `conn`（绝不自行 `commit`）；在 `SAVEPOINT` 内二次复核实例版本（`409 WORLD_REVISION_CONFLICT`）、NPC 版本（`409 NPC_REVISION_CONFLICT`）、到期状态（`409 NPC_NOT_DUE`）、因果存在性与任务去重，任一失败完整回滚。支持 `job_id` / `idempotency_key` 幂等重放。 |
| **快照与恢复** | `snapshot(conn, instance_id)` / `restore(conn, target_instance_id, snapshot, ...)` / `NpcsAdapter` | 导出含内部版本号的 JSON 快照；恢复在 `SAVEPOINT` 内完成，同时兼容位置参数 `(conn, target_instance_id, snapshot)` 与 `DomainAdapter` 关键字参数 `(conn, snapshot, *, new_instance_id, world_id)`，支持 `source_id_map` 重映射与损坏快照原子回滚。 |

## 3. 与脉动（Pulse）及 Ma 互动包（Encounters）的最小接线契约

### 3.1 与 `PulseWorker` 及 `core/integration.py` 的接线示例（供 DeepSeekHarness）

1. **数据库表初始化（`server/app/db.py`）**：
   ```python
   from .m3.npcs.schema import ensure_npcs_schema
   ensure_npcs_schema(conn)
   ```
2. **检查点与分支快照适配器注册（`server/app/m3/core/integration.py`）**：
   ```python
   from ..npcs import NpcsAdapter

   def builtin_adapters() -> list[Any]:
       return [EventsAdapter(), KnowledgeAdapter(), NpcsAdapter()]
   ```
3. **后台脉动回调组合（`PulseWorker`）**：
   - 读取阶段调用 `npc_snap = npcs.read_snapshot(conn, instance_id, job, player_location=player_loc)`；若 `npc_snap["skip"]` 为 `True`（如 `NO_DUE_NPCS`），无需发起 NPC 推演请求；
   - 纯校验阶段在事务外调用 `validated_npcs = npcs.validate_proposal(npc_proposal, npc_snap)`；
   - 原子提交阶段在同一个短写事务内调用 `npcs.apply_proposal(conn, instance_id, validated_npcs, job)`，任何异常均由 `SAVEPOINT` 与外层事务统一回滚。

### 3.2 与 Ma 玩家主动查证/交谈模块（`server/app/m3/encounters/`）的接线契约

Marcin 的 NPC 模块作为世界中人物地点与行程状态的权威来源，提供两个无副作用只读函数供 Ma 的 `encounters` 模块或集成层直接注入：

1. **单人可接触性核验**：
   ```python
   from app.m3 import npcs

   access = npcs.check_npc_accessibility(
       conn,
       instance_id,
       npc_id="npc_han_shopkeeper",
       player_location="霜木镇",
       world_minute=confirmed_world_minute,
   )
   # 返回结构：
   # {
   #   "instance_id": "inst_northern_frontier_001",
   #   "npc_id": "npc_han_shopkeeper",
   #   "name": "韩掌柜",
   #   "role": "霜木镇铁料与矿产原料商",
   #   "location": "白河城",
   #   "origin_location": "霜木镇",
   #   "destination_location": "白河城",
   #   "player_location": "霜木镇",
   #   "world_minute": 60,
   #   "contactable": False,          # 别名 accessible: False
   #   "availability": "in_transit",  # available / in_transit / busy / off_duty
   #   "reason_code": "NPC_DEPARTED_IN_TRANSIT",  # OK / NPC_DEPARTED_IN_TRANSIT / NPC_AT_DIFFERENT_LOCATION / NPC_OFF_DUTY / NPC_NOT_FOUND
   #   "reason_message": "韩掌柜已离开霜木镇，正在前往白河城途中。",
   #   "current_activity": "...",
   #   "next_due_minute": 150,
   #   "revision": 2,
   # }
   ```
2. **当前地点可接触人物列表查询**：
   ```python
   listing = npcs.list_contactable_npcs(
       conn,
       instance_id,
       player_location="霜木镇",
       world_minute=confirmed_world_minute,
       include_unavailable_at_location=True,
   )
   # listing["items"] 返回当前在霜木镇可立即交谈的 NPC（如玛塔老板娘、霍克铁匠）
   # listing["unavailable_items"] 返回原本常驻霜木镇但当前途中离开或换班歇业的 NPC（如前往白河城途中的韩掌柜），直接支撑 Ma 的「途中离开 / 晚到响应」场景！
   ```

## 4. 测试验证与证据记录

### 4.1 独立测试命令与实际结果

在沙箱环境（Python `3.11.2`）下实际执行：

```bash
PYTHONPATH=server python3 -m unittest discover -s server/tests/m3/npcs -t server -v
```

- **实际输出**：`Ran 12 tests in 0.080s — OK`（12 项自动化测试全部通过）。
- **与 `m3/events`、`m3/pulse`、`m3/knowledge` 回归联跑结果**：
  - `m3/events`：`Ran 23 tests in 0.111s — OK`
  - `m3/pulse`：`Ran 25 tests in 0.188s — OK`
  - `m3/knowledge`：`Ran 15 tests in 0.065s — OK`

### 4.2 测试覆盖明细

1. `test_three_tier_depth_classification_and_promotion_demotion_reasons`：验证近景（`foreground`）、关联（`associated`）、背景（`background`）三档动态判定；验证韩掌柜离开霜木镇前往白河城后自动降档（`foreground -> associated`，`LEFT_PLAYER_LOCATION`），瑟蕾娜议员参与到期区域事件后自动升档（`background -> associated`，`ACTIVE_DUE_EVENT_PARTICIPANT`）。
2. `test_foreground_and_background_measurable_budgets`：验证三档人数上限、单人字符预算与总字符预算严格生效，超出预算的候选记录明确的 `excluded_reason`。
3. `test_no_due_npcs_skip_and_no_state_mutation`：验证第 30 分钟无到期 NPC 时返回 `NO_DUE_NPCS` 且应用空操作零变更；验证超前 `target_world_minute` 返回 `DUE_TIME_EXCEEDS_CONFIRMED_MINUTE`。
4. `test_four_action_kinds_and_time_causal_constraints`：验证 `travel`、`conversation_plan`、`shift_change`、`task_progress` 四种动作的时间约束、实例内事件/知情事实因果引用校验，拦截无根据动机、不存在事件、越权引用他人知情记录及非法到期时间。
5. `test_duplicate_tasks_and_duplicate_operations_rejected`：验证同一批次重复 NPC 操作（`DUPLICATE_NPC_OPERATION`）、无进展重复任务（`DUPLICATE_NPC_TASK`）及已完成任务重复提交（`TASK_ALREADY_COMPLETED`）均被拦截。
6. `test_player_ledger_player_knowledge_and_arbitrary_path_protected`：验证拦截 `coins`/`inventory`/`stamina` 等玩家账本字段、拦截 `player_knowledge` 及将 `player` 设为后台交谈目标、拦截 `$set` 任意路径、拦截未到期 NPC 抢跑（`409 NPC_NOT_DUE`）、伪造 Canon 来源与跨实例引用。
7. `test_northern_trade_two_npcs_different_arrangements_and_mock_chain`：使用 `httpx.MockTransport` 跑通北境贸易四步链，验证韩掌柜（出行前往白河城）与霍克铁匠（留守霜木镇换班与废铁回炉）对同一事件 `evt_caravan_reroute_02` 采取不同安排，验证第 90 分钟无到期不变更，并与 `wave3_northern_trade_npc_fixture.json` 核对一致。
8. `test_idempotent_job_replay_protection`：验证相同 `idempotency_key` / `job_id` 重放时返回首次结果且不重复推进版本或追加日志。
9. `test_revision_conflict_and_savepoint_atomic_rollback`：验证多 NPC 批次中第 1 条合法、第 2 条触发 `409 NPC_REVISION_CONFLICT` 时，`SAVEPOINT` 将第 1 条写入完整回滚且保留外层未提交事务。
10. `test_snapshot_restore_branch_isolation_and_domain_adapter`：验证通过 `NpcsAdapter` 导出快照并恢复至 `inst_branch_a` 与 `inst_branch_b`，两分支中韩掌柜分别前往白河城与留在霜木镇商谈，原实例与两分支三方状态及可接触性完全隔离，损坏快照恢复自动回滚。
11. `test_fake_npc_model_client_and_accessibility_edge_cases`：验证 `FakeNpcModelClient` 闭环、异地/不存在/未确认时间可接触性边界，以及 `OpenAINpcModelClient` 异常与 `repr` 零 API Key 泄露。
12. `test_cross_module_discovery_with_events_and_knowledge_tables`：验证与同库 `world_events` 和 `m3_knowledge_facts` 自动联动核验真实事件 ID 与知情事实 ID。

### 4.3 证据分类记录

| 证据类别 | 状态 | 说明 |
| --- | --- | --- |
| **Fake 证据** | **已实跑通过** | `FakeNpcModelClient` 与 `build_fake_npc_pulse_generator` 通过北境贸易完整闭环测试。 |
| **Mock 证据** | **已实跑通过** | `OpenAINpcModelClient` 配合 `httpx.MockTransport` 跑通北境贸易四步演进链及错误脱敏测试。 |
| **APK 打包证据** | **待集成方 CI 执行** | 本包仅新增后端 `server/app/m3/npcs/` 与测试，Android APK 由 DeepSeekHarness 在合入后统一打包验证。 |
| **真实服务商证据** | **未运行（待用户开启）** | 遵守安全规范不在仓库或测试中内置真实 API Key，沙箱禁外网；待集成后由用户配置真实服务商验收。 |
| **安卓真机证据** | **待集成后验收** | 待公共接线 PR 合入并产出 APK 后在安卓设备验收。 |

## 5. 逐文件 SHA-256 校验清单

包内根目录附带标准 `manifest.json`（包含下列全部源码、测试文件以及本报告自身的字节级 SHA-256 摘要）：

| 仓库相对路径 | 操作 | SHA-256 |
| --- | --- | --- |
| `server/app/m3/npcs/__init__.py` | `add` | `1759ae9aac08007c141a1b2332d7d8d50118df97ba7a45e2a7d005ebcf9e9e2a` |
| `server/app/m3/npcs/ai.py` | `add` | `96bf29f0c7878692347f3ad60e1be8968da87877c7d043de72d1997a599d9541` |
| `server/app/m3/npcs/errors.py` | `add` | `2ccb6e61b50b6df1993a2b5b33d3e445fec9a00e4269d0b81f75df401388a71a` |
| `server/app/m3/npcs/fixtures.py` | `add` | `8ed48904b1596f98d9d4d69ff932f5d9793cb7313e6f3ad6131ed3f29c8a33f4` |
| `server/app/m3/npcs/schema.py` | `add` | `e1d96bddc48f3bd3663af43c42ef7a155f239017f01c1a87a4ebae2a460c442b` |
| `server/app/m3/npcs/service.py` | `add` | `7a61c89abeb005c01f52b0549a64c62488356eb4e30d9ce8bdf2939010c3224e` |
| `server/app/m3/npcs/types.py` | `add` | `24478c3326cc216954d28d17f2c62b3883305cb2c0a240bda55e7a4ee72dcab7` |
| `server/app/m3/npcs/validator.py` | `add` | `c426aeba66335e0f453ec7d0377f267fa3dc6fbc47a38396f39e7ac45797668a` |
| `server/app/m3/npcs/wave3_northern_trade_npc_fixture.json` | `add` | `93ca01962a8e95243711267fac5b99b579fa045b1061751467dd0981dc838402` |
| `server/tests/m3/npcs/__init__.py` | `add` | `486412924bd6cab57d5256931562f3a7ec56a4c0f14ac36208d2c470a5d8526b` |
| `server/tests/m3/npcs/test_depth_and_selection.py` | `add` | `3569dfedff1b0684984d46e05ce81ba8df9d47894f39660dc1a1fa4a4bdeb053` |
| `server/tests/m3/npcs/test_northern_trade_and_branches.py` | `add` | `252aafcdda3102153893bbcc76173c926c7340d4fd68fae9dd9f228428e6ffdb` |
| `server/tests/m3/npcs/test_validator_and_actions.py` | `add` | `86e66d08d401e4f7f8d678793726ee3654063d3627de9ee2722ee44efd923fb7` |
| `docs/reports/gemini-marcin-m3-wave3.md` | `add` | *(见包内 `manifest.json`)* |
