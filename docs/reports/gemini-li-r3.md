# Gemini（Li）｜R3-L 四格式导入正确性与告警质量交付报告

- **角色**：`Gemini（Li）`
- **任务编号**：`R3-L`
- **功能代码基线（code_baseline）**：`87b5b2fed12fad648eaba1930c3968a47a1665df`（PR #2 合并后 main）
- **当前完整快照提交（snapshot_commit）**：`2540a64e8db0b89df6af2eb6b2a046ea1ea39d28`
- **契约依据**：`docs/contracts/M1_R3_API.md`（版本 `1.0-r3` 冻结版）、`docs/collaboration/ROUND3.md`、`docs/collaboration/R3_GEMINI_LI.md`
- **交付包名称**：`gemini-li-R3-2540a64.zip`
- **独占目录范围**：
  - `server/app/import_policy/**`（新增：逐格式限额策略纯函数）
  - `server/tests/import_policy/**`（新增：策略单元测试）
  - `server/app/parsers/**`（维护硬化：UTF-16 计数、结构化告警、安全防护）
  - `server/tests/parsers/**`（维护硬化：R3 回归与安全测试套件）
  - `docs/reports/gemini-li-r3.md`（交付报告）
- **未修改任何共享文件声明**：严格遵守防覆写协议与所有权约定，未修改任何非独占文件（未触碰 `server/app/main.py`、`db.py`、`config.py`、`errors.py`、`importer.py`、`requirements.txt` 或前端目录）。所有接线均以 Proposal 形式在 `INTEGRATION.md` 中给出，由 DSH 统一合入。

---

## 1. 核心业务与交付概览

### 1.1 新增导入限额策略模块 (`server/app/import_policy/`)（彻底关闭 B1 / B2）

在审阅中发现，若主路由在读取文件流之前先执行 `file.read(cfg.max_upload_bytes + 1)`，因默认 `max_upload_bytes` 为 2 MiB，会导致 2–8 MiB 的合法 PDF / DOCX 率先被截断为 413，使得 8 MiB 承诺落空（缺陷 B1）。

Gemini（Li）交付了纯函数驱动的策略模块：
1. **纯策略函数与参数注入**：
   ```python
   def get_max_bytes_for_suffix(suffix: str | None, config: ImportPolicyConfig | dict | None = None) -> int
   def get_upload_read_limit(suffix: str | None, config: ImportPolicyConfig | dict | None = None) -> int
   def normalize_suffix(suffix: str | None) -> str
   def is_supported_suffix(suffix: str | None, config: ... = None) -> bool
   def get_capabilities_max_bytes(config: ... = None) -> dict[str, int]
   ```
2. **逐格式默认限额**：
   - `.txt` / `.md`: 2 MiB (2,097,152 字节)
   - `.pdf` / `.docx`: 8 MiB (8,388,608 字节)
   - 未知格式安全回退（默认 2 MiB）。
3. **主路由防护辅助**：提供 `get_upload_read_limit(suffix) = max_bytes + 1`，为 DSH 提供“先判扩展名，再按格式限额读取 `limit + 1`”的精确接线建议，超限立即返回 413，未超限则正常送入解析。
4. **配置隔离**：不直接读取 `os.environ`，允许由外部调用方传入 `ImportPolicyConfig` 或字典，保证纯净可测。

### 1.2 文本量按 UTF-16 统计（彻底关闭 B3）

1. **UTF-16 统计标准**：在 `types.py` 中引入 `u16len(text: str) -> int`（`len(text.encode("utf-16-le")) // 2`）。Emoji 等 SMP 字符（如 `🏰`、`🐉`）在 Python 原生 `len()` 中仅计为 1，但在 UTF-16 code units 中准确计为 2。
2. **统一限额检查**：在 `pdf.py`、`docx.py`、`extractor.py` 中，全文提取后的文本量计算全面切换为 `u16len`，与浏览器 JavaScript 及分块系统保持 100% 一致。
3. **专用异常映射**：文本量超出上限（`limits.effective_max_units`）时，抛出 `ExtractionLimitError`（对应 HTTP `422 EXTRACTION_LIMIT`，包含 `details: {"limit": N, "actual": M}`），彻底替换原先误报为字节超限的 413。

