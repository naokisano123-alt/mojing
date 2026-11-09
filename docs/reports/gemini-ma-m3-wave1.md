# 墨境 M3 Wave 1｜人物知识、信息传播与玩家视角交付报告（Gemini Ma）

- **负责方**：`Gemini Ma`
- **任务名称**：人物知识、信息传播与玩家消息（M3.3 首条端到端闭环）
- **主分支代码基线（`code_baseline`）**：`9388278f8a334f457e92ed820cc5b2456c76dfd8`
- **任务书快照（`snapshot_commit`）**：`20ba6c6fab40301252925acdae1d62471cc520ee`
- **契约与任务依据**：
  - `docs/collaboration/M3_WAVE1.md`
  - `docs/collaboration/M3_WAVE1_GEMINI_MA.md`
  - `docs/contracts/M3_WAVE1_API.md`（v1）
  - `docs/M3_DESIGN_DRAFT.md` 与 `docs/reports/codex-m3-architecture-review.md`
- **独占业务路径**：
  - `server/app/m3/knowledge/`
  - `server/tests/m3/knowledge/`
  - `src/features/world-knowledge/`
  - `tests/ui/world-knowledge/`
  - `docs/reports/gemini-ma-m3-wave1.md`
- **防覆写与零越界声明**：
  - 本交付包**仅包含上述独占路径内的新增文件**；
  - 未修改 `server/app/db.py`、`server/app/main.py`、`server/app/adventures/**`、`server/app/world_transfer.py`、`src/App.tsx`、`src/features/adventures/**`、`package.json`、`package-lock.json`、`.github/workflows/**` 或 Android 工程。

## 1. 交付文件清单

| 文件路径 | 操作 | 职责说明 |
| --- | --- | --- |
| `server/app/m3/knowledge/__init__.py` | Add | 知识与传播模块公共入口导出 |
| `server/app/m3/knowledge/errors.py` | Add | 继承自 `ApiError` 的脱敏业务异常 `KnowledgeError`（防止错误信息泄露隐藏事件） |
| `server/app/m3/knowledge/types.py` | Add | 四类认知类型、四类渠道常量、不透明分页游标编解码、防提示词注入清洗函数与返回容器 |
| `server/app/m3/knowledge/schema.py` | Add | `m3_knowledge_holders`、`m3_knowledge_facts`、`m3_knowledge_history` 等幂等表结构（逐条 DDL，不破坏外层事务或 SAVEPOINT） |
| `server/app/m3/knowledge/service.py` | Add | `record_transmission`、`list_known`、`snapshot`、`restore`、`project_player_news`、`build_narration_knowledge_context` 等核心实现 |
| `server/app/m3/knowledge/fixtures.py` | Add | 北境小镇多跳传播（商队首领 $\rightarrow$ 酒馆掌柜 $\rightarrow$ 玩家交谈）与同事件分歧认知 Fake Fixture |
| `server/tests/m3/knowledge/test_knowledge_service.py` | Add | 11 项后端单元测试（覆盖认知隔离、多跳传播、防无来源/防未获知转述/防时序倒置/防未来/防广播、零泄漏投影、防注入、快照恢复、跨实例拒绝及路由绑定） |
| `src/features/world-knowledge/types.ts` | Add | 前端 `WorldNewsItem`、`WorldNewsPage`、`WorldKnowledgeTransport` 等 TypeScript 契约类型 |
| `src/features/world-knowledge/api.ts` | Add | 默认 HTTP 传输 `worldKnowledgeApi`、独立验收 `createFakeWorldKnowledgeTransport` 及时间/可信度格式化纯函数 |
| `src/features/world-knowledge/WorldKnowledgePanel.tsx` | Add | 手机优先独立 React 消息面板（支持加载、零泄漏空态、失败重试、类型筛选、不透明分页、跨分支世代防竞态） |
| `src/features/world-knowledge/world-knowledge.css` | Add | 适配移动窄屏（`@media (max-width: 700px)`）与深浅主题变量的组件样式 |
| `src/features/world-knowledge/index.ts` | Add | 前端模块桶导出 |
| `tests/ui/world-knowledge/world-knowledge.test.tsx` | Add | 基于 Vitest + jsdom + React Testing Library 的 6 项 UI 行为测试 |
| `tests/ui/world-knowledge/world-knowledge-transport.test.ts` | Add | 基于 `node:test` 的前端契约类型、分页与分支快照隔离纯逻辑测试 |
| `docs/reports/gemini-ma-m3-wave1.md` | Add | 本轮交接与实跑验证报告 |

## 2. 集成契约对照表（`docs/contracts/M3_WAVE1_API.md`）

