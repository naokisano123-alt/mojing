# R4-L｜Gemini（Li）：真实导入管线回归与解析预算

从同一 R4 快照独立完成大块后端任务，交包经用户给 DeepSeekHarness。先读 ROUND4/审阅；不重做已完成的 UTF-16 计数和 warning_records，要让它们经过真实路由可靠落库。

## 独占范围

server/app/parsers/**、server/app/import_policy/**；server/tests/parsers/**、server/tests/import_policy/**；新增 server/tests/import_pipeline/**（内放自己的 fixtures/helpers，不改共享 conftest）；docs/reports/gemini-li-r4.md。不修改 main.py/db.py/config.py、依赖、共享 HTTP 旧测试；必要接线由 DSH 做。

## 交付块一：警告标准化与诚实语义

新增 import_policy/warnings.py，暴露 ROUND4 指定的 normalize_warning_records(records, legacy_warnings=()) -> list[dict]，模块 export 保持兼容。按当前 TypedDict 读值、count 缺省合法，输出 JSON 可序列化；明确处理坏形状、未知 code、legacy 字符串与空列表。只在 records 无有效项时做 legacy 通用回退；不靠中文子串猜 code，不重复同一告警。DSH 调用此函数，不要另改主路由抢文件。

PDF 当前声称 HEADERS_FOOTERS_SKIPPED，却只是不能分离页眉页脚：删除虚假“已跳过”机器语义，改用真实 COLUMN_ORDER_UNCERTAIN 或 EXTRACTION_WARNING 表达限制。保留 PARTIAL_TEXT/EMPTY_PAGES 对未提取页面的提示；异常页与空白/图片页不能武断等同检测到扫描件。DOCX 的提示也应区分“该内容在范围外”和“检测到并确实跳过了若干项”。

## 交付块二：提前预算与格式健壮性

- PDF 每页加入前、DOCX 每块加入前累计 UTF-16（包括分隔符），超过 effective_max_units 即停止，不先全部 join 才检查；规范化后最终守门仍由主路由保留。
- 保持正文顺序、标题、表格、UTF-16 来源语义及旧别名兼容；表格中管道符/换行应以约定纯文本保留，不悄悄改变单元格关系。
- 保留 ZIP 项数/解压总量/膨胀率约束、加密/损坏/空文档处理。检查单页 extract_text 抛异常、后续 reader.pages 访问抛异常能映射稳定错误，不能泄漏栈或原始文件内容。
- 当前每次 page.extract_text/python-docx 仍在进程内运行，逐步计数不保证单页内存或总CPU硬上限。说明真实保障与后续进程隔离建议，不宣称已防御所有资源耗尽；不要本轮引入大型作业系统。

## 交付块三：HTTP 成功路径回归矩阵

在 server/tests/import_pipeline/** 用项目真实 create_app + TestClient + 临时数据库，合成小 PDF/DOCX（可复用已有 sample_helpers）和 TXT/MD。不 mock extract_document 来绕开错误，不调用公开网络，不提交真实书籍。

关键验证：四格式201→GET详情/raw/chunks→重建app后仍可读；包含结构化 warning 的普通 PDF/DOCX；混合 PDF 的 PARTIAL_TEXT；count 缺省不会500；UTF-16 emoji边界；超限/损坏/纯空/加密/格式伪造稳定错误且无文档或切片残留；同内容409与来源/文本一致。可参数化减少重复，无须做所有无关功能的测试。

本快照会在正常富文档上传处重现 AttributeError；这个红灯必须记录为“基线缺陷由 DSH 修 main”，不能跳过、xfail 常驻或在测试里替生产修掉。你的纯模块与测试可独立交付，最终 DSH 集成后必须全部转绿。

## 交付和验收

运行所属单测及新 HTTP 矩阵，精确区分基线失败/你的模块失败/依赖未安装；记录命令、版本和输出摘要。依赖需要变动只给 DSH 列表，不改 requirements。报告说明新模块接线一行建议、warning 形状与限制、预算保证范围。

manifest、patch、files、报告须带实际快照 SHA 和逐文件旧新哈希；补丁从仓库根且 git apply --check；只交改动路径，由用户转 DSH 上传。禁止把上轮整文件包覆盖新版本。