### 1.3 结构化告警记录 `warning_records`（彻底关闭 B4）

契约要求前端展示机器码告警，且要求针对部分提取必须真产出 `PARTIAL_TEXT`，不得依靠主路由用中文子串模糊猜测。

1. **结构化载体定义**：
   ```python
   class WarningCode:
       PARTIAL_TEXT = "PARTIAL_TEXT"
       EMPTY_PAGES = "EMPTY_PAGES"
       TABLES_FLATTENED = "TABLES_FLATTENED"
       HEADERS_FOOTERS_SKIPPED = "HEADERS_FOOTERS_SKIPPED"
       TEXTBOXES_SKIPPED = "TEXTBOXES_SKIPPED"
       COLUMN_ORDER_UNCERTAIN = "COLUMN_ORDER_UNCERTAIN"
       EXTRACTION_WARNING = "EXTRACTION_WARNING"

   class DocumentWarningRecord(TypedDict, total=False):
       code: str
       message: str
       count: int
   ```
2. **多格式告警生成**：
   - **混合型 PDF**：当部分页面为空白或未提取到文字时，明确输出 `code: "PARTIAL_TEXT"`（携带 `count` 为缺失页数）与 `code: "EMPTY_PAGES"`；诚实描述为“无法确定是否空页或纯图片扫描件”，绝不编造 OCR 检测结果。
   - **DOCX 表格与特殊元素**：当文档包含表格并展平为 Markdown 时，输出 `code: "TABLES_FLATTENED"`（携带 `count` 为表格数）；同时输出 `HEADERS_FOOTERS_SKIPPED` 与 `TEXTBOXES_SKIPPED` 告警。
3. **平滑过渡兼容**：`ParsedDocument` 同时保留 `warnings: list[str]` 与 `warning_records: list[DocumentWarningRecord]`，旧代码与新前端适配层均可无缝消费。

### 1.4 安全防护与格式鲁棒性硬化

1. **DOCX ZIP 安全防护**：
   - 增加压缩包条目数上限校验（`max_zip_entries: int = 1000`），条目数超限抛出 `413 ZIP_BOMB_DETECTED`，有效防范解包 DoS。
   - 保持解包总大小（50 MiB）与解压膨胀率（100x）双重校验。
2. **PDF 加密规范化**：
   - 允许标准空密码解密（无密码但标记加密字典的文档正常读取）。
   - 设置真实密码的 PDF 诚实返回 `422 ENCRYPTED_DOCUMENT`，不让未授权文件进入解析流程。

---

## 2. 测试与验证证据

在无外网依赖的标准库环境中执行单元测试，全部通过：

### 2.1 导入限额策略测试 (`server/tests/import_policy/test_policy.py`)
- **执行命令**：`PYTHONPATH=server python3 -m unittest server/tests/import_policy/test_policy.py`
- **结果**：**7 passed in 0.000s（全部通过）**
  - `test_normalize_suffix`: 扩展名大小写与空格容错。
  - `test_default_format_limits`: TXT/MD 为 2 MiB，PDF/DOCX 为 8 MiB。
  - `test_unknown_format_fallback`: 未知格式安全 fallback。
  - `test_upload_read_limit_plus_one`: 上传读取限量为 limit + 1。
  - `test_is_supported_suffix`: 支持格式判定。
  - `test_config_injection_override`: 外部配置注入与覆盖。
  - `test_get_capabilities_max_bytes`: 能力表结构格式符合 R3 契约。

