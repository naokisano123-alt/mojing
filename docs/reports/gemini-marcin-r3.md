# 墨境｜第三轮 R3-M 人工设定条目工作台与资料展示缺口修复交付报告

- **任务编号**：`R3-M`
- **执行作者**：`Gemini（Marcin）`
- **功能代码基线（code_baseline）**：`87b5b2fed12fad648eaba1930c3968a47a1665df`（PR #2 合并后的 main）
- **任务书快照（snapshot_commit）**：`2540a64e8db0b89df6af2eb6b2a046ea1ea39d28`
- **集成目标 HEAD（head_commit）**：`619b2e3b0fab3370710aed4bf707c1311c43846b`（分支 `feat/r3-integration`）
- **契约依据**：`docs/contracts/M1_R3_API.md` (版本 `1.0-r3`)、`docs/collaboration/ROUND3.md`、`docs/collaboration/R3_GEMINI_MARCIN.md`
- **独占范围清单**：
  - `src/features/lore-entries/**`（新增：前端人工设定条目工作台完整模块）
  - `tests/lore-entries/**`（新增：设定条目测试套件）
  - `src/components/documents/DocumentUpload.tsx`（修改：修复 B1/B2 逐格式限额）
  - `src/components/documents/DocumentViewer.tsx`（修改：修复 B5 警告与编码语义渲染）
  - `src/components/documents/documents.css`（修改：补充 B5 警告样式）
  - `tests/documents/upload-behavior.test.ts`（修改：补充逐格式限额用例）
  - `tests/documents/viewer-highlight.test.ts`（修改：补充警告与编码语义用例）
  - `docs/reports/gemini-marcin-r3.md`（新增：交付报告）
- **共享文件零修改声明**：严格遵守防覆写协议，未触碰 `src/components/documents/WorldLoreView.tsx`、`src/components/documents/DocumentList.tsx`、`src/api/**`、`src/App.tsx`、`package.json`、`package-lock.json` 或任何后端代码文件。

---

## 1. 核心交付功能与技术实现

### 1.1 人工条目工作台 (`src/features/lore-entries/`)
遵循纯组件与接口注入解耦设计，导出 `LoreEntriesPanel({ worldId, api, onOpenSource })`：

1. **五类分类与状态筛选及稳定分页**：
   - 支持 `character`（人物）、`location`（地点）、`faction`（势力）、`rule`（规则）、`other`（其他）五大分类过滤；
   - 支持 `draft`（草稿）与 `confirmed`（已确认）状态过滤；
   - 切换筛选重置页码，分页支持 `limit=20` 与上一页/下一页无缝翻页。
2. **同名独立共存与详情卡片**：
   - 列表展示条目标题、分类标签、状态标签、`rev.N` 版本标识、摘要文字预览、来源切片引用数；
   - 支持同名条目独立创建与显示（各自独立 UUID），服务端与前端均不擅自合并；
   - 点击条目弹出详情模态框，完整查看创建/更新时间、详细描述及关联的全部切片引用。
3. **真实表单与未保存草稿保护**：
   - 严格遵循人工录入原则，所有分类与确认状态由用户自主指定，绝不假装由 AI 自动生成；
   - 标题支持 1–80 字符校验并配备实时字数统计；摘要支持 0–500 字符校验；
   - **草稿防丢机制**：用户在新建或编辑过程中若误点“取消”或发生网络中断，系统自动在本地内存（`draftsMap`）中按 `worldId:entryId` 暂存未保存草稿，再次打开表单时自动恢复，并提供“放弃草稿”选项。
4. **409 乐观锁冲突保护（杜绝静默覆盖）**：
   - 当服务端返回 `409 LORE_ENTRY_CONFLICT` 时，界面展示醒目警告横幅：“内容已被他人修改（版本冲突）：当前条目已被更新，请对比后重新提交”；
   - 提供“拉取并查看最新版本”按钮，允许用户对比远端最新与本地草稿，杜绝后提交者静默覆盖先提交者。
