# Gemini（Li）｜M3 Wave 3：可续玩迁移、已删除来源墓碑与快照性能交付报告

- **负责方**：`Gemini Li`
- **共同仓库快照（`base_sha`）**：[`22dfaaae63b715708321f2cd532c2b99774bdc95`](https://github.com/naokisano123-alt/mojing/archive/22dfaaae63b715708321f2cd532c2b99774bdc95.zip)（[PR #26](https://github.com/naokisano123-alt/mojing/pull/26) 合入 `main` 后含第三轮任务书的统一基线）
- **协作总表与任务书**：
  - [`docs/collaboration/M3_WAVE3.md`](../collaboration/M3_WAVE3.md)
  - [`docs/collaboration/M3_WAVE3_GEMINI_LI.md`](../collaboration/M3_WAVE3_GEMINI_LI.md)
  - [`docs/contracts/M3_WAVE2_API.md`](../contracts/M3_WAVE2_API.md)、[`docs/M3_DESIGN_DRAFT.md`](../M3_DESIGN_DRAFT.md)
  - [`docs/reports/codex-m3-wave2-completion.md`](codex-m3-wave2-completion.md)
- **独占业务与测试路径**：
  - `server/app/m3/portability/**`
  - `server/tests/m3/portability/**`
  - `docs/reports/gemini-li-m3-wave3.md`
- **防覆写与边界声明**：
  - 本交付包**仅包含 `server/app/m3/portability/`、`server/tests/m3/portability/` 与本报告共 10 个新增文件**，未修改 `server/app/world_transfer.py`、`server/app/db.py`、`server/app/main.py`、其他 M3 子包或前端/Android 工程；
  - **删除或重命名文件**：无（0 删除，0 重命名）。

---

## 1. 交付文件清单

| 文件路径 | 操作类型 | 说明 |
| --- | --- | --- |
| `server/app/m3/portability/__init__.py` | 新增（Add） | 导出可续玩迁移引擎、已删除历史来源墓碑接口、小型快照适配器与性能基准入口。 |
| `server/app/m3/portability/errors.py` | 新增（Add） | 定义 `TransferInvalidError`、`ForgedHistoricalReferenceError`、`CrossWorldSourceBorrowError`、`HistoricalSourceNotCanonError` 等错误类型。 |
| `server/app/m3/portability/schema.py` | 新增（Add） | 幂等创建可审计历史来源墓碑表 `m3_historical_source_refs`（强制 `selectable_as_canon = 0`、`searchable = 0`）。 |
| `server/app/m3/portability/types.py` | 新增（Add） | 定义 `HistoricalSourceRef`、`SnapshotStrategyMetrics` 及版本常量。 |
| `server/app/m3/portability/service.py` | 新增（Add） | 实现 `PortabilitySnapshotAdapter`、`HistoricalAwareEventsAdapter`、`transfer_export`、`import_transfer`、`assert_source_usable_as_current_canon` 等核心逻辑。 |
| `server/app/m3/portability/benchmark.py` | 新增（Add） | 可重现合成负载下的检查点大小、导出/导入/恢复时间测量，以及全量（`full`）、压缩（`compressed_zlib`）、增量（`incremental_delta`）三方案实现与对照。 |
| `server/app/m3/portability/fixtures.py` | 新增（Add） | 合成上传文件包装器与包含已软删除文档/设定条目、v4 存档及多分支检查点的测试 Fixture。 |
| `server/tests/m3/portability/__init__.py` | 新增（Add） | 可续玩迁移单元测试包初始化。 |
| `server/tests/m3/portability/test_deleted_sources_and_audit.py` | 新增（Add） | 验证 v4 已删除来源迁移、事件与知识历史保留、不可误用为当前 Canon、不恢复为可搜索条目、二次回导、旧分支续玩、伪造引用拦截与跨世界借用 ID 拦截。 |
| `server/tests/m3/portability/test_compatibility_and_performance.py` | 新增（Add） | 验证 v1–v4 全版本兼容读取、同一世界二次导入隔离、ZIP 限制、错误回滚及快照性能基准对照。 |
| `docs/reports/gemini-li-m3-wave3.md` | 新增（Add） | 本交付报告、格式兼容性说明与公共接线示例。 |

---

## 2. 已删除资料导致 v4 存档迁移失败的根因与修复设计

### 2.1 根因分析
在现有 `server/app/world_transfer.py` 中：
1. `_world_payload` 仅导出 `deleted_at IS NULL` 的活跃文档（`documents`）、分块（`chunks`）和设定条目（`lore_entries`）。
2. 当用户在开局或中途基于某份文档分块（`chunk:<id>`）或设定条目（`entry:<id>`）生成了世界事件（写入 `world_events.source_ids_json`、`world_event_revisions` 及 `adventure_checkpoints.instance_snapshot`），随后在资料库中**软删除**了该文档或条目，导出再导入时，`source_map` 中仅包含未删除的活跃资料映射。
3. `_restore_instance` 调用 `world_events.restore(..., source_id_map=source_map, allowed_source_ids=set(source_map.values()))` 时，因已删除的历史 `source_id` 不在 `allowed_source_ids` 内，触发 `SnapshotRestoreError("SNAPSHOT_SOURCE_UNRESOLVED")`，被外层捕获并转为 `422 WORLD_TRANSFER_INVALID`，导致整个 v4 存档无法迁移，同时本地检查点恢复（`checkpoints.restore`）也会因 `_discover_world_canon_sources` 过滤了 `deleted_at IS NULL` 而失败。

### 2.2 可审计的历史引用墓碑设计（`HistoricalSourceRef` & `m3_historical_source_refs`）
1. **独立墓碑表存储，零 Canon 污染、零搜索复活**：
   - 新增专用表 `m3_historical_source_refs(world_id, source_id, original_source_id, kind, title, document_filename, entry_type, deleted_at, content_sha256, status='deleted_historical', selectable_as_canon=0, searchable=0)`。
   - **绝不向 `documents`、`chunks`、`lore_entries` 表插入已删除资料**：因此资料库列表、`search_chunks` 全文检索以及 `events.select_canon_sources` 永远不会读到已删除资料，彻底杜绝“把删除资料恢复为可搜索条目”或“被误用为当前 Canon”。
   - 提供显式守卫 `assert_source_usable_as_current_canon(conn, world_id, source_id)`：一旦任何上层逻辑尝试将历史墓碑 ID 当作当前活跃 Canon 选用，立即抛出 `409 HISTORICAL_SOURCE_NOT_CANON`。
2. **四分类来源核验（防伪造、防跨世界借用）**：
   - `PortabilitySnapshotAdapter.validate_and_prepare_historical_sources` 与 `discover_world_source_catalog` 将快照中出现的每个 `source_id` 严格划分为四类：
     1. **当前世界活跃来源（`active_sources`）**：正常按 `active_source_map` 映射为新世界的活跃 UUID；
     2. **当前世界已软删除的合法历史来源（`deleted_historical_sources` / `historical_sources`）**：核对墓碑具备合法的 `chunk:`/`entry:` 前缀、非空 `deleted_at`、标题且 `selectable_as_canon=False, searchable=False`，为其分配目标世界内一致的新墓碑 UUID（同一存档内主线实例、历史回合与多个检查点共享同一重映射 UUID），写入 `m3_historical_source_refs` 并加入 `world_events.restore` 的允许集合；
     3. **其他世界的来源 ID（`other_world_sources`）**：若某 `source_id` 存在于数据库中其他 `world_id` 下且从未在源世界存在（即使攻击者在上传 JSON 中伪造了同名 `historical_sources` 墓碑），立即抛出 `422 WORLD_TRANSFER_INVALID`（`CrossWorldSourceBorrowError`, `reason_code="CROSS_WORLD_SOURCE_FORBIDDEN"`）；
     4. **从未存在的伪造引用**：既不在活跃来源中，也无合法墓碑或本地软删除证据的 ID，立即抛出 `422 WORLD_TRANSFER_INVALID`（`ForgedHistoricalReferenceError`, `reason_code="FORGED_HISTORICAL_SOURCE"`），并通过 `SAVEPOINT` 原子回滚整个导入事务。
3. **小型适配器兼容本地检查点恢复与跨设备二次回导**：
   - `HistoricalAwareEventsAdapter` 实现了 `DomainAdapter` 协议：在 `create_checkpoint` 时自动将引用的软删除来源记入 `events.historical_sources`，在 `checkpoints.restore` 时自动校验并放行合法的历史墓碑引用；
   - 导入后的新世界再次调用 `transfer_export` / `build_export_payloads` 导出时，自动从 `m3_historical_source_refs` 读取继承的历史墓碑，支持无限次跨设备回导（Re-export & Re-import）。

---

## 3. 快照性能实测与「全量 / 压缩 / 增量」方案对照

在沙箱环境（Python `3.11.2` + SQLite 3）下，使用 `app.m3.portability.run_snapshot_performance_benchmark(init_db, num_checkpoints=6)`（包含 2 份文档、2 条设定、4 条主线事件、6 轮事件修订与后果、6 条玩家知情记录、6 个完整检查点，其中第 2 与第 3 个检查点特意构造为**同一游戏分钟 `world_minute=60` 但不同实例版本 `world_revision=2 vs 3`**）实测结果如下：

| 方案 | 6 个检查点总序列化大小 | 平均单检查点大小 | 相对全量体积比 | 构建耗时 (ms) | 导出耗时 (ms) | 导入耗时 (ms) | 单次恢复耗时 (ms) | 同分钟不同版本精确恢复 | 数据库内 JSON 直接可读 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **全量快照（`full`，当前默认）** | `102,629 B` (~100.2 KB) | `17,104 B` (~16.7 KB) | `100.0%` | `18.60 ms` | `6.54 ms` | `30.51 ms` | `2.31 ms` | ✅ 精确恢复 (`rev 2` vs `rev 3`) | ✅ 是 |
| **压缩快照（`compressed_zlib`）** | `14,000 B` (~13.7 KB) | `2,333 B` (~2.3 KB) | `13.64%` | `2.15 ms` | `5.56 ms` | `27.46 ms` | `2.46 ms` | ✅ 精确恢复 (`rev 2` vs `rev 3`) | ❌ 否（Base64 二进制） |
| **增量快照（`incremental_delta`）** | `57,312 B` (~56.0 KB) | `9,552 B` (~9.3 KB) | `55.84%` | `0.71 ms` | `5.23 ms` | `28.98 ms` | `2.55 ms` | ✅ 精确恢复（需依赖基线链） | ✅ 是（需合并重建） |

### 3.1 是否引入新格式及升级/降级表现说明
- **不引入破坏性的顶层 v5 存档格式**：
  - 导出仍保持 `format="mojing-world-saves", version=4`（当不含 M3 实例时保持 v2/v3，世界观保持 v1/v2）。
  - 对于引用了已删除资料的事件快照，仅在 `domains.events` 内部以**可选加性字段** `historical_sources: list[HistoricalSourceRef]` 携带墓碑元数据；当世界未删除任何被引用资料时，生成的 JSON 与原 v4 字节级完全一致。
- **升级表现（旧版 v1–v4 存档导入当前版本）**：
  - **v1 / v2 / v3 存档**：自动补齐 `world_openings=[]`、`adventure_memory_events=[]`、`memory_cursor=0`、`opening_json=None`，并在导入后为旅程按已确认分钟初始化空 M3 实例，旧存档与旧分支 100% 可续玩。
  - **旧版 v4 存档（未含 `historical_sources` 字段，但在本地源世界删除了资料后单独导入 `saves.json`）**：自动从本地源世界的软删除记录提取墓碑并完成恢复。
- **降级表现（当前版本导出的存档导入未合入本包的旧版 v4 代码）**：
  - 若旅程未引用已删除资料，导出的 v4 存档在旧版代码中可直接无损导入；
  - 若旅程引用了已删除资料，当前版本导出的 `events` 快照带有 `historical_sources`，旧版 `events.restore` 会忽略未知字典键，仅因旧版 `world_transfer.py` 缺少墓碑白名单而报原有的 `WORLD_TRANSFER_INVALID`，不会造成旧库崩溃或数据损坏。
  - 同时，`benchmark.py` 中已一并交付 `encode_compressed_snapshot` / `decode_snapshot_envelope` 与增量计算函数，若未来超长旅程需要开启透明压缩，解码器已具备无缝识别能力。

---

## 4. 给 DeepSeekHarness 的公共迁移路由最小接线方式

DeepSeekHarness 在公共接线 PR 中，只需在 `server/app/world_transfer.py`（或 `main.py` / `db.py`）做如下 3 行委托接线即可：

```python
# 1. 在 server/app/db.py 的 init_db() 末尾补齐幂等建表与适配器挂载：
from .m3.portability import ensure_portability_schema, install_portability_adapters

ensure_portability_schema(conn)
install_portability_adapters()

# 2. 在 server/app/world_transfer.py 中将导出与导入委托给 m3.portability：
from .m3.portability import import_transfer, transfer_export
```

---

## 5. 独立测试命令与验证结果

在沙箱环境（Python `3.11.2`）下实际执行：

```bash
$ PYTHONPATH=server python3 -m unittest discover -s server/tests/m3/portability -t server -v
test_snapshot_performance_benchmark_and_same_minute_multi_revision_restore (tests.m3.portability.test_compatibility_and_performance.TestCompatibilityAndPerformance.test_snapshot_performance_benchmark_and_same_minute_multi_revision_restore)
运行可重现快照性能基准，验证全量、压缩与增量三方案对照及同分钟不同版本的精确恢复。 ... ok
test_v1_v2_v3_v4_backward_compatibility_and_reimport_isolation (tests.m3.portability.test_compatibility_and_performance.TestCompatibilityAndPerformance.test_v1_v2_v3_v4_backward_compatibility_and_reimport_isolation)
验证 v1、v2、v3、v4 各版本世界观与存档均可正常导入，且同一世界二次导入生成完全独立的副本。 ... ok
test_zip_security_limits_and_mismatch_errors (tests.m3.portability.test_compatibility_and_performance.TestCompatibilityAndPerformance.test_zip_security_limits_and_mismatch_errors)
验证 ZIP 路径穿越、多余文件、不匹配摘要、缺失世界观及超大文件限制均被准确拦截。 ... ok
test_forged_reference_and_cross_world_borrowed_id_rejected_with_atomic_rollback (tests.m3.portability.test_deleted_sources_and_audit.TestDeletedSourcesAndAudit.test_forged_reference_and_cross_world_borrowed_id_rejected_with_atomic_rollback)
验证伪造历史来源引用与跨世界借用同名 ID 均被拒绝，且数据库完整回滚无残留。 ... ok
test_saves_only_import_after_source_deleted_in_local_world (tests.m3.portability.test_deleted_sources_and_audit.TestDeletedSourcesAndAudit.test_saves_only_import_after_source_deleted_in_local_world)
验证当本地世界存在已软删除资料且用户仅上传 saves.json 时，仍可基于源世界软删除记录生成可审计墓碑并成功恢复。 ... ok
test_v4_deleted_sources_migrate_cleanly_preserve_history_and_never_become_canon_or_searchable (tests.m3.portability.test_deleted_sources_and_audit.TestDeletedSourcesAndAudit.test_v4_deleted_sources_migrate_cleanly_preserve_history_and_never_become_canon_or_searchable)
验证 v4 存档引用已软删除资料后成功迁移、保留事件与知识历史、不恢复为可搜索条目、不可用作当前 Canon，且支持二次回导与旧分支续玩。 ... ok

----------------------------------------------------------------------
Ran 6 tests in 0.277s

OK
```
