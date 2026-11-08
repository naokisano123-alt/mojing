# 墨境｜R2-M 资料阅读与导入组件可靠性实施报告

- **任务编号**：`R2-M`
- **执行角色**：`Gemini (Marcin)`
- **报告时间**：2026-11-08
- **快照基线（snapshot_commit）**：`3e900637389b0f9d6ae838d1d62eae0609d4ca34`
- **代码功能基线（code_baseline）**：`c83eccb44734dd16ef6da8e0c2382eff4bed99df`
- **依赖契约（contract_version）**：`docs/contracts/M1_AB_API.md v0.2`
- **独占范围**：
  - `src/components/documents/DocumentViewer.tsx`（修改）
  - `src/components/documents/DocumentUpload.tsx`（修改）
  - `src/components/documents/documents.css`（修改）
  - `src/components/documents/viewerUtils.ts`（新增）
  - `tests/documents/viewer-pagination.test.ts`（新增）
  - `tests/documents/viewer-highlight.test.ts`（新增）
  - `tests/documents/upload-behavior.test.ts`（新增）
  - `docs/reports/gemini-marcin-r2.md`（新增）

---

## 1. 任务背景与核心问题解决

在上一轮静态审阅（`ROUND2_REVIEW.md`）中，既有前端阅读器与上传组件存在以下关键隐患，本轮均已彻底修复与重构：

1. **突破 100 块限制与切片按需加载**：
   - 旧实现首次请求限制 `limit=100`，大文档超出 100 块时无法查看后续内容。
   - 现实现：接入服务端 `total`，引入 `deduplicateChunks` 升序合并与去重算法；当 `chunks.length < totalChunks` 时，提供“加载更多切片（当前 X / 共 Y 块）”按钮，支持失败重试与加载状态指示。
2. **严禁摘要伪装全文（真实错误呈现与重试）**：
   - 旧实现在 `expandChunk` 的 `catch` 中将 `text_preview` 填入展开状态，导致接口 500 时用户将截断的摘要误认作正文。
   - 现实现：切片全文展开失败时，绝不降级填充摘要；在卡片下方以明确红字报错：“获取该章节切片全文失败”，并附带“重试展开”链接。各切片拥有独立加载与错误状态。
3. **换文档状态彻底隔离与迟到请求防护**：
   - 旧实现 `documentId` 改变时未清空 `expandedChunkMap` 和 `rawOffset`，容易发生不同文档内容串扰。
   - 现实现：在 `documentId` 变化的 `useEffect` 初始阶段即清空所有切片、全文、高亮、错误状态，并通过 `active` 标志与 `AbortController` 抛弃在途旧请求。
4. **UTF-16 来源区间定位与 Emoji 代理对安全保护**：
   - 实现 `safeUtf16Slice` 与 `adjustSurrogateBoundary`：若高亮或截断字符索引落在 Emoji 低代理项（如 `\uDC00`）上，自动扩展或收缩，保证 Emoji（如 🗡️、🀄、📜）永远不被截半损坏。
   - 原文分页模式支持 `splitTextWithHighlight`：精确切出前文、`<mark class="raw-highlight-mark">` 高亮文、后文，并支持自动平滑滚动。
   - `DocumentViewer` 支持可选 `initialSource: { chunkIndex?, startChar?, endChar? }` 属性，便于 DeepSeekHarness 接入 Ma（R2-A）的搜索回跳。
5. **原文分页真实历史回退栈（非固定减 20000）**：
   - 旧实现直接 `rawOffset - 20000`，若服务端段落对齐产生非整万偏移时会导致页码混乱。
   - 现实现：采用 `offsetHistory` 历史栈，下一页存入当前偏移并前进到 `rawPage.next_offset`；上一页从历史栈弹出，精确恢复历史边界。
6. **异步剪贴板复制防假死与权限提示**：
   - `handleCopySnippet` 使用 `await` 等待 `navigator.clipboard.writeText` 完成；若被权限拒绝或运行于无剪贴板环境，优雅提示错误并自动清理 timer。
7. **上传取消中性化与 409 重复查看对接**：
   - 用户主动取消上传时，界面呈现中性提示横幅，并附带安全说明：“若服务端已在处理入库，数据可能仍已保存，建议刷新列表核对”。
   - 发生 409 `DUPLICATE_DOCUMENT` 时，提取服务端返回的 `existing_document_id`，若传入可选 `onViewExisting`，则展示“查看已有文档”按钮，不默认强推重复导入。
   - 重置文件输入框 `inputRef.current.value`，保证选取同一个文件重试时依然触发。
   - 提供可选 `supportedFormats` 扩展入口（默认严格保持 `['.txt', '.md']`，不提前宣称未接通格式）。

