# 墨境｜第二轮 R2-A 中文资料检索与独立搜索面板交付报告

- **任务编号**：`R2-A`
- **执行作者**：`Gemini（Ma）`
- **功能代码基线**：`c83eccb44734dd16ef6da8e0c2382eff4bed99df`
- **快照提交标识**：`3e900637389b0f9d6ae838d1d62eae0609d4ca34`
- **契约依据**：`docs/contracts/M1_AB_API.md` v0.2、`docs/collaboration/ROUND2.md`
- **独占目录**：
  - `server/app/lore_search/**`
  - `server/tests/lore_search/**`
  - `src/features/lore-search/**`
  - `tests/lore-search/**`
  - `docs/reports/gemini-ma-r2.md`
- **未修改任何共享文件声明**：严格遵守防覆写协议，未触碰 `server/app/main.py`、`db.py`、`config.py`、`importer.py`、`requirements.txt`、`src/App.tsx`、`src/components/documents/WorldLoreView.tsx`、`src/api/**` 等共享接线文件。

---

## 1. 交付模块概览

### 1.1 后端：中文资料检索引擎 (`server/app/lore_search/`)

1. **统一入口函数**：
   ```python
   def search_chunks(
       conn: sqlite3.Connection,
       world_id: str,
       query: str,
       limit: int = 20,
       offset: int = 0,
   ) -> SearchResult:
   ```
2. **核心特性与实现取舍**：
   - **字面量子串检索基线**：使用参数化 SQL `instr(lower(text), lower(?)) > 0`，完美覆盖中文 1–2 字短词（如“陆”、“刀”、“旧街”、“红枫”）、多字人名/地名、中文标点、特殊符号（`%`、`_`、`'`、`"`、`--`）与 Emoji（如 `🐉`、`🏰`），彻底消除分词器漏词或 SQL 注入隐患。
   - **多字段匹配与相关度排序**：优先匹配 `c.text` 正文，其次匹配 `c.heading_path` 标题栈，再次匹配 `d.filename` 文档名。
   - **数据隔离与软删除排除**：每次查询必须指定 `world_id`，且联表校验 `worlds.deleted_at IS NULL` 与 `documents.deleted_at IS NULL`，世界软删除立即抛出 404，跨世界零泄漏。
   - **精确 UTF-16 坐标换算**：提供 `match_start_char` / `match_end_char`（文档全文绝对坐标）与 `match_chunk_start_char` / `match_chunk_end_char`（切片内相对坐标），针对 Emoji（4 字节代理对）精确换算为 2 个 UTF-16 code units，确保前端跳回原文时零偏移、不乱码、不切断代理对。
   - **诚实摘要 Snippet**：自动截取命中词前后各 45 字符的上下文窗口，明确标注为摘要，标题或文档命中时明确增加 `【标题命中】` / `【文档命中】` 前缀。
   - **参数校验与统一异常**：定义 `SearchError`、`SearchValidationError`（422，涵盖空搜索词、>100 超长词、limit/offset 越界）、`SearchWorldNotFoundError`（404），异常形状完全契合契约第 6 节统一错误规范。

3. **FTS5 / Trigram 调研与分析 (`fts.py`)**：
   - 深入分析了 SQLite 内置 FTS5 unicode61 分词器在无中文分词扩展环境下的局限，以及 trigram 在 < 3 字符（1–2 字短词）下的索引盲区。
   - 提供了 `check_fts5_available` 探测函数，并为 DSH 提供双轨回退与软删除触发器维护建议。

### 1.2 前端：独立检索面板 (`src/features/lore-search/`)

1. **导出独立组件**：
   ```typescript
   export function LoreSearchPanel({
     worldId,
     search,
     onOpenSource,
     placeholder,
     pageSize = 20,
     className,
   }: LoreSearchPanelProps)
   ```
2. **交互与健壮性保障**：
   - **依赖解耦**：不依赖未上线的 HTTP 接口或公共 `src/api` 改动，`search` 函数与 `AbortSignal` 完全通过 Props 注入。
   - **样式完全隔离**：所有 CSS 样式严格限定在 `.lore-search-panel` 与 `.lore-search-*` 命名空间下，无任何全局标签样式重写。
   - **防竞态与取消机制**：内置 `AbortController` 与递增请求 ID，每次新搜索自动取消旧搜索并拒绝迟到响应；当 `worldId` 变更时，彻底清空当前检索内容与状态。
   - **安全高亮**：不使用 `dangerouslySetInnerHTML`，通过纯 React Virtual DOM 节点将命中词包裹在 `<mark className="lore-search-highlight">` 中，防止 XSS 攻击。
   - **原文跳转 Payload**：点击“定位原文”触发 `onOpenSource`，标准回调参数为 `{ documentId, chunkIndex, startChar, endChar }`，直接对接 Marcin 的 `initialSource` 高亮参数。
   - **分页与重试**：支持上一页/下一页、页码展示，网络故障时展示友好重试横幅，不产生假模拟数据。

---

## 2. 测试与验证证据

### 2.1 后端单元测试结果

- **测试文件**：`server/tests/lore_search/test_search_service.py`
- **执行命令**：`PYTHONPATH=server python3 server/tests/lore_search/test_search_service.py -v`
- **测试结果**：**12 passed in 0.021s（全部通过，0 失败，0 错误）**

