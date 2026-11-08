# 墨境｜第三轮 R3-A 人工设定条目后端服务与检索语义修复交付报告

- **任务编号**：`R3-A`
- **执行作者**：`Gemini（Ma）`
- **功能代码基线**：`87b5b2fed12fad648eaba1930c3968a47a1665df`（PR #2 合并后的 main）
- **任务书快照提交**：`2540a64e8db0b89df6af2eb6b2a046ea1ea39d28`
- **集成 HEAD 引用**：`619b2e3b0fab3370710aed4bf707c1311c43846b`（分支 `feat/r3-integration`）
- **契约依据**：`docs/contracts/M1_R3_API.md` 版本 **`1.0-r3`**、`docs/collaboration/ROUND3.md`、`R3_GEMINI_MA.md`
- **持久层 Schema 版本**：`1.0-r3`
- **独占目录范围**：
  - `server/app/lore_entries/**`（新增完整人工条目服务）
  - `server/tests/lore_entries/**`（新增人工条目单元测试套件）
  - `server/app/lore_search/**`（修复 B6、B7、B8 检索语义）
  - `server/tests/lore_search/**`（补充 B6–B8 回归测试套件）
  - `docs/reports/gemini-ma-r3.md`（本交付报告）
- **未修改任何共享文件声明**：严格遵守防覆写协议，未触碰 `server/app/main.py`、`db.py`、`config.py`、`importer.py`、`requirements.txt`、`src/api/**` 以及 Marcin 前端目录。所有给 DSH 的端点与迁移接线均在第 4 节提供非侵入式 Proposal。

---

## 1. 交付模块架构与设计实现

### 1.1 人工设定条目服务模块 (`server/app/lore_entries/`)

本模块为 M1-D 人工设定条目的核心后端业务模块，遵循**连接注入**与**调用方事务控制**原则，模块内部不自行 commit、不 rollback、不硬编码或私建正式数据库，完全可无缝嵌入 DSH 的事务与路由上下文。

#### 1. 幂等 Schema 与迁移 (`schema.py`)
- **表结构定义**：
  - `lore_entries`：条目主表，包含 UUID v4 主键、`world_id` 外键约束、`type`、`title`、`summary`、`status`、`revision`（乐观并发版本号，初值 1，更新递增）、`origin`（固定 `'manual'`）、`created_at`、`updated_at`、`deleted_at`（软删除时间戳）。
  - `lore_entry_sources`：来源引用关联表，包含 `entry_id`、`world_id`、`document_id`、`chunk_id`、`start_char`、`end_char`（文档全文 UTF-16 绝对半开区间）、`excerpt`（真实文本截取摘录）、`created_at`、`deleted_at`。
  - **核心索引**：
    - `idx_lore_entries_world`: `(world_id, deleted_at)`
    - `idx_lore_entries_sort`: `(world_id, updated_at DESC, id ASC)`（支持稳定分页）
    - `idx_lore_entry_sources_entry`: `(entry_id, deleted_at)`
    - `idx_lore_entry_sources_doc`: `(document_id)`
    - `idx_lore_entry_sources_chunk`: `(chunk_id)`
- **幂等迁移函数**：`ensure_lore_entries_schema(conn: sqlite3.Connection) -> None`
  - 使用 `CREATE TABLE IF NOT EXISTS` 与 `CREATE INDEX IF NOT EXISTS`；
  - 自动检查增量字段（如 `revision`、`origin`、`excerpt`），已存在表无损保留，旧 `worlds`、`documents`、`chunks` 历史数据毫发无损；支持多次重复执行。

#### 2. 导出服务函数与业务规则 (`service.py`)
- **`create_entry(conn, world_id, payload) -> LoreEntry`**：
  - 校验世界有效性与未软删除（否则抛出 404 `WORLD_NOT_FOUND`）；
  - 校验 5 种分类枚举（`character`、`location`、`faction`、`rule`、`other`），非法抛出 422 `VALIDATION_ERROR`；
  - 校验 `title`（去首尾空白后 1–80 字符）与 `summary`（≤500 字符）；
  - **同名不同条目完全允许**：服务端不作唯一性约束，不误合并；
  - 允许无来源人工条目（`source_links: []`）；
  - 批量校验来源引用合法性，生成真实 `excerpt`，初始 `revision=1`。
