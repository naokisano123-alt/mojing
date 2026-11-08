# 墨境 M1-A/B 最小接口契约

> **契约版本**：`0.2`（响应 GEM-01 审阅后修订，冻结待实施）
> **状态**：本文件先于代码提交。是否已实现、测试结果如何，一律以 `docs/reports/deepseekharness-m1-ab.md` 为准，本文件不代表功能已完成。
> **作者**：DeepSeekHarness（v0.2 采纳 Gemini `gemini-GEM-01-a71342a` 审阅意见）
> **分支**：`feat/m1-ab-backend`
> **基线**：`a71342a`（任务书基线 `06088df` 之上仅新增协作文档）
> **适用**：M1-A 世界数据持久化、M1-B TXT/MD 真实导入与原文来源。

---

## 0. 这份契约给谁看

- **Gemini（GEM-02）**：按本契约实现 `src/api/**`、`src/components/documents/**`。第 4 节路径与第 8.2 节 TS 类型为冻结内容；第 11 节逐条回答了你提出的 5 个问题和 DTO 建议，**并列出采纳/未采纳及原因**，请以第 11 节的对照表为准。
- **DeepSeekHarness（DSH-02/03）**：按本契约实现 `server/**` 并完成共享文件接线。
- **用户**：这里是本轮"真实导入"能做到什么、做不到什么的白纸黑字。

任何一方发现契约有误，先改契约、再改代码，并同步新快照；不允许两端各自形成不同版本。

### v0.1 → v0.2 变更摘要

| # | 变更 | 来源 |
| --- | --- | --- |
| 1 | 分块字段更名：`index`→`chunk_index`，`start_offset`→`start_char`，`end_offset`→`end_char` | GEM-01 §2.4 / §4.3 |
| 2 | 分块列表默认只回 `text_preview`，`?include_text=true` 才回全文 `text` | GEM-01 §2.4（手机端 payload） |
| 3 | 状态枚举 `parsing` → `processing` | GEM-01 §4.2 |
| 4 | 错误码 `DECODE_FAILED` → `ENCODING_UNSUPPORTED` | GEM-01 §2.3 |
| 5 | 新增纯文本中文章节行识别（`第X卷/章/回/节/篇/部`） | GEM-01 §3 样本 B |
| 6 | 新增 `source_kind`，明确契约内不存在任何 instruction 字段 | GEM-01 §2.5 |
| 7 | Big5 明确**默认不启用**并说明理由 | GEM-01 §2.3（未完全采纳） |
| 8 | 补充：删除语义、失败不落库、正文不内联的理由、超时值 | GEM-01 §4 五问 |
| 9 | 新增 UI 呈现基线（资料库模式横幅、按钮文案） | GEM-01 §5 |

---

## 1. 范围

### 本批实现（M1-A / M1-B）

| 能力 | 说明 |
| --- | --- |
| 世界持久化 | 创建、列表、详情、改名/改简介、软删除；服务重启后仍在 |
| 文档导入 | 真实上传 `.txt` / `.md`，服务端解析、保存原文、按标题与段落分块 |
| 原文与来源 | 读取全文（分页）、按块读取原文、每块带可回到原文的字符级偏移与标题路径 |
| 解析状态 | 成功即 `ready`；失败返回 4xx 且**不落库**，不产生"看起来能用"的半成品 |

### 本批不实现（见第 9 节）

PDF / DOCX / OCR、语义抽取、LoreEntry CRUD、全文检索、真实 AI、API Key、正式冒险状态机、账户、云同步、多人。

**M0 演示必须继续可用**：本批不改 `mojing-m0-v1` 的 localStorage 结构与演示玩法。

---

## 2. 通用约定

### 2.1 基址与版本

- 开发环境：`http://127.0.0.1:8000/api/v1`
- 前端默认同源前缀 `/api`，由 Vite 代理转发（见 8.1）；`/api/v1/...` 仍在 `/api` 前缀之下，代理规则无需特例
- 所有路径以 `/api/v1` 开头；破坏性变更升 `/api/v2`

### 2.2 格式与字符编码

