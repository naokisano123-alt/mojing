# 墨境 M1 第二轮补充接口契约（R2）

> **版本**：`1.0-r2`（在 v0.2 之上**追加**，不改写历史契约）
> **状态**：冻结待实施。本文件先于代码；是否已实现以 `docs/reports/r2-integration.md` 为准。
> **作者**：DeepSeekHarness（R2-D）
> **分支**：`feat/r2-integration`
> **功能基线**：`c83eccb44734dd16ef6da8e0c2382eff4bed99df`（PR #1 合并后的 main）
> **本文件基线**：`3e900637389b0f9d6ae838d1d62eae0609d4ca34`（第二轮任务书提交）
> **依赖契约**：[`docs/contracts/M1_AB_API.md`](M1_AB_API.md) v0.2 —— 14 个既有端点语义不变。

---

## 0. 与 v0.2 的关系

| 类别 | 规则 |
| --- | --- |
| 既有端点 | 路径、方法、字段、错误码**一律不变**；v0.2 客户端（含 Gemini 已交付的 `src/api/**`）继续可用 |
| 新增字段 | 只做**可加性扩展**：新字段可缺失，前端必须容忍 `undefined` |
| 新格式 | PDF / DOCX 走**同一个上传端点**，通过 `media_type` 区分 |
| 新端点 | 只新增 `GET /api/v1/search`，不动其它路由 |
| 破坏性变更 | 不允许。若将来必须，另开 `/api/v2` |

本文件写清三件事：**PDF/DOCX 的语义**、**检索的线上形状**、**取消与事务的诚实边界**。

---

## 1. PDF / DOCX 支持

### 1.1 media_type

