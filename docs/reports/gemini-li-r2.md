# Gemini（Li）｜R2-L PDF / DOCX 文本提取引擎交付报告

- 角色：Gemini（Li）
- 任务编号：R2-L
- 代码功能基线（code_baseline）：`c83eccb44734dd16ef6da8e0c2382eff4bed99df`
- 当前完整快照提交（snapshot_commit）：`3e900637389b0f9d6ae838d1d62eae0609d4ca34`
- 契约版本：`M1_AB_API.md v0.2` + `ROUND2.md` 拼装约定
- 交付日期：2026-11-08

---

## 1. 任务概述与职责边界

Gemini（Li）在本轮（Round 2）独立承担 **M1-C: PDF 与 DOCX 文本提取独立引擎及解析样本**。

### 独占路径与修改原则
- **独占新建目录**：
  - `server/app/parsers/**`（核心提取引擎、类型与异常定义）
  - `server/tests/parsers/**`（单元测试与可重现测试夹具）
  - `server/requirements-parsers.txt`（解析器独立依赖建议）
  - `docs/reports/gemini-li-r2.md`（本报告）
- **严格受保护文件（未做任何修改，遵循防覆写协议）**：
  - 未修改 `server/app/main.py`
  - 未修改 `server/app/db.py`
  - 未修改 `server/app/config.py`
  - 未修改 `server/app/importer.py`
  - 未修改 `server/app/errors.py`
  - 未修改 `server/requirements.txt`
  - 未修改任何前端文件（`src/**`）或根构建配置

所有的公共入口接线（路由挂载、错误码映射、依赖合并）均作为建议在 `INTEGRATION.md` 中给出，由集成负责人 DeepSeekHarness（DSH）统一接线。

---

## 2. 核心架构与实现细节

### 2.1 统一提取入口
- 函数签名：
  ```python
  def extract_document(
      data: bytes,
      filename: str,
      *,
      limits: ParseLimits | None = None,
  ) -> ParsedDocument
  ```
- 自动根据文件名扩展名（大小写不敏感）分发至对应提取器；
- 兼容现有 `.txt` 与 `.md` 文件通道，使该入口可作为统一的文档提取网关；
- 遇到不支持的文件类型抛出 `415 UNSUPPORTED_MEDIA_TYPE`。

### 2.2 结构化输出与安全配额
- `ParsedDocument`:
  - `text: str`: 提取并规整后的纯文本；
  - `media_type: str`: 如 `application/pdf` 或 `application/vnd.openxmlformats-officedocument.wordprocessingml.document`；
  - `encoding: str`: 统一标识为 `"utf-8"`，明确声明为提取纯文本的字符表示，绝不伪称为二进制原始文件的物理编码；
  - `warnings: list[str]`: 记录非致命降级提示（如跳过空白页、跳过页眉页脚与复杂形状等）；
  - `page_count: int | None`: PDF 返回总页数，DOCX 返回 None；
  - `extracted_chars: int`: 提取的总字符数。
- `ParseLimits`:
  - `max_bytes`: 原始上传文件字节上限（默认 2 MiB，与现有系统一致）；
  - `max_pages`: PDF 最大允许页数（默认 500 页）；
  - `max_extracted_chars`: 提取正文最大字符数（默认 2,000,000 字，防止长文本内存膨胀）；
  - `max_uncompressed_bytes`: DOCX 解包总大小上限（默认 50 MiB，防御 ZIP 炸弹）；
  - `max_uncompressed_ratio`: DOCX 压缩膨胀比率上限（默认 100.0x）。
- `ParseError`:
  - 继承自 `server.app.errors.ApiError`，原生适配 FastAPI 的统一异常响应结构 `{"error": {"code", "message", "details"}}`。

### 2.3 PDF 文本提取引擎（`server/app/parsers/pdf.py`）
- **技术方案**：选用 `pypdf`（BSD-3-Clause 许可证），纯 Python 实现，无 C 编译扩展或系统级动态库依赖。
- **解析流程与安全防护**：
  1. 文件头校验：校验前 1024 字节内必须包含 `%PDF-`，拦截伪造格式；
  2. 加密检测：检查 `reader.is_encrypted`，尝试空密码，无法解密时立即抛出 `422 ENCRYPTED_DOCUMENT`；
  3. 页面上限：页数超过 `limits.max_pages` 抛出 `413 FILE_TOO_LARGE`；
  4. 顺序提取：遍历 `reader.pages`，提取每页文本并规范化换行；
  5. 扫描件拒收（零伪造原则）：如果所有页面均无法提取出文本，判定为纯图片扫描件或空白文件，抛出 `422 SCANNED_OR_EMPTY_PDF`，明确提示不支持 OCR，绝不假装成功；
  6. 混合页警告：当部分页面有文字、部分为空白/扫描页时，提取可用文字，并在 `warnings` 中明确列出缺失文字的具体页码。