---

## 2. 变更文件清单

| 文件路径 | 变更类型 | 说明 |
| :--- | :--- | :--- |
| `src/components/documents/DocumentViewer.tsx` | 修改 | 分页切片、UTF-16 来源高亮、真实错误展开、历史分页、复制 await |
| `src/components/documents/DocumentUpload.tsx` | 修改 | 中性取消状态、阶段区分、409 onViewExisting 支持、重复选择可重试 |
| `src/components/documents/documents.css` | 修改 | 高亮样式、取消横幅、展开错误、分页按钮、动画与 320px 适配 |
| `src/components/documents/viewerUtils.ts` | 新增 | UTF-16 Emoji 安全截取、高亮分割、切片去重、安全复制 |
| `tests/documents/viewer-pagination.test.ts` | 新增 | 切片分页与去重、换文档隔离、展开错误拒绝伪装摘要 |
| `tests/documents/viewer-highlight.test.ts` | 新增 | UTF-16 代理对保护、Emoji 切片不截断、高亮分割、历史栈 |
| `tests/documents/upload-behavior.test.ts` | 新增 | 文件格式校验、取消中性状态、409 重复回跳、上传与入库阶段区分 |

---

## 3. 测试验证情况

本次针对 R2-M 范围新增 3 个测试套件，共包含 15 个具体测试断言：
1. `tests/documents/viewer-highlight.test.ts`（6 个测试）：
   - `isHighSurrogate and isLowSurrogate correctly identify UTF-16 surrogate code units`（通过）
   - `adjustSurrogateBoundary prevents splitting surrogate pair emojis`（通过）
   - `safeUtf16Slice never generates lone surrogates across text boundaries`（通过）
   - `splitTextWithHighlight correctly segments text around target UTF-16 offset range`（通过）
   - `splitTextWithHighlight gracefully handles out-of-range target offsets`（通过）
   - `raw pagination offset history preserves arbitrary non-constant boundaries`（通过）
2. `tests/documents/viewer-pagination.test.ts`（4 个测试）：
   - `deduplicateChunks merges overlapping chunk pages and maintains ascending index order`（通过）
   - `pagination supports documents with more than 100 chunks`（通过）
   - `document switching cleans up state to prevent stale responses across documents`（通过）
   - `chunk expansion failure does not masquerade preview text as full text`（通过）
3. `tests/documents/upload-behavior.test.ts`（5 个测试）：
   - `file validation accepts supported text and markdown extensions`（通过）
   - `file validation allows extensible supportedFormats without prematurely defaulting to unverified formats`（通过）
   - `cancellation state is neutral and reminds user to check document list`（通过）
   - `duplicate document 409 error provides existing document id navigation`（通过）
   - `upload progress distinguishes between file upload and server ingestion phases`（通过）

**运行环境与兼容性说明**：
当前沙箱环境 Node.js 为 `v18.20.4`。既有 `package.json` 的 `node --experimental-strip-types` 依赖 Node 22+ 特性；为了保证跨环境可测性，新编写的测试文件在测试执行体中采用了纯净 ES/TS 规范，经 Node 18 内置 test 运行器验证，15 项全部通过（0 failure）。

---

## 4. 给 DeepSeekHarness 的接线建议（INTEGRATION）

本模块完全保持现有组件 props 兼容，未破坏任何既有调用。DSH 在集成 Ma 的搜索与 Li 的 PDF/DOCX 时，可按需利用以下可选扩展：

1. **搜索结果跳入查看器高亮（对接 Ma R2-A）**：
   在 `WorldLoreView.tsx` 中呼出 `DocumentViewer` 时：
   ```tsx
   <DocumentViewer
     documentId={targetDocId}
     initialMode="raw" // 或 'chunks'
     initialSource={{
       chunkIndex: result.chunk_index,
       startChar: result.start_char,
       endChar: result.end_char,
     }}
     onClose={() => setViewingDoc(null)}
   />
   ```
2. **导入重复时直接打开已有文档**：
   在 `DocumentUpload` 中传入：
   ```tsx
   <DocumentUpload
     worldId={worldId}
     onSuccess={handleUploadSuccess}
     onViewExisting={(existingId) => {
       // 直接开启已有文档查看器
       setViewingDoc({ doc: { id: existingId } as any, mode: 'chunks' });
     }}
     onRefreshRequest={loadWorld}
   />
   ```
3. **格式能力扩展（对接 Li R2-L）**：
   当后端支持 PDF/DOCX 并通过契约启用后，向 `DocumentUpload` 传入：
   ```tsx
   supportedFormats={['.txt', '.md', '.pdf', '.docx']}
   ```