- 请求/响应体：`application/json; charset=utf-8`（上传接口除外）
- 上传：`multipart/form-data`，文件字段名固定为 `file`
- 文本一律 UTF-8 传输；数据库内部存 Unicode 字符串
- JSON 中中文不做 `\uXXXX` 转义（允许，但不要求）

### 2.3 标识

所有资源 ID 为 **UUID v4 小写字符串**（36 字符），由服务端生成，客户端不构造、不推测。与 M0 前端 `crypto.randomUUID()` 一致。

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

- 世界创建**不**幂等：同名世界允许存在
- 文档导入按 `(world_id, content_sha256)` 去重：重复返回 `409 DUPLICATE_DOCUMENT`
- 哈希由**服务端**计算，不信任客户端上报（客户端无需计算 hash，见 11.2）
- 需要强制导入同一文件时加 `?allow_duplicate=true`
- 删除幂等：已删除对象再次 `DELETE` 返回 `204`

### 2.8 请求取消与超时

- 客户端一律用 `AbortController`
- 默认超时：查询 30s，上传 60s（可在适配层统一定义常量）
- 取消后前端不把请求当失败弹错误、不自动重试；上传被用户取消时回到"可重新选择"状态
- 服务端不提供取消接口（本批解析为同步、毫秒到秒级）

### 2.9 CORS 与代理

- 开发期推荐：Vite `server.proxy['/api'] → http://127.0.0.1:8000`，前端用相对路径，**完全绕开 CORS**
- 直连时需要 `MOJING_CORS_ORIGINS` 逗号分隔白名单，默认 `http://localhost:5173,http://127.0.0.1:5173`
- 手机真机访问电脑局域网地址时，要么继续用代理，要么把该 origin 加进白名单
- 不允许 `Access-Control-Allow-Origin: *` 配合凭据；本批没有凭据，仍按显式白名单处理

### 2.10 安全边界

上传内容**只作为资料**保存与展示：不执行、不求值、不拼接进任何指令模板、不当作系统提示。

- 契约中**不存在** `system_prompt`、`instruction`、`config` 一类字段；任何导入文本只能出现在 `text` / `text_preview` / `raw` 中
- 文档模型带 `source_kind: "user_import"`，明确其来源属性（将来人工录入可为 `manual`）
- 文件名只做展示与扩展名判定，落库前取 basename、去掉控制字符与路径分隔符
- 资料中出现"忽略以上指令"之类文本，行为不变；服务端不做特殊转义或执行

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

详情接口额外返回 `documents`（按 `created_at` 升序）。

### 3.3 WorldDocument

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | UUID v4 |
| `world_id` | string | 所属世界 |
| `filename` | string | 原始文件名（basename），≤120 字符，保留扩展名 |
| `media_type` | string | `text/plain` \| `text/markdown` |
| `byte_size` | int | 原始字节数（含 BOM） |
| `encoding` | string | 实际解码方式：`utf-8` \| `utf-8-sig` \| `utf-16-le` \| `utf-16-be` \| `gb18030` \| `big5` |
| `content_sha256` | string | 原始字节 SHA-256，hex 小写 64 位 |
| `text_length` | int | 规范化后正文长度，单位 **UTF-16 code unit** |
| `line_count` | int | 规范化后 `\n` 数 + 1 |
| `chunk_count` | int | 分块数 |
| `source_kind` | string | 本批恒为 `user_import` |
| `status` | string | `ready`（本批唯一成功态）；`processing` / `failed` 为将来异步预留，当前不会出现 |
| `created_at` / `updated_at` | string | ISO-8601 |
| `deleted_at` | string \| null | 软删除 |

### 3.4 LoreChunk

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | UUID v4 |
| `document_id` | string | 所属文档 |
| `world_id` | string | 冗余，便于按世界查询 |
| `chunk_index` | int | 0 起连续序号，全文档唯一 |
| `heading_path` | string[] | 标题栈，如 `["第一章 雾港", "一、来信"]`；无标题为 `[]` |
| `start_char` | int | 块在正文中的起点（含），UTF-16 code unit |
| `end_char` | int | 终点（不含），UTF-16 code unit |
| `text_length` | int | `end_char - start_char` |
| `text` | string | 仅 `include_text=true` 或单块接口返回：`[start_char, end_char)` 的原文切片 |
| `text_preview` | string | 列表默认返回：原文前 ≤160 个 UTF-16 单位，**不截断代理对**，不加省略号（省略号由前端加） |

