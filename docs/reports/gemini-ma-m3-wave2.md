# 墨境 M3 Wave 2｜玩家知情边界、叙事上下文与手机端脉动设置交付报告（Gemini Ma）

- **负责方**：`Gemini Ma`
- **任务名称**：知识传播纯校验与事务应用、叙事知情投影与手机端脉动控制（M3 第二轮）
- **主分支代码基线（`code_baseline`）**：[`8a95587a778e3849385bc413632d402ecf09972f`](https://github.com/naokisano123-alt/mojing/commit/8a95587a778e3849385bc413632d402ecf09972f)（合入 PR #24）
- **任务书快照（`snapshot_commit`）**：[`d333af8757212894223b02de75eb2915dc40a4af`](https://github.com/naokisano123-alt/mojing/archive/d333af8757212894223b02de75eb2915dc40a4af.zip)（合入 [PR #25](https://github.com/naokisano123-alt/mojing/pull/25)）
- **契约与任务依据**：
  - [`docs/collaboration/M3_WAVE2.md`](https://github.com/naokisano123-alt/mojing/blob/main/docs/collaboration/M3_WAVE2.md)
  - [`docs/collaboration/M3_WAVE2_GEMINI_MA.md`](https://github.com/naokisano123-alt/mojing/blob/main/docs/collaboration/M3_WAVE2_GEMINI_MA.md)
  - [`docs/contracts/M3_WAVE2_API.md`](https://github.com/naokisano123-alt/mojing/blob/main/docs/contracts/M3_WAVE2_API.md)
  - [`docs/M3_DESIGN_DRAFT.md`](https://github.com/naokisano123-alt/mojing/blob/main/docs/M3_DESIGN_DRAFT.md) 与 [`docs/reports/codex-m3-wave1-integration.md`](https://github.com/naokisano123-alt/mojing/blob/main/docs/reports/codex-m3-wave1-integration.md)
- **独占业务路径**：
  - `server/app/m3/knowledge/`
  - `server/tests/m3/knowledge/`
  - `src/features/world-knowledge/`
  - `src/features/world-pulse/`（新目录）
  - `tests/ui/world-knowledge/`
  - `tests/ui/world-pulse/`（新目录）
  - `docs/reports/gemini-ma-m3-wave2.md`
- **防覆写与零越界声明**：
  - 本交付包**仅包含上述独占路径内的新增与改动文件**（共 20 个文件，无任何文件删除或重命名）；
  - 未修改 `server/app/adventures/**`、`server/app/narration/**`、`server/app/model_gateway/**`、`server/app/m3/core/**`、`server/app/m3/events/**`、`server/app/m3/pulse/**`、`src/features/adventures/AdventureJournal.tsx`、`package.json`、`.github/workflows/**` 或 Android 工程。

## 1. 交付文件清单

| 文件路径 | 操作 | 职责说明 |
| --- | --- | --- |
| `server/app/m3/knowledge/__init__.py` | Modify | 导出 `validate_proposal`、`apply_proposal`、`read_knowledge_snapshot`、`build_adventure_narration_knowledge_context`、`run_wave2_multi_hop_pulse_fixture` 等 Wave 2 公共入口 |
| `server/app/m3/knowledge/errors.py` | Modify | 扩展 `KnowledgeError` 安全详情白名单（支持版本与时间预期对比脱敏输出，零隐藏事件泄露） |
| `server/app/m3/knowledge/types.py` | Modify | 新增 `ValidatedTransmissionOp`、`ValidatedKnowledgeProposal`、`KnowledgeValidationContext`、临时事件 ID 检测 `is_temporary_event_id`、玩家账本字段与任意路径黑名单、增强型提示词注入过滤及 JSON 可序列化 `NarrationKnowledgeBundle` |
| `server/app/m3/knowledge/service.py` | Modify | 实现事务外纯校验 `validate_proposal`、短事务原子应用适配器 `apply_proposal`、只读上下文提取 `read_knowledge_snapshot` 及限长跨分支保护叙事投影 `build_narration_knowledge_context` |
| `server/app/m3/knowledge/fixtures.py` | Modify | 新增端到端三阶段脉动传播 Fixture `run_wave2_multi_hop_pulse_fixture`（「商队首领先得知改道 $\rightarrow$ 店主转述 $\rightarrow$ 玩家才看见」） |
| `server/tests/m3/knowledge/test_knowledge_service.py` | Modify | 扩展至 15 项后端单元测试（保留首轮 11 项，新增 Wave 2 纯校验、三步多跳演进、事务版本冲突与原子回滚、限长可序列化叙事上下文及复合提示词注入对照 4 大测试） |
| `src/features/world-knowledge/types.ts` | Modify | 新增 `NarrationKnowledgeContextItem`、`NarrationKnowledgeContextDto` 与 `FakeWorldKnowledgeTransport` 类型定义 |
| `src/features/world-knowledge/api.ts` | Modify | 新增 `sanitizeWorldNewsItem` / `sanitizeWorldNewsPage` 零泄漏清洗纯函数及 `createNorthTownMultiHopKnowledgeScenario` 三跳前端场景辅助器 |
| `src/features/world-knowledge/WorldKnowledgePanel.tsx` | Modify | 支持外部 `refreshTrigger` 联动刷新与响应数据脱敏清洗，读取消息不触发活动租约续期 |
| `src/features/world-knowledge/index.ts` | Modify | 导出新增的清洗函数、三跳场景辅助器与类型契约 |
| `src/features/world-pulse/types.ts` | Add | 手机端脉动控制契约类型（`PulseConnectionMode`、`PulseRuntimeState`、`PulseDayUsageSummary`、`WorldPulseStatus`、`PulseConnectConfig`、`WorldPulseTransport`） |
| `src/features/world-pulse/api.ts` | Add | 默认同源 HTTP 传输 `worldPulseApi`、状态脱敏归一 `sanitizePulseStatus`、实际/估算用量格式化 `formatPulseUsageSummary` 及独立确定性替身 `createFakeWorldPulseTransport` |
| `src/features/world-pulse/WorldPulsePanel.tsx` | Add | 手机优先的世界脉动控制面板（独立连接优先、支持复用叙事会话、连接与启用分离、首次启用额外消耗确认、六种状态与实际/估算用量展示、Key 内存生命周期清理、可见性与断网活动租约管理、切旅程防竞态） |
| `src/features/world-pulse/world-pulse.css` | Add | 适配移动窄屏（`@media (max-width: 700px)`）、触屏点击高度（`>= 40px`）与深浅主题变量的样式表 |
| `src/features/world-pulse/index.ts` | Add | `world-pulse` 模块桶导出 |
| `tests/ui/world-knowledge/world-knowledge-transport.test.ts` | Modify | 扩展为 4 项 `node:test` 测试（新增三跳传播可见性与叙事上下文 DTO 校验） |
| `tests/ui/world-knowledge/world-knowledge.test.tsx` | Modify | 扩展为 7 项 Vitest + React Testing Library 测试（新增三跳演进 UI 验收与脏字段剥离测试） |
| `tests/ui/world-pulse/world-pulse-transport.test.ts` | Add | 3 项 `node:test` 纯逻辑测试（覆盖四种模式、六种状态、连接与启用分离、过期重连、活动租约及凭据剥离） |
| `tests/ui/world-pulse/world-pulse.test.tsx` | Add | 7 项手机宽度（390px）Vitest + React Testing Library UI 测试（覆盖独立连接、Key 内存清空、复用叙事会话、显式启停与额外消耗提示、失效重连、预算受限与用量展示、切后台/断网及切旅程迟到响应隔离） |
| `docs/reports/gemini-ma-m3-wave2.md` | Add | 本轮交付与公共接线说明报告 |

## 2. 本轮接口约定对照（`docs/contracts/M3_WAVE2_API.md`）

### 2.1 后端传播纯校验、事务应用与叙事投影

| 契约边界 | 实现位置 | 行为与安全保障 |
| --- | --- | --- |
| `read_knowledge_snapshot(conn, instance_id, job=None, *, allowed_event_ids=None)` | `server/app/m3/knowledge/service.py` | 只读查询当前实例的已登记持有人、各持有人活跃认知最早获知分钟、权威事件创建分钟与文本，供 `PulseWorker` 的 `read_snapshot` 与事务外 `validate_proposal` 使用。 |
| `validate_proposal(proposal, context=None)` | `server/app/m3/knowledge/service.py` | **事务外纯函数**（零 SQLite / 网络 I/O）。核对回显的 `instance_id`、`kind`/`mode`、`world_minute`、`world_revision`；拦截未知根字段（`UNKNOWN_FIELD`）、任意字段路径（`ARBITRARY_PATH_FORBIDDEN`）与玩家账本字段（`PLAYER_LEDGER_PROTECTED`）；默认拒绝 `seed` 阶段夹带传播（`SEED_TRANSMISSION_FORBIDDEN`）；拦截本批新建事件临时 ID（`TEMP_EVENT_ID_FORBIDDEN`）、未获准使用的事件 ID（`UNAUTHORIZED_SOURCE_EVENT`）、未获知转述人（`RELAY_HOLDER_UNKNOWN`）、时序倒置（`TEMPORAL_INVERSION_FORBIDDEN`）、未来时刻（`FUTURE_TRANSMISSION_FORBIDDEN`）、通配符/全员广播（`GLOBAL_BROADCAST_FORBIDDEN`）以及将权威隐藏标题或原始描述直塞给玩家（`AUTHORITATIVE_DESCRIPTION_FORBIDDEN`）。 |
| `apply_proposal(conn, instance_id, validated, job=None, *, allowed_event_ids=None, assign_server_ids=False)` | `server/app/m3/knowledge/service.py` | **短写事务内原子适配器**（使用独立命名 `SAVEPOINT`，不自行 `commit`）。复核实例根 `revision == job.base_world_revision`（不符抛 `409 WORLD_REVISION_CONFLICT`）、复核 `pulse_instance_configs` 开关未关闭或暂停、复核实时库内权威事件与转述人知情链；支持服务端分配 `fact_id`；同一事件下保留不同 NPC 的认知版本（`confirmed`/`suspected`/`rumor`/`mistaken`），任何单条失败整体回滚。 |
| `build_narration_knowledge_context` / `build_adventure_narration_knowledge_context` | `server/app/m3/knowledge/service.py` | 严格复用 `project_player_news(conn, instance_id)`（固定 `holder_id="player"`），生成独立、限长（默认 `max_units=2400`，严格保証 `u16_len(prompt_block) <= max_units`）、JSON 可序列化的 `NarrationKnowledgeBundle`。保持四类认知标签区分，标注 `untrusted_data_only=True`、`can_settle_ledger=False`；校验 `expected_adventure_id` / `expected_world_id` 防止跨分支引用；对伪造段头（如 `【程序提供的当前角色状态】`、`【分支日志与当前事实】`）和结算键（`proposed_changes`、`coins_delta`）降权转义。 |

### 2.2 手机端世界脉动控制契约（`src/features/world-pulse/`）

| 契约方法 / 特性 | 实现位置 | 行为与安全保障 |
| --- | --- | --- |
| `getStatus(adventureId, options)` | `src/features/world-pulse/api.ts` | 返回经 `sanitizePulseStatus` 脱敏的 `WorldPulseStatus`（含 `enabled`、`mode`、`state`、`model`、`reason_code`、`day_usage`），剥离任何 `api_key`、`session_id`、`narration_session_id`、隐藏事件或任务负载；读取状态不续期活动租约。 |
| `connect(adventureId, config, options)` | `src/features/world-pulse/api.ts` & `WorldPulsePanel.tsx` | 独立配置优先展示（提交 `{ mode: 'independent', base_url, model, api_key }`），同时支持复用当前叙事会话（提交 `{ mode: 'reuse_narration', narration_session_id }`）。**连接与启用严格分离**：连接只绑定会话，不自动开启脉动，不发起付费测试请求。Key 仅在输入与请求期间存于组件内存，提交成功、切换模式、切换旅程或卸载后立即清空。 |
| `setEnabled(adventureId, enabled, options)` | `src/features/world-pulse/api.ts` & `WorldPulsePanel.tsx` | 正式旅程首次点击“开启世界脉动”时弹出内联确认框（`data-testid="pulse-cost-confirm-dialog"`），明确提示后台模型调用与额外 Token 消耗，确认后才调用 `setEnabled(adventureId, true)`。关闭时保留已绑定的叙事会话与世界历史。当处于 `waiting_for_model`（过期或重启）时展示重连提示并展开配置表单，绝不静默切换到其他 Key。 |
| `setActivity(adventureId, active, options)` | `src/features/world-pulse/WorldPulsePanel.tsx` | 仅当存在可见旅程（`document.visibilityState !== 'hidden'` 且 `navigator.onLine !== false`）且脉动开启时续期活动租约；监听 `visibilitychange`、`offline`/`online`、旅程切换与组件卸载，进入后台、断网或切走旅程时主动调用 `setActivity(prevAdventureId, false)` 并清理旧计时器与在途请求，用 `epochRef` 拦截旧旅程迟到响应污染当前页面。 |

## 3. 给 DeepSeekHarness 的最小挂载点与默认行为说明

### 3.1 后端 `PulseWorker` 三回调组合接线（`server/app/m3/core/`）

在组合 `events`（Marcin）与 `knowledge`（Ma）接入 `PulseWorker`（Li）时，按 `docs/contracts/M3_WAVE2_API.md` 拆分与组合：

```python
from app.m3 import events, knowledge


def combined_read_snapshot(conn, instance_id: str, job: dict) -> dict:
    event_snap = events.read_snapshot(conn, instance_id, job)
    allowed_event_ids = event_snap.get("allowed_event_ids")
    knowledge_snap = knowledge.read_knowledge_snapshot(
        conn, instance_id, job, allowed_event_ids=allowed_event_ids
    )
    return {
        **event_snap,
        "knowledge": knowledge_snap,
    }


def combined_validate_proposal(proposal: dict, context: dict) -> dict:
    # 1. 事件包校验 operations
    validated_events = events.validate_proposal(proposal, context)
    # 2. 知识包校验可选的 transmissions（Seed 默认省略传播，Advance 校验已获准的既有事件引用与转述链）
    validated_knowledge = knowledge.validate_proposal(proposal, context)
    return {
        "events": validated_events,
        "knowledge": validated_knowledge,
    }


def combined_apply_proposal(conn, instance_id: str, validated: dict, job: dict) -> dict:
    # 在 PulseWorker 同一短写事务与 SAVEPOINT 内先后应用事件与传播，任一失败整体回滚
    ev_res = events.apply_proposal(conn, instance_id, validated["events"])
    kn_res = knowledge.apply_proposal(
        conn,
        instance_id,
        validated["knowledge"],
        job,
        assign_server_ids=True,
    )
    return {
        "applied_operations": len(ev_res.get("events", ())),
        "applied_transmissions": kn_res["applied_count"],
    }
```

### 3.2 前台叙事请求接入玩家安全知情投影（`server/app/adventures/routes.py`）

在 `POST /api/v1/adventures/{adventure_id}/drafts`（`prepare`）读取一致性短快照事务内，调用 `build_adventure_narration_knowledge_context` 将玩家已获知的消息拼入 `world_intro`（或独立上下文字段）：

```python
from ..m3.knowledge import build_adventure_narration_knowledge_context

# 在 prepare() 的 with db(cfg.db_path) as conn: 块内：
knowledge_bundle = build_adventure_narration_knowledge_context(
    conn,
    adventure_id,
    action=action,
    location=(run.get("character") or {}).get("location", ""),
    limit=8,
    max_units=1800,
)
# 在构造 intro 时追加独立限长数据块（若玩家尚未获知任何消息，prompt_block 为空字符串 ""）：
if knowledge_bundle.prompt_block:
    intro += "\n" + knowledge_bundle.prompt_block
```

### 3.3 手机端脉动控制 API 路由映射与前端挂载点（`AdventureJournal.tsx`）

1. **后端路由映射**（`worldPulseApi` 默认请求路径）：
   - `GET /api/v1/adventures/{adventure_id}/world-pulse` $\rightarrow$ `getStatus(adventureId)`
   - `POST /api/v1/adventures/{adventure_id}/world-pulse/session` $\rightarrow$ `connect(adventureId, config)`
   - `PUT /api/v1/adventures/{adventure_id}/world-pulse` $\rightarrow$ `setEnabled(adventureId, enabled)`
   - `POST /api/v1/adventures/{adventure_id}/world-pulse/activity` $\rightarrow$ `setActivity(adventureId, active)`
2. **前端 `src/features/adventures/AdventureJournal.tsx` 最小挂载点**：
   在「世界消息」折叠区（`<details aria-label="世界消息">`）内，将原有的 `<PulsePreview />` 升级或并列挂载为 `<WorldPulsePanel />`：
   ```tsx
   import { WorldPulsePanel } from "../world-pulse";
   import { WorldKnowledgePanel } from "../world-knowledge";

   <details
     className="journal-sources"
     aria-label="世界消息"
     onToggle={event => setNewsOpen(event.currentTarget.open)}
   >
     <summary>世界消息 · 只显示你有渠道得知的消息</summary>
     {newsOpen && (
       <>
         <WorldPulsePanel
           key={`pulse:${run.id}`}
           adventureId={run.id}
           adventureMode={run.mode}
           narrationSessionId={sessionId ?? null}
           disabled={busy}
         />
         <WorldKnowledgePanel
           key={`news:${run.id}`}
           adventureId={run.id}
           startsAt={run.opening?.starts_at}
           refreshTrigger={run.revision}
           disabled={busy}
         />
       </>
     )}
   </details>
   ```

## 4. 测试执行与验证记录（严格区分证据级别）

### 4.1 已在本地沙箱实际执行并通过的测试（Fake / SQLite / FastAPI TestClient / Node 18）

1. **后端知识传播、纯校验、三跳 Fixture 与叙事投影单元测试（15/15 通过）**：
   - **执行命令**：
     ```bash
     cd server && PYTHONPATH=. python3 -m unittest discover -s tests/m3/knowledge -v
     ```
   - **实际输出摘要**：
     ```text
     test_cross_instance_access_and_contamination_rejected ... ok
     test_divergent_npc_beliefs_and_four_knowledge_types ... ok
     test_external_world_events_table_and_m2_secret_knowers_coexistence ... ok
     test_player_blind_before_channel_and_visible_after_multi_hop ... ok
     test_prompt_injection_in_rumor_is_neutralized_as_untrusted_data ... ok
     test_reject_ungrounded_unlearned_relay_temporal_inversion_and_future ... ok
     test_snapshot_and_restore_checkpoint_branch_isolation_and_remap ... ok
     test_strict_type_and_unknown_field_guards ... ok
     test_version_correction_retraction_and_atomic_batch_rollback ... ok
     test_wave2_apply_proposal_atomic_rollback_and_revision_guard ... ok
     test_wave2_end_to_end_multi_hop_caravan_to_innkeeper_to_player ... ok
     test_wave2_narration_context_serialization_length_budget_and_branch_isolation ... ok
     test_wave2_pure_validate_proposal_and_adapter_contract ... ok
     test_world_news_http_endpoint_binds_player_and_paginates ... ok
     test_zero_leakage_projection_and_opaque_pagination ... ok

     ----------------------------------------------------------------------
     Ran 15 tests in 1.893s

     OK
     ```

2. **前端 `world-knowledge` 与 `world-pulse` 传输契约及多跳/状态机纯逻辑测试（7/7 通过）**：
   - **执行说明**：沙箱内 Node 版本为 `v18.20.4`，将 `tests/ui/world-knowledge/world-knowledge-transport.test.ts`（4 项）与 `tests/ui/world-pulse/world-pulse-transport.test.ts`（3 项）剥离类型注解后由 `node --test` 实际运行验证：
   - **实际输出摘要**：
     ```text
     === tests/ui/world-knowledge/world-knowledge-transport.test.mjs ===
     ok 1 - world-knowledge types cover all 4 knowledge types and 4 channels
     ok 2 - player projection item excludes hidden event fields and supports opaque pagination
     ok 3 - wave2 multi-hop scenario: caravan leader -> innkeeper -> player visibility and branch isolation
     ok 4 - wave2 narration context DTO shares player projection and marks untrusted non-ledger semantics
     1..4
     # pass 4 / fail 0

     === tests/ui/world-pulse/world-pulse-transport.test.mjs ===
     ok 1 - world-pulse contract covers all 4 connection modes and 6 runtime states
     ok 2 - fake pulse transport enforces connect/enable separation, session expiry, activity lease, and credential scrubbing
     ok 3 - sanitizePulseStatus strips credentials and formatPulseUsageSummary distinguishes actual vs estimated tokens
     1..3
     # pass 3 / fail 0
     ```

### 4.2 待 DeepSeekHarness 在 CI / 集成环境中执行的验证项

| 验证类别 | 目标运行命令 | 当前状态说明 |
| --- | --- | --- |
| Node 22 原生 strip-types 契约测试 | `npm test` (`node --experimental-strip-types --test "tests/**/*.test.ts"`) | 已在沙箱 Node 18 下完成等价实跑（7/7 通过），待 CI Node 22.18 原生执行。 |
| 手机宽度 React 组件 UI 测试（`world-knowledge.test.tsx` 7 项 + `world-pulse.test.tsx` 7 项） | `npm run test:ui` (`vitest run`) | 代码已就绪；因离线沙箱不含 `node_modules`（禁止联网安装），待合入 PR 后由 GitHub Actions CI 执行 `npm ci && npm run test:ui && npm run build`。 |
| 跨模块全链路联调与 Android APK 构建 | `pytest`、`npm run build`、Android APK Workflow | 待三包合流并由 DeepSeekHarness 完成公共接线后执行组合验收；真实模型服务商与安卓真机证据留待实际环境验收。 |
