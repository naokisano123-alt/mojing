# Gemini（Li）｜M3 Wave 1：异步世界脉动、持久队列与模型预算交付报告

- **负责方**：`Gemini Li`
- **任务文档**：`docs/collaboration/M3_WAVE1.md`、`docs/collaboration/M3_WAVE1_GEMINI_LI.md`
- **契约版本**：`docs/contracts/M3_WAVE1_API.md`（v1）
- **功能代码基线（code_baseline）**：`9388278f8a334f457e92ed820cc5b2456c76dfd8`
- **任务书快照提交（snapshot_commit）**：`20ba6c6fab40301252925acdae1d62471cc520ee`
- **独占业务与测试路径**：
  - `server/app/m3/pulse/**`
  - `server/tests/m3/pulse/**`
  - `docs/reports/gemini-li-m3-wave1.md`
- **防覆写与边界声明**：
  - 仅新增 `server/app/m3/pulse/`、`server/tests/m3/pulse/` 与本报告文件；
  - 未修改 `server/app/db.py`、`server/app/main.py`、现有冒险/检查点/迁移模块、公共 `server/app/model_gateway/`、前端文件、依赖锁文件、Android 工程或 CI；
  - 未创建 `server/app/m3/__init__.py` 或 `server/tests/m3/__init__.py`（利用 Python PEP 420 原生命名空间包），彻底避免与 DeepSeekHarness（`m3/core`）、Gemini Marcin（`m3/events`）、Gemini Ma（`m3/knowledge`）发生共享父目录 `__init__.py` 路径冲突；
  - 零新增第三方依赖（完全基于现有标准库 `sqlite3`、`asyncio`、`unittest` 及已安装的 `httpx`）。

---

## 1. 交付文件清单

| 路径 | 操作 | 说明 |
| --- | --- | --- |
| `server/app/m3/pulse/__init__.py` | 新增 | M3.2 世界脉动公共导出入口 |
| `server/app/m3/pulse/types.py` | 新增 | `PulseTrigger`、`PulseBudgetConfig`、`PulseUsageEvidence`、`PulseModelRequest/Response` 等契约数据结构 |
| `server/app/m3/pulse/errors.py` | 新增 | `PulseError`、敏感键名拦截器 `assert_no_credentials` 与错误消息脱敏器 `sanitize_text` |
| `server/app/m3/pulse/schema.py` | 新增 | SQLite 幂等建表（`pulse_instance_configs`、`pulse_jobs`、`pulse_job_keys`、`pulse_usage_ledger`） |
| `server/app/m3/pulse/queue.py` | 新增 | 持久队列：`enqueue`（含唯一幂等键与连续回合合并）、`claim`（原子领取与过期租约回收）、`complete`、`fail_or_retry`、`wait_for_model`、`resume_waiting_jobs`、`cancel` |
| `server/app/m3/pulse/session.py` | 新增 | 内存会话路由器 `PulseSessionRouter`（独立会话 / 复用叙事会话 / Fake）与在线活动租约 `ActivityLeaseRegistry` |
| `server/app/m3/pulse/client.py` | 新增 | `OpenAIPulseModelClient`（复用 URL 校验、禁重定向、256KiB 响应上限、提取实测/估算/超时可能计费用量）与 `FakePulseModelClient` |
| `server/app/m3/pulse/budget.py` | 新增 | 标准复杂度默认配置、请求前预算预留、并发扣额、冷却控制与按游戏日归档账本 `PulseBudgetManager` |
| `server/app/m3/pulse/worker.py` | 新增 | `schedule_on_turn_commit` 调度器、事务外模型推演与提交前复核 `PulseWorker`、FastAPI `pulse_worker_lifespan` |
| `server/app/m3/pulse/observability.py` | 新增 | 脱敏任务状态 `get_pulse_status`、用量汇总 `get_usage_summary`、模块快照 `snapshot` 与恢复 `restore` |
| `server/tests/m3/pulse/__init__.py` | 新增 | M3.2 测试模块包声明 |
| `server/tests/m3/pulse/test_queue.py` | 新增 | 队列幂等、连续回合合并、多连接争抢、租约过期回收、旧 Worker 提交拒绝与凭据拦截单测（7 项） |
| `server/tests/m3/pulse/test_session_and_budget.py` | 新增 | 独立/复用会话模式、重启无 Key 等待、请求前预留、并发扣额、MockTransport 实测/估算/超时计费与预算延后单测（5 项） |
| `server/tests/m3/pulse/test_worker_and_observability.py` | 新增 | 默认关闭、暂停恢复不补造历史、离线暂停、后台慢请求下前台确认回合与旧版本冲突、慢请求租约被夺拒绝提交、SAVEPOINT 回滚及脱敏可观察性单测（6 项） |
| `docs/reports/gemini-li-m3-wave1.md` | 新增 | 本交付报告与公共接线说明 |