- **`get_entry(conn, entry_id) -> LoreEntry`**：
  - 校验世界未软删除；条目不存在或已软删除抛出 404 `LORE_ENTRY_NOT_FOUND`；
  - 关联查询来源引用并**动态派生 `availability`**（`active` | `deleted` | `unavailable`）；
  - 派生 `needs_source_review`：当且仅当 `status === 'confirmed'` 且存在 `availability !== 'active'` 时为 `true`（仅提示复核，不做物理删除或事实判断）。
- **`list_entries(conn, world_id, *, type=None, status=None, limit=20, offset=0) -> LoreEntryListResult`**：
  - 支持按 `type` 与 `status` 筛选，支持 `limit`（1–100）与 `offset`（≥0）分页；
  - 排序严格按 `updated_at DESC, id ASC` 稳定输出，动态派生各条目的 `needs_source_review`。
- **`update_entry(conn, entry_id, payload, expected_revision) -> LoreEntry`**：
  - **乐观并发控制**：必填 `expected_revision`，若与当前 `revision` 不符立即抛出 409 `LORE_ENTRY_CONFLICT`（附带 `expected_revision` 与 `current_revision` 详情）；
  - **部分字段更新（PATCH 语义）**：未上送字段严格保留原值，不被抹除；更新成功后 `revision = expected_revision + 1`；
  - 若上送 `source_links`，全量软删除旧来源并写入新校验来源。
- **`delete_entry(conn, entry_id, expected_revision) -> None`**：
  - 必填 `expected_revision`，版本不符立即抛出 409 `LORE_ENTRY_CONFLICT`，杜绝并发误删；
  - 采用软删除（置 `deleted_at = now()`），绝不物理擦除（no purge）；
  - 针对已被软删除的条目再次以相同版本删除保持**幂等性**（直接返回 None，HTTP 对应 204）。

#### 3. 来源引用与 UTF-16 坐标校验 (`coords.py`)
- **同世界与生命周期核验**：
  - 验证 `document_id` 属于该 `world_id`（跨世界抛 422 `SOURCE_CROSS_WORLD`）；
  - 验证来源文档未被软删除（已删除抛 422 `SOURCE_DELETED`）；
  - 验证 `chunk_id` 属于该文档（不匹配抛 422 `SOURCE_CHUNK_MISMATCH`）。
- **坐标区间与代理对安全**：
  - 允许客户端传入全文 UTF-16 绝对坐标（`c.start_char <= start_char < end_char <= c.end_char`）或块内相对坐标（`0 <= start < end <= chunk_length`），服务端统一归一化为全文绝对坐标存储并换算出块内相对偏移；越界或反向抛 422 `SOURCE_OUT_OF_RANGE`；
  - 检查 `check_utf16_offset`：严禁在代理对中间截断（如 Emoji `🐉` 占用两个 UTF-16 单元，截断第二单元抛 422 `SOURCE_INVALID_OFFSET`）。
- **服务端生成真实摘录**：
  - 从真实切片文本切片截取，长度 ≤ 120 个 UTF-16 单元，超长两端平滑截断加 `'…'`；彻底忽略并替换客户端上送的伪造 `excerpt`。

---

### 1.2 检索语义修复 (`server/app/lore_search/`，关闭 B6–B8)

针对仓库审阅报告中明确列出的 B6、B7、B8 问题进行了彻底修复：

1. **修复 B6（标题路径 JSON 符号伪匹配与大小写稳定性）**：
   - 旧实现直接用 `instr(lower(c.heading_path), lower(?))`，导致搜索 `'['`、`']'`、`'"'`、`','` 时伪命中 JSON 序列化存储语法。
   - **修复方案**：SQL 查询中使用 SQLite 原生 `EXISTS (SELECT 1 FROM json_each(c.heading_path) WHERE instr(lower(json_each.value), lower(?)) > 0)` 展开数组逐项比对；Python 内部摘要生成也逐项检查标题数组，彻底消除 JSON 括号与逗号的伪命中。
   - **大小写转换对齐**：使用 `_ASCII_LOWER_TABLE` 做仅 ASCII 的小写映射，非 ASCII 字符（中文、Emoji、变音符）1:1 Verbatim 保留，与 SQLite 内置 `lower()` 语义完全一致，杜绝 Unicode `lower()` 产生字符扩增导致高亮定位偏移。
