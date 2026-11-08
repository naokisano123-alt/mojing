# 墨境 M1-A/B 最小接口契约

> **契约版本**：`0.1`（冻结待实施）
> **状态**：本文件先于代码提交。是否已实现、测试结果如何，一律以 `docs/reports/deepseekharness-m1-ab.md` 为准，本文件不代表功能已完成。
> **作者**：DeepSeekHarness
> **分支**：`feat/m1-ab-backend`
> **基线**：`a71342ad8a751ffb98adaaa102fe85691a0f9cf1`（任务书基线 `06088df` 之上仅新增协作文档，无业务代码变化）
> **适用**：M1-A 世界数据持久化、M1-B TXT/MD 真实导入与原文来源。

---

## 0. 这份契约给谁看

- **Gemini（GEM-02）**：按本契约实现 `src/api/**`、`src/components/documents/**`。第 4 节的路径与第 8.2 节的 TS 类型为冻结内容，可直接照写；第 9 节标注 `planned` 的接口**不得**在前端调用或伪造。
- **DeepSeekHarness（DSH-02/03）**：按本契约实现 `server/**` 并完成 `src/App.tsx`、`src/pages/WorldPages.tsx` 等共享文件的接线。
- **用户**：这里是本轮"真实导入"能做到什么、做不到什么的白纸黑字。

任何一方发现契约有误，先改契约、再改代码，并同步新快照；不允许两端各自形成不同版本。

---

## 1. 范围

### 本批实现（M1-A / M1-B）

| 能力 | 说明 |
| --- | --- |
| 世界持久化 | 创建、列表、详情、改名/改简介、软删除；服务重启后仍在 |
| 文档导入 | 真实上传 `.txt` / `.md`，服务端解析、保存原文、按标题与段落分块 |
| 原文与来源 | 读取全文（分页）、按块读取原文、每块带可回到原文的偏移与标题路径 |
| 解析状态 | 成功即 `ready`；失败返回 4xx 且**不落库**，不产生"看起来能用"的半成品 |

### 本批不实现（见第 9 节）

PDF / DOCX / OCR、语义抽取、LoreEntry CRUD、全文检索、真实 AI、API Key、正式冒险状态机、账户、云同步、多人。

**M0 演示必须继续可用**：本批不改 `mojing-m0-v1` 的 localStorage 结构与演示玩法。

---

## 2. 通用约定

### 2.1 基址与版本

- 开发环境：`http://127.0.0.1:8000/api/v1`
- 前端默认同源前缀 `/api`，由 Vite 代理转发（见 8.1）
- 所有路径以 `/api/v1` 开头；破坏性变更升 `/api/v2`

### 2.2 格式与字符编码

- 请求/响应体：`application/json; charset=utf-8`（上传接口除外）
- 上传：`multipart/form-data`，文件字段名固定为 `file`
- 文本一律 UTF-8 传输；数据库内部存 Unicode 字符串
- JSON 中中文不做 `\uXXXX` 转义（允许，但不要求）

### 2.3 标识

所有资源 ID 为 **UUID v4 小写字符串**（36 字符），由服务端生成，客户端不构造、不推测。

### 2.4 时间

ISO-8601 UTC，秒精度，带 `Z`：`2026-11-08T06:30:00Z`。客户端只做本地化展示，不参与比较。

### 2.5 分页

列表接口统一 `?limit=<1..200>&offset=<0..>`，默认 `limit=50, offset=0`，响应：

~~~json
{ "items": [], "total": 0, "limit": 50, "offset": 0 }
~~~

`offset` 超出总数时返回空 `items`，不是错误。

### 2.6 错误结构

所有非 2xx 响应体形状固定：

~~~json
{
  "error": {
    "code": "DUPLICATE_DOCUMENT",
    "message": "这个世界里已经导入过内容相同的文件。",
    "details": { "existing_document_id": "6f1c...", "content_sha256": "ab12..." }
  }
}
~~~

- `code`：稳定英文机器码（前端据此分支，不解析 `message`）
- `message`：中文，可直接展示给用户
- `details`：可选，结构见第 6 节
- 校验类错误（422）额外带 `details.field` 指出字段名

### 2.7 幂等与重复