| 契约入口 / 约定 | 实现位置 | 行为与保障说明 |
| --- | --- | --- |
| `record_transmission(conn, instance_id, proposal)` | `server/app/m3/knowledge/service.py` | 接收已打开的 `sqlite3.Connection`，使用独立命名 `SAVEPOINT` 保证单批原子性，不自行 `commit`。支持单条字典、列表或包含 `transmissions/operations/items` 的包裹结构；严格校验字段白名单、同实例持有人、同实例来源事件、四类渠道规则、转述人已知前提与时间先后。 |
| `list_known(conn, instance_id, holder_id, *, before=None, limit=30)` | `server/app/m3/knowledge/service.py` | 返回 `KnownPage`（字典结构 `{"items": [...], "next_before": str \| None}`，同时兼容下标读取）。仅返回公开投影字段（`fact_id`, `holder_id`, `knowledge_type`, `title`, `content`, `channel`, `source_person_id`, `source_label`, `learned_minute`, `confidence`, `status`, `revision`），剔除 `source_event_id`、权威事件隐藏标题与未来时间。 |
| `snapshot(conn, instance_id)` | `server/app/m3/knowledge/service.py` | 导出格式为 `mojing.m3.knowledge.snapshot`（`version: 1`）的纯 JSON 可序列化字典，包含 `holders`、`facts` 与 `history`；不含任何活跃任务、API 凭据或进程租约。 |
| `restore(conn, target_instance_id, snapshot, *, event_id_map=None, holder_id_map=None)` | `server/app/m3/knowledge/service.py` | 在 `SAVEPOINT` 内原子恢复知识状态至目标实例；支持检查点分支复用领域 `fact_id`（主键为 `(instance_id, fact_id)`），支持跨设备导入时通过 `event_id_map` 重映射事件引用；对跨实例污染引用、未来时间或断裂转述链直接拒绝并完整回滚。 |
| `GET /api/v1/adventures/{id}/world-news` | `service.py: get_world_news` / `register_knowledge_routes` | 从当前 `adventure_id` 解析所属 `instance_id`，服务端强制绑定 `holder_id = "player"`，忽略客户端任何伪造 `holder_id` 参数，返回 `{"items": [...], "next_before": ...}`。 |
| 前台叙事上下文共用投影与防提示词注入 | `service.py: build_narration_knowledge_context` | 与前端面板共用 `project_player_news`，将玩家已获知消息清洗控制标记（如 `【系统指令】`、`<\|im_start\|>`、代码块围栏）后封装为 `NarrationKnowledgeBundle(untrusted_data_only=True)`，明确标注为不可信角色主观认知/传闻。 |

## 3. 核心设计要点与 M2 秘密知情人对接边界

### 3.1 按持有人隔离认知与同事件分歧理解
- `m3_knowledge_facts` 与权威事件分表存储，主键为 `(instance_id, fact_id)`。
- 支持 `confirmed`（确认事实）、`suspected`（怀疑推测）、`rumor`（市井传闻）、`mistaken`（误信传言）四种认知类型。
- 同一权威事件（如 `evt_caravan_02` 灰羽商队改道）可被 `npc:caravan_leader` 记录为亲历事实（`confirmed`, `confidence=1.0`），同时被 `npc:gate_guard` 记录为遭遇山贼覆没的误信传言（`mistaken`, `confidence=0.7`），而玩家在通过具体渠道获知前查询结果为空列表 `[]`。

### 3.2 四渠道传播校验与防全局广播
- **渠道白名单**：仅允许 `observed`、`notice`、`conversation`、`rumor`。
- **禁止无来源授予**：`source_event_id` 必填且必须存在于当前 `instance_id` 的事件表中（自动兼容 `m3_knowledge_event_stubs` 与事件模块的 `world_events` / `m3_events` 表）。
- **禁止未获知的 NPC 转述与时序倒置**：`conversation` 必须指定 `source_person_id`（且不能等于接收人自己），系统核查 `source_person_id` 在同一实例中已持有该事件的 `active` 知情记录，且接收人 `learned_minute >= 转述人最早 learned_minute >= 事件 created_minute`，同时 `learned_minute <= 实例当前 world_minute`。
- **禁止一次全局推演广播**：拦截 `*`、`all`、`all_npcs` 等通配持有人，并拦截在无前置知情记录时单次批量将实例内全部已注册 NPC 与 `player` 同时标为知情的操作（`GLOBAL_BROADCAST_FORBIDDEN`）。

