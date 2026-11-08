# 墨境 M1 第三轮补充接口契约（R3）

> **版本**：`1.0-r3`（在 v0.2 与 `1.0-r2` 之上**追加**；旧端点语义不变）
> **状态**：冻结待实施；实施状态以 `docs/reports/r3-integration.md` 为准
> **作者**：DeepSeekHarness（R3-D）
> **分支**：`feat/r3-integration`
> **功能基线**：`87b5b2fed12fad648eaba1930c3968a47a1665df`（PR #2 合并后的 main）
> **本文件基线**：`2540a64`（第三轮任务书提交）

---

## 0. 本文件解决什么

第三轮聚焦两件事：**M1-D 人工条目的前后端闭环**，以及**关闭第二轮遗留的 B1–B8 缺口**。本文件只写可加性内容：

| 类别 | 处理 |
| --- | --- |
| 既有端点 | 路径/方法/字段/错误码不变；R2 客户端继续可用 |
| 新增字段 | 一律可选；前端必须容忍缺失 |
| 新增端点 | 世界内条目 CRUD（5 个）；不新增其它路由 |
| 语义修正 | 检索排序/命中字段、逐格式限额、UTF-16 计法，**以本文件为准** |

---

## 1. 人工设定条目（M1-D）

### 1.1 模型

> 来源以纯文本形式保存
>
> ```ts
> interface LoreEntry {
>   id: string;                 // UUID v4
>   world_id: string;
>   type: 'character' | 'location' | 'faction' | 'rule' | 'other';
>   title: string;              // 1–80，去首尾空白
>   summary: string;            // 0–500，人工填写，不做 AI 抽取
>   status: 'draft' | 'confirmed';
>   revision: number;           // 初值 1，每次**成功**更新 +1
>   origin: 'manual';           // 本批恒为 manual
>   source_links: LoreEntrySource[];
>   needs_source_review: boolean;  // 只做提示，不等于事实真伪判定
>   created_at: string;
>   updated_at: string;
>   deleted_at: string | null;
> }
>
> interface LoreEntrySource {
>   document_id: string;
>   chunk_id: string;
>   start_char: number;         // **全文** UTF-16，半开区间 [start, end)
>   end_char: number;
>   excerpt: string;            // 服务端从真实块文本生成，≤120 字符
>   availability: 'active' | 'deleted' | 'unavailable';
> }
> ```

规则：

- **同名不同条目允许**，服务端不做合并、不做去重
- **无来源的人工条目合法**：`source_links: []`，UI 必须标明"人工录入 · 无文档引用"
- `needs_source_review`：当且仅当 `status === 'confirmed'` 且存在 `availability !== 'active'` 的来源时为 `true`
- 无来源的 manual 条目 **不会** 因此被判失效

### 1.2 来源校验（服务端强制）

写入 `source_links` 时逐条核验，任一失败整单拒绝：

| 检查 | 失败码 |
| --- | --- |
| `document_id` 属于同 `world_id` 且未软删 | `SOURCE_CROSS_WORLD` / `SOURCE_DELETED` |
| `chunk_id` 属于该 document 且未删 | `SOURCE_CHUNK_MISMATCH` |
| `0 <= start_char < end_char` 且 `end_char <= chunk.end_char - chunk.start_char`（块内相对全文区间之内） | `SOURCE_OUT_OF_RANGE` |
| 起止不能切开代理对（UTF-16 边界合法） | `SOURCE_INVALID_OFFSET` |
| `excerpt` 不接受客户端提供 | 忽略并按真实文本生成 |

`excerpt` 生成规则：取 `[start_char, end_char)` 对应文本，≤120 个 UTF-16 单位，两端截断加 `…`。

### 1.3 端点

| # | 方法 | 路径 | 说明 |
| --- | --- | --- | --- |
| 1 | GET | `/api/v1/worlds/{world_id}/lore-entries` | 列表（筛选 + 分页） |
| 2 | POST | `/api/v1/worlds/{world_id}/lore-entries` | 新建 |
| 3 | GET | `/api/v1/lore-entries/{entry_id}` | 详情 |
| 4 | PATCH | `/api/v1/lore-entries/{entry_id}` | 更新（乐观并发） |
| 5 | DELETE | `/api/v1/lore-entries/{entry_id}` | 软删除（乐观并发） |

