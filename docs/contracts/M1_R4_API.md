# 墨境 M1 第四/五轮补充接口契约（R4/R5）

> **版本**：`1.0-r4`（含 R5 的增量调整；在 v0.2、`1.0-r2`、`1.0-r3` 之上**追加**）
> **状态**：冻结待实施；实施以 `docs/reports/r5-integration.md` 与 `docs/M1_ACCEPTANCE.md` 为准
> **作者**：DeepSeekHarness（R4-D / R5-D）
> **代码基线**：`8296630`（R4 审阅固定）→ `1e5ea77`（R5 开工快照）
> **被本文件修正的旧条文**：`M1_R3_API.md` §1.2 中 `end_char <= chunk.end_char - chunk.start_char` 的公式笔误**作废**，以本文件 §1.1 为准。

---

## 0. 兼容性

| 项 | 规则 |
| --- | --- |
| 既有端点 | 路径/方法/字段不变；R3 客户端继续可用 |
| 新增字段 | 一律可加性，可缺失 |
| 破坏性变更 | 不允许；本文件只修正语义笔误与补齐校验 |

---

## 1. 来源坐标（修正 R3 笔误）

### 1.1 唯一合法写法：全文绝对 UTF-16 半开区间

```text
chunk.start_char <= start_char < end_char <= chunk.end_char
```

- `start_char` / `end_char` 都是**文档全文**坐标，与 `GET /chunks` 返回的 `start_char` / `end_char` 同一坐标轴
- 必须落在**指定块**的区间内（不是"相对块偏移"）
- 不得切开代理对（UTF-16 边界合法）
- **禁止任何"绝对失败后按相对猜测"的回退**：不符合就报错，不静默改写

> R3 契约写的 `end_char <= chunk.end_char - chunk.start_char` 是笔误，会让"块内相对偏移"看起来合法，已作废。

### 1.2 来源元数据（可加性）

`LoreEntrySource` 新增：

~~~ts
interface LoreEntrySource {
  // …既有字段
  chunk_index: number | null;   // 来自真实 chunks；查不到时 null，不得填 0
}
~~~

### 1.3 写引用的校验与错误

| 检查 | 失败码 |
| --- | --- |
| document 属于该世界且未软删 | `SOURCE_CROSS_WORLD` / `SOURCE_DELETED` |
| chunk 属于该 document | `SOURCE_CHUNK_MISMATCH` |
| 绝对区间落在块内且 start < end | `SOURCE_OUT_OF_RANGE` |
| UTF-16 边界合法 | `SOURCE_INVALID_OFFSET` |

服务端仍负责生成 `excerpt`，客户端传值一律忽略。

---

## 2. 条目更新（PATCH）语义

| 客户端行为 | 服务端语义 |
| --- | --- |
| 不传 `source_links` | **保留全部既有引用**（含非 active 的历史引用），不得因引用失效阻断纯文本编辑 |
| 传 `[]` | 显式移除全部引用 |
| 传 `null` | `422 VALIDATION_ERROR`（禁止用 null 静默清空） |
| 传数组 | 可保留"与现有引用完全相同"的历史非 active 项；**不能新增或改写无效引用** |

`expected_revision` 仍必填，冲突 `409 LORE_ENTRY_CONFLICT`。

---

## 3. 并发与事务

- `revision` 更新必须是**原子条件更新**：`UPDATE … WHERE id = ? AND revision = ? AND deleted_at IS NULL`
- 影响行数为 0 时：区分"不存在/已删（404）"与"版本冲突（409）"，不得都转 500
- 来源写入与条目写入同一事务；任一步失败整单回滚
- `DELETE` 的 `expected_revision` 必须是**严格正整数**：`true`、`1.5`、`"1abc"` 一律 `422`，不接受参数强转

---

## 4. 检索与来源跳转

- `match_field: 'text' | 'heading' | 'filename'`
- 仅 `text` 命中带 `match_start` / `match_end`（块内 UTF-16）；`heading` / `filename` 命中为 `null`
- 来源跳转请求（前端内部）：

~~~ts
interface SourceTarget {
  documentId: string;
  chunkId?: string;      // 优先：按真实块 ID 解析
  chunkIndex?: number;   // 可选：已知真实序号时才传
  startChar?: number;    // 仅正文命中且区间有效时传
  endChar?: number;
}
~~~

- **禁止**把 `chunkIndex` 缺省补 0；无 `chunkIndex` 时由客户端/适配层用 `chunkId` 调 `GET /chunks/{id}` 解析真实序号
- 标题/文件名命中只打开真实块，不高亮正文

---

## 5. 警告标准化

解析模块输出的 `warnings` 元素必须是 JSON 安全对象：

~~~ts
interface DocumentWarning {
  code: string;      // 机器码
  message: string;
  count?: number;    // 可缺省
}
~~~

- 主管线调用 Li 提供的 `normalize_warning_records(records, legacy_warnings=())`：
  - 接受 dict 结构，`count` 缺省不报错
  - 纯字符串回退为 `{code: 'EXTRACTION_WARNING', message: s}`
  - **不按中文子串推断机器码**
  - 未知但合法的 code 保留原值
- `PARTIAL_TEXT` 必须在 UI 醒目提示"部分内容未提取"

---

## 6. 前端草稿（R5，仅前端约定）

- 面板接受可选 `draftStore`：

~~~ts
interface DraftStore {
  get(key: string): { draft: LoreEntryDraft; baseRevision: number | null } | undefined;
  set(key: string, value: { draft: LoreEntryDraft; baseRevision: number | null }): void;
  delete(key: string): void;
}
~~~

- key = `worldId + ":" + entryId`，新建用 `new`
- 由 `WorldLoreView` 用**稳定的 `useRef` Map** 注入，切换子页签不丢草稿；不同世界 key 隔离
- 只存在当前前端会话，不写 localStorage、不含任何 Key
- 恢复草稿必须保留 `baseRevision`，不得自动绑定新 revision；409 后由用户明确选择基线

---

## 7. 能力与限额

- `GET /api/v1/capabilities` 逐格式给 `max_bytes`，前端按后缀取用，不得用"是否启用 PDF"统一放大
- 能力请求失败时前端保守回退 TXT/MD 2 MiB
- `parsers` 字段如实反映依赖是否可用：`{"pdf": true, "docx": true}`

---

## 8. 变更记录

| 版本 | 日期 | 变更 | 作者 |
| --- | --- | --- | --- |
| 1.0-r4 | 2026-11-08 | 修正来源坐标公式笔误；chunk_index 可加性；PATCH source_links 三态；原子 revision 与严格 DELETE；检索 null 坐标；警告标准化；draftStore | DeepSeekHarness |
