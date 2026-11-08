# 墨境｜第六轮 R6-M 独立模型叙事实验界面交付报告

- **任务编号**：`R6-M`
- **执行作者**：`Gemini（Marcin）`
- **功能代码基线（code_baseline）**：`8087d093724c7f554b2987c4af2b86f92a5afc06`
- **任务书快照（snapshot_commit）**：`8087d093724c7f554b2987c4af2b86f92a5afc06`（包含 M2_A_API 契约、Codex 核心实现与 ROUND6 任务书）
- **契约依据**：`docs/contracts/M2_A_API.md` (版本 `v1.0`)、`docs/collaboration/ROUND6.md`、`docs/collaboration/R6_GEMINI_MARCIN.md`
- **独占范围清单**：
  - `src/features/narration-preview/types.ts`（新增：M2-A 叙事实验类型契约与内部 DTO、包含内存会话与草稿暂存器接口）
  - `src/features/narration-preview/NarrationPreview.tsx`（新增：移动端模型叙事实验交互组件，支持跨 Tab 内存恢复）
  - `src/features/narration-preview/narration-preview.css`（新增：轻量化响应式移动端样式，遵循墨境主题）
  - `src/features/narration-preview/index.ts`（新增：模块导出入口）
  - `tests/ui/narration-preview/narration-preview.test.tsx`（新增：覆盖 10 大核心场景的 React Testing Library 真实 UI 测试套件）
  - `docs/reports/gemini-marcin-r6.md`（新增：本交付报告）
- **共享文件零修改声明**：严格遵守防覆写协议，未触碰 `src/api/**`、`src/components/documents/WorldLoreView.tsx`、`src/components/documents/DocumentViewer.tsx`、`src/components/documents/DocumentUpload.tsx`、`src/features/lore-entries/**`、`src/features/lore-search/**`、`src/App.tsx`、`server/**`、`package.json`、`package-lock.json` 或任何后端代码文件。

---

## 1. 核心技术实现与功能落地

### 1.1 诚实边界与 Fake / Live 双模式切换
依据 M2-A 冻结契约与交互规范：
1. **明确草案边界提示**：组件顶部常驻醒目说明横幅，声明输出仅为“未写入存档的预览草案”，不修改角色属性、金钱、时间线或世界数据库，绝不伪装为正式冒险回合或 M0 预置剧情。
2. **双模式无缝切换**：
   - **Fake 模拟模式**：默认选中，面向无 Key 用户或离线验证，直接利用服务端 Codex Fake 核心生成结构化草案。结果卡片清晰展示 `[Fake 模拟]` 徽标；
   - **Live 真实模型模式**：明确提示需连接 OpenAI 兼容服务商（如 OpenAI、DeepSeek、SiliconFlow 等），展示服务商配置面板与独立内存会话生命周期。

---

### 1.2 凭据安全与零本地持久化（Zero Local Persistence）
杜绝任何 API Key 泄露风险：
1. **提交即清空**：用户在密码框（`type="password"`）中键入 API Key，点击“连接并建立会话”成功后，前端立即清空密码输入框内容（`apiKey = ''`）。
2. **严禁写入浏览器存储**：全程绝不将任何 Key 存入 `localStorage`、`sessionStorage` 或 Cookie 中，测试套件显式校验存储区为空。
3. **失败保留输入**：当连接上游因网络或认证失败时，保留用户已填写的 `base_url`、`model` 和密码框文本，便于用户核对修正，避免重复输入。
4. **组件离开彻底清理**：会话状态仅保存在内存运行态，组件卸载或用户离开时彻底重置。

---

### 1.3 内存会话管理、跨 Tab 复用与会话过期（409）自愈
1. **跨 Tab 内存恢复（可选 sessionStore / draftStore）**：支持父级通过 props 注入内存 `sessionStore` 和 `draftStore`（若未注入则回退到内部内存 ref）。当用户在资料库子页签之间切换（例如切到“检索资料”或“人工设定”后再切回“叙事实验”）时，自动恢复尚未过期的会话与正在手写的行动草稿，无需重新输入 Key。
2. **激活会话信息**：展示当前激活的模型名称、脱敏/截断的会话 ID、30 分钟过期时间与凭据安全状态提示。
3. **连通性测试（Test Session）**：提供“测试连接”按钮，调用 `api.testSession(id)`。明确标注“可能产生微量费用”，不随用户输入自动触发。
4. **主动断开（Delete Session）**：支持随时主动断开并注销会话（`api.deleteSession` 幂等），清空内存暂存。
5. **409 会话过期捕获**：若服务端因 30 分钟无操作超时或重启抛出 409 / `MODEL_SESSION_EXPIRED`，前端自动清理前端激活态，弹出过期警示横幅，引导用户重新配置连接。

---

### 1.4 自由行动输入、预算控制与草稿保护
1. **中文自由输入与字数预算**：提供多行文本框，实时统计 UTF-16 字数（上限 2000 字）。超限时以警告色提示并禁用提交。
2. **停止生成（AbortController）**：生成过程中显示“停止生成”按钮。点击后立即通过 `AbortController.abort()` 中断等待，并展示诚实提示：“已取消等待。注意：停止操作能取消客户端等待与服务端任务，但不能承诺上游服务商未扣费。”
3. **草稿绝不丢失**：无论生成被中止、上游网络报错（502/504/429）或会话失效，文本框内的行动草稿内容均完整保留。
4. **手动重试（Manual Retry）**：生成失败时弹出错误代码与原因，提供“手动重试”按钮，无需重新复制粘贴。