---

## 2. 契约对照与实现要点

### 2.1 持久队列（`server/app/m3/pulse/queue.py`）
- **任务字段与状态**：严格实现 `job_id`、`instance_id`、`kind`（`seed`/`advance`）、`trigger_adventure_id`、`trigger_revision`、`base_world_revision`、`target_world_minute`、`status`（`pending/running/completed/failed/waiting_for_model/cancelled`）、`attempts`、`next_retry_at`、`lease_token`、`lease_until`、`error_code` 与脱敏 `error_summary`。
- **唯一幂等与连续回合合并**：
  - `pulse_jobs` 与 `pulse_job_keys` 按 `(instance_id, idempotency_key)` 唯一约束；已 `completed` 的任务再次触发直接返回已完成记录，绝不重跑。
  - 当同一实例已有处于 `pending` 或 `waiting_for_model` 的 `advance` 任务时，后续连续确认回合自动合并（coalesce）至最新的 `target_world_minute` 与 `base_world_revision`，同时将原幂等键与新幂等键全部登记到 `pulse_job_keys`，确保合并前后的任意幂等键均不会重复执行。
- **原子领取、租约回收与旧令牌拦截**：
  - `claim(conn, now)` 采用单条带状态与旧令牌前置条件的 `UPDATE ... WHERE job_id = ? AND status = ? AND IFNULL(lease_token, '') = ?` 并检查 `rowcount == 1`，支持多 SQLite 连接并发争抢与过期 `running` 租约自动回收。
  - 租约一旦过期被新 Worker 接管，`lease_token` 立即轮换；旧 Worker 即使随后收到模型响应，在调用 `complete` 或 `PulseWorker` 第三阶段复核时均会抛出 `PulseError("PULSE_LEASE_STOLEN" / "PULSE_LEASE_EXPIRED", status=409)` 并放弃写入。
- **零凭据落盘**：`enqueue`、快照与状态输出均经 `assert_no_credentials` 校验，任何含 `api_key`、`session_id`、`Authorization`、`Bearer` 或 `sk-...` 的输入均被拦截或自动脱敏为 `[REDACTED]`。

### 2.2 调度与游戏时间（`server/app/m3/pulse/worker.py`）
- **默认关闭与暂停/恢复语义**：
  - 实例脉动配置默认 `enabled=False, paused=False`。
  - 暂停或关闭时自动取消未开始执行的排队任务；恢复启用（`resume_pulse`）时将 `resume_anchor_minute` 与 `last_pulsed_minute` 锚定到当前已确认的 `world_minute`，绝不补造暂停期间流逝的游戏时间历史。
- **在线活动租约与现实时间隔离**：
  - `ActivityLeaseRegistry` 维护内存级在线活动租约；玩家离线或租约到期时，Worker 将任务转入 `waiting_for_model`（`CLIENT_OFFLINE`），不发送付费模型请求，且现实时间流逝绝不改变 `world_minute`。
- **三阶段事务边界与防前后台覆盖**：
  1. **阶段 1（短写事务）**：领取任务、检查开关/活动租约/模型会话、读取当前实例快照并预留预算额度，随后立即提交释放 SQLite 写锁。
  2. **阶段 2（SQLite 事务外）**：调用 `model_client.propose(request)` 并执行 `proposal_validator` 纯计算结构校验。在此期间数据库无写锁，前台玩家可随时确认新回合。
  3. **阶段 3（短写事务 + SAVEPOINT）**：归档本次模型调用用量；依次复核队列 `lease_token`、脉动开关、活动租约及 `world_instances.revision == snapshot_revision`。若后台慢请求期间前台已确认新回合（版本已推进），立即拒绝应用旧提议，将任务基准版本更新至最新 `revision` 与 `world_minute` 后退避重试；若校验通过，在 `SAVEPOINT sp_pulse_apply` 内原子执行 `proposal_applier` 并推进 `world_instances.revision + 1`。

