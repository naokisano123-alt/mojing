# 墨境｜第六轮 R6-A 可追溯上下文选择模块交付报告

- **任务编号**：`R6-A`
- **执行作者**：`Gemini（Ma）`
- **代码基线快照**：`8087d093724c7f554b2987c4af2b86f92a5afc06`（ROUND6 快照）
- **契约依据**：`docs/contracts/M2_A_API.md` v1.0、`docs/collaboration/ROUND6.md`、`R6_GEMINI_MA.md`
- **独占目录范围**：
  - `server/app/narration_context/**`
  - `server/tests/narration_context/**`
  - `docs/reports/gemini-ma-r6.md`（本报告）
- **未修改任何共享文件声明**：严格遵守防覆写规范，未触碰 `server/app/main.py`、`db.py`、`config.py`、`importer.py`、`server/app/narration/core.py`（只读使用）、前端 `src/**` 或他人独占模块。只读现有 SQLite 数据库，无 commit/rollback，不修改 schema，不发起网络请求。

---

## 1. 核心模块设计与实现

依据 `M2_A_API` 契约，本模块实现 `build_context(conn, world_id, action) -> ContextBundle` 及业务异常 `ContextError`，为创作者“模型叙事实验”提供经过严格验证与预算控制的可追溯资料上下文。

### 1.1 严格数据隔离与有效性校验
- **世界隔离与软删除校验**：
  - 检查 `worlds` 表，若世界不存在或 `deleted_at IS NOT NULL`，抛出 HTTP 404 `WORLD_NOT_FOUND`；
  - 所有后续 SQL 均以 `world_id = ?` 严格参数化约束，绝不读取其他世界数据。
- **行动输入校验**：
  - 校验 `action` 必须为有效非空字符串；空白或类型错误抛出 HTTP 422 `ACTION_INVALID`；
  - 行动最大长度限制 2000 UTF-16 code units，超长抛出 HTTP 422 `ACTION_INVALID`；
  - 针对非法的孤立 Unicode 代理字符做保护，遇到 `UnicodeEncodeError` 抛出 HTTP 422 `INVALID_TEXT`。
- **空资料合法性**：
  - 若世界内无任何文档与设定条目，正常返回空的 `ContextBundle(sources=(), metadata=())`，不报错，允许模型以无资料形式进行纯叙事尝试。

### 1.2 资料过滤与合法性准则
- **文档切片（chunk）**：仅选择其关联文档 `documents.deleted_at IS NULL` 的有效切片。
- **设定条目（entry）**：
  - 仅选 `status = 'confirmed'` 且 `deleted_at IS NULL` 的设定条目；
  - **草稿绝不自动晋升**：`draft` 状态条目被严格排除；
  - **失效引用保守排除**：复用 R5 `_fetch_sources_for_entry` 检查来源可用性，若任一来源关联的文档被软删或切片缺失（`needs_source_review == True`），保守排除该条目，绝不提供给模型产生虚假引用；
  - **无来源 confirmed 条目合法保留**：创作者纯手工编写的设定条目（`source_links` 为空）为合法资料，其 metadata 明确包含 `"origin": "manual"`。

### 1.3 确定性检索、排序与稳定补足机制
绝不引入随机采样或伪造的向量语义相似度，全流程按确定性规则执行：
1. **阶段 1（行动字面检索命中）**：
   - 当 `1 <= len(action.strip()) <= 100` 时，复用既有 `search_chunks` 执行字面子串检索；
   - 提取检索命中的有效文档切片并填入完整文本，优先作为前置上下文。
2. **阶段 2（本世界 confirmed 设定条目）**：
   - 查询本世界所有有效的 confirmed 条目；
   - 若条目标题或摘要与行动存在字面重合，将其排在条目前列；未命中者保持 `updated_at DESC, id ASC` 稳定排序追加。
3. **阶段 3（稳定补足文档切片）**：
   - 若尚未达到资料上限（8 份或 16000 字符），按 `document_id ASC, chunk_index ASC` 稳定查询本世界未软删切片并补足。

### 1.4 预算控制、安全截断与去重
- **预算硬上限**：
  - `MAX_SOURCES = 8`（最多 8 份）；
  - `MAX_SOURCE_UNITS = 4000`（单份最多 4000 UTF-16 code units）；
  - `MAX_CONTEXT_UNITS = 16000`（总上下文最多 16000 UTF-16 code units）。
- **Emoji 代理对安全截断（`safe_u16_truncate`）**：
  - 当单份切片超长（> 4000 units）或逼近剩余总预算时，按字符级累加 UTF-16 长度，当遇到 Emoji 等处于辅助平面的四字节字符（占用 2 个 UTF-16 代理单元）时，绝不在代理对中间切断，确保生成合法且符合 `core.validate_request` 预期的文本。