2. **修复 B7（明确 `match_field` 与准确坐标）**：
   - 契约 M1_R3_API.md §2.3 要求：正文命中返回真实 UTF-16 区间；标题命中（`match_field='heading'`）或文件名命中（`match_field='filename'`）时，`match_start_char`、`match_end_char`、`match_chunk_start_char`、`match_chunk_end_char` 必须严格返回 `None` (JSON `null`)。
   - **修复方案**：`_build_snippet_and_coords` 与 `types.py` 均调整为允许 `int | None`，当非正文命中时坐标统一置为 `None`，绝不补 0 或虚构前 80 字符正文。
3. **修复 B8（排序规则与契约冻结对齐）**：
   - 排序优先级严格统一为：`text(1) > heading(2) > filename(3)`，之后按 `c.document_id ASC, c.chunk_index ASC` 稳定输出，确保分页结果确定且可复现。

---

## 2. 完整测试验证结果

测试基于 Python 3.11 标准库 `unittest` 编写，不引入任何未安装的第三方依赖，在当前环境下即开即用、100% 独立运行。

### 2.1 执行命令与汇总结果

- **测试套件 1**：人工设定条目测试 `server/tests/lore_entries/`
- **测试套件 2**：检索服务与回归测试 `server/tests/lore_search/`
- **执行命令**：
  ```bash
  python3 -m unittest discover -s server/tests/lore_entries -v
  python3 -m unittest discover -s server/tests/lore_search -v
  ```
- **测试统计**：**34 passed in 0.079s（共 34 项测试全部绿灯通过，0 失败，0 错误）**

### 2.2 详细测试运行清单

#### 1. 人工设定条目测试集 (`server/tests/lore_entries/` - 18 passed)

```text
test_create_and_get_entry_all_types (test_entries_crud.TestLoreEntriesCRUD.test_create_and_get_entry_all_types)
测试五类设定类型的创建与字段校验。 ... ok
test_database_persistence_and_reopen (test_entries_crud.TestLoreEntriesCRUD.test_database_persistence_and_reopen)
测试真实文件数据库下创建/更新/软删在重启后的一致性。 ... ok
test_delete_entry_revision_conflict_and_idempotency (test_entries_crud.TestLoreEntriesCRUD.test_delete_entry_revision_conflict_and_idempotency)
并发 DELETE 校验：revision 错误报 409 且不抹新版本；成功后重复删除保持幂等。 ... ok
test_homonymous_entries_coexist_not_merged (test_entries_crud.TestLoreEntriesCRUD.test_homonymous_entries_coexist_not_merged)
测试同名不同条目合法共存，绝不被服务端悄悄合并。 ... ok
test_optimistic_concurrency_conflict_409 (test_entries_crud.TestLoreEntriesCRUD.test_optimistic_concurrency_conflict_409)
两人从同 revision 更新仅一人成功，后提交者收到 409 LORE_ENTRY_CONFLICT。 ... ok
test_pagination_and_stable_sorting (test_entries_crud.TestLoreEntriesCRUD.test_pagination_and_stable_sorting)
测试分页与稳定排序（updated_at DESC, id ASC）。 ... ok
test_soft_deleted_world_entries_unreachable (test_entries_crud.TestLoreEntriesCRUD.test_soft_deleted_world_entries_unreachable)
测试世界整体软删除后，该世界下所有条目均不可读写（404 WORLD_NOT_FOUND）。 ... ok
test_transaction_rollback_atomicity (test_entries_crud.TestLoreEntriesCRUD.test_transaction_rollback_atomicity)
测试调用方回滚时，条目与引用同时撤销，不留下半吊子脏数据。 ... ok
test_update_entry_partial_fields_and_revision_increment (test_entries_crud.TestLoreEntriesCRUD.test_update_entry_partial_fields_and_revision_increment)
测试 PATCH 部分字段更新：未传字段保持不变，revision 递增。 ... ok
test_validation_errors (test_entries_crud.TestLoreEntriesCRUD.test_validation_errors)
测试非法标题、超长摘要、未知类型的错误拦截。 ... ok
test_idempotent_migration_preserves_old_data (test_entries_migration.TestLoreEntriesMigration.test_idempotent_migration_preserves_old_data)
测试在已有旧版世界、文档与切片的库上无损执行迁移，且重复运行幂等。 ... ok
test_document_soft_delete_lifecycle_and_needs_source_review (test_entries_sources.TestLoreEntriesSources.test_document_soft_delete_lifecycle_and_needs_source_review)
文档软删后的来源生命周期：保留条目，availability 变为 deleted，confirmed 派生 needs_source_review: True。 ... ok
test_emoji_surrogate_pairs_boundary_validation (test_entries_sources.TestLoreEntriesSources.test_emoji_surrogate_pairs_boundary_validation)
测试包含 Emoji 时的 UTF-16 边界：合法则成功提取，切断代理对则报 422 SOURCE_INVALID_OFFSET。 ... ok
test_server_generated_excerpt_and_length_cap (test_entries_sources.TestLoreEntriesSources.test_server_generated_excerpt_and_length_cap)
测试服务端摘录生成：严格由真实块文本截取（≤120 UTF-16 单位加 '…'），拒绝客户端伪造。 ... ok
test_source_already_deleted_doc_rejected (test_entries_sources.TestLoreEntriesSources.test_source_already_deleted_doc_rejected)
测试引用已软删除的文档必报 422 SOURCE_DELETED。 ... ok
test_source_chunk_mismatch_rejected (test_entries_sources.TestLoreEntriesSources.test_source_chunk_mismatch_rejected)
测试切片 ID 与文档不匹配必报 422 SOURCE_CHUNK_MISMATCH。 ... ok
test_source_cross_world_rejected (test_entries_sources.TestLoreEntriesSources.test_source_cross_world_rejected)
测试跨世界引用拦截：世界 A 的条目引用世界 B 的文档必报 422 SOURCE_CROSS_WORLD。 ... ok
test_source_out_of_range_rejected (test_entries_sources.TestLoreEntriesSources.test_source_out_of_range_rejected)
测试区间非法（负数、反向、超出切片长度）必报 422 SOURCE_OUT_OF_RANGE。 ... ok

----------------------------------------------------------------------
Ran 18 tests in 0.050s
OK
```