**不变式（服务端必须自测，用 `include_text=true` 校验）**：

1. `chunks[0].start_char === 0`
2. `chunks[i].end_char === chunks[i+1].start_char`（无空隙、无重叠）
3. 末块 `end_char === document.text_length`
4. 依次拼接 `text` 与全文逐字符相等
5. `chunk_index` 从 0 连续递增

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
  start_char: number;
  end_char: number;
  excerpt: string;        // ≤120 字符，列表展示用
}
~~~

本批**不提供**任何 `/lore-entries` 端点，前端不得假设其存在。

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
| 12 | GET | `/documents/{document_id}/chunks/{chunk_index}` | 按序号取块 | 本批 |
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

错误：`422 WORLD_NAME_INVALID`、`422 VALIDATION_ERROR`。

### 4.3 GET /worlds

`?limit&offset`，按 `created_at` 升序，不含 `documents`。

### 4.4 GET /worlds/{world_id}

World + `documents`（3.3 完整元信息，不含正文）。`404 WORLD_NOT_FOUND`。

### 4.5 PATCH /worlds/{world_id}

字段均可选，只改传入的；`{}` 合法（返回原样，不写入）。

### 4.6 DELETE /worlds/{world_id}

`204`，软删除世界及其文档与分块（`deleted_at` 置位，行保留）。重复删除仍 `204`。

### 4.7 POST /worlds/{world_id}/documents

`multipart/form-data`，字段 `file`；可选 query `allow_duplicate=true`。

| 项 | 默认 |
| --- | --- |
| 单文件大小 | 2 MiB（2 097 152 字节） |
| 每世界未删除文档数 | 20 |
| 扩展名 | `.txt` / `.md`，大小写不敏感 |
| 文件名长度 | ≤120 字符 |

响应 `201`（成功即解析完成）：

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
  "source_kind": "user_import",
  "status": "ready",
  "created_at": "2026-11-08T06:31:10Z",
  "updated_at": "2026-11-08T06:31:10Z",
  "deleted_at": null
}
~~~

失败见 5.5 与第 6 节。

### 4.8 GET /worlds/{world_id}/documents

`?limit&offset&include_deleted=false`，按 `created_at` 升序。

### 4.9 GET /documents/{document_id}

单条元信息（3.3）。已删除默认 `404 DOCUMENT_NOT_FOUND`。

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

### 4.11 GET /documents/{document_id}/chunks

`?limit=50&offset=0&include_text=false`，按 `chunk_index` 升序。

默认（`include_text=false`）每项：

~~~json
{
  "id": "e71a...",
  "document_id": "5c2a...",
  "world_id": "0f3b...",
  "chunk_index": 0,
  "heading_path": ["第一章 雾港"],
  "start_char": 0,
  "end_char": 1372,
  "text_length": 1372,
  "text_preview": "# 第一章 雾港\n\n潮水退去的时候……"
}
~~~

`include_text=true` 时，每项额外带完整 `text`（`text_preview` 仍可保留）。

> 设计理由：手机端首屏只需要标题与摘要；14 块 × 1800 字一次性下发会明显拖慢，因此列表默认瘦身，用户点开某块再取全文。

### 4.12 GET /documents/{document_id}/chunks/{chunk_index}

按序号直达，恒返回完整 `text`。越界 `404 CHUNK_NOT_FOUND`。

### 4.13 GET /chunks/{chunk_id}

单块，恒返回完整 `text`。来源回跳入口：拿到 `chunk_id` 即可展示原文，也可据 `start_char` 调 `/raw` 取上下文。

### 4.14 DELETE /documents/{document_id}

`204` 软删除；世界 `document_count` 随之减少；重复删除 `204`。语义见 11.4。

---

## 5. 导入与解析规则

### 5.1 接受与拒绝