- 世界创建**不**幂等：同名世界允许存在（用户可能故意建两个）
- 文档导入按 `(world_id, content_sha256)` 去重：重复返回 `409 DUPLICATE_DOCUMENT`
- 需要强制导入同一文件时加 `?allow_duplicate=true`，此时允许，两份文档独立存在
- 删除操作幂等：已删除对象再次 `DELETE` 返回 `204`

### 2.8 请求取消

- 客户端一律用 `AbortController`；取消后前端不得把请求当失败弹错误，也不得重试
- 上传被中断时服务端不落库（见 5.5）
- 服务端不提供"取消任务"接口（本批解析是同步的，毫秒到秒级）

### 2.9 CORS 与代理

- 开发期推荐：Vite `server.proxy['/api'] → http://127.0.0.1:8000`，前端用相对路径，**完全绕开 CORS**
- 直连时需要 `MOJING_CORS_ORIGINS` 逗号分隔白名单，默认 `http://localhost:5173,http://127.0.0.1:5173`
- 手机真机访问电脑的局域网地址（如 `http://192.168.1.8:5173`）时，要么继续用代理，要么把该 origin 加进白名单
- 不允许 `Access-Control-Allow-Origin: *` 配合凭据；本批没有凭据，仍按显式白名单处理

### 2.10 安全边界

上传内容**只作为资料**保存与展示：不执行、不求值、不拼接进任何指令模板、不当作系统提示。文件名只做展示与扩展名判定，落库前取 basename、去控制字符与路径分隔符。资料中出现"忽略以上指令"之类文本，行为不变。

---

## 3. 数据模型

### 3.1 关系

~~~text
World 1 ─── n WorldDocument 1 ─── n LoreChunk
  └────────── n LoreEntry（本批仅预留结构，不实现 CRUD）
                    └── n LoreEntrySource（指向 chunk 的区间，planned）
~~~

### 3.2 World

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `id` | string | UUID v4 |
| `name` | string | 去首尾空白后 1–40 字符 |
| `description` | string | 0–600 字符，允许空串 |
| `document_count` | int | 未删除文档数 |
| `created_at` / `updated_at` | string | ISO-8601 |
| `deleted_at` | string \| null | 软删除时间；普通接口不返回已删除世界 |

详情接口额外返回 `documents`（该世界的文档列表，按 `created_at` 升序）。

### 3.3 WorldDocument

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | UUID v4 |
| `world_id` | string | 所属世界 |
| `filename` | string | 原始文件名（basename），≤120 字符，保留扩展名 |
| `media_type` | string | `text/plain` \| `text/markdown` |
| `byte_size` | int | 原始字节数（BOM 计入） |
| `encoding` | string | 实际采用的解码：`utf-8` \| `utf-8-sig` \| `utf-16-le` \| `utf-16-be` \| `gb18030` |
| `content_sha256` | string | 原始字节的 SHA-256，hex 小写 64 位 |
| `text_length` | int | 规范化后正文长度，单位 **UTF-16 code unit** |
| `line_count` | int | 规范化后 `\n` 数 + 1 |
| `chunk_count` | int | 分块数 |
| `status` | string | 本批成功态恒为 `ready`；`parsing`/`failed` 为将来异步预留，当前不会出现 |
| `created_at` / `updated_at` | string | ISO-8601 |
| `deleted_at` | string \| null | 软删除 |

### 3.4 LoreChunk

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | UUID v4 |
| `document_id` | string | 所属文档 |
| `world_id` | string | 冗余，便于按世界查询 |
| `index` | int | 0 起连续序号，全文档唯一 |
| `heading_path` | string[] | 标题栈，如 `["第一章 雾港", "一、来信"]`；无标题时为 `[]` |
| `start_offset` | int | 块在正文中的起点（含），UTF-16 code unit |
| `end_offset` | int | 终点（不含） |
| `text_length` | int | `end_offset - start_offset` |
| `text` | string | `[start_offset, end_offset)` 的原文切片，服务端直接给出，前端无需自己切 |

**不变式（服务端必须自测）**：

1. `chunks[0].start_offset === 0`
2. `chunks[i].end_offset === chunks[i+1].start_offset`（无空隙、无重叠）
3. 末块 `end_offset === document.text_length`
4. 依次拼接 `text` 与全文逐字符相等
5. `index` 从 0 连续递增