#### 2. 检索服务与 B6–B8 回归测试集 (`server/tests/lore_search/` - 16 passed)

```text
test_b6_ascii_case_insensitivity_and_coordinates (test_search_service.TestLoreSearchService.test_b6_ascii_case_insensitivity_and_coordinates)
[R3 回归 B6] ASCII 大小写不敏感且绝对不导致坐标偏移。 ... ok
test_b6_no_json_syntax_pseudo_match (test_search_service.TestLoreSearchService.test_b6_no_json_syntax_pseudo_match)
[R3 回归 B6] 搜索 '['、']' 等 JSON 存储语法符号不应产生虚假命中。 ... ok
test_b7_non_text_coords_are_strictly_none (test_search_service.TestLoreSearchService.test_b7_non_text_coords_are_strictly_none)
[R3 回归 B7] 标题/文件名命中时，坐标严格为 None，绝不填 0 或编造前 80 字。 ... ok
test_b8_sorting_priority_text_over_heading_over_filename (test_search_service.TestLoreSearchService.test_b8_sorting_priority_text_over_heading_over_filename)
[R3 回归 B8] 命中排序严格保证 text(1) > heading(2) > filename(3)。 ... ok
test_chinese_single_char_search (test_search_service.TestLoreSearchService.test_chinese_single_char_search)
测试中文单字短词匹配（如 '刀'、'陆'）。 ... ok
test_chinese_two_char_and_name_search (test_search_service.TestLoreSearchService.test_chinese_two_char_and_name_search)
测试中文双字人名/地名匹配（如 '陆平'、'红枫'、'苏晚'）。 ... ok
test_cross_world_isolation (test_search_service.TestLoreSearchService.test_cross_world_isolation)
测试跨世界严格隔离：搜索世界 A 中的 '陆平' 绝不返回世界 B 的切片。 ... ok
test_emoji_and_utf16_coordinates (test_search_service.TestLoreSearchService.test_emoji_and_utf16_coordinates)
测试包含 Emoji（占用 2 个 UTF-16 code unit 的代理对）情况下的精确定位。 ... ok
test_filename_match (test_search_service.TestLoreSearchService.test_filename_match)
当关键词仅出现在文件名中时，能够正确返回并标注 match_field='filename'。 ... ok
test_heading_path_match (test_search_service.TestLoreSearchService.test_heading_path_match)
当关键词仅出现在标题路径中时，能够正确返回并标注 match_field='heading'。 ... ok
test_pagination (test_search_service.TestLoreSearchService.test_pagination)
测试分页稳定性和 limit/offset。 ... ok
test_realistic_twenty_docs_performance (test_search_service.TestLoreSearchService.test_realistic_twenty_docs_performance)
性能样本证据：在达到 M1 上限约 20 个文档、约 200 个切片规模下测试检索耗时（实测 < 2.5ms）。 ... ok
test_soft_deleted_document_exclusion (test_search_service.TestLoreSearchService.test_soft_deleted_document_exclusion)
测试软删除文档的切片不会被检索出来。 ... ok
test_soft_deleted_world_raises_404 (test_search_service.TestLoreSearchService.test_soft_deleted_world_raises_404)
测试世界被软删除时直接抛出 404 WORLD_NOT_FOUND。 ... ok
test_sql_injection_and_special_characters (test_search_service.TestLoreSearchService.test_sql_injection_and_special_characters)
测试 SQL 注入片段和特殊字符（%、_、'、"、--）作为字面量安全检索。 ... ok
test_validation_errors (test_search_service.TestLoreSearchService.test_validation_errors)
测试参数非法时的错误码与错误信息。 ... ok

----------------------------------------------------------------------
Ran 16 tests in 0.029s
OK
```

