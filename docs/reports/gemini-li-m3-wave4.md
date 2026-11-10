# Gemini Li — M3 Wave 4 交付报告（NPC 自主脉动接通与生命力提升）

- **角色**：Gemini Li（`M3.3` NPC 自主行动、生命周期与后台脉动整合）
- **轮次**：`m3-wave4`
- **基线快照 SHA (`base_sha`)**：`9117404aa307c53de235b2959507d8d577d9fbca`（基于 PR #27 合并提交 `46fb8deb8cdfe725bad5ae241b953d255eb078f1` 及 Wave 4 协作指令文档）
- **状态**：已完成全部实现、跨模块组合回调验证、旧表无损迁移与 16 项自动化测试验收

---

## 1. 交付文件清单与边界合规说明

本轮严格遵循 `docs/collaboration/M3_WAVE4.md` 与 `docs/collaboration/M3_WAVE4_GEMINI_LI.md` 的文件所有权约束，**仅新增/修改 `server/app/m3/npcs/**`、`server/tests/m3/npcs/**` 与本报告 `docs/reports/gemini-li-m3-wave4.md`**，未触碰 `server/app/main.py`、`server/app/m3/pulse/`、`server/app/m3/encounters/`、`server/app/m3/portability/`、`web/` 等其他成员专属路径（`delete` 数量为 `0`）。

| 仓库相对路径 | 动作 (`action`) | 说明 |
| :--- | :--- | :--- |
| `server/app/m3/npcs/__init__.py` | `modify` | 导出 Wave 4 新增的初始化种子桥接、后台脉动三阶段适配函数、组合回调构建器、`NpcPulseModelClientAdapter` 及两套自定义世界全链路仿真函数 |
| `server/app/m3/npcs/types.py` | `modify` | 扩充 8 种核心动作类型（`travel`、`stay`、`wait`、`no_change`、`plan_change`、`conversation_plan`、`shift_change`、`task_progress`）、中英文别名映射、设定来源分类（`canon` vs `inferred`）与人物关系结构 |
| `server/app/m3/npcs/schema.py` | `modify` | 升级 Schema 版本至 `1.1-m3-wave4`，扩充 `m3_npcs` 与 `m3_npc_action_log` 的动作类型 `CHECK` 约束，并提供 `SAVEPOINT` 安全的旧版 Wave 3 四动作枚举表原地无损迁移逻辑 |
| `server/app/m3/npcs/validator.py` | `modify` | 实现 `normalize_action_kind`、`normalize_initial_npc_seed_proposal`（承接 AI 世界设定草案/人物关系卡并区分 `canon` 与 `inferred`），完成 8 种动作因果与时间单调性纯函数校验，修复 `due_npcs` 在紧凑快照下的上下文提取 |
| `server/app/m3/npcs/service.py` | `modify` | 解决 `apply_proposal` 与 `PulseWorker` 双重更新 `world_instances.revision` 的冲突（引入 `advance_world_revision` 控制与运行态任务自动识别）；修复已应用任务幂等重放先于版本比对返回；提供 `seed_npcs_from_initialization`、`read_pulse_snapshot`、`validate_pulse_proposal`、`apply_pulse_proposal_in_worker` 与 `build_npc_pulse_callbacks` |
| `server/app/m3/npcs/ai.py` | `modify` | 升级 `PULSE_NPC_OUTPUT_SCHEMA` 与中文化提示词模板（支持 8 种动作、`new_plan`、`previous_plan`、`grounding`、`relationships`），新增 `NpcPulseModelClientAdapter`（适配 `PulseModelClientProtocol`） |
| `server/app/m3/npcs/fixtures.py` | `modify` | 新增两套不同题材自定义世界（星澜海港贸易 `harbor`、白塔秘术学院 `academy`）的 AI 初始化草案转换器和 `run_wave4_pulse_lifecycle_simulation` 端到端脉动与长生命周期仿真 |
| `server/tests/m3/npcs/test_wave4_pulse_and_lifecycle.py` | `add` | Wave 4 新增专项测试集：覆盖多题材 AI 草案初始化与 8 动作校验、旧表无损升级、单次版本递增与组合事务原子回滚、以及 6 大生命周期场景端到端仿真 |
| `docs/reports/gemini-li-m3-wave4.md` | `add` | 本交付报告 |

---

## 2. 核心设计与关键问题解决说明

### 2.1 从世界初始化结果建立人物初始状态（`seed_npcs_from_initialization`）

针对 `docs/collaboration/M3_WAVE4_GEMINI_LI.md` 第 1 项与 `docs/contracts/M3_WAVE4_API.md` 第 1.2 节要求，我们在 `validator.py` 与 `service.py` 中实现了将 `M3.5`（AI 世界设定分析草案）或通用世界初始化输出的人物关系卡直接转化为首批 NPC 状态与初始计划的能力：

