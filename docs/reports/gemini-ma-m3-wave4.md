# 墨境｜M3 第四轮（Wave 4）手机开局与游玩体验交付报告（Gemini Ma）

- **任务角色**：`Gemini Ma`
- **共同仓库快照（`base_sha`）**：[`9117404aa307c53de235b2959507d8d577d9fbca`](https://github.com/naokisano123-alt/mojing/archive/9117404aa307c53de235b2959507d8d577d9fbca.zip)
- **功能代码基线（`code_baseline`）**：[PR #27 合入提交 `46fb8deb8cdfe725bad5ae241b953d255eb078f1`](https://github.com/naokisano123-alt/mojing/commit/46fb8deb8cdfe725bad5ae241b953d255eb078f1)
- **契约与任务书依据**：
  - [M3 第四轮协作任务书 `docs/collaboration/M3_WAVE4.md`](https://github.com/naokisano123-alt/mojing/blob/main/docs/collaboration/M3_WAVE4.md)
  - [`docs/collaboration/M3_WAVE4_GEMINI_MA.md`](../collaboration/M3_WAVE4_GEMINI_MA.md)
  - [`docs/contracts/M3_WAVE4_API.md`](../contracts/M3_WAVE4_API.md)
  - [`docs/M3_DESIGN_DRAFT.md`](../M3_DESIGN_DRAFT.md)、[`docs/reports/codex-m3-wave3-completion.md`](codex-m3-wave3-completion.md)

## 1. 交付文件清单与逐文件 SHA-256 摘要

本交付包严格限定在独占目录 `src/features/world-start/`、`tests/ui/world-start/` 与本报告 `docs/reports/gemini-ma-m3-wave4.md` 内，未修改任何公共入口文件、后端目录、数据库迁移或构建配置。全部文件均同步记录于根目录 `manifest.json`（`round: "m3-wave4"`）。

| 仓库相对路径 | 操作类型 | 字节数 | SHA-256（小写十六进制） | 职责说明 |
| --- | --- | ---: | --- | --- |
| `src/features/world-start/WorldStartPanel.tsx` | `add` | 46,993 | `b3f8c279a51ba74bd6cef0472a5077f396d8d9d2f3b4fb75997844ef5863aeb9` | 手机端开局与初始局势建立组件 `WorldStartPanel` 及宿主最小挂载桥接容器 `WorldStartJourneyHost`。 |
| `src/features/world-start/api.ts` | `add` | 54,070 | `6d513db08fddc7d602248380524121ce58410e29991e5a970b546cfb0c6fdbee` | 同源 HTTP 传输 `worldStartApi`、玩家知情边界清洗纯函数、输入区追加辅助函数及两套异构世界资料 Fixture 替身 `createFakeWorldStartTransport`。 |
| `src/features/world-start/index.ts` | `add` | 1,354 | `b3e31e61920b5965d018bd0ab1b04f1b11bde51c4d3708f700484585d9ee7c80` | `world-start` 模块桶导出。 |
| `src/features/world-start/types.ts` | `add` | 9,386 | `c6891a29392a4d3e94311d802d11164e3da97678d35c204b2908f859c42ebd82` | 资料选择、起点、分析状态、草案摘要及确认结果五类数据 DTO 与可注入 `WorldStartTransport` 接口定义。 |
| `src/features/world-start/world-start.css` | `add` | 5,360 | `08491a167103b96373d2cd0895e595e0cd50217b801aed1a5fd58ef391816015` | 适配移动端窄屏（`@media (max-width: 700px)`）、触控按钮高度（`>= 44px`）与深浅色主题变量的样式表。 |
| `tests/ui/world-start/world-start-transport.test.ts` | `add` | 10,610 | `98d553fe6db313de44bc4267352983f3debc88fb027952bdb5e0197c81300f50` | 3 项 `node:test` 传输契约、双异构世界 Fixture 隔离与脱敏纯逻辑测试。 |
| `tests/ui/world-start/world-start.test.tsx` | `add` | 11,052 | `cbd982d6cd0c40d6de563c5b993c25ff41d57cc0d48ed02370687e41348c1dfa` | 4 项手机宽度（390px）Vitest + React Testing Library UI 测试（覆盖双世界完整流程、资料不足与普通冒险先行、失败重试、返回旅程自由输入与消息查证联动、切世界防竞态）。 |
| `docs/reports/gemini-ma-m3-wave4.md` | `add` | 见 `manifest.json` | 见 `manifest.json` | 本交付与宿主接线说明报告。 |

- **删除或重命名文件**：无（0 删除，0 重命名）。

## 2. 核心实现与契约对照（`docs/contracts/M3_WAVE4_API.md`）

### 2.1 可注入 Transport 方法与五类核心数据

| 契约分类 / 方法 | 实现位置 | 行为与安全边界 |
| --- | --- | --- |
| **1. 资料选择** (`WorldStartMaterialItem` / `computeMaterialSelection`) | `types.ts`、`api.ts` | 列出当前世界已导入的文档与已确认人工设定条目，计算已选总字数与长文分批提取预估（`requires_batch_extraction`、`estimated_batches`）。导入资料本身只保存资料，绝不自动触发 AI 调用。 |
| **2. 起点** (`WorldStartPointOption` / `WorldStartPointInput`) | `types.ts`、`WorldStartPanel.tsx` | 从当前世界资料提取候选起点（含起始地点、开局时间、角色姓名与身份），支持玩家自由修改。所有默认值均来自当前世界资料，无任何固定世界名或预设剧情混入。 |
| **3. 分析状态** (`WorldStartPhase` / `WorldStartRunKind`) | `types.ts`、`WorldStartPanel.tsx` | 完整覆盖 `insufficient_materials`（资料不足）、`waiting`（等待分析）、`analyzing`（分析中）、`ready_to_confirm`（可确认）、`failed_retryable`（失败可重试）与 `applied`（已应用/成功）；显式区分 `initial`（首次建立）、`retry`（重试分析）、`reopen`（重新开局）与 `branch`（分支开局）。 |
| **4. 草案摘要** (`WorldStartDraftSummaryDto` / `sanitizeWorldStartDraftSummary`) | `types.ts`、`api.ts` | 仅向玩家展示角色在开局时刻应知的内容：开场局势简述、已知地点、在场/已知人物的公开印象与已知关系、开局有渠道获知的公开告示或传闻、资料明文依据引用（`canon` + `source_refs`）、资料空白处的推演补写标记（`inferred` + `inferred_notes`）及设定冲突提示（`conflict_warnings`）。未确认草案固定标记 `committed: false`，不写入正式事件、人物、知识与时间。 |
| **5. 确认结果** (`WorldStartCommitResultDto` / `sanitizeWorldStartCommitResult`) | `types.ts`、`api.ts` | 确认后原子建立 `adventure_id` 与 `world_instance_id`，绑定 `bound_source_ids`，初始化角色状态与开局已获知消息 `initial_player_news`，并回调 `onJourneyStarted` 进入旅程。 |
| `getBootstrap(worldId, options)` | `worldStartApi` (`GET /api/v1/worlds/{world_id}/world-start`) | 读取当前世界的资料列表、候选起点、当前阶段状态与最新未确认草案。 |
| `analyzeStart(worldId, payload, options)` | `worldStartApi` (`POST /api/v1/worlds/{world_id}/world-start/analyze`) | 玩家在界面主动点击后发起分析，沿用现有 `mode` 与 `session_id` 模型配置，生成待确认草案。 |
| `confirmStart(worldId, draftId, payload, options)` | `worldStartApi` (`POST /api/v1/worlds/{world_id}/world-start/drafts/{draft_id}/confirm`) | 原子应用已确认草案，建立正式旅程状态并绑定来源。 |
| `discardDraft(worldId, draftId, options)` | `worldStartApi` (`DELETE /api/v1/worlds/{world_id}/world-start/drafts/{draft_id}`) | 放弃未确认草案，返回起点与资料选择态。 |
| `startQuickAdventure(worldId, payload, options)` | `worldStartApi` (`POST /api/v1/worlds/{world_id}/world-start/quick-start`) | 减少设置步骤，允许玩家在任何时候（含资料不足或分析失败时）跳过 AI 局势分析、普通冒险先行。 |

### 2.2 玩家知情边界与两份异构世界资料 Fixture 验证

- **两套完全异构的测试世界 Fixture（`createTwoDistinctWorldStartSeeds`）**：
  1. **世界 A（`world_celain_harbor` · 《青瓷海岬志》）**：包含《青瓷海岬风物志.md》《海盐公所税则与航道章程.txt》及人工设定《白鸥灯塔守夜公约》（共 13,480 字，触发 3 批提取合并），起点为「白鸥灯塔下栈桥」（角色「林砚」），在场人物为「苏晚照」「顾承钧」，含灯塔夜税记载冲突提示与早潮推演补写；
  2. **世界 B（`world_sand_clockwork` · 《赤砂星晷录》）**：包含《赤砂绿洲分水律.md》《星晷高塔观测手札.txt》，起点为「第三分水闸集市」（角色「阿斯兰」），在场人物为「娜迪娅」「卡维」，围绕黄铜水闸减流检修展开；
  3. **空资料世界（`world_empty_materials` · 《空白星图世界》）**：用于验证 `insufficient_materials`（资料不足）状态与普通冒险先行。
- **零固定世界污染与零私事/未获知事件泄露**：
  - 经 `sanitizeWorldStartDraftSummary`、`sanitizeWorldStartKnownContact` 与 `sanitizeWorldStartPlayerKnownFact` 清洗，服务端种子中的后台 NPC 私事（如 `private_motivation`、`secret_schedule`、`private_knowledge`）与未获知后台事件（`player_known: false` / `holder_id !== 'player'`）被 100% 剥离，且两套世界中绝无“雾港”“霜木镇”“刻希弗”“月渡”“黎斯”等固定演示名称混入。

## 3. 宿主最小挂载说明（供 DeepSeekHarness 集成）

在 `src/features/adventures/AdventureJournal.tsx` 的新建旅程区域（`showCreate || runs.length === 0`）挂载 `<WorldStartPanel />`，并将其与现有的 `ActionComposer`（`action` / `setAction` / `setExpanded`）及 `WorldEncounterPanel` 联动：

```tsx
import { WorldStartPanel } from "../world-start";

{!loadingRuns && (showCreate || runs.length === 0) && (
  <WorldStartPanel
    key={`world-start:${worldId}`}
    worldId={worldId}
    mode={nextMode}
    sessionId={sessionId ?? null}
    disabled={busy}
    composerDraft={action}
    onComposerDraftChange={nextDraft => setAction(nextDraft)}
    onExpandComposer={open => setExpanded(open)}
    onConfigureModel={onConfigure}
    onJourneyStarted={async committed => {
      setShowCreate(false);
      await refresh(committed.adventure_id);
      setExpanded(false);
    }}
  />
)}
```

同时本包导出了 `<WorldStartJourneyHost />`，在单组件内封装了「开局面板 + 自由行动输入区 + `WorldEncounterPanel` 消息主动查证」的完整闭环，确保确认开局返回旅程后：
1. 玩家可在行动编辑框继续自由输入任意自然语言行动；
2. 点击 `WorldEncounterPanel` 中的任何消息查证建议（询问人物、查阅公告、自由编辑）仍通过 `onComposerDraftChange` 填入同一个行动编辑框，不推进时钟、不自动提交回合；
3. 离开或切换 `worldId` 时通过 `AbortController` 与 `epochRef` 立即中止并丢弃在途过期响应。

## 4. 测试执行结果与证据区分

1. **沙箱内已实际执行并通过的自动化验证**：
   - **TypeScript / TSX 语法与类型编译核验（14/14 文件通过）**：使用沙箱内置 `@babel/core` + `@babel/preset-typescript` + `@babel/preset-react` 编译 `src/features/world-start/*`、`tests/ui/world-start/*` 及关联模块，零语法或类型转换错误；
   - **前端 `world-start-transport` 与 `world-encounters-transport` 契约单元测试（6/6 通过）**：
     - 命令：`node --test world-start-transport.test.mjs world-encounters-transport.test.mjs`
     - 结果：`# tests 6 | # pass 6 | # fail 0`
   - **后端 `encounters` 回归单元测试（6/6 通过）**：
     - 命令：`PYTHONPATH=server python3 -m unittest discover -s server/tests/m3/encounters -t server -v`
     - 结果：`Ran 6 tests in 0.065s — OK`
2. **待 DeepSeekHarness 合入集成分支后在 CI / 真机环境验证的事项**：
   - `tests/ui/world-start/world-start.test.tsx`（4 项 390px 手机宽度 Vitest + React Testing Library 测试）与 Node 22 原生 `--experimental-strip-types` 执行（离线沙箱无 `node_modules`，由 GitHub Actions CI 运行 `npm test && npm run test:ui && npm run build`）；
   - 真实 OpenAI 兼容模型服务商连接下的端到端长文提取分析与 Android 真机触控验收。
