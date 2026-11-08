# Gemini（Li）｜R5-L 独立导入解析、逐步预算与告警规范化交付报告

- **角色**：`Gemini（Li）`
- **任务编号**：`R5-L`
- **功能代码基线（code_baseline）**：`8bdf92cc88c6d1aba6673e58cf3bd913e1396d35`
- **当前完整快照提交（snapshot_commit）**：`1e5ea7776576e4ce4b56b2236c3661ddc81e0971`（包含 Codex 来源导航修复与 Round 5 协作规约）
- **依据契约与规范**：`docs/collaboration/ROUND5.md`、`docs/collaboration/R5_GEMINI_LI.md`、`docs/collaboration/R4_GEMINI_LI.md`、`docs/contracts/M1_R3_API.md`
- **交付包名称**：`gemini-li-R5-1e5ea77.zip`
- **独占目录范围**：
  - `server/app/import_policy/**`（维护扩展：告警规范化纯函数 `normalize_warning_records` 导出）
  - `server/tests/import_policy/**`（维护扩展：告警转换单测套件 `test_warnings.py`）
  - `server/app/parsers/**`（维护硬化：逐步 UTF-16 预算累计与 PDF 诚实告警纠正）
  - `server/tests/parsers/**`（维护硬化：逐步预算与诚实告警单测 `test_r5_progressive_budget.py`）
  - `server/tests/import_pipeline/**`（新增：真实 HTTP 导入矩阵与零残留测试套件）
  - `docs/reports/gemini-li-r5.md`（本交付报告）
- **防覆写与所有权声明**：严格遵守 R5 协作边界，未修改任何非独占共享文件（未修改 `server/app/main.py`、`db.py`、`config.py`、共享依赖配置或前端目录）。所有对共享主路由的建议均以独立纯函数及单行接线 Proposal 呈现，由 DeepSeekHarness 统一审查合入。

---

## 1. 核心交付块一：告警规范化与诚实语义 (`normalize_warning_records`)

在 R4 审阅与 R5 任务书中明确指出，主路由原先直接读取字典或粗暴转换存在 TypedDict 兼容性与 count 缺省时的边界缺陷，且历史上存在依靠中文关键词模糊推断机器码的问题。

Gemini（Li）在 `server/app/import_policy/warnings.py` 交付了纯函数驱动的规范化转换器：
1. **输入健壮性与 TypedDict 兼容**：
   - 支持 `DocumentWarningRecord`、普通 `dict` 以及含有非字典脏项的任意序列，自动过滤非字典、无 code 或空白 code 的无效项。
   - `count` 字段完全可缺省：当 `count` 存在且为有效非负整数时予以保留；缺失或为 `None` 时输出不含 `count` 键的精简字典，杜绝任何 Key 缺失异常。
2. **合法未知码完全保留**：
   - 对任何非空白字符串 `code`（包含未来可能扩展的机器码）予以原样保留，不设封闭白名单硬编码拦截。
3. **严格 JSON 安全**：
   - 输出纯净的 `list[dict[str, Any]]`，仅含 Python 原生基础类型（`str`、`int`），可直接供 `json.dumps()` 或 FastAPI JSON 响应序列化，彻底避免非标准对象或异常污染。
4. **告警去重与 count 补齐**：
   - 基于 `(code, message)` 唯一键进行精准去重；若后续重复项包含有效 `count` 而前项缺失，自动补齐 `count` 数值。
5. **诚实回退（彻底杜绝中文子串猜测）**：
   - 严格遵循规约：**仅当 `records` 完全无有效结构化项时**，才触发对 `legacy_warnings` 字符串列表的回退消费；
   - 回退转换时，统一使用通用告警机器码 `EXTRACTION_WARNING`（`code: "EXTRACTION_WARNING"`），绝不根据中文子串（如出现“页眉”就武断猜测 `HEADERS_FOOTERS_SKIPPED`）进行不可靠推断。

### 给 DeepSeekHarness 的主路由最小接线建议

在 `server/app/main.py` 中，DSH 仅需替换原有临时 dict 复制逻辑为如下单行调用：

```python
# 在 server/app/main.py 顶部导入：
from app.import_policy import normalize_warning_records

# 在 POST /api/v1/worlds/{world_id}/documents 路由提取正文后（约 461-466 行）：
# 原代码：
# records = getattr(parsed, "warning_records", None) or []
# if records:
#     warnings = [dict(record) for record in records]
# else:
#     warnings = [_warning_payload(item) for item in parsed.warnings]

# 建议替换为：
warnings = normalize_warning_records(
    getattr(parsed, "warning_records", None),
    getattr(parsed, "warnings", ()),
)
```