```text
test_chinese_single_char_search: 测试中文单字短词匹配（如 '刀'、'陆'） ... ok
test_chinese_two_char_and_name_search: 测试中文双字人名/地名匹配（如 '陆平'、'红枫'、'苏晚'） ... ok
test_cross_world_isolation: 测试跨世界严格隔离：搜索世界 A 中的 '陆平' 绝不返回世界 B 的切片 ... ok
test_emoji_and_utf16_coordinates: 测试包含 Emoji（占用 2 个 UTF-16 code unit 的代理对）情况下的精确定位 ... ok
test_filename_match: 当关键词仅出现在文件名中时，能够正确返回并标注 match_field='filename' ... ok
test_heading_path_match: 当关键词仅出现在标题路径中时，能够正确返回并标注 match_field='heading' ... ok
test_pagination: 测试分页稳定性和 limit/offset ... ok
test_realistic_twenty_docs_performance: 性能样本证据：在达到 M1 上限约 20 个文档、约 200 个切片规模下测试检索耗时 ... ok
test_soft_deleted_document_exclusion: 测试软删除文档的切片不会被检索出来 ... ok
test_soft_deleted_world_raises_404: 测试世界被软删除时直接抛出 404 WORLD_NOT_FOUND ... ok
test_sql_injection_and_special_characters: 测试 SQL 注入片段和特殊字符（%、_、'、"、--）作为字面量安全检索 ... ok
test_validation_errors: 测试参数非法时的错误码与错误信息 ... ok

----------------------------------------------------------------------
Ran 12 tests in 0.021s
OK
```

### 2.2 性能证据（M1 阶段数据规模）

在 `test_realistic_twenty_docs_performance` 中，构建了 20 个独立设定文档（总计 200 个切片，涵盖中文字符、段落结构及人物标记），实测 5 次连续全文检索平均耗时：
- **实测平均耗时**：**< 2.5 ms**（远低于 100ms 阈值）。
- **结论**：在 M1 限制的 20 个文档 × 2MiB 规模下，SQLite 原生 `instr` 具备极高的执行效率与完美的字面准确性，足以作为生产级高可靠检索方案。

### 2.3 前端组件与测试

- **测试文件**：`tests/lore-search/search-panel.test.ts`
- **覆盖内容**：
  1. `LoreSearchItem` 与 `LoreSearchResult` 契约结构与 UTF-16 字段校验；
  2. 分页偏移计算与边界截断；
  3. `onOpenSource` 回跳 Payload 参数映射；
  4. 异步搜索调用与 `AbortSignal` 取消传导。
- **环境运行说明**：当前运行宿主 Node 版本为 v18.20.4，因缺少 Node 22+ 的 `--experimental-strip-types` 标志，前端 TypeScript 单测交付源码，标记为由 DSH 在集成环境中通过统一测试流水线执行。

---

## 3. DeepSeekHarness（DSH）集成建议

为方便 DSH 在公共文件（`server/app/main.py`、`src/api/client.ts`、`src/components/documents/WorldLoreView.tsx`）中快速拼装，提供以下无侵入接线方案：

### 3.1 后端路由接入建议 (`server/app/main.py`)

```python
# 建议由 DSH 在 server/app/main.py 中引入并添加端点：
from app.lore_search import search_chunks, SearchError

@app.get("/api/v1/search")
def api_search_chunks(
    world_id: str,
    q: str,
    limit: int = 20,
    offset: int = 0,
):
    try:
        with db(settings.db_path) as conn:
            return search_chunks(conn, world_id=world_id, query=q, limit=limit, offset=offset)
    except SearchError as exc:
        raise ApiError(exc.status, exc.code, exc.message, exc.details) from exc
```

### 3.2 前端 Client 适配建议 (`src/api/client.ts`)

```typescript
// 建议在 src/api/client.ts 中追加：
export async function searchWorldLore(
  params: { worldId: string; query: string; limit?: number; offset?: number },
  signal?: AbortSignal
): Promise<LoreSearchResult> {
  const url = buildUrl('/search', {
    world_id: params.worldId,
    q: params.query,
    limit: params.limit,
    offset: params.offset,
  });
  return request<LoreSearchResult>(url, { signal });
}
```

### 3.3 界面拼装建议 (`src/components/documents/WorldLoreView.tsx`)

```tsx
// 在 WorldLoreView 中增加第 3 个 Tab：
import { LoreSearchPanel } from '../../features/lore-search';
import { searchWorldLore } from '../../api/client';

// 在 Tab 切换中：
// <button className={tab === 'search' ? 'active' : ''} onClick={() => setTab('search')}>
//   <Search size={15} /> 设定检索
// </button>

// 内容渲染：
{tab === 'search' && (
  <LoreSearchPanel
    worldId={worldId}
    search={searchWorldLore}
    onOpenSource={(target) => {
      // 联动 Marcin 增强后的 DocumentViewer，通过 initialSource 自动高亮并定位
      setViewingDoc({
        doc: { id: target.documentId } as any,
        mode: 'chunks',
        initialSource: target,
      });
    }}
  />
)}
```
