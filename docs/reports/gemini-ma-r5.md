# 墨境｜第五轮 R5-A 人工设定条目一致性服务交付报告

- **任务编号**：`R5-A`
- **执行作者**：`Gemini（Ma）`
- **代码基线**：`8bdf92cc88c6d1aba6673e58cf3bd913e1396d35`（ROUND5 起点）
- **契约依据**：`docs/contracts/M1_R3_API.md` 版本 **`1.0-r3`**、`docs/collaboration/ROUND5.md`、`R5_GEMINI_MA.md`、`ROUND4_REVIEW.md`
- **独占目录范围**：
  - `server/app/lore_entries/**`
  - `server/tests/lore_entries/**`
  - `docs/reports/gemini-ma-r5.md`（本报告）
- **未修改任何共享文件声明**：严格遵守防覆写与连接注入规范，未触碰 `server/app/main.py`、`db.py`、`config.py`、`importer.py`、前端 `src/**` 或他人独占模块。

---

## 1. 核心问题定位与修复实现

在 R4 审阅报告（`ROUND4_REVIEW.md`）中，指出了若干关键的并发一致性与坐标语义缺陷。本轮对 `server/app/lore_entries/` 进行了闭环修复：

### 1.1 原子并发与防覆盖修复（关闭 R4-02）
- **问题根源**：原 `update_entry` 先 `SELECT` 读取比较 revision，再执行无版本条件的 `UPDATE lore_entries SET ... WHERE id = ?`。在多连接并发提交时，两个写事务均能成功且均递增到同一版本号，造成后写覆盖前写。
- **修复方案**：
  - 更新操作改为原子 SQL 执行：
    ```sql
    UPDATE lore_entries
    SET type = ?, title = ?, summary = ?, status = ?,
        revision = ?, updated_at = ?
    WHERE id = ? AND revision = ? AND deleted_at IS NULL
    ```
  - 检查受影响行数 `cur.rowcount`：
    - 若 `rowcount == 0`，说明在读取之后条目被并发修改或软删除。此时重新查询数据库状态：若已被软删除，抛出 404 `LoreEntryNotFoundError`；若仍为 active 但版本不符，抛出 409 `LoreEntryConflictError`。
  - **防复活保证**：已软删除的条目在 `WHERE deleted_at IS NULL` 守卫下无法被 `update_entry` 篡改或复活，绝不复活已删除条目。
  - **软删除幂等**：`delete_entry` 同样采用原子 `UPDATE ... WHERE id = ? AND revision = ? AND deleted_at IS NULL`，若检测到已被以同版本删除，保持幂等返回 204（`None`）；版本不一致则抛出 409。

### 1.2 事务原子性与保存点（SAVEPOINT）机制
- **机制引入**：为符合外部连接注入原则（不直接执行 `conn.commit()` 或 `conn.rollback()` 影响外层调用方可能管理的全局事务），我们在 `create_entry`、`update_entry`、`delete_entry` 的执行入口创建独立命名的保存点：
  ```python
  sp_name = f"sp_op_{uuid.uuid4().hex}"
  cur.execute(f"SAVEPOINT {sp_name}")
  try:
      # 执行原子变更与来源校验写入...
      cur.execute(f"RELEASE {sp_name}")
  except Exception:
      cur.execute(f"ROLLBACK TO {sp_name}")
      cur.execute(f"RELEASE {sp_name}")
      raise
  ```
- **效果保证**：若来源校验失败、格式非法或外键错误，SAVEPOINT 自动回滚，绝不残留已递增的 revision 或半份来源记录；真实底层数据库异常不被吞掉伪装成 409。

### 1.3 来源全文绝对 UTF-16 坐标强制与契约守卫（关闭 R4-03）
- **问题根源**：原逻辑包含 `elif 0 <= start_char < end_char <= chunk_u16_len`，当非首个切片传入相对坐标时被错误接受并篡改成错误的绝对坐标。
- **修复方案**：
  - 彻底剔除相对坐标猜测分支，严格校验：
    ```python
    if not (c_start <= start_char < end_char <= c_end):
        raise LoreEntrySourceOutOfRangeError(start_char, end_char, chunk_u16_len)
    ```
  - **类型安全**：明确拒绝布尔值坐标（Python 中 `bool` 是 `int` 子类，原 `isinstance(x, int)` 会放行 `True`/`False`），非整型一律拦截报 422 `VALIDATION_ERROR`。
  - **null 守卫**：若请求体传入 `"source_links": null`，绝不隐式转为空列表，必须抛出 422 校验错误；无来源时需显式传入 `[]`。