---

## 2. 核心交付块二：逐步累计 UTF-16 文本量预算与 PDF 告警纠正

### 2.1 逐步累加 UTF-16 预算（熔断防膨胀）
在原实现中，PDF 会先全部遍历所有页面并拼接为巨型字符串后才判断文本量超限；DOCX 也会将所有段落与表格收集完毕再统一校验。这导致超限文档依然会引起巨大的内存分配开销。

本次在 `pdf.py` 与 `docx.py` 中实现了精确的逐步累计预算：
- **PDF 按页逐步累计**：
  - 维护 `accumulated_units`；
  - 每处理一页有效文本 `normalized_page`，计算该页的 UTF-16 码元 `u16len(normalized_page)` 以及拼接分隔符 `"\n\n"` 的码元（2 个码元）；
  - 候选总码元 `candidate_units = accumulated_units + delimiter_units + page_units`；
  - 若 `candidate_units > effective_limit`，**立即抛出 `ExtractionLimitError` 中断后续提取**，不再解析后续页面。
- **DOCX 按块（段落/表格）逐步累计**：
  - 遍历 `body` 时，对每个有效段落或 Markdown 转换后的表格实时计算码元与分隔符开销；
  - 一旦累积超过 `effective_limit`，立即熔断抛出 `ExtractionLimitError`，彻底停止读取剩余正文。

### 2.2 资源隔离与防御边界说明（诚实合规说明）
- **保障范围**：逐步累计 UTF-16 码元有效阻断了文本层面的大内存分配与字符串 join 拼接膨胀，能够在正文规模失控时及早熔断。
- **限制与真实边界**：
  由于 `pypdf` 与 `python-docx` 的单页/单元素解析目前仍在当前 Python 进程内运行，若单个页面包含数十万微小复杂矢量图元、极其庞大的嵌套字体流或构造的畸形流，解析该单页时的瞬间 CPU / 内存消耗**不受本计数器硬隔离**。在未引入系统级独立沙箱子进程（如 OS 级超时与 cgroups 隔离）前，不能宣称已 100% 杜绝底层单页的资源耗尽 DoS。

### 2.3 PDF 诚实告警语义纠正
- **移除不实的 `HEADERS_FOOTERS_SKIPPED`**：
  原代码在无法分离页眉页脚时误导性地输出了 `HEADERS_FOOTERS_SKIPPED`，导致调用方误以为已成功剔除页眉页脚。本次已彻底移除该虚假机器码，改用符合实际的 `COLUMN_ORDER_UNCERTAIN`（排版顺序受数据流影响）与 `EXTRACTION_WARNING`（无法独立分离页眉、页脚及复杂矢量标注）。
- **空白页与异常页诚实描述**：
  未提取到文字的页面在告警消息中明确表述为“可能为空白页、格式异常或未嵌入文字的图片”，不再武断判定为“纯图片扫描件”，真实保留 `PARTIAL_TEXT`（携带缺失页数 `count`）与 `EMPTY_PAGES`。

---

## 3. 核心交付块三：真实 HTTP 导入回归测试矩阵 (`import_pipeline/`)

为满足 R5 真实端到端验收要求，Gemini（Li）新增了独立的端到端 HTTP 测试套件：
- 目录：`server/tests/import_pipeline/`
- 工具类：`pipeline_helpers.py`（提供隔离临时 SQLite 数据库管理、TestClient 上下文管理器、文档创建与全文/切片拉取辅助函数，不修改共享 `conftest.py`）。
- 测试矩阵文件：`test_import_matrix.py`。

### 测试矩阵覆盖场景：
1. **四格式 201 导入与切片无缝覆盖 (`test_four_formats_201_and_persistence_across_restart`)**：
   - 端到端测试 TXT、MD、PDF、DOCX 真实上传返回 201 `status: ready`；
   - 校验切片首尾边界 `start_char` 从 0 开始、`end_char` 覆盖至 `text_length`，切片首尾连续拼接 100% 还原 raw 全文；
   - 跨重启测试：销毁当前 TestClient，使用相同 `db_path` 重新初始化 `create_app`，验证 4 个文档及其切片与元数据完整持久化且立即可读。
2. **结构化告警与 count 缺省安全 (`test_rich_document_structured_warnings`)**：
   - 验证普通 PDF 产出 `COLUMN_ORDER_UNCERTAIN` 与 `EXTRACTION_WARNING`，无 `HEADERS_FOOTERS_SKIPPED`，且无 count 时不引发 500；
   - 验证包含表格与段落的 DOCX 产出 `TABLES_FLATTENED`（`count=1`）、`HEADERS_FOOTERS_SKIPPED` 与 `TEXTBOXES_SKIPPED`。