---

## 3. 交付文件清单与哈希校验

本交付包所有新增与改动均局限于 Gemini（Ma）独占路径：

| 动作 | 文件路径 | 大小 (Bytes) | SHA-256 校验和 |
|---|---|---|---|
| **ADD** | `server/app/lore_entries/__init__.py` | 1,860 | `7ea2d46e911c4ea212365ca964b071e62c1eb6bcfa58ce88e89456ce452eaee7` |
| **ADD** | `server/app/lore_entries/coords.py` | 2,431 | `a65cf804e178129da5e656da6f01111005a964ca612e52467d1cfa97607a39ba` |
| **ADD** | `server/app/lore_entries/errors.py` | 4,805 | `0a5d487cb1e5b85a1efea03080c57ffad0059c3a2f3a6162fffaea4a3d4d4aa9` |
| **ADD** | `server/app/lore_entries/schema.py` | 3,614 | `1bc7cb7bfd8b813b5e43cfc09fef591beba4e59074d0dbd4c4a6ae0ba09ef1e8` |
| **ADD** | `server/app/lore_entries/service.py` | 22,457 | `ceee707293b6e87f3ca9eec4d59f20663ee33b1e847c2ca1c42f0a831e5f03d5` |
| **ADD** | `server/app/lore_entries/types.py` | 1,856 | `a84cb9b06fb01aeaf291b5853e5e01b39219e830f368f541315fa12d22168393` |
| **MODIFY** | `server/app/lore_search/service.py` | 11,460 | `95a7c293ee0997193b0df3f707f5022a101b0f59c878b1d9bf89635e804f553a` |
| **MODIFY** | `server/app/lore_search/types.py` | 2,756 | `d31295325992985f5298a58a74ec397ea3eb36a9b407bc434220a67e416a575a` |
| **ADD** | `server/tests/lore_entries/__init__.py` | 40 | `6067b8a75e373a6a9be512a8a8163f5d52ad5898fa50be066d7e008fa7a00f86` |
| **ADD** | `server/tests/lore_entries/test_entries_crud.py` | 14,569 | `4c3fe755f1025a1ff7d1c6ee3a06fae981ea7383a042978cf23e2069ce5d341b` |
| **ADD** | `server/tests/lore_entries/test_entries_migration.py` | 4,697 | `baee5eb854c8651fe2ddf1673898f8245b0a34bcf9f53835f8d97034d6185856` |
| **ADD** | `server/tests/lore_entries/test_entries_sources.py` | 20,507 | `432d667cbe5f5b2b2b1d79860b769ea4a569d677a297e68c8309f471a2a11b70` |
| **MODIFY** | `server/tests/lore_search/test_search_service.py` | 24,960 | `a793a38faea515d165f17d3d5b78cb4ef95c0235dcb46a9a3b63ee9a998b3f15` |
| **ADD** | `docs/reports/gemini-ma-r3.md` | 本文件 | — |

---

## 4. DeepSeekHarness（DSH）无损集成建议与接入代码

为方便 DSH 在共享接线文件（`server/app/db.py` 与 `server/app/main.py`）中快速组装，提供以下最小、无冲突的代码 Proposal：