### 2.4 DOCX 结构化提取引擎（`server/app/parsers/docx.py`）
- **技术方案**：选用 `python-docx`（MIT 许可证）。
- **解析流程与安全防护**：
  1. ZIP 结构与压缩炸弹防御：先通过 `zipfile` 检查解包大小总和与压缩比率，拦截压缩炸弹（`413 ZIP_BOMB_DETECTED`）；
  2. 核心结构完整性：检查必须存在 `word/document.xml`，防止残缺或伪造 DOCX（`422 CORRUPT_DOCUMENT`）；
  3. 文档流阅读顺序：直接遍历 `doc.element.body`，保持段落（`<w:p>`）与表格（`<w:tbl>`）的原生交错顺序；
  4. 标题级别自动识别与转换：读取 Word 内置 `Heading 1~6` / `标题 1~6` 样式及 `outlineLvl`，转换为 Markdown ATX 语法（`# `、`## `），使得提取出的正文无需修改即可被 `importer.py` 的分块系统自动识别出 `heading_path`；
  5. 表格结构化保留：将表格行列转换为紧凑的 Markdown 表格语法，保持数据关联性；
  6. 范围界定：在 `warnings` 中明确说明不支持页眉、页脚、批注及独立绘图文本框。

---

## 3. 支持边界与格式矩阵

| 格式与场景 | 处理策略 | 判定状态 | 行为与输出表现 |
| :--- | :--- | :--- | :--- |
| **标准文本型 PDF** | 顺序提取各页文字，`\n\n` 分隔 | **可靠支持** | 返回纯文本与页数元数据，`warnings` 附带排版顺序提示 |
| **标准 DOCX（含段落与表格）** | 保留交错顺序，标题转 Markdown `#`，表格转 Markdown 表格 | **可靠支持** | 完整保留结构，与后续 `chunk_text` 标题栈完美对接 |
| **混合型 PDF（部分文字，部分图片）** | 提取文字页，记录空页页码 | **警告支持** | 返回可用正文，并在 `warnings` 中精确指出哪些页码未提取到文本 |
| **DOCX 包含页眉/页脚/文本框** | 提取正文与表格，跳过页眉页脚与浮动画布文本框 | **警告支持** | 返回可用正文，并在 `warnings` 中明确声明被忽略的非正文元素 |
| **多栏排版 PDF** | 按 PDF 内部文本流顺序输出 | **警告支持** | 提示排版顺序可能受数据流影响，与视觉栏位存在差异 |
| **纯图片/扫描件 PDF** | 明确拒绝，禁止假装成功，不擅自调用 OCR | **明确拒绝** | 抛出 `422 SCANNED_OR_EMPTY_PDF` |
| **加密/密码保护 PDF** | 拒绝导入未授权文件 | **明确拒绝** | 抛出 `422 ENCRYPTED_DOCUMENT` |
| **损坏/伪造文档（非标准 PDF/DOCX）** | 校验拦截 | **明确拒绝** | 抛出 `422 CORRUPT_DOCUMENT` |
| **超限文档（文件过大、超页数、解压炸弹）** | 资源配额拦截 | **明确拒绝** | 抛出 `413 FILE_TOO_LARGE` / `413 ZIP_BOMB_DETECTED` |
| **空文件（0 字节或纯空白字符）** | 拒绝空导入 | **明确拒绝** | 抛出 `422 EMPTY_DOCUMENT` |
| **不支持的格式（如 .exe, .png, .rtf）** | 扩展名白名单校验 | **明确拒绝** | 抛出 `415 UNSUPPORTED_MEDIA_TYPE` |

---

## 4. 与既有系统（importer.py）的不变式兼容性验证

