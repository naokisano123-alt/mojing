# Gemini（Li）｜M3 Wave 2：付费运行期、在线闸门、离线恢复与模型预算交付报告

- **负责方**：`Gemini Li`
- **协作总表与任务书**：[`docs/collaboration/M3_WAVE2.md`](https://github.com/naokisano123-alt/mojing/blob/main/docs/collaboration/M3_WAVE2.md)（[PR #25](https://github.com/naokisano123-alt/mojing/pull/25)）、`docs/collaboration/M3_WAVE2_GEMINI_LI.md`
- **接口契约版本**：`docs/contracts/M3_WAVE2_API.md`、`docs/contracts/M3_WAVE1_API.md`
- **功能代码基线（`code_baseline`）**：[`8a95587a778e3849385bc413632d402ecf09972f`](https://github.com/naokisano123-alt/mojing/commit/8a95587a778e3849385bc413632d402ecf09972f)
- **任务书快照提交（`snapshot_commit`）**：[`d333af8757212894223b02de75eb2915dc40a4af`](https://github.com/naokisano123-alt/mojing/archive/d333af8757212894223b02de75eb2915dc40a4af.zip)
- **独占业务与测试路径**：
  - `server/app/m3/pulse/**`
  - `server/tests/m3/pulse/**`
  - `docs/reports/gemini-li-m3-wave2.md`
- **防覆写与边界声明**：
  - 本交付包**仅包含改动与新增的 12 个文件**，未触碰 `server/app/m3/core/`、`server/app/m3/events/`、`server/app/m3/knowledge/`、`server/app/main.py`、`server/app/db.py`、`server/app/adventures/`、`server/app/model_gateway/`、前端 `src/`、Android 工程或依赖锁文件；
  - **删除或重命名文件**：无（0 项删除，0 项重命名）；
  - 完整保留 M3 Wave 1 全部导出符号与函数签名，现有 `pulse_preview.py` 无费用模拟脉动与 Wave 1 单元测试 100% 兼容通过。

---

## 1. 交付文件清单

| 路径 | 操作 | 说明 |
| --- | --- | --- |
| `server/app/m3/pulse/__init__.py` | 修改 | 导出 M3 Wave 2 新增的控制入口、安全状态 DTO、回调组合器、Fake 领域底座与重启恢复接口 |
| `server/app/m3/pulse/schema.py` | 修改 | `pulse_instance_configs` 增加代次列 `generation INTEGER NOT NULL DEFAULT 1`，并在 `ensure_pulse_schema` 中提供幂等 `ALTER TABLE` 迁移 |
| `server/app/m3/pulse/types.py` | 修改 | 新增手机端 6 态枚举 `PulseControlState`（`off / ready / waiting_for_model / offline / budget_limited / working`）与安全状态 DTO `PulsePublicStatusDTO` |
| `server/app/m3/pulse/session.py` | 修改 | `ActivityLeaseRegistry` 短租约（默认 45s）、切旅程独占续期（`exclusive`）与在线代次追踪；`PulseSessionRouter` 只读非续期状态检查、401 认证失效标记、会话代次追踪与托管 HTTPS/允许地址校验 |
| `server/app/m3/pulse/queue.py` | 修改 | 新增 `recover_jobs_on_startup` 服务重启安全恢复；`cancel_instance_jobs` 支持按 `kinds` 与 `max_target_world_minute` 精确清理待办 |
| `server/app/m3/pulse/budget.py` | 修改 | 新增 `estimate_call_reservation_tokens`（上下文估算 + 输出上限预留）、`advance_pulse_time_anchor`（离线/暂停/未绑定推进时间锚）、开关切换代次自增、实测结算差额对账，以及模拟用量（`potentially_billed=0`）与计费用量（`potentially_billed=1`）分账隔离 |
| `server/app/m3/pulse/client.py` | 修改 | `OpenAIPulseModelClient` 区分连接建立前失败（未计费 `aborted_unbilled`）、请求发出后超时（`MODEL_TIMEOUT` / `timeout_maybe_billed`）与响应流中途丢失（`MODEL_RESPONSE_LOST` / `response_lost_maybe_billed`） |
| `server/app/m3/pulse/observability.py` | 修改 | `get_pulse_status` 推导 6 态 `state` 与 `reason_code`（只读不续期）；`get_usage_summary` 将 `actual_tokens`、`estimated_tokens`、`potentially_billed_tokens`、`timeout_maybe_billed_*` 与 `simulated_calls/tokens`、`aborted_unbilled_calls` 分开统计 |
| `server/app/m3/pulse/worker.py` | 修改 | 实现请求前与提交前两道门、代次（`generation`）比对拦截、快照 `skip`（无到期事件不发请求）与 `blocked`（缺资料阻塞）处理、模型输出根信封校验 `validate_pulse_envelope` 与 `recover_on_startup` |
| `server/app/m3/pulse/control.py` | 新增 | 实现手机端四项控制契约（`get_control_status`、`connect_pulse_session`、`set_pulse_enabled`、`set_pulse_activity`）、运行期控制器 `PulseRuntimeController`、跨模块回调组合器 `build_combined_pulse_callbacks` 及 SQLite Fake 领域底座 `FakePulseDomainHarness` |
| `server/tests/m3/pulse/test_wave2_runtime_and_control.py` | 新增 | M3 Wave 2 专项测试（7 大验收测试场景：控制入口与会话生命周期、在线闸门与离线时间锚、两道门代次拦截「关->开」慢响应、进程重启恢复、Mock HTTP 全分支与实测结算、Fake 事件+知识回调及SAVEPOINT原子回滚、信封与传播引用安全边界） |
| `docs/reports/gemini-li-m3-wave2.md` | 新增 | 本交付报告、可保证边界说明与公共接线示例 |

---

## 2. 核心实现与契约对照

### 2.1 独立/复用模型会话控制与零凭据落盘（`session.py`、`control.py`）
- **双模式会话与所有权隔离**：
  - `independent` 模式在脉动专属 `pulse_store` 中创建内存会话；切换或解绑时仅清理脉动持有的独立会话。
  - `reuse_narration` 模式仅在内存中保存对前台 `narration_store` 会话 ID 的引用；调用 `set_pulse_enabled(..., False)`、`unbind()` 或 `session_router.clear()` **绝不删除** `narration_store` 中的前台叙事会话。
  - 正式（`live`）旅程禁止绑定 `fake` 客户端；模拟（`fake`）旅程保留既有无费用入口并拒绝绑定付费会话。
- **开关与会话分离、失效等待重连**：
  - `connect_pulse_session` 仅验证并绑定有效会话，**不自动开启脉动开关，也不发送付费连通性测试请求**。
  - 会话过期（`MODEL_SESSION_EXPIRED`）、401/403 认证失败（`MODEL_AUTH_FAILED`）或后端重启后内存会话丢失（`MODEL_SESSION_UNBOUND`）时，实例状态自动转为 `waiting_for_model` 并附带精确的脱敏 `reason_code`，等待用户重新连接，绝不静默切换到其他 Key。
- **安全状态 DTO（`PulsePublicStatusDTO`）**：
  - `get_control_status` 返回 `available`、`enabled`、`paused`、`mode`（`fake / independent / reuse_narration / unbound`）、`state`（`off / ready / waiting_for_model / offline / budget_limited / working`）、脱敏 `model`、`reason_code`、`world_minute`、`world_revision`、`current_game_day` 与当游戏日 `usage` 明细。
  - 全链路通过 `assert_no_credentials` 校验，确保 API Key、会话 ID、租约令牌、请求头、隐藏事件标题或任务负载绝不进入持久队列、错误回包、日志、用量、快照或状态响应。

### 2.2 在线闸门、两道门代次校验与时间锚推进（`session.py`、`budget.py`、`worker.py`）
- **在线闸门与短租约兜底**：
  - `ActivityLeaseRegistry` 默认采用可配置短租约（`DEFAULT_TTL_SECONDS = 45.0`）。
  - `set_pulse_activity(conn, adventure_id, active=True, exclusive=True)` 仅从当前可见旅程续期，切换旅程时自动使先前旅程租约立即失效；离开页面或切后台调用 `active=False` 立即失效；心跳缺失时由短 TTL 自动兜底失效。
  - 读取状态（`get_control_status` / `get_pulse_status`）与读取消息均为只读操作，**绝不续期**活动租约或模型会话 TTL。
- **请求前（Gate 1）与提交前（Gate 2）两道门 + 代次（Generation）隔离**：
  - `pulse_instance_configs` 维护持久化代次 `generation`（每次开关/暂停切换时递增），`PulseSessionRouter` 与 `ActivityLeaseRegistry` 分别维护会话代次与在线周期代次（正常心跳续期不改变在线代次，离线后再上线递增代次）。
  - **第一道门（阶段 1 短写事务）**：核验开关、时间锚、在线租约、模型会话与快照（`advance` 无到期事件直接标记 `skipped_no_due_events` 完成且零请求零扣费；`seed` 缺 Canon 资料返回 `blocked_snapshot` 转入等待），并记录 `start_config_gen`、`start_session_gen`、`start_activity_gen` 与 `snapshot_revision`。
  - **第二道门（阶段 3 短写事务）**：在将模型响应写入事件与知识表之前，重新核对当下开关、`generation == start_config_gen`、`session_gen == start_session_gen`、任务 `lease_token`、`activity_gen == start_activity_gen` 及 `world_instances.revision == snapshot_revision`。即使慢响应在途期间玩家执行了「关闭脉动 -> 再次开启脉动」，也会因 `generation` 不一致被 `discarded_stale_generation`（`PULSE_GENERATION_CHANGED`）精准拦截丢弃。
- **离线、暂停、关闭和未绑定期间推进时间锚，不积压旧任务**：
  - 在关闭、暂停、离线或未绑定期间确认前台回合时，`schedule_on_turn_commit` / `advance_pulse_time_anchor` 直接将 `resume_anchor_minute` 与 `last_pulsed_minute` 推进至当前已确认的 `world_minute`，并清理未执行的排队 `advance` 任务。
  - 前台恢复（`setActivity(True)`）、重新连接（`connect`）或重新启用（`setEnabled(True)`）时，以当前已确认分钟锚定，**既不推进世界时间，也不补演离线或暂停期间的历史事件**。

### 2.3 付费调用路径、上下文+输出上限预留与实测结算（`budget.py`、`client.py`、`observability.py`）
- **调用前预留与实测结算对账**：
  - 调用前通过 `estimate_call_reservation_tokens` 按提示词上下文估算值加上本次请求输出上限（`max_output_tokens`）预留并发额度。
  - 响应返回后，`PulseBudgetManager.settle()` 释放预留额度，并以服务商返回的 `actual` 实测 Token（缺失 `usage` 时回退为正文估算 `estimated`）写入 `pulse_usage_ledger` 与单轮累计器；若实测值高于或低于预留估算，后续调用严格按已落盘的实测总额受控。
- **模拟用量与可能计费用量严格分离**：
  - `FakePulseModelClient` 记录 `potentially_billed=False`，`OpenAIPulseModelClient` 在请求已发出且成功（`succeeded`）、超时（`timeout_maybe_billed`）、响应丢失（`response_lost_maybe_billed`）或上游 5xx（`upstream_error_maybe_billed`）时记录 `potentially_billed=True`；在连接建立前失败或 401/403/429 时记录 `potentially_billed=False, outcome="aborted_unbilled"`。
  - `PulseBudgetManager.reserve(..., is_paid=True)` 仅统计 `potentially_billed=1` 的账本行，确保模拟演示用量绝不占用真实 API 预算；`get_usage_summary` 与状态 DTO 清晰分列 `actual_tokens`、`estimated_tokens`、`potentially_billed_tokens`、`timeout_maybe_billed_*`、`simulated_ tokens/calls` 与 `aborted_unbilled_calls`。

---

## 3. 关闭、断网与 App 被系统杀掉时的可保证边界说明

| 场景 | 阻止新请求（Gate 1） | 丢弃未提交的在途响应（Gate 2） | 已发出调用可能产生的费用说明 |
| --- | --- | --- | --- |
| **用户主动关闭脉动（`setEnabled(False)`）** | **立即保证**：短事务内将 `enabled` 置 0、`generation + 1`，并取消所有 `pending / waiting_for_model / running` 任务，后续不会发出任何新请求。 | **100% 保证**：已经在途的慢模型请求返回后，在阶段 3 第二道门因 `enabled=False` 或 `generation` 已变更（即使中途又重开）及 `lease_token` 已失效而被直接丢弃，绝不写入事件或知识表。 | 关闭操作发生**之前**已经通过网络发往上游服务商的那 1 次在途请求，上游服务商可能已实际处理并计费；本模块会将该次在途返回或超时的 Token 如实记入 `pulse_usage_ledger`（`potentially_billed=True`）供用户查账，同时回滚/丢弃其领域变更。 |
| **手机断网 / 离开页面 / 切入后台** | **立即或短租约内保证**：前端触发 `setActivity(False)` 时立即使在线租约失效；若因断网导致离开请求未送达后端，后端在短租约（默认 45s）到期后自动判定离线，阻止后续新请求并发起时间锚推进。 | **保证**：若模型请求返回时客户端已处于离线状态（或经历了离线再重连导致 `activity_generation` 变更），阶段 3 第二道门拒绝提交领域数据，任务转入 `waiting_for_model`（`CLIENT_OFFLINE_BEFORE_COMMIT`）。 | 断网发生前已由后端发往模型服务商的在途请求，服务商仍可能计费；后端在收到上游响应或超时异常时会将其记入 `potentially_billed` 账本，但不会在玩家离线状态下偷偷推进世界事件。 |
| **手机 App 被系统强行杀掉 / 浏览器崩溃** | **短租约兜底保证**：App 被系统强杀无法发送 `setActivity(False)`，后端最多在当前活动租约剩余 TTL（默认 ≤ 45s）到期后自动停止新请求；期间游戏时间 `world_minute` 绝不随现实时间流逝而推进。 | **保证**：若在途请求返回时短租约已到期，阶段 3 拒绝提交；即便在杀进程后几秒内（租约尚未到期前）有 1 个在途请求完成提交，其推进的也是杀进程前已确认回合的到期事件，绝不会推进新的游戏分钟。 | 租约到期前已发出的至多 1 次在途请求可能产生上游费用并被记入账本；租约到期后零新请求、零额外费用。 |
| **后端服务进程重启 / 崩溃** | **立即保证**：API Key 与会话绑定仅保存在内存中，进程重启后内存清空，`recover_on_startup()` 将遗留任务置于 `waiting_for_model`（`MODEL_SESSION_UNBOUND`），在用户重新连接并上线前绝不发出任何请求。 | **保证**：进程崩溃前的旧事务由 SQLite 自动回滚，重启后旧 `lease_token` 被清理，不会残留半笔事件或知识写入。 | 崩溃瞬间若已有请求发往上游服务商但进程在落盘前退出，该笔上游消耗以模型服务商后台账单为准。 |

---

## 4. 给 DeepSeekHarness 的公共接线建议与调用样例

### 4.1 组合事件（Marcin）与知识（Ma）回调并初始化控制器
```python
from app.db import db
from app.m3.pulse import (
    ActivityLeaseRegistry,
    PulseBudgetManager,
    PulseRuntimeController,
    PulseSessionRouter,
    PulseWorker,
    build_combined_pulse_callbacks,
    pulse_worker_lifespan,
)

# 1. 组合 Marcin 的事件回调与 Ma 的知识回调
read_snapshot, validate_proposal, apply_proposal = build_combined_pulse_callbacks(
    events_snapshot_reader=events_module.read_snapshot,
    events_validator=events_module.validate_proposal,
    events_applier=events_module.apply_proposal,
    knowledge_validator=knowledge_module.validate_transmissions,
    knowledge_applier=knowledge_module.apply_transmissions,
)

# 2. 创建路由器、在线闸门、Worker 与运行期控制器
session_router = PulseSessionRouter(
    narration_store=narration_store,
    allowed_base_urls=allowed_model_urls if hosted else None,
    require_https=hosted,
)
activity_registry = ActivityLeaseRegistry(default_ttl_seconds=45.0)
budget_manager = PulseBudgetManager()

pulse_worker = PulseWorker(
    lambda: db(cfg.db_path),
    session_router=session_router,
    activity_registry=activity_registry,
    budget_manager=budget_manager,
    snapshot_reader=read_snapshot,
    proposal_validator=validate_proposal,
    proposal_applier=apply_proposal,
)
pulse_controller = PulseRuntimeController(lambda: db(cfg.db_path), worker=pulse_worker)
```

### 4.2 映射手机端四项控制路由（`M3_WAVE2_API.md` §手机端控制契约）
```python
@app.get("/api/v1/adventures/{adventure_id}/world-pulse")
def get_world_pulse_status(adventure_id: str):
    # 对应 transport.getStatus(adventureId)：只读不续期，返回安全 DTO
    return pulse_controller.get_status(adventure_id)

@app.post("/api/v1/adventures/{adventure_id}/world-pulse/session")
def connect_world_pulse_session(adventure_id: str, payload: dict):
    # 对应 transport.connect(adventureId, config)：支持 independent / reuse_narration / fake
    return pulse_controller.connect(adventure_id, payload)

@app.put("/api/v1/adventures/{adventure_id}/world-pulse")
def set_world_pulse_enabled(adventure_id: str, payload: dict):
    # 对应 transport.setEnabled(adventureId, enabled)
    res = pulse_controller.set_enabled(adventure_id, payload.get("enabled"))
    if res["enabled"]:
        pulse_worker.start()
    return res

@app.post("/api/v1/adventures/{adventure_id}/world-pulse/activity")
def set_world_pulse_activity(adventure_id: str, payload: dict):
    # 对应 transport.setActivity(adventureId, active)
    res = pulse_controller.set_activity(adventure_id, payload.get("active"))
    if res["enabled"] and res["state"] in ("ready", "working"):
        pulse_worker.start()
    return res
```

### 4.3 前台回合确认事务内接线（`adventures/service.py`）
在 `adventures/service.py` 的 `commit()` 短写事务末尾调用：
```python
pulse_controller.on_turn_committed(
    conn,
    instance_id=updated["id"],
    adventure_id=adventure_id,
    adventure_revision=run["revision"] + 1,
    world_revision=updated["world_revision"],
    world_minute=updated["world_minute"],
)
```
（注：直接沿用原 `schedule_on_turn_commit(...)` 调用也完全兼容；传入 `session_router` 与 `activity_registry` 或调用 `pulse_controller.on_turn_committed` 可在离线/未绑定确认回合时立即推进时间锚而不入队。）

---

## 5. 独立测试命令与验证证据分类

按照协作规则分开记录各类测试证据：
- **Fake 证据**：**已实跑通过**（验证持久队列、在线闸门、切旅程独占续期、离线/暂停/未绑定推进时间锚不积压历史、两道门与 `generation` 代次拦截、进程重启恢复、Fake 事件与知识传播回调及 `SAVEPOINT` 原子回滚）。
- **Mock HTTP（`httpx.MockTransport`）证据**：**已实跑通过**（验证 `OpenAIPulseModelClient` 正常 `actual` usage 实测结算、缺失 `usage` 估算结算、401 认证失败自动置为 `waiting_for_model` 且零凭据泄漏、响应流中途丢失 `MODEL_RESPONSE_LOST` 与请求超时 `MODEL_TIMEOUT` 计入 `potentially_billed`、以及关闭/重开切换后慢响应被丢弃）。
- **APK 打包证据**：未在本包运行（归属 DeepSeekHarness CI 整体验收）。
- **真实服务商（Live API）证据**：未运行（严格遵守“任何测试不得请求真实供应商”约束）。
- **安卓真机证据**：未运行（归属集成后真机验收）。

### 5.1 独立测试执行记录
```bash
$ PYTHONPATH=server python3 -m unittest discover -s server/tests/m3/pulse -p "test_*.py" -v
test_consecutive_turns_coalesce_pending_advance_jobs (test_queue.TestPulseQueue.test_consecutive_turns_coalesce_pending_advance_jobs) ... ok
test_enqueue_idempotency_and_completed_never_reruns (test_queue.TestPulseQueue.test_enqueue_idempotency_and_completed_never_reruns) ... ok
test_lease_expiration_recovery_and_stale_worker_commit_rejected (test_queue.TestPulseQueue.test_lease_expiration_recovery_and_stale_worker_commit_rejected) ... ok
test_multi_connection_contention_atomic_claim (test_queue.TestPulseQueue.test_multi_connection_contention_atomic_claim) ... ok
test_queue_rejects_credential_storage_and_redacts_errors (test_queue.TestPulseQueue.test_queue_rejects_credential_storage_and_redacts_errors) ... ok
test_retry_exponential_backoff_and_max_attempts_failure (test_queue.TestPulseQueue.test_retry_exponential_backoff_and_max_attempts_failure) ... ok
test_wait_for_model_reclaim_and_cancel_transitions (test_queue.TestPulseQueue.test_wait_for_model_reclaim_and_cancel_transitions) ... ok
test_budget_exhausted_defers_job_without_busy_loop (test_session_and_budget.TestPulseSessionAndBudget.test_budget_exhausted_defers_job_without_busy_loop) ... ok
test_independent_and_reuse_narration_session_modes (test_session_and_budget.TestPulseSessionAndBudget.test_independent_and_reuse_narration_session_modes) ... ok
test_mock_transport_actual_vs_estimated_usage_and_timeout_billing (test_session_and_budget.TestPulseSessionAndBudget.test_mock_transport_actual_vs_estimated_usage_and_timeout_billing) ... ok
test_pre_request_reservation_and_concurrent_deduction (test_session_and_budget.TestPulseSessionAndBudget.test_pre_request_reservation_and_concurrent_deduction) ... ok
test_restart_without_key_waits_for_model_and_resumes_on_reconnect (test_session_and_budget.TestPulseSessionAndBudget.test_restart_without_key_waits_for_model_and_resumes_on_reconnect) ... ok
test_control_entrypoints_session_lifecycle_and_dto_redaction (test_wave2_runtime_and_control.TestM3Wave2RuntimeAndControl.test_control_entrypoints_session_lifecycle_and_dto_redaction) ... ok
test_envelope_validation_rejects_unknown_root_player_ledger_and_seed_transmissions (test_wave2_runtime_and_control.TestM3Wave2RuntimeAndControl.test_envelope_validation_rejects_unknown_root_player_ledger_and_seed_transmissions) ... ok
test_fake_domain_callbacks_seed_block_and_atomic_rollback_on_invalid_relay (test_wave2_runtime_and_control.TestM3Wave2RuntimeAndControl.test_fake_domain_callbacks_seed_block_and_atomic_rollback_on_invalid_relay) ... ok
test_mock_http_paid_path_actual_settlement_simulated_separation_and_failures (test_wave2_runtime_and_control.TestM3Wave2RuntimeAndControl.test_mock_http_paid_path_actual_settlement_simulated_separation_and_failures) ... ok
test_online_gate_exclusive_activity_and_offline_time_anchor_no_backlog (test_wave2_runtime_and_control.TestM3Wave2RuntimeAndControl.test_online_gate_exclusive_activity_and_offline_time_anchor_no_backlog) ... ok
test_startup_recovery_checks_switch_connection_and_activity (test_wave2_runtime_and_control.TestM3Wave2RuntimeAndControl.test_startup_recovery_checks_switch_connection_and_activity) ... ok
test_two_gates_and_generation_blocks_slow_response_after_off_on_toggle (test_wave2_runtime_and_control.TestM3Wave2RuntimeAndControl.test_two_gates_and_generation_blocks_slow_response_after_off_on_toggle) ... ok
test_atomic_rollback_when_proposal_applier_fails (test_worker_and_observability.TestPulseWorkerAndObservability.test_atomic_rollback_when_proposal_applier_fails) ... ok
test_default_disabled_and_pause_resume_no_retroactive_history (test_worker_and_observability.TestPulseWorkerAndObservability.test_default_disabled_and_pause_resume_no_retroactive_history) ... ok
test_foreground_commits_turn_during_slow_background_model_and_stale_revision_conflict (test_worker_and_observability.TestPulseWorkerAndObservability.test_foreground_commits_turn_during_slow_background_model_and_stale_revision_conflict) ... ok
test_observability_redaction_snapshot_restore_and_lifespan (test_worker_and_observability.TestPulseWorkerAndObservability.test_observability_redaction_snapshot_restore_and_lifespan) ... ok
test_offline_activity_lease_pauses_paid_requests_and_preserves_clock (test_worker_and_observability.TestPulseWorkerAndObservability.test_offline_activity_lease_pauses_paid_requests_and_preserves_clock) ... ok
test_stale_worker_rejected_after_lease_reclaimed_during_model_call (test_worker_and_observability.TestPulseWorkerAndObservability.test_stale_worker_rejected_after_lease_reclaimed_during_model_call) ... ok

----------------------------------------------------------------------
Ran 25 tests in 0.199s

OK
```