### 3.5 LoreEntry（本批只冻结形状，不实现）

~~~ts
interface LoreEntry {
  id: string;
  world_id: string;
  type: 'character' | 'location' | 'faction' | 'rule' | 'other';
  title: string;          // 1–80
  summary: string;        // 0–500，人工维护
  status: 'draft' | 'confirmed';
  source_links: LoreEntrySource[];
  created_at: string;
  updated_at: string;
}

interface LoreEntrySource {
  document_id: string;
  chunk_id: string;
  start_offset: number;   // 相对文档正文，UTF-16
  end_offset: number;
  excerpt: string;        // ≤120 字符的原文摘录，便于列表展示
}
~~~

本批**不提供**任何 `/lore-entries` 端点。前端不得假设它存在；后续 M1-D 再冻结其 API。

---

## 4. 接口清单

| # | 方法 | 路径 | 说明 | 状态 |
| --- | --- | --- | --- | --- |
| 1 | GET | `/health` | 健康检查 | 本批 |
| 2 | POST | `/worlds` | 创建世界 | 本批 |
| 3 | GET | `/worlds` | 世界列表 | 本批 |
| 4 | GET | `/worlds/{world_id}` | 世界详情（含文档） | 本批 |
| 5 | PATCH | `/worlds/{world_id}` | 改名 / 改简介 | 本批 |
| 6 | DELETE | `/worlds/{world_id}` | 软删除世界 | 本批 |
| 7 | POST | `/worlds/{world_id}/documents` | 上传 TXT/MD 并解析 | 本批 |
| 8 | GET | `/worlds/{world_id}/documents` | 文档列表 | 本批 |
| 9 | GET | `/documents/{document_id}` | 文档元信息 | 本批 |
| 10 | GET | `/documents/{document_id}/raw` | 原文分页读取 | 本批 |
| 11 | GET | `/documents/{document_id}/chunks` | 分块列表 | 本批 |
| 12 | GET | `/documents/{document_id}/chunks/{index}` | 按序号取块 | 本批 |
| 13 | GET | `/chunks/{chunk_id}` | 按 ID 取块 | 本批 |
| 14 | DELETE | `/documents/{document_id}` | 软删除文档 | 本批 |
| 15 | GET | `/search` | 全文检索 | planned（M1-E） |
| 16 | GET/POST | `/lore-entries` | 条目 CRUD | planned（M1-D） |

### 4.1 GET /health

~~~json
{ "ok": true, "version": "0.1.0", "db": "ok" }
~~~

数据库不可用返回 `503`，`{"ok": false, "db": "error"}`。

### 4.2 POST /worlds

请求：

~~~json
{ "name": "潮汐档案馆", "description": "一座建在退潮线上的档案馆。" }
~~~

响应 `201`：

~~~json
{
  "id": "0f3b2c1a-9d4e-4a71-8c55-2b1e6a7d9f10",
  "name": "潮汐档案馆",
  "description": "一座建在退潮线上的档案馆。",
  "document_count": 0,
  "created_at": "2026-11-08T06:30:00Z",
  "updated_at": "2026-11-08T06:30:00Z",
  "deleted_at": null
}
~~~

错误：`422 WORLD_NAME_INVALID`（空名或超 40 字）、`422 VALIDATION_ERROR`。

### 4.3 GET /worlds

`?limit&offset`，按 `created_at` 升序，不含 `documents` 字段。

~~~json
{ "items": [ { "id": "...", "name": "...", "description": "...", "document_count": 2, "created_at": "...", "updated_at": "...", "deleted_at": null } ], "total": 1, "limit": 50, "offset": 0 }
~~~

### 4.4 GET /worlds/{world_id}

返回 World + `documents`（每个文档为 3.3 的完整元信息，不含 `text`）。

`404 WORLD_NOT_FOUND`：不存在或已删除。

### 4.5 PATCH /worlds/{world_id}

请求体字段均可选，只改传入的：

~~~json
{ "name": "潮汐档案馆 · 修订", "description": "新的简介" }
~~~

响应 `200` 同 4.2 形状。空对象 `{}` 合法（返回原样），不做无意义写入。

### 4.6 DELETE /worlds/{world_id}

`204`，软删除世界及其文档与分块（`deleted_at` 置位，行保留以便将来恢复）。重复删除仍 `204`。