| 情况 | 结果 |
| --- | --- |
| `.txt` / `.md`，可解码 | 接受 |
| `.pdf` / `.docx` / 其他扩展名 | `415 UNSUPPORTED_MEDIA_TYPE`，message 注明"本批只支持 TXT/Markdown" |
| 超过 2 MiB | `413 FILE_TOO_LARGE` |
| 0 字节或全空白 | `422 EMPTY_DOCUMENT` |
| 含 NUL 字节且无 UTF-16 BOM | `415 BINARY_CONTENT`（提示可能是二进制改了扩展名） |
| 世界文档数已达 20 | `409 WORLD_DOCUMENT_LIMIT` |

### 5.2 编码策略（明确，不静默乱码）

探测顺序：

1. **BOM**：`EF BB BF` → `utf-8-sig`；`FF FE` → `utf-16-le`；`FE FF` → `utf-16-be`
2. **严格 UTF-8**（无 BOM）
3. **严格 GB18030**（`MOJING_ALLOW_GB18030=1`，默认开）——覆盖 GBK/GB2312
4. **严格 Big5**（`MOJING_ALLOW_BIG5=0`，**默认关**，见下）
5. 全部失败 → `422 ENCODING_UNSUPPORTED`，`details: { "tried": ["utf-8","gb18030"], "hint": "请另存为 UTF-8 后重新导入" }`

为什么不默认开 Big5：Big5 与 GB18030 的字节空间高度重叠，对同样的字节序列两者都可能"解码成功"，一旦猜错就是**看起来正常、实际全是错字**的正文，比直接报错更糟。繁体用户请另存为 UTF-8；真正需要时设 `MOJING_ALLOW_BIG5=1`。

约束：

- 解码失败**不**落库、**不**写入替代字符（`\uFFFD`）文本
- 响应的 `encoding` 如实反映实际解码方式，前端可提示"已按 GB18030 读取，如显示乱码请转存 UTF-8"
- 不做 `errors='ignore'` / `'replace'` 这类静默降级

### 5.3 规范化

- CRLF、CR 统一为 `\n`
- 去除开头 BOM 字符
- 不裁剪正文首尾空白（保留作者排版），但全空白判定按 `text.strip()` 为空
- `text_length` 为规范化后正文的 UTF-16 code unit 数：Python 侧用 `len(text.encode('utf-16-le')) // 2`

### 5.4 分块算法（确定性，非 AI）

目标：块大小 600–1800 UTF-16 单位，硬上限 4000；边界尽量落在段落/标题处。

1. 按 `\n\n` 切段，保留段落之间的空行归属（后一段前导空行计入后一段）
2. **标题识别**（两种，均开新块并更新 `heading_path`）：
   - Markdown：行首匹配 `^(#{1,6})\s+` 且不在围栏代码块内；按 `#` 数量压栈（`##` 会替换同级及更深层级）
   - 纯文本中文章节行：整行去空白后 ≤30 字，且匹配 `^第[0-9一二三四五六七八九十百千零〇两]+[卷章回节篇部]\s*.*$`；视为一级标题，`heading_path = [该行]`
3. 累积段落直到长度超过 1800 → 收口成块
4. 单段超过 4000 → 在最近的 `。！？；\n` 处软切，仍保证连续覆盖；确实无标点时才按 4000 硬切，且不切断代理对
5. 每块 `start_char` 取上一块 `end_char`，`end_char` 为收口位置，`text` 即原文切片
6. 空块不产出；空白归属允许实现自由，但**必须**满足 3.4 的覆盖不变式

> GEM-01 样本 C 期望"超长段落按字符数软截断（如单块最大 1000）"：本契约取 4000 硬上限，因为 1000 会把中文长段落切得过碎、增大块数与请求量；若实测手机端单块渲染有压力，可在实施报告中提出调整（改契约不改代码）。

### 5.5 失败与回滚

- 解析在单个事务内完成：文档行 + 全部分块要么全写，要么全不写
- 任何 4xx 失败：库里**查不到**这条文档（不是 `failed` 状态，而是不存在）
- 上传中断（连接断开）：事务不提交
- 服务端 5xx 不返回部分数据；前端展示"导入失败，请重试"