**列表**：`?type=&status=&limit=20&offset=0`，`type` / `status` 可省略表示不过滤；排序 `updated_at DESC, id ASC`（稳定）；响应 `{items,total,limit,offset}`。

**新建**：body `{type,title,summary,status,source_links:[{document_id,chunk_id,start_char,end_char}]}` → `201` 返回完整条目（`revision: 1`）。

**更新**：body 任意子集 + **必填** `expected_revision`。未传的字段保持不变。成功返回 `revision + 1`。

**删除**：`DELETE /api/v1/lore-entries/{entry_id}?expected_revision=N` → `204`；软删除，重复删除同一 revision 仍 `204`（幂等），revision 不匹配则 `409`。

### 1.4 冲突与错误码

| HTTP | code | 场景 | details |
| --- | --- | --- | --- |
| 404 | `LORE_ENTRY_NOT_FOUND` | 不存在或已删除 | — |
| 404 | `WORLD_NOT_FOUND` | 世界不存在/已删 | — |
| 409 | `LORE_ENTRY_CONFLICT` | `expected_revision` 与当前不符 | `{ "expected_revision": 2, "current_revision": 3 }` |
| 422 | `SOURCE_CROSS_WORLD` | 来源文档不属于该世界 | `{ "document_id": ... }` |
| 422 | `SOURCE_DELETED` | 来源文档已软删 | `{ "document_id": ... }` |
| 422 | `SOURCE_CHUNK_MISMATCH` | chunk 不属于该文档 | `{ "chunk_id": ... }` |
| 422 | `SOURCE_OUT_OF_RANGE` | 区间越界或反向 | `{ "start_char", "end_char", "chunk_length" }` |
| 422 | `SOURCE_INVALID_OFFSET` | 切开代理对 | `{ "start_char", "end_char" }` |
| 422 | `VALIDATION_ERROR` | 标题/摘要/枚举非法 | `{ "field": "title" }` |

**没有来源时 `source_links` 传 `[]`，不是 `null`。**

### 1.5 来源生命周期

- 文档软删除后：条目保留，`source_links[].availability = 'deleted'`
- 块无法定位（数据异常）：`'unavailable'`
- 已 confirmed 且存在非 active 来源 → `needs_source_review: true`（**只提示复核**）
- 世界整体软删除后：该世界下条目不可读写（`404 WORLD_NOT_FOUND`），数据保留
- **不物理删除条目**，不做 purge

---

## 2. 第二轮缺口的语义修正

### 2.1 逐格式限额（关闭 B1 / B2）

`GET /api/v1/capabilities` 的 `max_bytes` 必须**逐格式**给出真实值：

~~~json
{
  "formats": [".txt", ".md", ".pdf", ".docx"],
  "max_bytes": { ".txt": 2097152, ".md": 2097152, ".pdf": 8388608, ".docx": 8388608 },
  "search": true,
  "lore_entries": true,
  "parsers": { "pdf": true, "docx": true }
}
~~~

行为要求：

- 上传入口**先按扩展名确定上限，再按该上限限量读取**；不能先按 2 MiB 读完再判格式（B1）
- 前端按**后缀**取对应上限传给上传组件；不得用"是否启用 PDF"决定所有格式的上限（B2）
- 解析依赖缺失时，`formats` 与 `lore_entries`/解析能力必须如实反映；不允许"声称支持但 import 就崩"

### 2.2 文本量按 UTF-16 计（B3）

- `max_text_units` 指**规范化后正文的 UTF-16 code unit 数**，不是 Python `len()`
- 主管线在写入前做最终守门：超过上限 → `422 EXTRACTION_LIMIT`，`details: { "limit": N, "actual": M }`
- 解析模块内部计数可以有自己的实现，但不得与这条冲突

### 2.3 检索语义（关闭 B7 / B8）

响应每项新增 **`match_field`**：

| 值 | 含义 |
| --- | --- |
| `text` | 命中块正文 |
| `heading` | 命中标题路径 |
| `filename` | 命中文档名 |