### 3.3 隐藏信息零泄漏设计
- **不透明局部游标**：每位持有人在实例内维护独立的局部递增序号 `holder_seq`（`1, 2, ...`），游标 `next_before` 仅编码当前持有人的 `holder_seq`（如 `kcur1_djE6Mg`），绝不使用全局自增主键或权威事件总数。即使后台已发生 100 条 NPC 秘密传播，只要玩家仅获知 1 条消息，玩家的 `holder_seq` 仍为 `1` 且 `next_before` 为 `null`，无法推断后台隐藏事件的存在或数量。
- **标题安全派生**：公开投影中的 `title` 仅从该持有人已知的 `title` 或 `content` 首句截断生成，绝不关联查询权威事件表中的真实隐藏标题。

### 3.4 已有 M2 秘密知情人（`server/app/adventures/memory.py`）保留与对接边界
- **现状保留**：M2 的 `adventure_memory_events` 表（含 `kind="secret"` 及 `knowers: list[str]`）作为前台冒险回合内的轻量叙事备忘（押金、约定、当面秘密）完整保留，本模块不修改、不删除 M2 既有表结构或逻辑。
- **职责边界**：
  - M2 `memory.py` 的 `secret` 记录的是**玩家在前台回合叙事中已经知晓并参与维护的秘密备忘**（玩家可在前端 `MemoryPanel` 查看和手动更正知情人名单）；
  - M3 `m3/knowledge/` 记录的是**整个动态世界实例中各 NPC 与玩家的独立认知账本**（包含玩家尚未获知的 NPC 认知、跨地点多跳传播、不同可信度与误信版本）。
- **后续对接建议（供 M3.4 / 集成阶段参考）**：
  - 前台生成草案时，原有的 `adventure_memory`（M2 押金/约定/已知秘密）继续原样传入 `NarrationRequest.adventure_memory`；
  - 新增的世界动态知情投影通过 `build_narration_knowledge_context(conn, instance_id, action=action, location=location)` 生成 `prompt_block`，作为补充世界见闻拼入 `world_intro` 或独立上下文字段，两者互不覆盖。

## 4. 给 DeepSeekHarness 的公共接线说明

1. **数据库初始化（`server/app/db.py`）**：
   在 `init_db` 中完成 `world_instances` 建表后调用：
   ```python
   from .m3.knowledge.schema import ensure_knowledge_schema
   ensure_knowledge_schema(conn)
   ```
2. **挂载玩家世界消息路由（`server/app/main.py` 或 `server/app/adventures/routes.py`）**：
   ```python
   from .m3.knowledge import register_knowledge_routes
   register_knowledge_routes(app, cfg)
   ```
   这将注册 `GET /api/v1/adventures/{adventure_id}/world-news`，自动从当前旅程解析 `instance_id` 并绑定 `holder_id="player"`。
3. **检查点与恢复分支接入（`server/app/adventures/checkpoints.py` / `server/app/m3/core/`）**：
   - 创建检查点时调用 `knowledge_snap = snapshot(conn, instance_id)` 并存入实例快照；
   - 恢复检查点创建新实例 `new_instance_id` 时，在同一写事务内（恢复事件快照后）调用：
     ```python
     from ..m3.knowledge import restore as restore_knowledge
     restore_knowledge(conn, new_instance_id, knowledge_snap)
     ```
4. **前端冒险页挂载手机消息面板（`src/features/adventures/AdventureJournal.tsx`）**：
   在 `AdventureJournal` 当前旅程详情区（如 `MemoryPanel` 下方）挂载：
   ```tsx
   import { WorldKnowledgePanel } from "../world-knowledge";

   {run && (
     <WorldKnowledgePanel
       key={`news:${run.id}:${run.revision}`}
       adventureId={run.id}
       startsAt={run.opening?.starts_at}
       disabled={busy}
     />
   )}
   ```

## 5. 测试执行与验证记录（区分证据级别）

### 5.1 已在本地沙箱实际执行的测试（Fake / SQLite / FastAPI TestClient）

- **执行命令**：
  ```bash
  cd server && PYTHONPATH=. python3 -m unittest discover -s tests/m3/knowledge -v
  ```
