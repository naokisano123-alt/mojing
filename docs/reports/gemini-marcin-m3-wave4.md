# 墨境｜M3 第四轮（Wave 4）导入设定与 AI 初始化交付报告

- **任务角色**：`Gemini Marcin`（Marin）
- **共同仓库快照（base_sha）**：[`9117404aa307c53de235b2959507d8d577d9fbca`](https://github.com/naokisano123-alt/mojing/archive/9117404aa307c53de235b2959507d8d577d9fbca.zip)（基于 [PR #27 合入提交 `46fb8deb8cdfe725bad5ae241b953d255eb078f1`](https://github.com/naokisano123-alt/mojing/commit/46fb8deb8cdfe725bad5ae241b953d255eb078f1) 并包含第四轮任务书的固定快照）
- **契约与任务书依据**：
  - [`docs/collaboration/M3_WAVE4.md`](../collaboration/M3_WAVE4.md)
  - [`docs/collaboration/M3_WAVE4_GEMINI_MARCIN.md`](../collaboration/M3_WAVE4_GEMINI_MARCIN.md)
  - [`docs/contracts/M3_WAVE4_API.md`](../contracts/M3_WAVE4_API.md)
  - [`docs/M3_DESIGN_DRAFT.md`](../M3_DESIGN_DRAFT.md)、[`docs/reports/codex-m3-wave3-completion.md`](codex-m3-wave3-completion.md)

## 1. 交付文件清单与独占边界声明

本交付包严格限定在独占目录 `server/app/m3/world_analysis/`、`server/tests/m3/world_analysis/` 与本报告 `docs/reports/gemini-marcin-m3-wave4.md` 内，未修改任何公共入口、数据库主初始化、前端代码、Android 工程或其它助手的独占目录。

| 文件路径 | 操作类型 | 说明 |
| --- | --- | --- |
| `server/app/m3/world_analysis/__init__.py` | 新增（`add`） | 模块公共导出入口（资料抽取、长文分批合并、提示词、客户端、纯校验、草案适配层与两套测试世界 Fixture）。 |
| `server/app/m3/world_analysis/errors.py` | 新增（`add`） | 遵循 `ApiError` 规范的异常体系（403/404/409/422/502），含缺资料、跨世界/跨实例越界引用、伪造来源、重名冲突、未公开信息泄露、玩家账本保护与重复分析冲突异常。 |
| `server/app/m3/world_analysis/types.py` | 新增（`add`） | 领域数据结构定义，含起点规范 `StartingPointSpec`、批次输入范围 `BatchInputRange`、设定冲突 `SettingConflictItem`、资料依据与旅程补写枚举 `GroundingType`、初始人物/地点/关系/事件线及已校验草案 `ValidatedWorldAnalysisDraft`。 |
| `server/app/m3/world_analysis/schema.py` | 新增（`add`） | 幂等建表函数 `ensure_world_analysis_schema(conn)`（`m3_world_analysis_drafts` 暂存表，不隐式提交外层事务、不破坏 `SAVEPOINT`）。 |
| `server/app/m3/world_analysis/extractor.py` | 新增（`add`） | 按起点挑选已确认设定与导入分块、文内非设定指令剥离、设定冲突识别、长文分批规划（生成每次请求的精确 `BatchInputRange`）及跨批次合并器 `merge_batch_extractions`。 |
| `server/app/m3/world_analysis/validator.py` | 新增（`add`） | 事务外纯函数校验器 `validate_analysis_draft` 与玩家开局摘要构建器 `build_player_opening_summary`（强制过滤角色未获知设定与 NPC 私密动机）。 |
| `server/app/m3/world_analysis/adapter.py` | 新增（`add`） | 草案适配层 `adapt_draft_to_domain_proposals`（直接转换为 `m3/events`、`m3/npcs`、`m3/knowledge` 落库结构与 Li/Ma 消费结构）及草案暂存、失败重试与 `SAVEPOINT` 原子应用辅助函数。 |
| `server/app/m3/world_analysis/ai.py` | 新增（`add`） | `WORLD_ANALYSIS_OUTPUT_SCHEMA`、`build_world_analysis_prompts`、`FakeWorldAnalysisModelClient`、`OpenAIWorldAnalysisModelClient`（支持 `httpx.MockTransport`）与多批调度入口 `analyze_world_initial_situation`。 |
| `server/app/m3/world_analysis/fixtures.py` | 新增（`add`） | 两套人物、地理与矛盾不同的世界设定 Fixture（`world_yunze_salt` 云泽水驿 vs `world_skyforge_city` 赤霄机关城）、长文与提示词注入样本、以及 Mock HTTP 传输层构建器。 |
| `server/tests/m3/world_analysis/__init__.py` | 新增（`add`） | 测试包初始化文件。 |
| `server/tests/m3/world_analysis/test_two_worlds_and_starting_points.py` | 新增（`add`） | 验证两套不同世界设定内容跟随资料变化、同世界切换起点重排节选与在场人物、资料依据与旅程补写区分、以及 HTTP Mock 与 API Key 脱敏。 |
| `server/tests/m3/world_analysis/test_batching_conflicts_and_identities.py` | 新增（`add`） | 验证缺资料阻塞、设定冲突检测与保留、长文多批 `BatchInputRange`、多批 HTTP Mock、批次内越界来源拦截、跨批次同人别名合并与同名不同人消歧。 |
| `server/tests/m3/world_analysis/test_security_adapter_and_lifecycle.py` | 新增（`add`） | 验证跨世界/伪造来源拦截、未公开信息限制、玩家账本保护、重复分析/失败重试/重开/分支隔离、三大账本原子写入及 `SAVEPOINT` 中途冲突回滚。 |
| `docs/reports/gemini-marcin-m3-wave4.md` | 新增（`add`） | 本交付与测试报告。 |

- **删除或重命名文件**：无（0 删除，0 重命名）。

## 2. 核心实现与契约对照

### 2.1 按起点挑选节选、长文分批策略与每次请求的输入范围（`BatchInputRange`）

1. **只读当前世界有效资料与越界拦截（`discover_world_source_ownership` & `extract_and_batch_world_sources`）**：
   - 仅选取当前 `world_id` 下 `deleted_at IS NULL AND status = 'confirmed'` 的人工设定条目（`entry:<id>`）以及 `d.deleted_at IS NULL AND (d.status IS NULL OR d.status = 'ready')` 的导入文档分块（`chunk:<id>`）；
   - 当调用方传入 `selected_source_ids` / `selected_document_ids` / `selected_entry_ids` 时，严格核验来源归属：若指向其他世界则立即抛出 `403 CROSS_WORLD_SOURCE_FORBIDDEN`，若指向未确认草稿、已软删除或不存在的来源则抛出 `422 SOURCE_FABRICATED`；
   - 若当前世界无任何可用确认条目或就绪文档分块，返回 `status="insufficient_sources", blocked=True, reason_code="ANALYSIS_SOURCE_REQUIRED"`（或抛出 `409 ANALYSIS_SOURCE_REQUIRED`），避免无资料空跑模型。
2. **按起点相关性排序与长文分批规划**：
   - 根据 `StartingPointSpec`（`location`、`world_minute`、`focus_keywords`）对候选条目与分块计算相关性得分（标题/章节路径命中起点权重最高，正文多次命中次之，叠加设定条目类型优先级 `location -> character -> faction -> world_rule -> economy -> history`）；
   - **第 0 批（核心开局批）**：优先打包与起点直接匹配的确认设定条目与高相关分块；
   - **后续批次（长文顺序批）**：当长文超过单次请求预算（默认 `batch_char_budget=2600` UTF-16 单位、`batch_max_entries=6`、`batch_max_chunks=5`）时，后续批次按文档与章节顺序（`document_id ASC, chunk_index ASC`）切分，每次请求均输出明确的 `BatchInputRange`：
     - `batch_index` / `total_batches`：当前批次序号与总批次数；
     - `source_ids` / `entry_ids` / `chunk_ids`：本次请求允许引用的精确来源 ID 列表；
     - `document_ranges`：覆盖的每份文档的 `start_chunk_index`、`end_chunk_index`、`start_char`、`end_char` 与 `chunk_count`；
     - `used_chars` / `char_budget` / `selection_reason`：字符预算占用与分批原因说明。
3. **多批结果合并策略（`merge_batch_extractions`）**：
   - 逐批核对模型输出引用的 `source_ids` 必须属于对应批次的 `input_range.source_ids`（严禁第 $k$ 批越界引用其它批次或外部世界的来源 ID）；
   - 自动合并跨批次重复出现的地点与事件线，维护 `event_id_remap` 与 `npc_id_remap` 确保跨批次因果与关系引用一致。

### 2.2 资料依据（`canon`）与旅程内补写（`adventure_supplement`）双轨标注

每个提议的地点（`ProposedLocation`）、人物（`ProposedInitialNpc`）、人物关系（`ProposedNpcRelationship`）和初始主要事件线（`ProposedInitialEvent`）均包含显式的 `grounding_type` 与审计字段：

| 依据类型（`grounding_type`） | 校验与来源绑定规则 | 账本与审计表现 |
| --- | --- | --- |
| **`canon`（资料依据）** | 必须提供非空的 `source_ids`（格式为 `chunk:<id>` 或 `entry:<id>`），且全部属于当前世界本次分析的 `allowed_source_ids` | 直接将 `source_ids` 写入 `world_events` 与 `m3_npcs`，在玩家开局摘要中标识为原作设定依据 |
| **`adventure_supplement`（旅程内补写）** | 允许 AI 在资料留白处补写支撑开局运转的人物职责、日常关系或本地局势，必须填写非空 `supplement_reason` | 汇总记入草案的 `supplement_ledger`（标记为该旅程推演，绝不反写或污染世界固定 Canon 表）；适配至 `world_events` 时绑定起点锚点来源并在 `reason` 中标明 `[旅程内补写:...]` |

### 2.3 重名消歧、跨批身份复用、设定冲突保留与未公开信息限制

1. **重名消歧（Homonyms）与跨批身份复用（Identity Reuse）**：
   - **同人跨批次复用/别名合并**：若不同分块以本名与别称指代同一人物（如「沈渡头」与「老沈」、「墨衡」与「墨执事」），`merge_batch_extractions` 将其合并为单一实例内稳定 `npc_id`，保留 `aliases=("老沈",)` 并合并其 `source_ids` 与关系网络，记入 `identity_resolution_log`（`action="merged_alias"`）；
   - **同名不同人消歧**：若世界中存在两名同名但不同身份/常驻地的人物（如「白鹭水驿茶栈的韩掌柜」与「千帆港船行的韩掌柜」），自动分配独立的实例内 `npc_id` 并填充 `homonym_disambiguator`（`白鹭水驿·白鹭水驿迎客茶栈掌柜` vs `千帆港·千帆港远洋船行掌柜`），记入 `identity_resolution_log`（`action="disambiguated_homonym"`）；若出现未消歧的同地同职重复人物，纯校验器立即以 `422 UNRESOLVED_DUPLICATE_NPC` 拒绝；
   - **重开与分支身份复用**：在重开（`run_mode="reopen"`）或分支（`run_mode="branch"`）的新 `WorldInstance` 中，允许安全复用相同的实例内领域标识（`npc_id`、`event_id`、`location_id`），各实例通过复合主键 `(instance_id, ...)` 完全隔离。
2. **设定冲突检测与保留（`detect_setting_conflicts`）**：
   - 自动识别已确认设定条目与导入文档分块之间的矛盾（如 `entry:yunze_curfew_rule_02` 规定「白鹭水驿亥时初刻严格宵禁封闸」，而 `chunk:yunze_doc1_c2` 称「白鹭水驿从不宵禁，全天开放」）；
   - 生成结构化 `SettingConflictItem`，默认以已确认设定条目为准（`resolution_policy="prefer_confirmed_entry"`）并将异文保留为传闻，同时在 `player_opening_summary.conflicts` 中提供玩家可见提示。
3. **未公开信息限制与提示词注入防护**：
   - `sanitize_untrusted_source_text` 剥离源文本中冒充系统指令或试图篡改 `coins`/`player_ledger` 的注入片段并标记 `prompt_injection_filtered=True`；
   - `build_player_opening_summary` 与 `validate_analysis_draft` 严格保证：非起点在场人物（`present_at_start=False`）、隐藏人物（`visibility="secret"`）、NPC 私密动机与目标（`motivation`、`goal`、`cause_event_ids`）以及角色在开局未获知的远方/内部事件（`player_known_at_start=False`）绝不进入 `player_opening_summary`；若输入草案试图在玩家摘要中夹带上述未公开信息，立即抛出 `422 UNPUBLIC_INFO_LEAK_FORBIDDEN`。

## 3. 与 Gemini Li、Gemini Ma 及 DeepSeekHarness 的接线契约

### 3.1 供 DeepSeekHarness 接入的完整分析与原子落库流水线

```python
from app.m3 import world_analysis

# 1. 只读抽取与长文分批（不调模型，若缺资料返回 status='insufficient_sources'）
extraction = world_analysis.extract_and_batch_world_sources(
    conn,
    world_id=world_id,
    starting_point={
        "location": "白鹭水驿",
        "world_minute": 60,
        "starts_at": "0001-03-15T09:00",
        "character_name": "陆行舟",
        "run_mode": "initial",      # initial / retry / reopen / branch
        "request_id": request_id,
    },
)

# 2. 在 SQLite 写事务外调用 Fake 或 OpenAI 兼容模型完成单批/多批分析与纯函数校验
draft = await world_analysis.analyze_world_initial_situation(
    extraction,
    model_client,
    instance_id=instance_id,
    base_world_revision=current_world_revision,
)

# 3. 暂存未确认草案（此时 world_events、m3_npcs、m3_knowledge_facts 保持零写入）
saved_info = world_analysis.save_analysis_draft(conn, draft, adventure_id=adventure_id)
# 返回给手机端 Ma 的开局摘要：saved_info["player_opening_summary"]

# 4. 玩家确认进入旅程后，在调用方事务（SAVEPOINT）内原子写入事件、NPC 与知识三大账本
applied_info = world_analysis.apply_draft_to_instance_in_savepoint(
    conn,
    instance_id,
    draft,
    advance_instance_revision=False,  # 由外层集成事务统一控制实例版本推进
)
```

### 3.2 供 Gemini Li 读取的初始人物状态与时间语义

`adapt_draft_to_domain_proposals(draft)` 同时输出：
1. 直接兼容 `npcs.apply_proposal(conn, instance_id, bundle["npc_seed_proposal"])` 的 `seed_npc` 批次载荷（写入 `m3_npcs`、`m3_npc_tasks` 与 `m3_npc_action_log`），使 Li 的 `npcs.read_snapshot(conn, instance_id, job)` 可直接按 `world_minute` 与 `next_due_minute` 选出到期人物；
2. `bundle["li_npc_initial_states"]`：包含 `npc_id`、`plan_kind`（`stay / travel / change_plan / conversation_plan`）、映射后的 `action_kind`（`shift_change / travel / task_progress / conversation_plan`）、`world_minute`、`duration_minutes`、`next_due_minute`、`target_npc_id`、`cause_event_ids`、`known_event_ids` 与 `relationships`。

### 3.3 供 Gemini Ma 渲染的手机端开局摘要（`player_opening_summary`）

`draft.player_opening_summary` 结构完全对齐手机端五类数据与状态流转（`waiting -> analyzing -> ready_to_confirm / failed_retryable / insufficient_sources -> applied`），仅包含起点在场可接触人物的公开外貌与当前活动、玩家开局可获知的传闻/公告、公开人际关系及设定冲突提示，不含任何后台 NPC 私事或隐藏事件。

## 4. 测试验证与证据记录

### 4.1 独立测试命令与实际结果

在沙箱环境（Python `3.11.2`）下实际执行：

```bash
PYTHONPATH=server python3 -m unittest discover -s server/tests/m3/world_analysis -t server -v
```

- **实际输出**：`Ran 12 tests in 0.107s — OK`（12 项自动化测试全部通过）。
- **与 M3 其它后端模块联跑回归结果**：
  - `m3/npcs`：`Ran 12 tests in 0.074s — OK`
  - `m3/events`：`Ran 23 tests in 0.116s — OK`
  - `m3/pulse`：`Ran 25 tests in 0.166s — OK`
  - `m3/portability`：`Ran 6 tests in 0.302s — OK`

### 4.2 测试覆盖明细

1. `test_two_distinct_worlds_produce_world_specific_npcs_locations_and_events`：使用两套人物、地理与矛盾完全不同的导入资料（`world_yunze_salt` 云泽水驿 vs `world_skyforge_city` 赤霄机关城），验证生成的地点、人物、动机、关系和初始事件线 100% 跟随各自资料变化，且明确区分 `canon`（资料依据）与 `adventure_supplement`（旅程内补写）。
2. `test_changing_starting_point_reorders_excerpts_and_changes_present_npcs`：验证同一世界切换不同起点（白鹭水驿 vs 千帆港）及不同开局时刻（60 分钟 vs 120 分钟）时，节选优先级、在场人物与 Li 的 `next_due_minute` 动态随之变化。
3. `test_openai_model_client_with_http_mock_transport_and_key_redaction`：验证 `OpenAIWorldAnalysisModelClient` 配合 `httpx.MockTransport` 完成结构化分析、统计 token usage，且 `repr` 严格脱敏 API Key。
4. `test_missing_or_unconfirmed_sources_blocked_clearly`：验证空世界或仅有 `draft` 未确认设定条目时返回 `status='insufficient_sources'` 并以 `409 ANALYSIS_SOURCE_REQUIRED` 拦截模型请求。
5. `test_setting_conflicts_detected_and_preserved_in_draft_and_summary`：验证已确认设定条目（亥时宵禁）与导入文档分块（从不宵禁）冲突时被自动识别、优先采用确认条目并保留异文传闻提示。
6. `test_long_document_batching_input_ranges_and_multi_batch_merging`：验证长文多批切分、每批精确的 `BatchInputRange`（分块索引与字符偏移区间）、文内提示词注入指令剥离（`coins=99999` 被隔离）、跨批次同人别名合并（「沈渡头」与「老沈」合并）以及同名不同人消歧（白鹭水驿茶栈「韩掌柜」vs 千帆港船行「韩掌柜」）。
7. `test_multi_batch_http_mock_and_out_of_batch_source_rejection`：验证多批次 HTTP Mock 逐批携带各自 `input_range`，且单批次越界引用相邻批次 `source_id` 时立即被 `422 SOURCE_FABRICATED` 拒绝。
8. `test_unresolved_duplicate_npc_or_duplicate_id_rejected`：验证未消歧的同地同职同名重复人物被 `422 UNRESOLVED_DUPLICATE_NPC` 拦截。
9. `test_cross_world_source_and_fabricated_source_rejected`：验证在资料抽取或草案校验阶段越界引用其他世界的 `entry:` / `chunk:`（`403 CROSS_WORLD_SOURCE_FORBIDDEN`）、跨实例 ID（`403 CROSS_INSTANCE_FORBIDDEN`）或伪造来源（`422 SOURCE_FABRICATED`）均被拦截。
10. `test_unpublic_info_restriction_and_player_ledger_protection`：验证未获知隐藏事件或 NPC 私密 `motivation` 混入 `player_opening_summary` 时被 `422 UNPUBLIC_INFO_LEAK_FORBIDDEN` 拦截，夹带 `coins_delta` 等玩家账本字段被 `422 PLAYER_LEDGER_PROTECTED` 拦截。
11. `test_duplicate_analysis_retry_reopen_branch_and_atomic_domain_integration`：验证失败记录（`failed_retryable`）与未确认草案（`ready_to_confirm`）零污染正式表、相同 `request_id` 幂等重放与参数冲突拦截（`409 DUPLICATE_ANALYSIS_CONFLICT`）、失败后重试（`retry`）、重开（`reopen`）跨实例复用领域 ID，以及确认后原子写入 `events`/`npcs`/`knowledge` 并直接通过 `npcs.read_snapshot` 与 `npcs.list_contactable_npcs` 供 Li 模块读取。
12. `test_savepoint_atomic_rollback_on_mid_apply_conflict`：验证确认落库中途发生冲突时，`SAVEPOINT` 将同批次已写入的 `world_events` 完整回滚且保留外层未提交事务。

### 4.3 证据分类记录

| 证据类别 | 状态 | 说明 |
| --- | --- | --- |
| **Fake 证据** | **已实跑通过** | `FakeWorldAnalysisModelClient` 通过两套异构世界、起点切换、长文多批合并与三大账本联调测试。 |
| **Mock 证据** | **已实跑通过** | `OpenAIWorldAnalysisModelClient` 配合 `httpx.MockTransport` 跑通单批与多批长文抽取及密钥脱敏测试。 |
| **APK 打包证据** | **待集成方 CI 执行** | 本包仅新增后端 `server/app/m3/world_analysis/` 与测试，Android APK 由 DeepSeekHarness 合入后统一打包验证。 |
| **真实服务商证据** | **未运行（待用户开启）** | 遵守安全规范不在仓库或测试中内置真实 API Key，沙箱禁外网；待集成后由用户配置真实服务商验收。 |
| **安卓真机证据** | **待集成后验收** | 待公共接线 PR 合入并产出 APK 后在安卓设备验收。 |

## 5. 逐文件 SHA-256 校验清单

包内根目录附带标准 `manifest.json`（`round: "m3-wave4"`，`base_sha: "9117404aa307c53de235b2959507d8d577d9fbca"`，包含下列全部源码、测试文件以及本报告自身的字节级 SHA-256 摘要）：

| 仓库相对路径 | 操作 | SHA-256 |
| --- | --- | --- |
| `server/app/m3/world_analysis/__init__.py` | `add` | `d7cd5930658016c877d8814e4ea848b8a5182c7480877f2777a321679eebe3cb` |
| `server/app/m3/world_analysis/adapter.py` | `add` | `1c136b03bc11ae452d8259fb7fdc450c61c974bf38bcc29ba26dd65cd65becbc` |
| `server/app/m3/world_analysis/ai.py` | `add` | `4b1cbdd6bc7ffed4a35ee199c9232fb44c917e604ce16770550a62318b8a9b97` |
| `server/app/m3/world_analysis/errors.py` | `add` | `b6492eef442b953f36a79934614c19842a961e0136997d628c9d409866908eb5` |
| `server/app/m3/world_analysis/extractor.py` | `add` | `a0b3bffff802129624a5f42f9c71058810bdb85f4adcb1016671896dcc169db0` |
| `server/app/m3/world_analysis/fixtures.py` | `add` | `ca9f3146f652a299636ee8a07c90d6333337fc98da68b2c4fd9031df3e8b7182` |
| `server/app/m3/world_analysis/schema.py` | `add` | `8ebe10d96a035ff0974262da43e4b73fe4202c7c3784d30f70fbaf43d5d99bd5` |
| `server/app/m3/world_analysis/types.py` | `add` | `5165b6b6c639c4718d3eb24a573fb19d9a03b6d9c0b9bf794e4bb6d3d0c760f9` |
| `server/app/m3/world_analysis/validator.py` | `add` | `a054e90946f19bd5e054092c3f2df72391a5efceaf138c67c0d1204610525dd7` |
| `server/tests/m3/world_analysis/__init__.py` | `add` | `6290414c438d8a1d451ffe6d1bf1aa5ada267858f6dca93ce7b7c5e77ab9aa81` |
| `server/tests/m3/world_analysis/test_batching_conflicts_and_identities.py` | `add` | `67c69cd0e81923c33bec22dafe99b5eeae152f70693a3a975c6e2da585cf83b8` |
| `server/tests/m3/world_analysis/test_security_adapter_and_lifecycle.py` | `add` | `cf32229253142ebc8619a3e37497ca2df09878043213f6398d647551b65f744a` |
| `server/tests/m3/world_analysis/test_two_worlds_and_starting_points.py` | `add` | `304e99cf370d8bd68e07d4ef4f57c2ec00bd8030abddf326f7a5aad11db7d48f` |
| `docs/reports/gemini-marcin-m3-wave4.md` | `add` | *(见包内 `manifest.json`)* |