3. **混合页 PDF 的 PARTIAL_TEXT 真实保留 (`test_mixed_pdf_partial_text_warning`)**：
   - 混合有效页与空页的 PDF 导入成功，验证产出 `PARTIAL_TEXT`（`count=1`），正文提取完整。
4. **UTF-16 Emoji 边界与超限熔断 (`test_utf16_emoji_boundary_and_limit_enforcement`)**：
   - 导入高平面 Emoji（🏰🐉🗡️🛡️✨），验证文本长度按 UTF-16 码元准确统计；
   - 注入较小上限配置，验证超限时准确返回 HTTP `422 EXTRACTION_LIMIT` 并携带 `limit` 与 `actual` 详情。
5. **异常与恶意输入零残留防护 (`test_corrupt_encrypted_fake_format_zero_residue`)**：
   - 纯空文件 -> 422 `EMPTY_DOCUMENT`；
   - 伪造 PDF（纯文本充当 .pdf）-> 422 `PDF_CORRUPTED` / `CORRUPT_DOCUMENT`；
   - 损坏 PDF（结构破损）-> 422；
   - 纯空白/无文字扫描件 PDF -> 422 `PDF_SCANNED`；
   - 真实密码加密 PDF -> 422 `PDF_ENCRYPTED`；
   - 伪造 DOCX（非合法 zip）-> 422 `DOCX_INVALID` / `CORRUPT_DOCUMENT`；
   - 超限文件（TXT > 2 MiB）-> 413 `FILE_TOO_LARGE`；
   - **零残留断言**：上述全部失败场景触发后，查询世界文档列表，`total == 0`，确认数据库内无任何残留孤儿切片或半拉子文档记录。
6. **重复文档 409 拦截与覆盖 (`test_duplicate_document_rejection_and_override`)**：
   - 验证同内容再次上传准确返回 409 `DUPLICATE_DOCUMENT`；
   - 验证携带 `allow_duplicate=true` 时顺利返回 201 允许并存。

---

## 4. 测试实跑与验证结果

在真实沙箱环境中，运行全部 Li 独占模块与测试套件，**58 项测试全部通过，0 失败，0 错误**：

### 命令 1：解析层单元测试（含 R5 逐步预算与诚实告警）
```bash
PYTHONPATH=server:server/tests python3 -m unittest discover -s server/tests/parsers -t server/tests -p "test_*.py"
```
**运行结果**：
```text
Ran 37 tests in 0.362s
OK
```

### 命令 2：限额与告警规范化策略测试（含 normalize_warning_records）
```bash
PYTHONPATH=server:server/tests python3 -m unittest discover -s server/tests/import_policy -t server/tests -p "test_*.py"
```
**运行结果**：
```text
Ran 15 tests in 0.001s
OK
```

### 命令 3：真实 HTTP 导入回归测试矩阵
```bash
PYTHONPATH=server:server/tests python3 -m unittest discover -s server/tests/import_pipeline -t server/tests -p "test_*.py"
```
**运行结果**：
```text
Ran 6 tests in 0.311s
OK
```

**测试汇总**：37 + 15 + 6 = 58 项单元与集成测试均 100% 绿灯。

---

## 5. 变更清单与哈希记录 (Manifest Summary)

| 操作 | 路径 | 说明 |
|---|---|---|
| `modify` | `server/app/import_policy/__init__.py` | 导出 `normalize_warning_records` 与常量 |
| `add` | `server/app/import_policy/warnings.py` | 结构化告警规范化纯函数实现 |
| `add` | `server/tests/import_policy/test_warnings.py` | 告警规范化完整单元测试 |
| `modify` | `server/app/parsers/pdf.py` | 逐步 UTF-16 预算累计、超限及早熔断、移除假 HEADERS_FOOTERS_SKIPPED |
| `modify` | `server/app/parsers/docx.py` | 逐步 UTF-16 预算累计、超限及早熔断 |
| `add` | `server/tests/parsers/test_r5_progressive_budget.py` | 逐步预算与告警语义纠正测试 |
| `add` | `server/tests/import_pipeline/__init__.py` | 导入管线测试包标识 |
| `add` | `server/tests/import_pipeline/pipeline_helpers.py` | 隔离测试客户端与重启动作辅助类 |
| `add` | `server/tests/import_pipeline/test_import_matrix.py` | 端到端 HTTP 导入回归矩阵（5 大类场景） |
| `add` | `docs/reports/gemini-li-r5.md` | 本交付报告 |