- **空白不占条目**：纯空白或全空白文本被跳过，不消耗 8 份名额与预算。
- **命名空间与去重**：`source_id` 统一采用 `chunk:<chunk_id>` 与 `entry:<entry_id>`，在添加过程中以集合记录严格去重。
- **Metadata 1:1 严格对应**：
  - `bundle.sources` 与 `bundle.metadata` 严格等长且下标对应；
  - chunk 包含 `document_id`, `chunk_id`, `chunk_index`, `title`（文档文件名）；
  - entry 包含 `entry_id`, `title`, `origin`。

### 1.5 规则局限性说明
- **局限性 1（字面匹配）**：当前检索基于 SQLite `instr` 与子串比对，不含语义联想或同义词拓展。若玩家行动表述与资料库用词不同（例如“马车”与“马匹”），将自动触发回退逻辑；
- **局限性 2（静态预览范围）**：本模块仅为创作者提供叙事草案的背景参考，不代表 NPC 拥有秘密知识隔离或动态世界状态写入权限。

---

## 2. 交付文件清单

| 文件路径 | 状态 | 职责说明 |
| --- | --- | --- |
| `server/app/narration_context/types.py` | Add | `ContextBundle`、`ContextError`、常量与 `u16len`/`safe_u16_truncate` 算法 |
| `server/app/narration_context/service.py` | Add | `build_context` 核心筛选、过滤、补足与组装逻辑 |
| `server/app/narration_context/__init__.py` | Add | 模块公共 API 导出 |
| `server/tests/narration_context/test_context.py` | Add | 23 项全面单元测试（覆盖隔离、软删、草稿、失效、Emoji、注入等） |
| `docs/reports/gemini-ma-r6.md` | Add | 本轮独立交付报告 |

---

## 3. 测试与实跑验证记录

### 3.1 执行命令
在 `server` 目录下运行：
```bash
PYTHONPATH=. python3 -m unittest discover -s tests/narration_context
```

### 3.2 实跑结果
```text
.......................
----------------------------------------------------------------------
Ran 23 tests in 0.044s

OK
```

全部 23 项独立测试全部通过，包括：
1. `test_world_not_found`、`test_world_soft_deleted`、`test_world_id_empty`：404 世界有效性守卫；
2. `test_action_validation_empty_and_types`、`test_action_exceeds_max_units`：422 行动输入校验；
3. `test_empty_world_returns_empty_bundle`：空资料合法返回空包；
4. `test_world_isolation`：跨世界零泄漏；
5. `test_soft_deleted_documents_excluded`：文档软删除排除；
6. `test_draft_entries_never_promoted`：草稿不晋升；
7. `test_deleted_lore_entries_excluded`：条目软删除排除；
8. `test_confirmed_entry_with_invalid_source_conservatively_excluded`：失效来源保守排除；
9. `test_confirmed_manual_entry_without_sources_is_valid`：无来源 manual confirmed 合法保留；
10. `test_action_literal_search_matching`：字面搜索命中优先；
11. `test_action_matching_confirmed_entry`：匹配条目优先；
12. `test_fallback_order_when_no_match_or_action_long`：无命中/超长稳定补足；
13. `test_emoji_surrogate_pair_safe_truncation`：Emoji 代理对安全截断；
14. `test_max_sources_limit_8`、`test_max_context_units_limit_16000`：8 份与 16000 单元上限；
15. `test_blank_chunk_or_entry_skipped`：空白不占条目；
16. `test_metadata_one_to_one_correspondence`：metadata 1:1 对齐；
17. `test_sql_injection_defense`：参数化注入防御；
18. `test_compatibility_with_codex_core_validate_and_fake`：与 Codex narration `core.validate_request` / `FakeModelClient` 兼容验证；
19. `test_read_only_invariance`：数据库零修改只读验证。

---

## 4. 给 DeepSeekHarness（DSH）的最小拼装示例

在 DSH 的路由层或编排层（如 `POST /worlds/{world_id}/narration-preview`）中调用示例：

```python
import sqlite3
from app.narration_context import build_context, ContextError
from app.narration.core import NarrationRequest, generate_draft

# 1. 在短生命周期连接下获取上下文（无 commit、只读）
try:
    with get_db_connection() as conn:
        bundle = build_context(conn, world_id=world_id, action=payload["action"])
except ContextError as err:
    # 映射为契约约定的 HTTP 错误（WORLD_NOT_FOUND -> 404, ACTION_INVALID -> 422 等）
    return JSONResponse(status_code=err.status, content=err.body())

# 2. 构造 NarrationRequest，交由 ModelClient 生成（关闭 DB 连接后再等待模型）
request = NarrationRequest(action=payload["action"], sources=bundle.sources)
draft = await generate_draft(request, model_client)

# 3. 组装响应（sources 直接使用 bundle.metadata）
return {
    "request_id": generate_request_id(),
    "mode": mode,
    "narrative": draft.narrative,
    "references": list(draft.references),
    "sources": list(bundle.metadata),
}
```