### 5.6 重复与删除

- 同世界 `content_sha256` 相同 → `409`，`details.existing_document_id` 指向已有文档，UI 应能"查看已有文档"
- `allow_duplicate=true` 时允许
- 删除为软删除；列表默认不含已删除项；重复删除幂等 `204`

---

## 6. 错误码表

| HTTP | code | 触发 | details |
| --- | --- | --- | --- |
| 404 | `WORLD_NOT_FOUND` | 世界不存在或已删除 | — |
| 404 | `DOCUMENT_NOT_FOUND` | 文档不存在或已删除 | — |
| 404 | `CHUNK_NOT_FOUND` | 块不存在或序号越界 | `{ "chunk_index": 42 }` |
| 409 | `DUPLICATE_DOCUMENT` | 同世界内容重复 | `{ "existing_document_id", "content_sha256" }` |
| 409 | `WORLD_DOCUMENT_LIMIT` | 文档数达上限 | `{ "limit": 20 }` |
| 413 | `FILE_TOO_LARGE` | 超过 2 MiB | `{ "max_bytes": 2097152, "received_bytes": 5242880 }` |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | 扩展名不支持 | `{ "allowed": [".txt", ".md"] }` |
| 415 | `BINARY_CONTENT` | 二进制内容 | — |
| 422 | `WORLD_NAME_INVALID` | 名字为空或超长 | `{ "field": "name", "max_length": 40 }` |
| 422 | `EMPTY_DOCUMENT` | 空文件 | — |
| 422 | `ENCODING_UNSUPPORTED` | 编码无法可靠识别 | `{ "tried": [...], "hint": "..." }` |
| 422 | `INVALID_RANGE` | 分页/偏移越界 | `{ "field": "offset" }` |
| 422 | `VALIDATION_ERROR` | 其他请求体校验失败 | `{ "field": "..." }` |
| 500 | `INTERNAL_ERROR` | 未预期异常 | 不返回堆栈、不返回路径 |
| 503 | `DB_UNAVAILABLE` | 数据库不可用 | — |

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
2. 不改变 `mojing-m0-v1` 的既有字段形状；演示进度必须原样可读
3. 前端使用**独立类型** `RemoteWorld`（8.2），不与 `model.ts` 的 `World` 混存
4. 后端不可达时，真实世界区域显示错误与重试；**不得**回退成模拟数据假装成功
5. 真实世界暂不进入故事页

### 7.1 UI 呈现基线（采纳 GEM-01 §5）

| 位置 | 要求 |
| --- | --- |
| 演示世界卡片 | Tag `M0 · 演示世界`，按钮"继续冒险" |
| 真实世界卡片 | Tag `世界资料库 · 已就绪`，副标题"已导入 X 个文档 · Y 条切片"，主按钮**"查看世界设定库"**（不得写成"开启冒险"） |
| 真实世界顶部 | 固定轻提示条：**【世界资料库模式】** 设定已导入并持久化；AI 角色与剧情生成将在 M2 开启。当前可检索设定、浏览章节切片并核对原文。 |
| 降级入口 | 次级按钮"以演示模式试玩 (M0 体验)"，点击先确认："当前剧情为本地关键词模拟演示，不会根据您导入的文档内容生成回答。是否继续？" |

文案可微调，但**不得**出现"AI 已读懂你的设定""开始生成剧情"一类表述。

---

## 8. 前端接入约定

### 8.1 地址配置

- 环境变量 `VITE_API_BASE_URL`，默认空字符串（同源 `/api`）
- 开发期 `vite.config.ts` 增加 `server.proxy['/api'] = 'http://127.0.0.1:8000'`（该文件由集成者修改，Gemini 不直接改）
- 组件内**禁止**硬编码 `127.0.0.1`、端口或局域网 IP
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

export type DocumentStatus = 'ready' | 'processing' | 'failed';
export type DocumentEncoding =
  | 'utf-8' | 'utf-8-sig' | 'utf-16-le' | 'utf-16-be' | 'gb18030' | 'big5';