### 4.7 POST /worlds/{world_id}/documents

`multipart/form-data`，字段 `file`；可选 query `allow_duplicate=true`。

限制（服务端可配，默认值即验收值）：

| 项 | 默认 |
| --- | --- |
| 单文件大小 | 2 MiB（2 097 152 字节） |
| 每世界未删除文档数 | 20 |
| 扩展名 | `.txt` / `.md`，大小写不敏感 |
| 文件名长度 | ≤120 字符 |

响应 `201`（成功即解析完成，`status: "ready"`）：

~~~json
{
  "id": "5c2a...",
  "world_id": "0f3b...",
  "filename": "潮汐档案馆设定.md",
  "media_type": "text/markdown",
  "byte_size": 48213,
  "encoding": "utf-8",
  "content_sha256": "9f2c...",
  "text_length": 23814,
  "line_count": 612,
  "chunk_count": 14,
  "status": "ready",
  "created_at": "2026-11-08T06:31:10Z",
  "updated_at": "2026-11-08T06:31:10Z",
  "deleted_at": null
}
~~~

错误：见 5.2、5.5、6。

### 4.8 GET /worlds/{world_id}/documents

`?limit&offset&include_deleted=false`，按 `created_at` 升序。

### 4.9 GET /documents/{document_id}

单条文档元信息（3.3 形状）。已删除文档默认 `404 DOCUMENT_NOT_FOUND`。

### 4.10 GET /documents/{document_id}/raw

`?offset=0&limit=20000`，单位 UTF-16 code unit。

~~~json
{
  "document_id": "5c2a...",
  "encoding": "utf-8",
  "offset": 0,
  "length": 20000,
  "total_length": 23814,
  "text": "\n# 潮汐档案馆\n\n……",
  "next_offset": 20000
}
~~~

末页 `next_offset` 为 `null`。`offset > total_length` 或 `limit > 50000` 返回 `422 INVALID_RANGE`。

> 设计说明：原文分页是为了手机端不一次渲染 2 MiB 正文；默认 `limit=20000` 约等于 2 万汉字以内一次读完。

### 4.11 GET /documents/{document_id}/chunks

`?limit=50&offset=0`，按 `index` 升序。

~~~json
{
  "items": [
    {
      "id": "e71a...",
      "document_id": "5c2a...",
      "world_id": "0f3b...",
      "index": 0,
      "heading_path": ["第一章 雾港"],
      "start_offset": 0,
      "end_offset": 1372,
      "text_length": 1372,
      "text": "# 第一章 雾港\n\n潮水退去的时候……"
    }
  ],
  "total": 14, "limit": 50, "offset": 0
}
~~~

### 4.12 GET /documents/{document_id}/chunks/{index}

按序号直达，供"跳转到第 N 块/章节"。越界 `404 CHUNK_NOT_FOUND`。

### 4.13 GET /chunks/{chunk_id}

单块，形状同 4.11 的 item。用于来源回跳：前端拿到 `chunk_id` 后既可直接展示 `text`，也可据 `start_offset` 调 `/raw` 取上下文。

### 4.14 DELETE /documents/{document_id}

`204` 软删除；世界 `document_count` 随之减少；重复删除 `204`。

本批无 LoreEntry，因此不存在"悬空引用"；M1-D 实现条目时，此接口需同步维护 `source_links`（契约到那时再补，不改本节语义）。

---

## 5. 导入与解析规则

### 5.1 接受与拒绝

| 情况 | 结果 |
| --- | --- |
| `.txt` / `.md`，UTF-8 | 接受 |
| `.pdf` / `.docx` / 其他扩展名 | `415 UNSUPPORTED_MEDIA_TYPE`，message 注明"本批只支持 TXT/Markdown" |
| 超过 2 MiB | `413 FILE_TOO_LARGE`，details 带 `max_bytes` |
| 0 字节或全空白 | `422 EMPTY_DOCUMENT` |
| 含 NUL 字节且无 UTF-16 BOM | `415 BINARY_CONTENT`（提示可能是二进制文件改了扩展名） |
| 世界文档数已达 20 | `409 WORLD_DOCUMENT_LIMIT` |

### 5.2 编码策略（明确，不静默乱码）