- **入口函数**：`seed_npcs_from_initialization(conn, instance_id, raw_seed, *, world_minute=None, allowed_source_ids=None, default_source_ids=None, job=None, advance_world_revision=False) -> dict[str, Any]`
- **多形态输入兼容**：支持传入人物列表 `list[dict]`、带 `initial_npcs` / `npc_cards` / `characters` / `npcs` 的世界设定分析字典，或标准 `operations` 提议字典。
- **区分原文事实（`canon`）与合理补全（`inferred`）**：
  - 当人物卡携带有效的原文设定引用（`chunk:*` / `entry:*`）且未显式标记为推断时，归类为 `grounding = "canon"`；
  - 当世界资料仅有简略人物设定（未写明当前时刻具体小动作或驻留计划）时，允许模型或初始化器补全合理的初始职责、驻留位置、当前动机与关系网（`relationships`），自动归类为 `grounding = "inferred"` 并关联世界的 `default_source_ids`，将分类结果与关系表完整持久化在 `m3_npcs.action_meta_json` 与 `m3_npc_action_log.details_json` 中，避免将合理补全伪装成原文铁律。
- **多题材验证**：在 `fixtures.py` 的 `build_wave4_custom_world_init_payload(theme="harbor" | "academy")` 中分别验证了“星澜港海港贸易”与“白塔秘术学院”两套截然不同题材的初始化草案转化。

### 2.2 后台脉动三阶段正式接通与解决“双重更新实例版本”冲突

#### 冲突根因定位
在 Wave 3 中，`npcs.apply_proposal` 在自身 `SAVEPOINT` 结束前无条件执行了：
```sql
UPDATE world_instances SET revision = revision + 1 WHERE id = ?
```
然而，`PulseWorker.run_once`（`server/app/m3/pulse/worker.py` 第 985–999 行）在同一个 `SAVEPOINT sp_pulse_apply` 内调用完 `self._proposal_applier(conn, instance_id, validated, current_job)` 后，**还会紧接着执行一次乐观锁递增**：
```sql
UPDATE world_instances SET revision = revision + 1 WHERE id = ? AND revision = ?
```
如果 `npcs.apply_proposal` 在 `_proposal_applier` 内部已经把 `world_instances.revision` 加了 1，`PulseWorker` 的 `WHERE id = ? AND revision = snapshot_revision` 就会匹配 0 行，从而误抛 `409 WORLD_REVISION_CONFLICT` 并回滚整个后台脉动任务。

#### 解决方案与精确事务语义
1. **显式参数与运行态自动识别双重保障**：
   - `apply_proposal(conn, instance_id, validated_or_raw, *, context=None, job=None, advance_world_revision: bool | None = None)` 新增 `advance_world_revision` 参数：
     - **独立调用模式**（默认 `advance_world_revision=None` 且 `job` 不是正在持有租约运行的 `PulseWorker` 任务，或显式传 `advance_world_revision=True`）：由 `npcs.apply_proposal` 在自身 `SAVEPOINT` 内将 `world_instances.revision` 递增 `+1`，100% 保持 Wave 3 独立调用契约与旧测试兼容。
     - **后台脉动 Worker 内部模式**（显式传 `advance_world_revision=False`，或调用 `apply_pulse_proposal_in_worker`，或传入带有 `lease_token` 且 `status == 'running'` 的 `PulseWorker` 任务字典）：`npcs.apply_proposal` **不再递增 `world_instances.revision`**，仅原子写入 `m3_npcs`、`m3_npc_tasks`、`m3_npc_action_log` 与 `m3_npc_applied_jobs`，将唯一的 `world_instances.revision = revision + 1` 交由外层 `PulseWorker` 统一完成。
2. **修复幂等重放与版本检查的先后顺序**：
   - 将 `m3_npc_applied_jobs` 的幂等键查询置于 `base_world_revision != current_world_rev` 检查**之前**。这样当同一个 `job_id` / `idempotency_key` 在实例版本已经推进后发生重放时，能够直接命中幂等缓存返回首次结果，而不会误报 `WORLD_REVISION_CONFLICT`。