### 2.3 模型用途与预算（`server/app/m3/pulse/session.py`、`budget.py`、`client.py`）
- **内存会话绑定与所有权隔离**：
  - `PulseSessionRouter` 支持 `independent`（独立脉动会话池）、`reuse_narration`（内存引用现有 `narration_store` 会话）与 `fake` 三种模式。
  - `reuse_narration` 模式仅在内存中保存会话 ID 引用，不改变 `narration_store` 会话所有权；调用 `unbind`、`disable_pulse` 或 `router.clear()` 绝不删除前台叙事会话。
  - 服务重启后内存会话为空时，队列任务自动进入 `waiting_for_model`（`MODEL_SESSION_UNBOUND`），等待用户重新连接后通过 `resume_waiting_jobs` 恢复。
- **预算预留、并发扣额与超时可能计费**：
  - 默认采用 `standard` 标准复杂度（支持 `light` / `standard` / `deep` 及逐项自定义每轮调用数、每轮 Token、每游戏日调用数、每游戏日 Token 与冷却秒数）。
  - 请求发出前通过 `PulseBudgetManager.reserve()` 在线程锁内检查冷却窗口、单轮上限及当日已落盘用量 + 在途并发预留额度；超限时抛出 `BudgetExhaustedError` 并将任务延后 `defer_seconds`（不消耗失败重试次数，杜绝忙循环）。
  - `OpenAIPulseModelClient` 优先读取服务商返回的 `usage`（标记 `usage_source="actual"`）；若服务商未返回 `usage` 则按 UTF-16 长度估算（标记 `usage_source="estimated"`）；若请求已发出但发生 `TimeoutException` 或上游 5xx 错误，记录 `usage_source="estimated", potentially_billed=True, outcome="timeout_maybe_billed"` 并计入当日保守预算，防止超时重试刷爆用户真实账单；若为 401/403/429 或连接拒绝等未执行情形，则标记 `potentially_billed=False` 并释放预留额度。

---

## 3. 实际测试命令与结果（Evidence Classification）

按照契约要求，严格区分四种验证证据：
- **Fake 证据**：已实跑通过（验证队列、调度、版本冲突、暂停恢复、离线保护、快照恢复）。
- **Mock（`httpx.MockTransport`）证据**：已实跑通过（验证 `OpenAIPulseModelClient` 实测/估算用量、超时可能计费、401 脱敏与慢请求并发）。
- **真实服务商（Live API）证据**：未运行（按契约不在自动化测试中使用真实付费 Key）。
- **安卓真机证据**：未运行（本轮为后端独立模块交付）。