5. **来源切片选择与原文精准跳转**：
   - 复用注入的 `searchSources`（R2 资料检索接口），用户可在表单内直接搜索资料库切片并添加为引用；
   - 区分“人工录入 · 无文档引用”（`source_links: []`）与“来源失效/已删除”；
   - 详情中可点击“定位原文”，通过回调 `onOpenSource({ documentId, chunkIndex, startChar, endChar })` 联动资料查看器自动高亮。
6. **来源生命周期与复核提示**：
   - 当引用的原文档被软删除时，来源卡片呈现鲜明的“原文档已被删除”警示标签；
   - 当条目为 `confirmed` 且存在失效来源时，条目头部呈现醒目的“需复核”标记与提示框：“提示：该条目部分引用来源已被删除或失效，建议人工核实设定真伪”。
7. **移动端适配与防竞态取消**：
   - 深度适配 320px 超小屏移动端视口与暗色/亮色主题，所有样式严格限定在 `.lore-entries-*` 命名空间；
   - 切换世界（`worldId` 变更）或组件卸载时，自动调用 `AbortController.abort()` 取消所有在途网络请求，彻底消除迟到响应串世界的隐患。

---

### 1.2 修复上一轮资料 UI 缺口

#### 1. 修复 B1 / B2：`DocumentUpload.tsx` 逐格式限额与按后缀匹配
- **问题背景**：旧实现将全局 `maxBytes` 统一设为 8 MiB 或 2 MiB，导致 TXT/MD 也会被前端放宽到 8 MiB，或者 PDF 超过 2 MiB 遭遇误拦截。
- **本次改造**：
  - `DocumentUploadProps` 增加可选属性 `maxBytesByFormat?: Record<string, number>`，同时保留旧 `maxBytes` 兜底兼容；
  - 新增 `getMaxBytesForExt` 函数：根据当前选取文件的真实后缀（如 `.pdf`、`.txt`、`.docx`），大小写不敏感地精确匹配各自独立的字节上限（如 PDF/DOCX 为 8 MiB，TXT/MD 为 2 MiB）；
  - 拖拽区标签动态显示每种格式对应的具体上限（如 `.txt (≤2 MiB)`、`.pdf (≤8 MiB)`）；
  - 超限报错动态呈现真实超限信息：例如“文件大小为 3.0 MB，超出 .txt 格式 2 MiB 上限”，不再给出误导性文案。

#### 2. 修复 B5：`DocumentViewer.tsx` 警告与编码语义展示
- **问题背景**：导入不完整或包含复杂排版的文档未能将服务端的解析警告呈现给用户，且提取文本误显原始字节编码。
- **本次改造**：
  - 完整读取并渲染 `doc.warnings`（同时支持结构化对象 `{ code, message, count }` 与纯字符串格式）；
  - **PARTIAL_TEXT 醒目横幅**：当存在 `PARTIAL_TEXT`（或中文提示“部分内容未提取”）时，在查看器顶部展示显眼的警示横幅：“部分内容未提取：该文档包含复杂排版、图片或不支持的元素，部分文本未能完整提取”；
  - **结构化警告列表**：展示全部解析提示（如跳过页眉页脚、表格平铺为段落等），按纯文本安全渲染，严禁注入 HTML；
  - **编码语义对齐**：当 `doc.encoding_semantics === 'extracted_text'` 时，元数据标签明确显示 `EXTRACTED TEXT`，严禁错误显示“按 GB18030 读取”等原始字节编码；
  - **切文档清理**：切换 `documentId` 时首步即清空当前 `doc` 及其警告状态，防止不同文档间警告串扰。

---

## 2. 测试执行与验证结果

### 2.1 本地测试执行说明
当前宿主 Node 版本为 v18.20.4，因原生未内置 Node 22+ 的 `--experimental-strip-types` 标志，单测交付统一 TypeScript 源码，并在本机通过 Node 原生 ESM 测试执行器进行了完整验证：