3. **提供开箱即用的三阶段回调与多域组合器（供 DeepSeekHarness 一行装配）**：
   - 单域三阶段回调：
     - `read_pulse_snapshot(conn, instance_id, job) -> dict`
     - `validate_pulse_proposal(proposal, context) -> ValidatedNpcProposal`
     - `apply_pulse_proposal_in_worker(conn, instance_id, validated, job) -> dict`
   - 多域组合回调构建器 `build_npc_pulse_callbacks(...)`：
     - 可将 `m3/events`（Marcin）、`m3/npcs`（Li）与 `m3/knowledge`（Ma）的快照读取、纯函数校验与事务内落库组合为一套统一的 `(snapshot_reader, proposal_validator, proposal_applier)`；
     - 自动对快照进行预算友好的紧凑化裁剪（`_compact_npc_for_pulse_snapshot`），确保组合快照在 `PulseWorker` 的单轮 Token 预算（4096 tokens）内顺畅通过；
     - 在 `PulseWorker` 的外层 `SAVEPOINT sp_pulse_apply` 下，若 `events`、`npcs` 或 `knowledge` 任一环节抛错，三方写入全部原子回滚，不会留下半成品状态。

#### DeepSeekHarness 在 `main.py` 中的推荐装配示例
```python
from app.m3 import events, knowledge, npcs

read_cb, val_cb, app_cb = npcs.build_npc_pulse_callbacks(
    events_snapshot_reader=lambda c, iid, j: events.read_snapshot(c, iid, j),
    events_validator=events.validate_proposal,
    events_applier=lambda c, iid, v, j: events.apply_proposal(
        c, iid, v, world_minute=int(j["target_world_minute"])
    ),
    knowledge_validator=knowledge.validate_proposal,
    knowledge_applier=lambda c, iid, t, _j: knowledge.apply_proposal(c, iid, t),
)
```

### 2.3 更自然的生命力：8 种动作类型与“何时不该动”

为防止后台脉动沦为“每轮所有人都机械搬家”，本轮将 NPC 自主动作扩充并规范为 8 种类型（同时兼容中文与下划线别名）：

| 动作枚举 (`action_kind`) | 中文名称 | 行为语义与校验规则 |
| :--- | :--- | :--- |
| `travel` | 出行 | 地点发生转移（`to_location != from_location`），更新 `origin_location`、`destination_location` 与 `location` |
| `stay` | 留在原处 | 根据当前局势或职责继续留在原地处理事务，要求目标位置与当前位置一致，不产生虚假位移 |
| `wait` | 等待 | 在原地等待特定时机、消息或会合对象，可设为 `available` 或 `busy` |
| `no_change` | 无变化 | 评估后认为当前无需改变既有安排，保持原地点与原状态，顺延 `next_due_minute` |
| `plan_change` | 改变计划 | 因新获知局势/事实而调整原定计划，记录 `previous_plan` 与必填的 `new_plan` |
| `conversation_plan` | 交谈安排 | 与同地点（或同行）的目标人物 `counterpart_npc_id` 发起交谈或商议 `conversation_topic` |
| `shift_change` | 换班 | 切换当班/下班休息状态（`shift_name`、`availability`），直接影响玩家能否当面接触 |
| `task_progress` | 任务进度 | 创建或推进多步长期任务（`m3_npc_tasks`），严格校验进度百分比单调递增或阶段实质变化 |

### 2.4 与当面查证（`encounters`）和主叙事的共享人物状态一致性

- **`encounters` 自动联动**：`server/app/m3/encounters/service.py` 通过动态发现调用 `app.m3.npcs.check_npc_accessibility` 与 `get_npc`。当 NPC 在后台脉动中执行 `travel` 离开当前地点、或执行 `shift_change` 下班休息（`off_shift`）时，玩家在原地点发起 `ask_npc` 查证会立即依据最新 `location` 与 `availability` 被拦截或返回不在场说明；当后续脉动使 NPC 返回玩家所在地并恢复当班后，玩家无需刷新即可直接发起 `ask_npc` 查证并获得基于该 NPC 最新认知与状态的回应。
- **下一回合主叙事注入**：`build_adventure_narration_npc_context(conn, instance_id, player_location=..., world_minute=...)` 将同场景在场 NPC（`present_npcs`）、刚离开该场景的动向线索（`departed_clues`）及近期高优变动（`recent_npc_changes`）压缩在受控字符预算（默认 600 字）内供主叙事直接拼接。

---

## 3. 六大生命周期场景验证（Mock 全链路与测试覆盖）

我们在 `server/app/m3/npcs/fixtures.py`（`run_wave4_pulse_lifecycle_simulation`）与 `server/tests/m3/npcs/test_wave4_pulse_and_lifecycle.py` 中完整跑通并验证了任务书要求的全部场景：

1. **同一事件下不同人物采取不同反应**：
   - 在“星澜港海港贸易”场景中，面对同一场“外海浓雾导致香料船队改泊北栈桥”事件（`evt_harbor_fog_01`），领航员林船长（`npc_lin_navigator`）因职责与牵挂选择 `travel` 前往黑礁灯塔引航，而造船匠周师傅（`npc_zhou_shipwright`）因守备船坞职责选择 `stay` 留在星澜港码头加固缆桩，两人动作、去向与理由完全分化。