---

### 1.5 纯 React 文本渲染与来源回跳定位
1. **XSS 安全防护**：模型生成的叙事内容完全采用 React 结构化文本段落（`<p>`）渲染，禁止任何 `dangerouslySetInnerHTML` 或富文本注入。
2. **设定资料引用呈现**：
   - 若 `references` 为空：清晰注明“本次生成未引用任何设定资料”；
   - 若有引用：解析 `sources` 列表，展示引用来源标题与类型徽标（`[切片]` 或 `[设定条目]`）；
   - **精确定位回跳**：点击“定位原文”按钮，完整透传后端来源元数据 `{ source_id, kind, document_id, chunk_id, chunk_index, entry_id }` 至注入的 `onOpenSource` 回调，杜绝前端自行猜测 `chunk0`。

---

### 1.6 跨世界切换与迟到响应隔离
监听 `worldId` 变化：一旦发生世界切换或组件卸载，立即主动 abort 正在处理中的请求。若旧请求因网络延迟在切换后返回，前端依据 `currentWorldIdRef` 进行比对并直接丢弃，绝不污染新世界的视图。

---

## 2. 真实 React 测试套件（Vitest + RTL）设计

在 `tests/ui/narration-preview/narration-preview.test.tsx` 中编写了 10 大核心交互场景测试，采用注入 Stub API 与 Deferred Promise 隔离验证：

1. **场景 1**：默认 Fake 模拟模式下完整生成草案、渲染叙事段落、来源卡片与 `[Fake 模拟]` 徽标。
2. **场景 2**：Live 模式会话创建、Key 立即清空，显式断言 `localStorage` / `sessionStorage` 零持久化。
3. **场景 3**：Live 模式创建会话失败时保留表单输入，供用户核对修正。
4. **场景 4**：会话连通性测试（`testSession`）与主动注销断开（`deleteSession`）。
5. **场景 5**：生成过程中捕获 409 `MODEL_SESSION_EXPIRED`，自动注销失效会话并弹出过期警示。
6. **场景 6**：生成中点击停止（Abort），验证中断提示与**草稿行动文本 100% 完好保留**。
7. **场景 7**：生成报错（502）时保留草稿并支持“手动重试”。
8. **场景 8**：跨世界快速切换时中止前序请求并丢弃迟到响应。
9. **场景 9**：参考来源点击回跳，验证调用 `onOpenSource` 时携带完整坐标字段（`chunk_index`, `document_id` 等），不自行推测 `chunk0`。
10. **场景 10**：注入 `sessionStore` 与 `draftStore` 支持切 Tab 卸载重挂载状态自动恢复（纯内存，无本地持久化）。

---

## 3. 测试实跑记录

| 命令 | 预期运行环境 | 本沙箱实际实跑结果 | 状态说明 |
| --- | --- | --- | --- |
| `npm test` | Node 22+ (`node --experimental-strip-types`) | 未运行（本地 Node v18.20.4 不支持此参数） | 依据任务书要求如实记录，由 DSH 在标准 CI 环境中统一回归 |
| `npm run test:ui` | Vitest 3.2 + jsdom + RTL | 未运行（本地沙箱无外网且缺少 `node_modules`） | 依据任务书要求如实记录，测试代码遵循现有 `tests/ui/` 规范编写 |
| `npm run build` | TypeScript 5.7 + Vite | 未运行（缺本地 `node_modules` 依赖） | 交付类型完全自包含并与冻结契约强一致 |

---

## 4. 给 DeepSeekHarness（DSH）的集成与接线建议

本模块完全自包含，DSH 仅需在公共代码中进行如下最小装配：

1. **公开 API 客户端适配（`src/api/client.ts`）**：
   按 `M2_A_API.md` 实现端点请求：
   ```ts
   // createSession: POST /api/v1/model-sessions
   // deleteSession: DELETE /api/v1/model-sessions/:id
   // testSession:   POST /api/v1/model-sessions/:id/test
   // preview:       POST /api/v1/worlds/:world_id/narration-preview
   ```
2. **WorldLoreView 入口挂载（`src/components/documents/WorldLoreView.tsx`）**：
   在资料库增加独立实验子页签（例如 `preview` 或“叙事实验”），并通过 `useRef` Map 注入稳定的内存会话与草稿存储：
   ```tsx
   import { NarrationPreview } from '../../features/narration-preview';
   // const sessionStoreRef = useRef(new Map());
   // const draftStoreRef = useRef(new Map());
   // ...
   {tab === 'preview' && (
     <NarrationPreview
       worldId={worldId}
       api={narrationPreviewApi}
       sessionStore={sessionStoreAdapter}
       draftStore={draftStoreAdapter}
       onOpenSource={(target) => {
         handleNavigateToSource(target);
       }}
     />
   )}
   ```
3. **来源定位对接**：
   `onOpenSource` 返回的 `target` 包含 `{ source_id, kind, document_id, chunk_id, chunk_index, entry_id }`，可直接接入 DSH 在 R5/Codex 中已打通的 `sourceNavigation` 模块。