export type SourceKind = 'user_import' | 'manual';

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
  source_kind: SourceKind;
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
  chunk_index: number;
  heading_path: string[];
  start_char: number;
  end_char: number;
  text_length: number;
  text_preview: string;
  /** 仅 include_text=true 或单块接口存在 */
  text?: string;
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

### 8.3 错误映射

| code | 建议交互 |
| --- | --- |
| `DUPLICATE_DOCUMENT` | 提示 + "查看已有文档"（用 `details.existing_document_id`） |
| `ENCODING_UNSUPPORTED` | 展示 `details.hint`，指导另存为 UTF-8 |
| `FILE_TOO_LARGE` | 展示上限，不自动重试 |
| `UNSUPPORTED_MEDIA_TYPE` | 选文件阶段就拦（`accept=".txt,.md"`），服务端错误作兜底 |
| `WORLD_DOCUMENT_LIMIT` | 提示上限，引导删除旧文档 |
| 网络/5xx | "暂时连不上服务"，可重试；**不得**显示成功 |

### 8.4 取消与超时

- 查询 30s、上传 60s；组件卸载时取消未完成请求
- 上传取消后回到"未选择/可重选"状态，不留半条 UI 状态

### 8.5 不得伪造成功

真实请求失败时插入演示数据、把 `failed` 显示成 `ready`、或对 planned 接口做本地假实现后告诉用户"已导入"——都是验收不通过项。

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
| 文档物理清除（purge） | M1-D 与条目联动时再定 |

**本契约不承诺**上述任何一项，UI 也不得暗示已具备。

---

## 10. 验收样例

基址 `http://127.0.0.1:8000/api/v1`；样例文件均为虚构资料。

~~~bash
# 1. 建世界
curl -s -X POST http://127.0.0.1:8000/api/v1/worlds \
  -H 'Content-Type: application/json' \
  -d '{"name":"潮汐档案馆","description":"一座建在退潮线上的档案馆。"}'

# 2. 导入中文 Markdown（期望 201，status=ready，chunk_count ≥ 1）
curl -s -X POST http://127.0.0.1:8000/api/v1/worlds/$WID/documents \
  -F "file=@潮汐档案馆设定.md"

# 3. 重复导入（期望 409 DUPLICATE_DOCUMENT）
curl -s -X POST http://127.0.0.1:8000/api/v1/worlds/$WID/documents \
  -F "file=@潮汐档案馆设定.md"

# 4. 分块（默认瘦身；比对 start_char/end_char 连续性）
curl -s "http://127.0.0.1:8000/api/v1/documents/$DID/chunks?limit=5"

# 5. 分块全文（校验拼接等于全文）
curl -s "http://127.0.0.1:8000/api/v1/documents/$DID/chunks?include_text=true&limit=200"

# 6. 原文分页
curl -s "http://127.0.0.1:8000/api/v1/documents/$DID/raw?offset=0&limit=200"

# 7. 不支持格式（期望 415）
curl -s -X POST http://127.0.0.1:8000/api/v1/worlds/$WID/documents -F "file=@设定.pdf"

# 8. 空文件（期望 422 EMPTY_DOCUMENT，且文档列表不新增）
curl -s -X POST http://127.0.0.1:8000/api/v1/worlds/$WID/documents -F "file=@empty.txt"
~~~

**必测清单**：中文 TXT/MD、标题与段落顺序、空/损坏/不支持编码、非法格式与超限、同名与重复文档、来源定位、失败状态、服务重启后仍在、不同世界隔离、演示存档不变、错误输入不丢失。

---

## 11. 对 GEM-01 五个问题与 DTO 建议的答复

### 11.1 逐条回答

**Q1 实体 ID 是否统一 UUID v4？**
是。World / WorldDocument / LoreChunk 全部 UUID v4 小写字符串，与 M0 的 `crypto.randomUUID()` 一致（见 2.3）。