2. **任务失败重试与幂等保护**：
   - 第 120 分钟林船长首次尝试返航时，模拟网络/模型瞬态故障（`HTTP 503`），`PulseWorker` 将任务标记为 `retry_pending`（`attempts=1`），期间数据库内林船长位置、版本与实例版本零污染；推进重试时钟后第二次调用成功，林船长平稳返回星澜港码头；对已应用任务再次执行幂等重放，不会重复递增版本号或写入重复日志。
3. **人物离开后玩家回头再遇见**：
   - 第 60 分钟林船长前往黑礁灯塔后，留在星澜港码头的玩家查询 `check_npc_accessibility` 得到 `accessible=False, reason_code="LOCATION_MISMATCH"`，且 `build_adventure_narration_npc_context` 提供“林船长已从星澜港码头前往黑礁灯塔”的离开线索；第 120 分钟林船长返航后，玩家在星澜港码头再次查询 `check_npc_accessibility` 立即恢复为 `accessible=True`，并成功通过 `encounters` 完成当面查证。
4. **先离线后恢复（按已确认世界时间补跑，不按现实挂钟乱跳）**：
   - 模拟实例租约过期离线（`activity_registry.revoke`），在玩家离线期间即使入队了脉动任务且现实挂钟流逝了数小时，`PulseWorker.run_once` 均返回 `skipped_inactive`，绝不按现实挂钟时间乱跳或消耗预算；当玩家重新上线恢复活跃租约并确认世界时间推进至 210 分钟后，后台脉动严格按已确认的 `world_minute = 210` 恢复执行（林船长发起 `conversation_plan` 与周师傅商议，周师傅执行 `plan_change` 调整修船计划）。
5. **便携包导出/迁移后继续推进状态一致**：
   - 挂载 `NpcsAdapter` 并通过 `PortabilitySnapshotAdapter.build_instance_snapshot` 将第 210 分钟的实例状态完整导出并恢复至新实例 `inst_wave4_portable_target`，随后在新实例中推进至第 270 分钟继续执行 NPC 动作，人物版本号、任务进度（100% 完成）与行动轨迹完全连续无断层。
6. **旧版 Wave 3 数据库原地无损升级**：
   - 对已存有数据的 Wave 3 四动作 `CHECK` 约束旧表执行 `ensure_npcs_schema(conn)`，在不脱离外层事务、不破坏外层 `SAVEPOINT` 的前提下自动迁移为支持 8 种动作的 Wave 4 表结构，原有行数据与索引 100% 保留。

---

## 4. 自动化测试命令与运行结果

在仓库根目录下执行以下命令（使用标准库 `unittest`）：

```bash
PYTHONPATH=server python3 -m unittest discover -s server/tests/m3/npcs -t server -v
```

### 4.1 `server/tests/m3/npcs` 执行输出（16 项全绿）
- `test_foreground_and_background_measurable_budgets` ... `ok`
- `test_no_due_npcs_skip_and_no_state_mutation` ... `ok`
- `test_three_tier_depth_classification_and_promotion_demotion_reasons` ... `ok`
- `test_cross_module_discovery_with_events_and_knowledge_tables` ... `ok`
- `test_fake_npc_model_client_and_accessibility_edge_cases` ... `ok`
- `test_idempotent_job_replay_protection` ... `ok`
- `test_northern_trade_two_npcs_different_arrangements_and_mock_chain` ... `ok`
- `test_revision_conflict_and_savepoint_atomic_rollback` ... `ok`
- `test_snapshot_restore_branch_isolation_and_domain_adapter` ... `ok`
- `test_duplicate_tasks_and_duplicate_operations_rejected` ... `ok`
- `test_four_action_kinds_and_time_causal_constraints` ... `ok`
- `test_player_ledger_player_knowledge_and_arbitrary_path_protected` ... `ok`
- `test_initialization_from_world_analysis_drafts_and_action_kinds` ... `ok`
- `test_single_revision_increment_in_pulse_worker_and_combined_commit_rollback` ... `ok`
- `test_wave3_schema_in_place_migration_preserves_rows_and_savepoint` ... `ok`
- `test_wave4_mock_full_lifecycle_different_reactions_re_encounter_retry_offline_and_migration` ... `ok`

### 4.2 跨模块联合回归验证
- `server/tests/m3/events`：23 tests `OK`
- `server/tests/m3/pulse`：25 tests `OK`
- `server/tests/m3/encounters`：6 tests `OK`
- `server/tests/m3/portability`：6 tests `OK`
- `server/tests/m3/npcs`：16 tests `OK`