### 4.1 数据库初始化与迁移接入建议 (`server/app/db.py`)

在 `server/app/db.py` 的 `init_db` 函数中，直接引入并调用 `ensure_lore_entries_schema`：

```python
# 建议在 server/app/db.py 顶部追加导入：
from app.lore_entries import ensure_lore_entries_schema

def init_db(path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        _ensure_columns(conn)
        ensure_lore_entries_schema(conn)  # 幂等建表与迁移 lore_entries / lore_entry_sources
        conn.commit()
```

### 4.2 FastAPI 路由端点接入建议 (`server/app/main.py`)

在 `server/app/main.py` 中引入 `lore_entries` 模块并挂载契约规定的 5 个端点：

```python
# 建议在 server/app/main.py 顶部追加导入：
from app.lore_entries import (
    create_entry,
    delete_entry,
    get_entry,
    list_entries,
    update_entry,
    LoreEntryError,
)

# 1. 列表端点
@app.get("/api/v1/worlds/{world_id}/lore-entries")
def api_list_lore_entries(
    world_id: str,
    type: str | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    try:
        with db(settings.db_path) as conn:
            return list_entries(conn, world_id=world_id, type=type, status=status, limit=limit, offset=offset)
    except LoreEntryError as exc:
        raise ApiError(exc.status, exc.code, exc.message, exc.details) from exc

# 2. 新建端点 (HTTP 201)
@app.post("/api/v1/worlds/{world_id}/lore-entries", status_code=201)
def api_create_lore_entry(
    world_id: str,
    payload: dict,
):
    try:
        with db(settings.db_path) as conn:
            return create_entry(conn, world_id=world_id, payload=payload)
    except LoreEntryError as exc:
        raise ApiError(exc.status, exc.code, exc.message, exc.details) from exc

# 3. 详情端点
@app.get("/api/v1/lore-entries/{entry_id}")
def api_get_lore_entry(entry_id: str):
    try:
        with db(settings.db_path) as conn:
            return get_entry(conn, entry_id=entry_id)
    except LoreEntryError as exc:
        raise ApiError(exc.status, exc.code, exc.message, exc.details) from exc

# 4. 部分更新端点 (带 expected_revision 乐观并发)
@app.patch("/api/v1/lore-entries/{entry_id}")
def api_update_lore_entry(
    entry_id: str,
    payload: dict,
):
    expected_revision = payload.get("expected_revision")
    try:
        with db(settings.db_path) as conn:
            return update_entry(conn, entry_id=entry_id, payload=payload, expected_revision=expected_revision)
    except LoreEntryError as exc:
        raise ApiError(exc.status, exc.code, exc.message, exc.details) from exc

# 5. 软删除端点 (HTTP 204，带 expected_revision 乐观并发)
@app.delete("/api/v1/lore-entries/{entry_id}", status_code=204)
def api_delete_lore_entry(
    entry_id: str,
    expected_revision: int,
):
    try:
        with db(settings.db_path) as conn:
            delete_entry(conn, entry_id=entry_id, expected_revision=expected_revision)
            return None
    except LoreEntryError as exc:
        raise ApiError(exc.status, exc.code, exc.message, exc.details) from exc
```

---

## 5. 总结与交付确认

Gemini（Ma）已严格按照第三轮四助手任务总表 `ROUND3.md`、`R3_GEMINI_MA.md` 与冻结契约 `docs/contracts/M1_R3_API.md`（版本 1.0-r3）的全部要求完成交付：
1. **完整闭环**：提供了完整的 `lore_entries` 服务模块、幂等迁移及涵盖 CRUD、同名共存、UTF-16 代理对、文档软删状态派生、并发版本冲突（409）、回滚原子性在内的 18 项单测；
2. **缺口修复**：彻底关闭 B6（消除 JSON 符号伪命中与大小写偏移）、B7（非正文命中坐标严格为 null）、B8（text > heading > filename 优先级排序）并提供 4 项专项回归单测验证；
3. **安全隔离**：全量 34 项测试在标准库 `unittest` 下纯离线运行秒级全绿，未修改任何非独占路径共享文件；
4. **交付物归档**：配套生成标准 Zip 归档包（含 `DELIVERY.md`、`manifest.json`、`patch.diff`、`files/`、`TEST_RESULTS.md`、`INTEGRATION.md`），供用户转交 DeepSeekHarness 集成。