### 3.1 本模块单元测试实跑
```bash
$ PYTHONPATH=server python3 -m unittest discover -s server/tests/m3/pulse -p "test_*.py" -v
test_consecutive_turns_coalesce_pending_advance_jobs (test_queue.TestPulseQueue.test_consecutive_turns_coalesce_pending_advance_jobs)
验收：连续回合可合并待办为更新的已确认时钟目标与基准版本，且旧/新幂等键均不重复执行。 ... ok
test_enqueue_idempotency_and_completed_never_reruns (test_queue.TestPulseQueue.test_enqueue_idempotency_and_completed_never_reruns)
验收：相同任务只完成一次，已完成任务再次登记不重复执行。 ... ok
test_lease_expiration_recovery_and_stale_worker_commit_rejected (test_queue.TestPulseQueue.test_lease_expiration_recovery_and_stale_worker_commit_rejected)
验收：租约过期后被新 Worker 回收接管，旧 Worker 即使拿到模型响应也绝不能提交。 ... ok
test_multi_connection_contention_atomic_claim (test_queue.TestPulseQueue.test_multi_connection_contention_atomic_claim)
验收：多连接争抢同一条待办任务时，只有一个连接能成功领取。 ... ok
test_queue_rejects_credential_storage_and_redacts_errors (test_queue.TestPulseQueue.test_queue_rejects_credential_storage_and_redacts_errors)
验收：队列严禁写入 API Key、会话 ID 或密钥请求头，错误信息自动脱敏。 ... ok
test_retry_exponential_backoff_and_max_attempts_failure (test_queue.TestPulseQueue.test_retry_exponential_backoff_and_max_attempts_failure)
验收：失败重试退避与达到最大重试次数后归档为 failed。 ... ok
test_wait_for_model_reclaim_and_cancel_transitions (test_queue.TestPulseQueue.test_wait_for_model_reclaim_and_cancel_transitions)
验收：等待模型、唤醒恢复、过期显式回收与取消语义。 ... ok
test_budget_exhausted_defers_job_without_busy_loop (test_session_and_budget.TestPulseSessionAndBudget.test_budget_exhausted_defers_job_without_busy_loop)
验收：预算耗尽时将任务延后（设置 next_retry_at），不消耗失败次数，避免忙循环。 ... ok
test_independent_and_reuse_narration_session_modes (test_session_and_budget.TestPulseSessionAndBudget.test_independent_and_reuse_narration_session_modes)
验收：支持独立会话与复用叙事会话两种模式，关闭/清理脉动绝不删除复用的叙事会话。 ... ok
test_mock_transport_actual_vs_estimated_usage_and_timeout_billing (test_session_and_budget.TestPulseSessionAndBudget.test_mock_transport_actual_vs_estimated_usage_and_timeout_billing)
验收：MockTransport 下服务商返回 actual usage、缺失时标记 estimated、以及请求超时可能计费的账本记录。 ... ok
test_pre_request_reservation_and_concurrent_deduction (test_session_and_budget.TestPulseSessionAndBudget.test_pre_request_reservation_and_concurrent_deduction)
验收：标准复杂度默认配置、请求前预算预留与并发扣额防超支。 ... ok
test_restart_without_key_waits_for_model_and_resumes_on_reconnect (test_session_and_budget.TestPulseSessionAndBudget.test_restart_without_key_waits_for_model_and_resumes_on_reconnect)
验收：服务重启后内存无 Key 时，已入队任务转入 waiting_for_model 不发请求；重连后正常恢复完成。 ... ok
test_atomic_rollback_when_proposal_applier_fails (test_worker_and_observability.TestPulseWorkerAndObservability.test_atomic_rollback_when_proposal_applier_fails)
验收：当 proposal_applier 内部抛出异常时，SAVEPOINT 原子回滚，不留半写入数据且不推进实例版本。 ... ok
test_default_disabled_and_pause_resume_no_retroactive_history (test_worker_and_observability.TestPulseWorkerAndObservability.test_default_disabled_and_pause_resume_no_retroactive_history)
验收：默认关闭；暂停期间推进游戏时间不产生后台模拟，恢复时从当前时刻继续，不补造暂停期间的历史。 ... ok
test_foreground_commits_turn_during_slow_background_model_and_stale_revision_conflict (test_worker_and_observability.TestPulseWorkerAndObservability.test_foreground_commits_turn_during_slow_background_model_and_stale_revision_conflict)
验收：网络调用在 SQLite 写事务外，后台慢请求期间前台可立即确认新回合；后台返回后检测到旧版本冲突，拒绝覆盖并重排。 ... ok
test_observability_redaction_snapshot_restore_and_lifespan (test_worker_and_observability.TestPulseWorkerAndObservability.test_observability_redaction_snapshot_restore_and_lifespan)
验收：可观察性状态与用量汇总脱敏、快照不含活跃任务与凭据、以及 lifespan 正常启停。 ... ok
test_offline_activity_lease_pauses_paid_requests_and_preserves_clock (test_worker_and_observability.TestPulseWorkerAndObservability.test_offline_activity_lease_pauses_paid_requests_and_preserves_clock)
验收：玩家离线或活动租约过期时不发付费请求，现实时间流逝不推进游戏分钟。 ... ok
test_stale_worker_rejected_after_lease_reclaimed_during_model_call (test_worker_and_observability.TestPulseWorkerAndObservability.test_stale_worker_rejected_after_lease_reclaimed_during_model_call)
验收：Worker A 在慢模型请求期间租约过期被 Worker B 接管完成，Worker A 随后拿到模型响应也无法提交。 ... ok

----------------------------------------------------------------------
Ran 18 tests in 0.098s

OK
```