探测顺序：

1. BOM：`EF BB BF` → `utf-8-sig`；`FF FE` → `utf-16-le`；`FE FF` → `utf-16-be`
2. 无 BOM：严格 UTF-8 解码（失败即不进第 3 步的 UTF-8）
3. 仍失败且服务端允许（`MOJING_ALLOW_GB18030=1`，默认开）：严格 GB18030 解码
4. 都失败 → `422 DECODE_FAILED`，`details: { "tried": ["utf-8", "gb18030"], "hint": "请另存为 UTF-8 后重新导入" }`

约束：

- 解码失败**不**落库、**不**生成替代字符（`\uFFFD`）文本
- 中文 Windows 记事本常见的 GBK 文本可被 GB18030 覆盖；`.md`/`.txt` 明确写 UTF-8 时优先走 UTF-8
- 响应的 `encoding` 字段如实反映实际解码方式，前端可提示"已按 GB18030 读取，如显示乱码请转存 UTF-8"

### 5.3 规范化

- CRLF、CR 统一为 `\n`
- 去除开头 BOM 字符
- 不裁剪正文首尾空白（保留作者排版），但全空白判定按 `text.strip()` 为空
- `text_length` 为规范化后正文的 UTF-16 code unit 数：Python 侧用 `len(text.encode('utf-16-le')) // 2`

### 5.4 分块算法（确定性，非 AI）

目标：块大小 600–1800 UTF-16 单位，硬上限 4000；边界尽量落在段落/标题处。

1. 按 `\n\n` 切段，保留段落之间的空行归属（后一段前导空行计入后一段）
2. 行首匹配 `^(#{1,6})\s+` 且不在围栏代码块（`~~~` 或三连反引号）内 → 视为标题：压掉该级及更深的 `heading_path`，压入新标题，并**在此开新块**
3. 累积段落直到长度超过 1800 → 收口成块
4. 单段超过 4000 → 在最近的 `。！？；\n` 处软切，仍保证连续覆盖
5. 每块 `start_offset` 取上一块 `end_offset`，`end_offset` 为收口位置，`text` 即原文切片
6. 空块不产出（相邻块之间不会存在纯空白块，空白归属于边界前一或后一块均可，但**必须**满足 3.4 的覆盖不变式）

> 边界归属（空白算哪块）允许实现自由，但覆盖不变式与 `text` 与原文一致是硬要求；测试会用"拼接 == 全文"来卡。

### 5.5 失败与回滚

- 解析在单个事务内完成：文档行 + 全部分块要么全写，要么全不写
- 任何 4xx 失败：库里**查不到**这条文档（不是 `failed` 状态，而是不存在）
- 上传中断（连接断开）：事务不提交
- 服务端 5xx 时不返回部分数据；前端展示"导入失败，请重试"

### 5.6 重复与删除

- 同一世界内 `content_sha256` 相同 → `409`，`details.existing_document_id` 指向已有文档，UI 应能"查看已有文档"而不是只弹错误
- `allow_duplicate=true` 时允许重复，两份独立
- 删除为软删除；文档列表默认不含已删除项；重复删除幂等返回 `204`

---

## 6. 错误码表

| HTTP | code | 触发 | details |
| --- | --- | --- | --- |
| 404 | `WORLD_NOT_FOUND` | 世界不存在或已删除 | — |
| 404 | `DOCUMENT_NOT_FOUND` | 文档不存在或已删除 | — |
| 404 | `CHUNK_NOT_FOUND` | 块不存在或序号越界 | `{ "index": 42 }` |
| 409 | `DUPLICATE_DOCUMENT` | 同世界内容重复 | `{ "existing_document_id", "content_sha256" }` |
| 409 | `WORLD_DOCUMENT_LIMIT` | 文档数达上限 | `{ "limit": 20 }` |
| 413 | `FILE_TOO_LARGE` | 超过 2 MiB | `{ "max_bytes": 2097152, "received_bytes": 5242880 }` |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | 扩展名不支持 | `{ "allowed": [".txt", ".md"] }` |
| 415 | `BINARY_CONTENT` | 二进制内容 | — |
| 422 | `WORLD_NAME_INVALID` | 名字为空或超长 | `{ "field": "name", "max_length": 40 }` |
| 422 | `EMPTY_DOCUMENT` | 空文件 | — |
| 422 | `DECODE_FAILED` | 编码无法识别 | `{ "tried": [...] , "hint": "..." }` |
| 422 | `INVALID_RANGE` | 分页/偏移越界 | `{ "field": "offset" }` |
| 422 | `VALIDATION_ERROR` | 其他请求体校验失败 | `{ "field": "..." }` |
| 500 | `INTERNAL_ERROR` | 未预期异常 | 不返回堆栈、不返回路径 |
| 503 | `DB_UNAVAILABLE` | 数据库不可用 | — |