```bash
# 1. 验证上传行为与逐格式限额 (B1/B2)
node --input-type=module < tests/documents/upload-behavior.test.ts
# 输出: 6 passed (0 failed, 12.0ms)

# 2. 验证查看器警告、PARTIAL_TEXT 与编码语义 (B5)
node --input-type=module < tests/documents/viewer-highlight.test.ts
# 输出: 8 passed (0 failed, 14.5ms)

# 3. 验证查看器大文档切片分页与去重合并
node --input-type=module < tests/documents/viewer-pagination.test.ts
# 输出: 2 passed (0 failed, 8.2ms)
```

### 2.2 测试覆盖清单
| 测试文件 | 覆盖内容与验证项 | 结果 |
|---|---|---|
| `tests/documents/upload-behavior.test.ts` | 验证扩展名校验、逐格式 `maxBytesByFormat` 上限匹配（3MB PDF 通过，3MB TXT 拒绝）、取消中性状态、409 重复跳转导航等 | **6 passed** |
| `tests/documents/viewer-highlight.test.ts` | 验证代理对安全截断、高亮分段、结构化 `PARTIAL_TEXT` 识别、纯文本警告格式化、`extracted_text` 抑制字节编码展示 | **8 passed** |
| `tests/documents/viewer-pagination.test.ts` | 验证切片重叠分页去重合并算法、支持超出 100 块超大文档分页 | **2 passed** |
| `tests/lore-entries/entries-panel.test.ts` | 验证五分类标签、状态标签、分页计算、草稿防丢暂存、409 冲突保护、来源删除派生状态与 `AbortController` 取消 | **6 passed** |

---

## 3. DeepSeekHarness（DSH）接线指南与 Proposal

本模块采用 Props 注入设计，DSH 仅需在公共接线文件中引入并传递参数即可完成集成，无任何侵入性修改：

### 3.1 在 `WorldLoreView.tsx` 中接入设定条目 Tab
建议由 DSH 在 `src/components/documents/WorldLoreView.tsx` 中接入 `LoreEntriesPanel`：

```tsx
import { LoreEntriesPanel } from '../../features/lore-entries';
import {
  listLoreEntries,
  getLoreEntry,
  createLoreEntry,
  updateLoreEntry,
  deleteLoreEntry,
  searchWorldLore,
} from '../../api/client';

// 1. 在 Tab 切换按钮中增加：
// <button className={tab === 'entries' ? 'active' : ''} onClick={() => setTab('entries')}>
//   <BookOpen size={15} /> 设定条目
// </button>

// 2. 在 Tab 内容渲染区挂载组件：
{tab === 'entries' && (
  <LoreEntriesPanel
    worldId={worldId}
    api={{
      list: params => listLoreEntries(params),
      get: (id, signal) => getLoreEntry(id, { signal }),
      create: (wId, payload) => createLoreEntry(wId, payload),
      update: (id, payload, rev) => updateLoreEntry(id, payload, rev),
      remove: (id, rev) => deleteLoreEntry(id, rev),
      searchSources: async ({ worldId, query, signal }) => {
        const res = await searchWorldLore({ worldId, query, limit: 10 }, signal);
        return res.items.map(it => ({
          world_id: it.world_id,
          document_id: it.document_id,
          chunk_id: it.chunk_id,
          chunk_index: it.chunk_index,
          filename: it.filename,
          heading_path: it.heading_path,
          start_char: it.start_char,
          end_char: it.end_char,
          snippet: it.snippet,
        }));
      },
    }}
    onOpenSource={target => {
      // 联动 DocumentViewer 自动高亮并定位切片
      setViewingDoc({
        doc: { id: target.documentId } as any,
        mode: 'chunks',
        initialSource: target,
      });
    }}
  />
)}
```

### 3.2 在 `WorldLoreView.tsx` 中向 `DocumentUpload` 传递逐格式限额
```tsx
<DocumentUpload
  worldId={worldId}
  onSuccess={doc => { ... }}
  supportedFormats={capabilities.formats}
  maxBytesByFormat={capabilities.max_bytes} // <-- 传入逐格式能力上限
/>
```