### 3.2 既有 `model_gateway` 回归测试实跑
```bash
$ PYTHONPATH=server python3 -m unittest discover -s server/tests/model_gateway -p "test_*.py"
.......................
----------------------------------------------------------------------
Ran 23 tests in 0.045s

OK
```

### 3.3 待 DeepSeekHarness 集成后联调的项
- 与 `server/app/m3/core/`（真实 `world_instances` 迁移）、`server/app/m3/events/`（Marcin 的 `validate_proposal` / `apply_proposal`）及 `server/app/m3/knowledge/`（Ma 的 `record_transmission`）的跨模块回调组装；
- 在 `server/app/db.py`、`server/app/main.py` 与 `server/app/adventures/service.py` 中的公共路由与事务挂载。

---

## 4. 给 DeepSeekHarness 的公共接线说明

### 4.1 `server/app/db.py` 建表挂载
在 `init_db` 中完成 `world_instances` 建表后，调用：
```python
from .m3.pulse.schema import ensure_pulse_schema

ensure_pulse_schema(conn, ensure_instance_table=False)
```

### 4.2 前台回合确认事务内登记任务（`server/app/adventures/service.py`）
在 `commit(conn, adventure_id, id, apply_changes=False)` 同一短写事务内更新实例 `world_minute` 与 `revision` 后，调用：
```python
from app.m3.pulse import schedule_on_turn_commit

# 仅在实例脉动已启用且未暂停时登记 advance 任务，默认关闭时不产生任何任务
schedule_on_turn_commit(
    conn,
    instance_id=instance_id,
    adventure_id=adventure_id,
    adventure_revision=new_adventure_revision,
    world_revision=new_world_revision,
    world_minute=confirmed_world_minute,
    kind="advance",
)
```

### 4.3 `server/app/main.py` Lifespan 与回调组装示例
```python
from app.m3.pulse import (
    ActivityLeaseRegistry,
    PulseBudgetManager,
    PulseSessionRouter,
    PulseWorker,
    get_pulse_status,
    get_usage_summary,
    pulse_worker_lifespan,
)

# 在 create_app 中组装（复用现有的 _store() 作为 narration_store）：
pulse_session_router = PulseSessionRouter(narration_store=_store())
pulse_activity_registry = ActivityLeaseRegistry()
pulse_budget_manager = PulseBudgetManager()

def _apply_pulse_proposal(conn, instance_id, validated_proposal, job):
    # 串联 Marcin 的事件账本 apply_proposal 与 Ma 的知识传播 record_transmission
    event_res = events_module.apply_proposal(conn, instance_id, validated_proposal)
    for transmission in validated_proposal.get("transmissions", []):
        knowledge_module.record_transmission(conn, instance_id, transmission)
    return event_res

pulse_worker = PulseWorker(
    lambda: db(cfg.db_path),
    session_router=pulse_session_router,
    activity_registry=pulse_activity_registry,
    budget_manager=pulse_budget_manager,
    snapshot_reader=events_module.build_pulse_input,
    proposal_validator=events_module.validate_proposal,
    proposal_applier=_apply_pulse_proposal,
)

# 在 FastAPI lifespan 中挂载：
# async with pulse_worker_lifespan(pulse_worker, auto_start_loop=True):
#     yield
```

### 4.4 检查点分支与存档迁移挂载
- 创建检查点或导出存档时调用 `pulse_snapshot = app.m3.pulse.snapshot(conn, instance_id)`；
- 恢复检查点分支或导入存档时调用 `app.m3.pulse.restore(conn, new_instance_id, pulse_snapshot)`。快照仅含配置与已确认时刻锚点，不含进行中任务、租约或内存凭据。