前端映射建议见 8.3。

---

## 7. 演示数据与真实数据的隔离

| 维度 | M0 演示 | M1 真实 |
| --- | --- | --- |
| 存储 | 浏览器 `localStorage['mojing-m0-v1']` | 后端 SQLite `server/data/mojing.db` |
| 世界来源 | `initialState()` 预置两卡 | `POST /worlds` 创建 |
| 能否驱动冒险 | 演示回合（关键词） | **不能**，本批只有资料管理 |
| 生命周期 | 清站点数据即消失 | 服务端持久化 |

规则：

1. 不迁移、不合并、不把演示世界自动写进后端
2. 不改变 `mojing-m0-v1` 的任何既有字段形状；演示进度必须原样可读
3. UI 必须能一眼区分：演示世界标"演示"，真实世界标"资料库 · 尚不能驱动 AI 冒险"
4. 后端不可达时，真实世界区域显示错误与重试；**不得**回退成模拟数据假装成功
5. 真实世界暂不进入故事页；"进入"按钮若保留，必须明确提示本批未开放该能力

---

## 8. 前端接入约定

### 8.1 地址配置

- 环境变量 `VITE_API_BASE_URL`，默认空字符串（同源 `/api`）
- 开发期 `vite.config.ts` 增加：`server.proxy['/api'] = 'http://127.0.0.1:8000'`
- 组件内**禁止**硬编码 `127.0.0.1`、端口或局域网 IP；禁止把地址写进 `src/pages/**`
- 不在 localStorage/sessionStorage 存任何后端凭据（本批无凭据）

### 8.2 TS 类型（冻结，可照抄）

~~~ts
export type IsoTime = string;
export type Uuid = string;

export interface ApiErrorBody {
  error: { code: string; message: string; details?: Record<string, unknown> };
}

export interface WorldSummary {
  id: Uuid;
  name: string;
  description: string;
  document_count: number;
  created_at: IsoTime;
  updated_at: IsoTime;
  deleted_at: IsoTime | null;
}

export type DocumentStatus = 'ready' | 'parsing' | 'failed';
export type DocumentEncoding =
  | 'utf-8' | 'utf-8-sig' | 'utf-16-le' | 'utf-16-be' | 'gb18030';

export interface WorldDocument {
  id: Uuid;
  world_id: Uuid;
  filename: string;
  media_type: 'text/plain' | 'text/markdown';
  byte_size: number;
  encoding: DocumentEncoding;
  content_sha256: string;
  text_length: number;
  line_count: number;
  chunk_count: number;
  status: DocumentStatus;
  created_at: IsoTime;
  updated_at: IsoTime;
  deleted_at: IsoTime | null;
}

export interface WorldDetail extends WorldSummary {
  documents: WorldDocument[];
}