- **实际运行结果**：
  ```text
  test_cross_instance_access_and_contamination_rejected (test_knowledge_service.KnowledgeServiceTests.test_cross_instance_access_and_contamination_rejected)
  验收 8：跨实例访问、跨实例事件引用、跨分支 NPC 转述及污染快照恢复一律拒绝（403）。 ... ok
  test_divergent_npc_beliefs_and_four_knowledge_types (test_knowledge_service.KnowledgeServiceTests.test_divergent_npc_beliefs_and_four_knowledge_types)
  验收 1：相同权威事件可被不同 NPC 作不同理解（confirmed/suspected/rumor/mistaken），按持有人严格隔离。 ... ok
  test_external_world_events_table_and_m2_secret_knowers_coexistence (test_knowledge_service.KnowledgeServiceTests.test_external_world_events_table_and_m2_secret_knowers_coexistence)
  验收 9：自动识别外部事件模块表（world_events），且与已有 M2 秘密知情人（adventure_memory_events）和平共存。 ... ok
  test_player_blind_before_channel_and_visible_after_multi_hop (test_knowledge_service.KnowledgeServiceTests.test_player_blind_before_channel_and_visible_after_multi_hop)
  验收 2：多跳传播前消息对玩家完全隐藏；玩家与酒馆掌柜交谈后才看见对应版本，且仍看不见王都密谋。 ... ok
  test_prompt_injection_in_rumor_is_neutralized_as_untrusted_data (test_knowledge_service.KnowledgeServiceTests.test_prompt_injection_in_rumor_is_neutralized_as_untrusted_data)
  验收 5：用含提示词注入的传闻测试它始终作为不可信资料，不变成系统指令，不泄露隐藏事件。 ... ok
  test_reject_ungrounded_unlearned_relay_temporal_inversion_and_future (test_knowledge_service.KnowledgeServiceTests.test_reject_ungrounded_unlearned_relay_temporal_inversion_and_future)
  验收 3：禁止无来源授予、未获知的 NPC 转述、时序倒置、非法渠道、未来传播及全局广播。 ... ok
  test_snapshot_and_restore_checkpoint_branch_isolation_and_remap (test_knowledge_service.KnowledgeServiceTests.test_snapshot_and_restore_checkpoint_branch_isolation_and_remap)
  验收 7：旧检查点恢复——在玩家得知消息前拍快照，原实例后续获知消息；从快照恢复的新分支仍看不到该消息，且支持导入 ID 重映射。 ... ok
  test_strict_type_and_unknown_field_guards (test_knowledge_service.KnowledgeServiceTests.test_strict_type_and_unknown_field_guards)
  验收 10：严格拦截布尔值充当整型/浮点型、未知字段注入等异常输入。 ... ok
  test_version_correction_retraction_and_atomic_batch_rollback (test_knowledge_service.KnowledgeServiceTests.test_version_correction_retraction_and_atomic_batch_rollback)
  验收 6：支持认知版本更正、撤回与 409 冲突保护；同一批次部分非法时 SAVEPOINT 整体回滚无残留写入。 ... ok
  test_world_news_http_endpoint_binds_player_and_paginates (test_knowledge_service.KnowledgeServiceTests.test_world_news_http_endpoint_binds_player_and_paginates)
  验收 11：GET `/api/v1/adventures/{id}/world-news` 从旅程解析实例并固定绑定 player 身份。 ... ok
  test_zero_leakage_projection_and_opaque_pagination (test_knowledge_service.KnowledgeServiceTests.test_zero_leakage_projection_and_opaque_pagination)
  验收 4：隐藏信息零泄漏——标题、数量、空态、分页游标、来源引用和错误信息均不能推断隐藏事件。 ... ok

  ----------------------------------------------------------------------
  Ran 11 tests in 0.262s

  OK
  ```

### 5.2 待 DeepSeekHarness 在 CI / 集成环境中执行的测试说明

| 测试文件 / 用例 | 目标运行命令 | 当前状态说明 |
| --- | --- | --- |
| `tests/ui/world-knowledge/world-knowledge-transport.test.ts` | `npm test` (`node --experimental-strip-types --test "tests/**/*.test.ts"`) | 代码已就绪；因当前离线沙箱 Node 版本为 `v18.20.4`（`--experimental-strip-types` 需 Node 22），已通过去除类型注解的等价脚本在 Node 18 下验证 3/3 通过，待 CI Node 22.18 原生执行。 |
| `tests/ui/world-knowledge/world-knowledge.test.tsx`（含空态、获知前后对比、旧检查点分支切换、不透明游标分页、错误重试与跨旅程迟到响应隔离共 6 项） | `npm run test:ui` (`vitest run`) | 代码已就绪；因离线沙箱快照不含 `node_modules`（无网络安装），待 DeepSeekHarness 合入分支后由 GitHub Actions CI（`npm ci && npm run test:ui && npm run build`）执行验证。 |
| 跨模块全链路集成（M3.0 实例底座 + M3.1 事件账本 + M3.2 异步脉动 + M3.3 玩家消息） | `python -m pytest` & `npm run test:ui` | 待四包合流后由 DeepSeekHarness 按 `M3_WAVE1.md` 集成顺序验收。 |