**Q2 上传用 multipart 还是 JSON Base64？同步 201 还是 202？**
multipart（字段 `file`）。本批**同步**返回 `201`，因为 TXT/MD 上限 2 MiB、解析是毫秒到百毫秒级，做 202 + 轮询会额外引入任务表、进度清理与超时处理，收益不抵成本。
结构上仍保留 `status` 字段（`processing`/`failed` 已定义），将来 M1-C 支持大 PDF 时改为 202 不需要破坏前端类型。
失败不产生文档行：失败态只存在于**上传交互**中（4xx + 错误码），不会出现在文档列表里——列表里出现的文档一定是可用的。

**Q3 正文和分块是否拆成两个接口？**
拆。`GET /documents/{id}` 仅元数据；正文走 `/raw`（分页，默认 2 万单位）；分块走 `/chunks`。
不用 `include_chunks=true` 的原因：手机端不需要在打开文档时同时拉全量正文和全量分块，两个接口可以分别缓存、分别重试。
列表默认只给 `text_preview`，全文按块取，避免 14×1800 字一次性下发。

**Q4 DELETE 是物理级联还是 tombstone？**
**软删除（tombstone）**：`deleted_at` 置位，行保留，普通查询不可见，`include_deleted=true` 可审计。
理由：M1-D 的 LoreEntry 会引用 chunk（`source_links`），物理删除会让历史条目瞬间失去来源；软删除保留重算与恢复的可能。
物理 purge 列为 planned，等条目联动规则定下来再做。

**Q5 FastAPI 端口与路径前缀？**
`127.0.0.1:8000`，全部路径 `/api/v1/...`（仍以 `/api` 开头，你给的 Vite 代理规则原样可用），CORS 默认白名单 `localhost:5173` / `127.0.0.1:5173`，可用环境变量追加。

### 11.2 DTO 命名对照（你提案 → 契约 v0.2）

| GEM-01 提案 | 契约 v0.2 | 说明 |
| --- | --- | --- |
| `start_char` / `end_char` | **采纳**，同名 | 单位是 UTF-16 code unit（见 3.4） |
| `chunk_index` | **采纳**，同名 | 原 `index` 更名 |
| `content`（块正文） | `text` / `text_preview` | 列表瘦身，见 4.11 |
| `file_size` | `byte_size` | 字节数，避免与字符数混淆 |
| `chunks_count` | `chunk_count` | 与 `document_count` 统一单数 |
| `status: 'processing'` | **采纳** | 原 `parsing` 更名；本批恒为 `ready` |
| `error_message` | 不设字段 | 失败不落库，错误只在 4xx 响应里 |
| `raw_content`（文档内联全文） | `/raw` 接口的 `text` | 不内联，避免 2 MiB 响应 |
| `is_user_canon` / `untrusted_source` | `source_kind: 'user_import'` | 契约中不存在任何 instruction 类字段（2.10）；信任等级由字段语义表达，不再堆布尔 |
| 客户端上报 `file_hash` 做幂等 | 不采纳 | 服务端自行算 SHA-256 去重，不信任客户端上报；客户端无需计算 |

### 11.3 未采纳项的替代方案

- **Big5 自动识别**：默认关（5.2 已说明误判风险），需要时开环境变量。
- **单块上限 1000**：取 4000 硬上限、600–1800 目标；如实测渲染压力大，先改契约再改代码。
- **客户端 hash 幂等**：服务端算；重复导入靠 409 + `existing_document_id` 实现"防重复"，效果一致且不可伪造。

---

## 12. 变更流程

1. 破坏性或语义变更：版本号 +0.1，在变更记录写清改了什么、为什么、谁提出
2. 契约提交后，把**确切 commit SHA** 交用户转给 Gemini；Gemini 的交付包必须声明同一 SHA
3. Gemini 提出有依据的冲突：先改契约并提交，再发新快照
4. 实施与契约不一致时，以契约为准或先改契约

## 变更记录

| 版本 | 日期 | 变更 | 作者 |
| --- | --- | --- | --- |
| 0.1 | 2026-11-08 | 初版：范围、模型、14 个本批接口、错误码、隔离与接入约定 | DeepSeekHarness |
| 0.2 | 2026-11-08 | 响应 GEM-01：字段更名、列表瘦身、状态枚举、章节行识别、source_kind、Big5 默认关、补五问答复与命名对照 | DeepSeekHarness |