export interface LoreChunk {
  id: Uuid;
  document_id: Uuid;
  world_id: Uuid;
  index: number;
  heading_path: string[];
  start_offset: number;
  end_offset: number;
  text_length: number;
  text: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface RawTextPage {
  document_id: Uuid;
  encoding: DocumentEncoding;
  offset: number;
  length: number;
  total_length: number;
  text: string;
  next_offset: number | null;
}
~~~

### 8.3 错误映射（UI 文案由组件层决定，服务端 message 可直接用）

| code | 建议交互 |
| --- | --- |
| `DUPLICATE_DOCUMENT` | 提示 + "查看已有文档"按钮（用 `details.existing_document_id`） |
| `DECODE_FAILED` | 展示 `details.hint`，提供"另存为 UTF-8"说明 |
| `FILE_TOO_LARGE` | 展示上限，不自动重试 |
| `UNSUPPORTED_MEDIA_TYPE` | 在选文件阶段就拦（accept=`.txt,.md`），服务端错误作为兜底 |
| `WORLD_DOCUMENT_LIMIT` | 提示上限，引导删除旧文档 |
| 网络/5xx | "暂时连不上服务"，可重试；**不得**显示成功 |

### 8.4 取消与超时

- 列表/详情：`AbortController` + 15s 超时
- 上传：60s 超时，允许用户取消；取消后 UI 回到"未选择/可重选"状态
- 组件卸载时取消未完成请求

### 8.5 不得伪造成功

前端任何路径都不允许：真实请求失败时插入演示数据、把 `failed` 显示成 `ready`、或对 planned 接口做本地假实现后告诉用户"已导入"。这条是验收项。

---

## 9. 明确不实现（标记 planned）

| 项 | 计划阶段 |
| --- | --- |
| `GET /search`（全文检索、中文分词/trigram） | M1-E |
| `/lore-entries` CRUD 与 source_links 维护 | M1-D |
| PDF / DOCX 解析、OCR | M1-C |
| 自动语义抽取、实体识别 | M2 起 |
| 真实模型调用、API Key 管理、流式输出 | M2 |
| 正式冒险状态机、事件日志、分支事务 | M3 |
| 账户、多用户、云同步、导入导出 | M4 起 |

**本契约不承诺**上述任何一项，UI 也不得暗示已具备。

---

## 10. 验收样例

以下命令以 `http://127.0.0.1:8000/api/v1` 为基址；样例文件为虚构资料（`潮汐档案馆设定.md`），不得使用真实个人设定。

~~~bash
# 1. 建世界
curl -s -X POST http://127.0.0.1:8000/api/v1/worlds \
  -H 'Content-Type: application/json' \
  -d '{"name":"潮汐档案馆","description":"一座建在退潮线上的档案馆。"}'

# 2. 导入中文 Markdown（期望 201，status=ready，chunk_count ≥ 1）
curl -s -X POST http://127.0.0.1:8000/api/v1/worlds/$WID/documents \
  -F "file=@潮汐档案馆设定.md"

# 3. 重复导入同一文件（期望 409 DUPLICATE_DOCUMENT，带 existing_document_id）
curl -s -X POST http://127.0.0.1:8000/api/v1/worlds/$WID/documents \
  -F "file=@潮汐档案馆设定.md"

# 4. 分块与来源（期望 index 从 0 连续，heading_path 与原文标题一致）
curl -s "http://127.0.0.1:8000/api/v1/documents/$DID/chunks?limit=5"

# 5. 原文分页（期望 text 与 chunks 拼接一致）
curl -s "http://127.0.0.1:8000/api/v1/documents/$DID/raw?offset=0&limit=200"

# 6. 不支持格式（期望 415 UNSUPPORTED_MEDIA_TYPE）
curl -s -X POST http://127.0.0.1:8000/api/v1/worlds/$WID/documents \
  -F "file=@设定.pdf"

# 7. 空文件（期望 422 EMPTY_DOCUMENT，且文档列表不新增）
curl -s -X POST http://127.0.0.1:8000/api/v1/worlds/$WID/documents \
  -F "file=@empty.txt"
~~~

**必测清单**（与任务书一致）：中文 TXT/MD、标题与段落顺序、空/损坏/不支持编码、非法格式与超限、同名与重复文档、来源定位、失败状态、服务重启后仍在、不同世界隔离、演示存档不变、错误输入不丢失。

---

## 11. 变更流程

1. 破坏性或语义变更：把 `0.1` 升为 `0.2`，在文末"变更记录"写清改了哪个字段/端点、为什么、谁提出
2. 契约提交后，把**确切 commit SHA** 交用户转给 Gemini；Gemini 的交付包必须声明同一 SHA
3. Gemini 提出有依据的冲突：先改契约并提交，再发新快照，不允许前端先按旧版实现再回补
4. 实施与契约不一致时，以契约为准或先改契约；不允许"代码就是文档"

## 变更记录

| 版本 | 日期 | 变更 | 作者 |
| --- | --- | --- | --- |
| 0.1 | 2026-11-08 | 初版：M1-A/B 范围、模型、14 个本批接口、错误码、隔离与接入约定 | DeepSeekHarness |