### 1.4 编辑时保留失效历史引用语义闭环（关闭 R4-07）
- **场景痛点**：若条目引用的文档后续被软删除，用户仅想修改条目标题时，表单如果全量回传来源列表，会导致后端对所有来源重新按 active 校验而阻断保存。
- **修复方案**：
  - **PATCH 省略保留**：若 `payload` 中未携带 `source_links` 键，后端完全不变更已有来源引用，安全保留。
  - **历史引用白名单比对**：若显式传入 `source_links`，后端读取当前条目已存在的未删除来源映射 `(document_id, chunk_id, start_char, end_char)`：
    - 若传入项完全匹配当前条目已有的历史引用，即便该文档已被软删除或切片缺失，依然允许保留原引用及原摘录，确保编辑不被阻断；
    - 若传入项为新增或修改过的引用，必须严格通过同世界 active 校验；禁止复用或借用其他条目的失效引用。

### 1.5 可用性派生（Availability）准确判断与 additive chunk_index
- **核对文档/块关系**：
  - 文档不存在或 `doc.deleted_at IS NOT NULL` $\rightarrow$ `deleted`；
  - 切片不存在或 `chunk.document_id != doc.id` $\rightarrow$ `unavailable`；
  - 文档与切片均正常匹配且未软删 $\rightarrow$ `active`。
- **additive chunk_index**：在切片记录存在时，在来源 DTO 中可选输出 `chunk_index` 字段，供前端多级导航与定位使用。

---

## 2. 变更文件清单

| 文件路径 | 操作 | 说明 |
| --- | --- | --- |
| `server/app/lore_entries/types.py` | Modify | 在 `LoreEntrySource` 中添加可选 `chunk_index: int` 字段 |
| `server/app/lore_entries/service.py` | Modify | 原子并发 UPDATE、SAVEPOINT 回滚、全文绝对坐标校验、历史引用保留、null 拦截 |
| `server/tests/lore_entries/test_entries_crud.py` | Modify | 新增双连接 Barrier 线程并发测试、软删防复活测试、并发幂等测试、保存点回滚测试 |
| `server/tests/lore_entries/test_entries_sources.py` | Modify | 新增非首块绝对坐标测试（R4-03）、布尔拦截、null 拦截、保留失效引用测试（R4-07）、additive chunk_index 测试 |
| `docs/reports/gemini-ma-r5.md` | Add | 本轮交付与实跑验证报告 |

---

## 3. 测试与实跑验证记录

### 3.1 运行测试命令
在 `server` 目录下执行：
```bash
PYTHONPATH=. python3 -m unittest discover -s tests/lore_entries
```

### 3.2 实际执行输出
```text
----------------------------------------------------------------------
Ran 27 tests in 0.100s

OK
```

全部 27 项单元测试一次性通过，覆盖场景包括：
1. `test_dual_connection_concurrent_update_barrier`：两个真实连接在 WAL 模式下通过 `threading.Barrier` 同步执行并发更新，验证恰好一个连接提交成功，另一个连接被原子条件拦截抛出 409 `LoreEntryConflictError`。
2. `test_concurrent_delete_and_update_cannot_resurrect`：验证软删除后的条目无法通过更新复活，抛出 404。
3. `test_concurrent_duplicate_delete_idempotent`：验证双连接同时软删除时保持幂等（204）。
4. `test_savepoint_rollback_on_update_failure`：验证校验失败时保存点完整回滚，版本号未递增，外层无脏数据。
5. `test_non_first_chunk_absolute_coordinates_and_reject_relative_guess`：验证第二切片 [1610, 3219) 传入 [0, 10) 严格报错 `SOURCE_OUT_OF_RANGE`。
6. `test_source_coordinates_type_safety_and_reject_booleans`：验证坐标传布尔值（`True`/`False`）或字符串报 422 错误。
7. `test_source_links_null_rejected`：验证传入 `null` 时报 422 错误。
8. `test_preserve_historical_deleted_sources_on_update_title`：验证文档软删后修改标题支持省略保留、支持显式保留原引用，并拦截新增或篡改引用。
9. `test_source_availability_and_additive_chunk_index`：验证软删/缺失准确派生及切片索引输出。

---

## 4. 给 DeepSeekHarness（DSH）的集成建议

1. **事务边界无需改动**：
   - 本模块所有函数内部均使用带唯一名称的 `SAVEPOINT` 进行局部保护，并在异常时回滚局部保存点后重新抛出异常；
   - DSH 在外层统一使用 `conn.commit()` 或全局 `conn.rollback()` 依然完全兼容，两层事务语义嵌套工作良好。
2. **HTTP 异常映射支持**：
   - `LoreEntryConflictError` 映射为 HTTP 409，响应体带 `expected_revision` 与 `current_revision`；
   - `LoreEntryNotFoundError` 与 `LoreEntryWorldNotFoundError` 映射为 HTTP 404；
   - 其他 `LoreEntryValidationError`、`LoreEntrySourceOutOfRangeError` 等继承自 `LoreEntryError`，均自带 `status: 422` 与标准错误格式。