| 扩展名 | `media_type` |
| --- | --- |
| `.txt` | `text/plain` |
| `.md` | `text/markdown` |
| `.pdf` | `application/pdf` |
| `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |

`.doc`（旧二进制格式）**不支持**，返回 `415 UNSUPPORTED_MEDIA_TYPE`，提示"请另存为 .docx"。

### 1.2 `encoding` 的语义（关键）

v0.2 里 `encoding` 指**原始字节的解码方式**。PDF/DOCX 没有这个含义，必须显式区分，否则会把"提取出的文本"误报成"原文件的编码"。

新增字段 `encoding_semantics`：

| 取值 | 含义 | 出现的格式 |
| --- | --- | --- |
| `source_bytes` | `encoding` 是原始字节的解码方式 | TXT / MD |
| `extracted_text` | `encoding` 恒为 `utf-8`，表示**提取出的 Unicode 文本**，与原文件内部编码无关 | PDF / DOCX |

- PDF/DOCX 的 `encoding` 字段固定返回 `"utf-8"`
- 前端展示时不得对 PDF/DOCX 显示"已按 GB18030 读取"这类文案
- v0.2 客户端不认识 `encoding_semantics` 时，行为不受影响（只少一条提示）

### 1.3 `warnings`（新增，可加性）

`WorldDocument` 新增：

~~~ts
warnings: DocumentWarning[];   // TXT/MD 恒为 []

interface DocumentWarning {
  code: string;      // 稳定机器码，见下
  message: string;   // 中文，可直接展示
  count?: number;    // 同类问题的数量（如空页数）
}
~~~

| code | 含义 | 是否影响成功 |
| --- | --- | --- |
| `PARTIAL_TEXT` | 混合文档：部分页无可提取文本（扫描页），其余页已提取 | 成功，但必须提示"不完整" |
| `EMPTY_PAGES` | 存在空页，已跳过 | 成功 |
| `TABLES_FLATTENED` | 表格按行列转为纯文本，排版不保真 | 成功 |
| `HEADERS_FOOTERS_SKIPPED` | 页眉页脚未提取 | 成功 |
| `TEXTBOXES_SKIPPED` | 文本框/浮动对象未提取 | 成功 |
| `COLUMN_ORDER_UNCERTAIN` | 双栏版面，阅读顺序可能交错 | 成功，需提示 |

规则：

- `warnings` 非空 ≠ 失败；失败只走 4xx，且**不落库**
- 出现 `PARTIAL_TEXT` 时，UI 必须显式提示"部分内容未能提取"，不得显示成完整成功
- 扫描件**整份**无可提取文本 → 不是 warning，而是 `422 PDF_SCANNED`

### 1.4 限制（默认值，可由环境变量覆盖）

| 项 | TXT/MD | PDF | DOCX |
| --- | --- | --- | --- |
| 单文件字节 | 2 MiB（`MOJING_MAX_UPLOAD_BYTES`） | 8 MiB（`MOJING_MAX_DOC_BYTES`） | 8 MiB（同上） |
| 页数 | — | ≤ 200（`MOJING_MAX_PDF_PAGES`） | — |
| 解压后字节 | — | — | ≤ 100 MiB（`MOJING_MAX_DOCX_UNZIPPED`） |
| 压缩包条目 | — | — | ≤ 2000 |
| 提取文本 | — | ≤ 2,000,000 UTF-16 单位（`MOJING_MAX_TEXT_UNITS`） | 同左 |
| 每世界文档数 | 20 | 20 | 20 |

> 说明：2 MiB → 8 MiB 是**集成方（DSH）记录的一次配置决策**，不是解析模块自行放宽；TXT/MD 上限保持 v0.2 不变。

### 1.5 错误码（新增，均为 4xx 且不落库）

| HTTP | code | 触发 | details |
| --- | --- | --- | --- |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | 扩展名不在四类内（含 .doc / .rtf / .epub） | `{ "allowed": [".txt",".md",".pdf",".docx"] }` |
| 413 | `FILE_TOO_LARGE` | 超过对应格式上限 | `{ "max_bytes": 8388608, "received_bytes": ... }` |
| 422 | `PDF_ENCRYPTED` | 加密 / 需要密码 | — |
| 422 | `PDF_CORRUPTED` | 结构损坏、无法打开 | — |
| 422 | `PDF_SCANNED` | 整份无可用文本（纯扫描件） | `{ "hint": "本批不做 OCR，请提供文本版 PDF 或转成 TXT/MD" }` |
| 422 | `DOCX_INVALID` | 不是有效 docx（含伪装的 zip） | — |
| 422 | `DOCX_PROTECTED` | 文档受保护 / 加密 | — |
| 422 | `EXTRACTION_LIMIT` | 页数或文本量超限 | `{ "field": "pages", "limit": 200, "actual": 312 }` |
| 422 | `ARCHIVE_BOMB` | 解压后体积或条目数异常 | `{ "max_unzipped": 104857600 }` |
| 422 | `EMPTY_DOCUMENT` | 提取结果为空（同 v0.2） | — |

### 1.6 提取管线（不变的部分）

```text
bytes → 格式判定 → 提取纯文本（parsers） → normalize（CRLF/BOM） → chunk_text（确定性规则） → 入库
```

- **规范化与分块沿用 v0.2 的 `importer.py`**，PDF/DOCX 不另建一套分块
- `start_char` / `end_char` 仍是**提取后规范化文本**的 UTF-16 偏移，**不是** PDF 页内坐标
- PDF 页码若在块上保留，字段为可选 `page_range: [start, end] | null`（1 起，闭区间）；本批允许恒为 `null`，不得为了凑字段编造页码
- 表格按行列转文本，单元格之间用 ` | ` 分隔、行间用换行；这是"可读"而非"还原排版"

### 1.7 前端能力开关

- 上传组件的 `accept` 与提示**只能列出后端实际已启用的格式**
- 后端新增 `GET /api/v1/capabilities`（可加性，本批新增）：

~~~json
{ "formats": [".txt", ".md", ".pdf", ".docx"], "max_bytes": { ".txt": 2097152, ".pdf": 8388608 }, "search": true }
~~~

- 前端启动时可拉取一次；拉不到时降级为 TXT/MD 并**不显示** PDF/DOCX 入口
- 这条是为了避免"UI 写着支持 PDF，实际后端没装解析依赖"

---

## 2. 检索接口 `GET /api/v1/search`

### 2.1 请求

```text
GET /api/v1/search?world_id=<uuid>&q=<关键词>&limit=20&offset=0
```

| 参数 | 必填 | 约束 |
| --- | --- | --- |
| `world_id` | 是 | UUID v4；不存在或已删除 → `404 WORLD_NOT_FOUND` |
| `q` | 是 | 去首尾空白后 1–100 个字符；空 → `422 SEARCH_QUERY_INVALID` |
| `limit` | 否 | 1–50，默认 20 |
| `offset` | 否 | ≥ 0，默认 0 |

匹配规则（第一版，确定性、无分词）：

- 按**字面**子串匹配，大小写对 ASCII 不敏感，对中文无影响
- `%`、`_`、引号、SQL 片段一律按字面处理（参数化查询，不做 LIKE 通配）
- 不做同义词、拼音、繁简转换、语义扩展；不承诺"分词级"召回
- 匹配范围：块正文 `chunks.text`（标题行若在块内，自然也命中）

### 2.2 响应

~~~json
{
  "query": "渡云港",
  "items": [
    {
      "world_id": "0f3b...",
      "document_id": "5c2a...",
      "document_filename": "云山纪.md",
      "chunk_id": "e71a...",
      "chunk_index": 3,
      "heading_path": ["地理与气候", "渡云港"],
      "start_char": 4120,
      "end_char": 5312,
      "snippet": "……位于山脚第一重云海旁，是凡间商贾与山民唯一的交汇之所……",
      "match_start": 96,
      "match_end": 99
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
~~~

字段语义（**这是本轮最容易搞错的地方，逐条固定**）：

| 字段 | 单位/基准 | 说明 |
| --- | --- | --- |
| `start_char` / `end_char` | **全文**、UTF-16 | 该块在文档正文中的区间，与 v0.2 的 `LoreChunk` 一致 |
| `match_start` / `match_end` | **块内**、UTF-16 | 命中在 `chunk.text` 内的区间；标题命中或无法定位时为 `null` |
| `snippet` | 字符串 | 摘要，**不是全文**；命中处前后各保留 ≤40 个 UTF-16 单位，两端截断处用 `…` 标记 |
| `heading_path` | 字符串数组 | 与 `LoreChunk.heading_path` 同义 |

- `snippet` 与 `match_*` 不可互相推导：`match_*` 相对块，`snippet` 是裁剪后的展示文本
- 前端需要高亮时，用 `chunk_id` 取整块文本 + `match_*`；用 `start_char` 回到全文时按全文坐标

### 2.3 排序与分页

- 稳定排序：`documents.created_at ASC, chunks.chunk_index ASC`
- `total` 为该世界命中块总数；`offset` 超出返回空 `items`
- 同一查询重复请求结果一致（无随机权重）

### 2.4 隔离与删除

- 只查未删除世界下、未删除文档的块
- 世界被软删除 → 该世界 `404`；文档被软删除 → 结果中消失
- 不跨世界：`world_id` 必填且强过滤

### 2.5 错误

| HTTP | code | 场景 |
| --- | --- | --- |
| 404 | `WORLD_NOT_FOUND` | 世界不存在/已删除 |
| 422 | `SEARCH_QUERY_INVALID` | `q` 为空、全空白或超 100 字符 |
| 422 | `INVALID_RANGE` | `limit`/`offset` 越界 |
| 503 | `DB_UNAVAILABLE` | 数据库不可用 |

### 2.6 性能立场（不吹牛）

本轮基线是**字面子串扫描**，适用于单世界 ≤20 文档 × ≤2 MiB 的小规模资料库。不承诺毫秒级，也不承诺"海量"。后续如引入 FTS5/trigram，必须保留无扩展环境的降级路径，且索引更新与软删除保持一致。

---

## 3. 取消与事务：把 v0.2 的模糊处说清楚

v0.2 未明确"客户端取消是否保证服务端未提交"。本轮修正为诚实口径：

| 事实 | 契约表述 |
| --- | --- |
| 客户端取消 `AbortController` | 只是**停止等待响应**；服务端可能已经完成解析与提交 |
| 上传成功的判定 | 只有收到 `201` 才代表已入库 |
| 取消后的正确做法 | UI 提示"已停止等待，结果可能已保存，请刷新列表核对"，并**刷新文档列表** |
| 重试 | 同一文件重试不会产生第二份：服务端按 `(world_id, content_sha256)` 去重返回 `409`（唯一例外：显式 `allow_duplicate=true`） |
| 服务端 | 单事务写入文档 + 全部分块；异常即回滚，不留下"半份文档" |

**禁止**在 UI 或报告里写"取消后服务端一定没有保存"——这一点无法从客户端保证。

---

## 4. 模块拼装接口（三人对齐用，不是 HTTP）

这些是模块边界，由集成方（DSH）接线，不是对外 API。列在这里是为了三人不要各自造一套。

### 4.1 解析（Li / R2-L）

~~~python
def extract_document(data: bytes, filename: str, *, limits: ParseLimits) -> ParsedDocument: ...

@dataclass
class ParsedDocument:
    text: str
    media_type: str
    encoding: str          # PDF/DOCX 恒为 "utf-8"
    warnings: list[Warning]

@dataclass
class ParseLimits:
    max_pages: int = 200
    max_text_units: int = 2_000_000
    max_unzipped_bytes: int = 100 * 1024 * 1024
    max_zip_entries: int = 2000
~~~

- 不访问数据库、不生成 chunk、不定义第二套偏移
- 失败抛模块异常，由 DSH 映射到 1.5 的错误码

### 4.2 检索（Ma / R2-A）

~~~python
def search_chunks(conn, world_id: str, query: str, limit: int = 20, offset: int = 0) -> dict: ...
~~~

- 返回结构即 2.2 的响应体（`query/items/total/limit/offset`）
- 连接由调用方注入；不私建数据库、不提交事务

### 4.3 前端面板（Ma）与查看器（Marcin）

~~~ts
interface LoreSearchPanelProps {
  worldId: string;
  search: (params: { worldId: string; query: string; limit: number; offset: number; signal?: AbortSignal }) => Promise<SearchResponse>;
  onOpenSource: (target: { documentId: string; chunkIndex: number; startChar: number; endChar: number }) => void;
}

interface DocumentViewerProps {
  documentId: string;
  initialMode?: 'chunks' | 'raw';
  initialSource?: { startChar: number; endChar: number };  // 可选，UTF-16 全文坐标
  onClose: () => void;
}
~~~

- 两者都不依赖未上线的 HTTP 端点：`search` 由 DSH 用公共 client 注入
- 类型放各自目录，由 DSH 写薄适配层，避免同时改 `src/api/types.ts`

---

## 5. 迁移、回归与验收

| 项 | 要求 |
| --- | --- |
| 数据库 | 本轮**原则上不加表/列**；若必须加，迁移幂等、有版本号、先备份，禁止 DROP 重建 |
| 旧数据 | 既有 `worlds/documents/chunks` 行继续可读；`warnings` 缺省视为 `[]` |
| 旧客户端 | v0.2 客户端不传新参数、不读新字段，功能不受影响 |
| 回归 | 既有 22 前端 + 37 后端用例不得删除；新增用例另行叠加 |
| 验收 | 建资料库 → 导入四种格式 → 检索 → 定位原文 → 删除后不可检索 → 重启仍保留；M0 手势与草稿单独回归 |

---

## 6. 变更记录

| 版本 | 日期 | 变更 | 作者 |
| --- | --- | --- | --- |
| 1.0-r2 | 2026-11-08 | 初版：PDF/DOCX 语义与错误码、`/search` 与来源字段、取消/事务口径、模块拼装接口 | DeepSeekHarness |