提取引擎提取出的正文直接送入 `server.app.importer.normalize` 与 `chunk_text` 进行端到端回归，严格验证了 `docs/contracts/M1_AB_API.md` 第 3.4 节的不变式：
1. **覆盖不变式（Coverage Invariant）**：
   $$\bigcup_{i} \text{chunk}[i].\text{text} \equiv \text{normalized\_text}$$
   拼接所有切片正文与规范化提取文本完全一致，无任何字符漏损或冗余插入。
2. **UTF-16 偏移连续性不变式**：
   - $\text{chunk}[0].\text{start\_char} = 0$
   - $\text{chunk}[i].\text{start\_char} = \text{chunk}[i-1].\text{end\_char}$
   - $\text{chunk}[-1].\text{end\_char} = \text{u16len}(\text{normalized\_text})$
3. **Emoji 与代理对安全**：
   测试了 `🏮`、`⚓`、`📜`、`🧭` 等 4 字节 Emoji，UTF-16 code unit 计数（2 units/字符）完全准确，切片从未切断代理对。
4. **标题路径透传**：
   DOCX 转换出的 `# 标题` 经由 `importer.py` 成功组装为 `heading_path: ['第一章 蒸汽公会', '公会主要人物']`。

---

## 5. 依赖选型与维护性评估

建议依赖集中记录于 `server/requirements-parsers.txt`：
```text
pypdf>=5.0.0,<=6.21.4
python-docx>=1.1.0,<=1.1.2
```

### 选型论证
1. **`pypdf` (BSD-3-Clause)**：
   - 纯 Python 开发，无需系统安装 `poppler`、`mupdf` 等底层 C++ 库，跨平台兼容极佳；
   - 避开了 `PyMuPDF (fitz)` 苛刻的 **AGPL-3.0** 传染性开源许可风险；
   - 相比 `pdfplumber`，无需引入 `Pillow` 等重型图像依赖，内存占用极低。
2. **`python-docx` (MIT)**：
   - 工业级标准库，支持直接通过底层 XML 遍历 body 节点，保障了段落与表格的严密时序。

---

## 6. 测试执行报告

所有测试用例均基于标准 `unittest` 框架编写（可无缝被 `pytest` 自动发现），包含独立的动态内存生成测试夹具（`sample_helpers.py`），完全无需本地中文系统字体或真实外部文件。

### 实际执行结果
- **执行命令**：`python3 -m unittest discover -s server/tests/parsers -t . -v`
- **执行环境**：Python 3.11.2, pypdf 6.21.4, python-docx 1.1.2
- **测试结果**：**26 项全部通过（26 passed, 0 failed, 0 errors, 耗时 0.28s）**
  - `test_docx.py`: 8 项测试通过（标题转换、图文表格交错顺序、空文件拦截、损坏文件拦截、缺失 xml 拦截、ZIP 炸弹拦截、文件大小超限、字符超限）
  - `test_pdf.py`: 10 项测试通过（中文单页提取、多页顺序提取、纯空白/扫描件拒绝、混合页警告提示、加密拦截、头标识损坏拦截、空文件拦截、大小超限、页数超限、字符数超限）
  - `test_extractor.py`: 5 项测试通过（PDF 分发、大小写后缀兼容、DOCX 分发、TXT/MD 兼容通道、非法扩展名 415 拒绝）
  - `test_importer_compat.py`: 3 项测试通过（PDF 覆盖与 UTF-16 连续性不变式、DOCX 标题路径继承识别、Emoji 代理对安全）

---

## 7. 给集成负责人（DSH）的极小接线建议

为协助 DeepSeekHarness 完成后续路由挂载与前端联动，提供以下最小接线差异建议（具体见交付包中的 `INTEGRATION.md`）：

1. **`server/app/main.py`**：
   - 在常量中扩展允许的后缀：
     ```python
     ALLOWED_SUFFIXES = {
         ".txt": "text/plain",
         ".md": "text/markdown",
         ".pdf": "application/pdf",
         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
     }
     ```
   - 在 `upload_document` 中引入提取器：
     ```python
     from .parsers import extract_document, ParseLimits
     ...
     # 替代原有的 decode_document(data, cfg) 针对单一文本的分支：
     parsed = extract_document(data, filename, limits=ParseLimits(max_bytes=cfg.max_upload_bytes))
     text = normalize(parsed.text)
     encoding = parsed.encoding
     media_type = parsed.media_type
     ```
2. **前端接收扩展**：
   - 在 `DocumentUpload.tsx` 的 `<input type="file" accept=".txt,.md,.pdf,.docx" />` 中启用 `.pdf,.docx`。