### 2.2 解析器原有测试套件 (`server/tests/parsers/`)
- **执行命令**：`PYTHONPATH=server:server/tests python3 -m unittest discover -s server/tests/parsers -t server/tests`
- **结果**：**26 passed in 0.295s（全部通过）**
  - 包括原有 PDF/DOCX 格式解析、Markdown 结构保留、损坏包拦截、错误码等测试全部保持绿灯。

### 2.3 R3 专项硬化测试套件 (`server/tests/parsers/test_r3_hardening.py`)
- **执行命令**：`PYTHONPATH=server:server/tests python3 -m unittest server/tests/parsers/test_r3_hardening.py`
- **结果**：**7 passed in 0.024s（全部通过）**
  - `test_u16len_with_emojis`: Emoji 占用 2 码元验证。
  - `test_txt_max_text_units_counts_utf16`: 纯文本按 UTF-16 准确拦截超限（422 EXTRACTION_LIMIT）。
  - `test_pdf_max_text_units_counts_utf16`: PDF 按 UTF-16 准确拦截超限。
  - `test_pdf_partial_text_warning_records`: 混合 PDF 产出 `PARTIAL_TEXT` 机器码与 `count`。
  - `test_docx_warning_records_for_tables`: DOCX 产出 `TABLES_FLATTENED` 机器码与 `count`。
  - `test_docx_max_zip_entries_protection`: DOCX ZIP 条目数过多拦截（413 ZIP_BOMB_DETECTED）。
  - `test_pdf_real_encryption_rejected`: 密码加密 PDF 报 422 ENCRYPTED_DOCUMENT。

- **小结**：Gemini（Li）独占范围内的 40 项单元测试在干净环境中运行全部通过（`40 passed, 0 failed, 0 errors`）。

---

## 3. DeepSeekHarness（DSH）集成建议

### 3.1 主路由上传限额接入 (`server/app/main.py`)

解决 B1 缺陷的核心接线方案：**先取扩展名确定限额，再按限额限量读取 `limit + 1`**：

```python
from app.import_policy import get_max_bytes_for_suffix, get_upload_read_limit, is_supported_suffix
from app.parsers import extract_document, ParseLimits, ExtractionLimitError

@app.post("/api/v1/worlds/{world_id}/documents")
async def api_upload_document(world_id: str, file: UploadFile = File(...)):
    filename = file.filename or "upload"
    suffix = Path(filename).suffix.lower()

    if not is_supported_suffix(suffix):
        raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", f"不支持的文件格式: {suffix}")

    # 1. 依据文件格式取得字节上限，并限量读取 limit + 1
    max_bytes = get_max_bytes_for_suffix(suffix)
    read_limit = max_bytes + 1
    data = await file.read(read_limit)

    # 2. 若实际读取达到 read_limit，确证文件超限，立即返回 413
    if len(data) > max_bytes:
        raise ApiError(
            413,
            "FILE_TOO_LARGE",
            f"文件大小超出限制（该格式上限为 {max_bytes // 1024 // 1024} MiB）。",
            {"filename": filename, "max_bytes": max_bytes, "received_bytes": len(data)},
        )

    # 3. 构造解析限制并执行解析
    limits = ParseLimits(max_bytes=max_bytes, max_text_units=2_000_000)
    try:
        parsed = extract_document(data, filename, limits=limits)
    except ExtractionLimitError as exc:
        raise ApiError(exc.status, exc.code, exc.message, exc.details) from exc
    except ParseError as exc:
        raise ApiError(exc.status, exc.code, exc.message, exc.details) from exc
```

### 3.2 暴露逐格式 Capabilities (`/api/v1/capabilities`)

在 `server/app/main.py` 的 capabilities 端点中直接调用：
```python
from app.import_policy import get_capabilities_max_bytes

@app.get("/api/v1/capabilities")
def get_capabilities():
    return {
        "formats": [".txt", ".md", ".pdf", ".docx"],
        "max_bytes": get_capabilities_max_bytes(),
        "search": True,
        "lore_entries": True,
        "parsers": {"pdf": True, "docx": True},
    }
```
