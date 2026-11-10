# 墨境｜M3 第三轮（Wave 3）玩家主动查证、交谈与消息后续行动交付报告（Gemini Ma）

- **任务角色**：`Gemini Ma`
- **共同仓库快照（`base_sha`）**：[`22dfaaae63b715708321f2cd532c2b99774bdc95`](https://github.com/naokisano123-alt/mojing/archive/22dfaaae63b715708321f2cd532c2b99774bdc95.zip)（M3 第二轮 [PR #26](https://github.com/naokisano123-alt/mojing/pull/26) 合入后主分支快照）
- **契约与任务书依据**：
  - [M3 第三轮协作任务书 `docs/collaboration/M3_WAVE3.md`](https://github.com/naokisano123-alt/mojing/blob/main/docs/collaboration/M3_WAVE3.md)
  - [`docs/collaboration/M3_WAVE3_GEMINI_MA.md`](../collaboration/M3_WAVE3_GEMINI_MA.md)
  - [`docs/M3_DESIGN_DRAFT.md`](../M3_DESIGN_DRAFT.md)、[`docs/contracts/M3_WAVE2_API.md`](../contracts/M3_WAVE2_API.md)
  - [第二轮集成复验 `docs/reports/codex-m3-wave2-completion.md`](https://github.com/naokisano123-alt/mojing/blob/main/docs/reports/codex-m3-wave2-completion.md)

## 1. 交付文件清单与逐文件 SHA-256 摘要

本交付包严格限定在独占路径 `server/app/m3/encounters/`、`server/tests/m3/encounters/`、`src/features/world-encounters/`、`tests/ui/world-encounters/` 与本报告 `docs/reports/gemini-ma-m3-wave3.md` 内，未触碰任何公共冒险入口、数据库主初始化、模型网关或 Android 工程。全部路径与原始字节 SHA-256 均同步记录于根目录 `manifest.json`。

| 仓库相对路径 | 操作类型 | 字节数 | SHA-256（小写十六进制） | 说明 |
| --- | --- | ---: | --- | --- |
| `server/app/m3/encounters/__init__.py` | `add` | 3,225 | `5e123bf5e26c496c0169a5756f76f758244b7fefeb7da2b246a9c0dc6ea7dcd4` | 后端查证与交谈模块桶导出。 |
| `server/app/m3/encounters/errors.py` | `add` | 1,383 | `b4cd461b950dbc2bc09db6fa8a4f663cbad98f558087e516d9edad5fc88fda0c` | 白名单脱敏异常 `EncounterError`，零隐藏事件泄露。 |
| `server/app/m3/encounters/fixtures.py` | `add` | 13,919 | `5e712d96051f173faf59cbe91b3b905d5207e733022d6e30aee74d7c0e63b0a0` | 「旅店店主转述商队改道」端到端五步闭环、NPC 不知情/途中离开/拒答/冲突说法与分支隔离 Fixture。 |
| `server/app/m3/encounters/schema.py` | `add` | 6,790 | `d0dab271b14ba24dd8f43ca60200d2cc0edfaaafb801f904ed9a199b96c50569` | `m3_encounter_npc_stubs`、`m3_encounter_knowledge_stubs`、`m3_encounter_notices`、`m3_encounter_drafts`、`m3_encounter_history` 幂等建表。 |
| `server/app/m3/encounters/service.py` | `add` | 87,856 | `fd25cc3f038b19afe8f93b87298018ea7b99b74fda6716383603aef0bba5697e` | 消息行动建议生成、可注入 NPC 可接触性与知情查询边界、草案生成、事务内确认、传播提议纯校验及快照恢复。 |
| `server/app/m3/encounters/types.py` | `add` | 10,234 | `2e102f3070105723007bbaa3d197da73a6b36c812bac6eaa698cadacee9f283e` | `NpcContactabilityProvider`、`HolderKnowledgeProvider` 协议与查证建议/草案/传播提议数据类。 |
| `server/tests/m3/encounters/__init__.py` | `add` | 100 | `131aa4ea8b0bba569549d52fe9d8f3e142f2445737738815a5038ef90f9186b8` | 后端测试包初始化。 |
| `server/tests/m3/encounters/test_encounters_service.py` | `add` | 19,505 | `1121cef7afde732e009df6e1f6ff96d26435d1fb9e2c87c11880aa81ae5e1479` | 覆盖全部 6 大核心验收场景的后端单元测试。 |
| `src/features/world-encounters/WorldEncounterPanel.tsx` | `add` | 17,760 | `a19322f1ec37e26c3c5bd7dc6c427ad04517997509ceaed19163de8c71262926` | 手机优先的「世界消息行动入口与主动查证」组件（折叠、触控、写入输入区、草案确认闭环、切旅程防竞态）。 |
| `src/features/world-encounters/api.ts` | `add` | 33,972 | `158369e9e322ec361aea970278211ce6dd605e19dae129c2e0aee03292d7a078` | 同源 HTTP 传输 `worldEncountersApi`、输入区草稿追加 `applyEncounterOptionToComposerDraft` 与「旅店店主转述商队改道」前端 Fake 替身。 |
| `src/features/world-encounters/index.ts` | `add` | 1,002 | `3e152f31ff996247f5f9333e00ad19d122bae1be18657f06d24f04795f762471` | 前端模块桶导出。 |
| `src/features/world-encounters/types.ts` | `add` | 4,876 | `e8b617720fdc194c1c9884bd158e3db3867ab0d571d970ad41975c0ef608762c` | 前端查证选项、NPC 可接触性、草案与确认响应类型定义。 |
| `src/features/world-encounters/world-encounters.css` | `add` | 3,236 | `fa66128c4e2b9b0809c9782999fd52dc870c76834d80d198ba2574ce029bd090` | 适配移动端窄屏、触控按钮高度（`>= 44px`）与深浅主题的样式表。 |
| `tests/ui/world-encounters/world-encounters-transport.test.ts` | `add` | 6,712 | `afaa098e3274a9083f8943ff544c7df148603d5a4e6ffd6e0ea184eb57967b5d` | 3 项 `node:test` 纯逻辑与传输契约测试。 |
| `tests/ui/world-encounters/world-encounters.test.tsx` | `add` | 9,406 | `9aadf8d04a3a50c89b6eea583b9ee9c8e7b235fdaff54840e608fc6b1b654be4` | 6 项手机宽度（390px）Vitest + React Testing Library UI 测试。 |
| `docs/reports/gemini-ma-m3-wave3.md` | `add` | 见 `manifest.json` | 见 `manifest.json` | 本交付与宿主接线说明报告。 |

- **删除或重命名文件**：无（0 删除，0 重命名）。

## 2. 核心实现与契约对照

### 2.1 把「世界消息」变成行动入口（选项不推进时间、不自动确认回合）

- **三类行动入口选项（`list_news_action_options` / `WorldEncounterPanel`）**：
  1. **询问可接触人物（`ask_npc`）**：列出当前地点可接触的 NPC（以及虽有关联但已动身离开的 NPC 状态说明），生成自然中文询问草稿；
  2. **查阅公告（`check_notice`）**：生成前往当前地点公告栏核实告示的行动草稿；
  3. **自由编辑行动（`free_edit`）**：围绕该传闻主题生成开放式查证前缀供玩家自由续写。
- **移动端输入区联动**：点击建议项后通过 `applyEncounterOptionToComposerDraft` 与 `onComposerDraftChange` / `onExpandComposer` 写入或追加到宿主 `ActionComposer`，**`advances_clock` 与 `auto_commits_turn` 恒为 `false`**，由玩家自由编辑后再发送生成草案。

### 2.2 可注入的 NPC 可接触性与知情查询边界及六种回应结果

- **可注入协议（`NpcContactabilityProvider` / `HolderKnowledgeProvider`）**：
  - 开发与单测阶段通过 `m3_encounter_npc_stubs` 与 `m3_encounter_knowledge_stubs`（或自定义 Provider 注入）独立运行，不导入 `server/app/m3/npcs/`；同时内置 `SqlNpcContactabilityProvider` 与 `SqlHolderKnowledgeProvider`，在集成环境中可无缝读取 `m3_npcs` 与 `m3_knowledge_facts`。
- **严格基于 NPC 自身知情记录回答（`evaluate_encounter_inquiry`）**：
  - 核对实例（拦截 `403 CROSS_INSTANCE_ACCESS`）、地点、渠道与确认时刻（拦截 `422 FUTURE_ENCOUNTER_FORBIDDEN`）；
  - NPC 只查询自身 `holder_id == target_npc_id` 且 `learned_minute <= confirmed_world_minute` 的记录，绝不越权读取 `world_events` 或其他 NPC 的认知：
    1. **获得线索（`clue`）**：NPC 已获知且态度合作，生成带 `source_person_id` 与 `source_fact_id` 的 `conversation` 传播提议；
    2. **互相冲突的说法（`conflicting`）**：当 NPC 的说法与玩家已有认知（如货栈杂役老巴克的 `mistaken` 误信“商队在边关被扣押” vs 玛塔老板娘的 `confirmed` 转述“商队改走白河谷地”）冲突时，输出 `conflict_summary` 与 `conflicting_fact_ids`，在确认后保留双方各自来源与认知类型（`confirmed` / `rumor` / `suspected` / `mistaken`），不互相覆盖；
    3. **含混回应（`vague`）**：NPC 自身把握度低或态度含糊，输出降权可信度与 `suspected`/`rumor` 提议；
    4. **拒答（`refused`）**：NPC 策略为 `reluctant`，返回拒答叙事且 `transmission_proposals = []`；
    5. **NPC 不知情（`unknown`）**：NPC 在当前时刻尚无该事件知情记录，返回 `NPC_HAS_NO_KNOWLEDGE` 且 `transmission_proposals = []`；
    6. **途中离开（`npc_departed`）**：NPC 已出行离开当前地点（`NPC_LEFT_FOR_TRAVEL`），返回不在场说明；若在草案生成后、确认前离开，`commit_encounter_draft` 在 `SAVEPOINT` 内抛出 `409 NPC_DEPARTED_BEFORE_COMMIT` 并原子回滚。
- **禁止传闻直接改账**：在 `prepare_encounter_draft`、`validate_encounter_proposal` 与 `commit_encounter_draft` 全链路递归拦截 `coins`、`coins_delta`、`inventory`、`inventory_changes`、`stamina_delta`、`proposed_changes` 等玩家账本字段（抛出 `422 PLAYER_LEDGER_PROTECTED`），所有传播提议固定标记 `can_settle_ledger: False`。

## 3. 宿主接线说明（供 DeepSeekHarness 集成）

### 3.1 前端 `src/features/adventures/AdventureJournal.tsx` 最小接线方式

在 `AdventureJournal.tsx` 的「世界消息」折叠区中挂载 `<WorldEncounterPanel />`，并将其 `onComposerDraftChange` 与 `onExpandComposer` 接到现有的 `ActionComposer` 状态（`setAction`、`setExpanded`）：

```tsx
import { WorldEncounterPanel } from "../world-encounters";

<details
  className="journal-sources"
  aria-label="世界消息"
  onToggle={event => setNewsOpen(event.currentTarget.open)}
>
  <summary>世界消息 · 只显示你有渠道得知的消息</summary>
  {newsOpen && (
    <>
      {run.mode === "fake" && <PulsePreview key={run.id} adventureId={run.id} />}
      <WorldEncounterPanel
        key={`encounters:${run.id}`}
        adventureId={run.id}
        disabled={busy}
        composerDraft={action}
        onComposerDraftChange={nextDraft => setAction(nextDraft)}
        onExpandComposer={open => setExpanded(open)}
        refreshTrigger={run.revision}
      />
    </>
  )}
</details>
```

### 3.2 后端与 Marcin `npcs` 包及 `core.adapters` 的接线方式

```python
from app.m3 import encounters, knowledge, npcs
from app.m3.core.adapters import register_adapter

# 1. 注册检查点快照适配器（name="encounters"）
register_adapter(encounters.EncountersAdapter())

# 2. 将 Marcin 的 npcs.check_npc_contactability / query_contactable_npcs 注入 encounters：
class MarcinNpcContactabilityBridge:
    def check_contactability(self, conn, instance_id: str, npc_id: str, *, player_location: str, world_minute: int) -> dict:
        return npcs.check_npc_contactability(
            conn, instance_id, npc_id, player_location=player_location, world_minute=world_minute
        )

    def list_contactable_npcs(self, conn, instance_id: str, *, location: str, world_minute: int) -> list[dict]:
        return npcs.query_contactable_npcs(
            conn, instance_id, location=location, world_minute=world_minute
        )

# 3. 在回合确认短写事务内调用 encounters.commit_encounter_draft，并通过 knowledge_applier 同步写入知识模块：
commit_res = encounters.commit_encounter_draft(
    conn,
    instance_id,
    draft_id,
    commit_world_minute=confirmed_minute,
    expected_world_revision=expected_world_rev,
    contactability_provider=MarcinNpcContactabilityBridge(),
    knowledge_applier=lambda c, iid, txs: knowledge.record_transmission(c, iid, {"transmissions": txs}),
)
```

## 4. 测试执行结果与证据记录

1. **后端 `encounters` 单元测试（6/6 通过）**：
   - 命令：`PYTHONPATH=server python3 -m unittest discover -s server/tests/m3/encounters -t server -v`
   - 结果：`Ran 6 tests in 0.058s — OK`
2. **后端 `knowledge` 回归单元测试（15/15 通过）**：
   - 命令：`PYTHONPATH=server python3 -m unittest tests.m3.knowledge.test_knowledge_service -v`
   - 结果：`Ran 15 tests in 0.641s — OK`
3. **前端 `world-encounters` 传输与场景契约及移动端 UI 测试**：
   - `tests/ui/world-encounters/world-encounters-transport.test.ts`（3 项）与 `tests/ui/world-encounters/world-encounters.test.tsx`（6 项）已随包交付；待 DeepSeekHarness 合入集成分支后在 CI 环境（Node 22 + Vitest + Android APK Workflow，继 [第二轮安卓构建 #42758113321](https://github.com/naokisano123-alt/mojing/actions/runs/42758113321)）统一执行全量验证。