- `match_start` / `match_end`：**仅当 `match_field === 'text'` 时**为块内 UTF-16 区间；`heading` / `filename` 命中时为 `null`——**不允许补 0 或编造区间**
- 排序（冻结）：`match_field` 优先级 `text(1) > heading(2) > filename(3)`，然后 `document_id ASC, chunk_index ASC`
- `total` 与分页稳定；同一 query 重复请求结果一致
- 大小写：ASCII 不区分；中文按字面；不做分词、不做繁简转换

### 2.4 警告结构化（B4 的契约面）

`WorldDocument.warnings` 保持数组，元素允许两种形状：

~~~ts
interface DocumentWarning {
  code: string;         // 机器码，优先使用
  message: string;      // 中文
  count?: number;
}
~~~

- 解析模块应直接给出机器码（`PARTIAL_TEXT` / `EMPTY_PAGES` / `TABLES_FLATTENED` / `HEADERS_FOOTERS_SKIPPED` / `TEXTBOXES_SKIPPED` / `COLUMN_ORDER_UNCERTAIN` / `EXTRACTION_WARNING`）
- 主管线**优先使用模块提供的 code**；仅在模块只给字符串时做兜底映射，**禁止长期靠中文子串猜**
- `PARTIAL_TEXT` 必须在 UI 上醒目提示"部分内容未提取"

### 2.5 前端展示（B5）

- `DocumentViewer` 必须渲染 `warnings` 与 `encoding_semantics`
- `encoding_semantics === 'extracted_text'` 时不得显示"按 GB18030 读取"一类文案
- 警告按纯文本渲染，不注入 HTML

---

## 3. 迁移与兼容

| 项 | 要求 |
| --- | --- |
| 新表 | `lore_entries`、`lore_entry_sources`（由 Ma 的 `schema.py` 提供，DSH 在 `db.py` 调用） |
| 迁移 | 幂等、带版本、不 DROP；旧 `worlds/documents/chunks` 数据必须保留 |
| 旧客户端 | 不认识新字段/新端点也能正常工作 |
| 旧测试 | 不得删除既有前端 41 例、后端 82 例；新增用例另行叠加 |
| 演示数据 | `mojing-m0-v1` 结构与 M0 图鉴不受影响；条目**不写入** M0 图鉴或 AI 上下文 |

---

## 4. 模块拼装接口（对齐用，不是 HTTP）

~~~python
# Ma / R3-A
create_entry(conn, world_id, payload) -> dict
list_entries(conn, world_id, *, type=None, status=None, limit=20, offset=0) -> dict
get_entry(conn, entry_id) -> dict
update_entry(conn, entry_id, payload, expected_revision) -> dict
delete_entry(conn, entry_id, expected_revision) -> None
~~~

- 连接由调用方注入；**模块不 commit**
- `schema.py` 暴露幂等 DDL 与版本号

~~~ts
// Marcin / R3-M
interface LoreEntriesPanelProps {
  worldId: string;
  api: {
    list(params: { worldId: string; type?: string; status?: string; limit?: number; offset?: number; signal?: AbortSignal }): Promise<{ items: LoreEntry[]; total: number }>;
    get(entryId: string, signal?: AbortSignal): Promise<LoreEntry>;
    create(worldId: string, payload: LoreEntryDraft): Promise<LoreEntry>;
    update(entryId: string, payload: LoreEntryDraft, expectedRevision: number): Promise<LoreEntry>;
    remove(entryId: string, expectedRevision: number): Promise<void>;
    searchSources(params: { worldId: string; query: string; signal?: AbortSignal }): Promise<SourceCandidate[]>;
  };
  onOpenSource?: (target: { documentId: string; chunkIndex: number; startChar: number; endChar: number }) => void;
}
~~~

---

## 5. 变更记录

| 版本 | 日期 | 变更 | 作者 |
| --- | --- | --- | --- |
| 1.0-r3 | 2026-11-08 | 初版：M1-D 条目模型/端点/冲突/来源校验，B1–B8 的契约面修正 | DeepSeekHarness |
