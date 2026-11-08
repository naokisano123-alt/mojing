# R5-L｜Li：独立导入解析与回归包

先读ROUND5；独占 parsers/**、import_policy/**及对应后端测试（含新增import_pipeline/**），报告docs/reports/gemini-li-r5.md。main.py由DSH负责，不改公共配置/依赖文件。

1. 交付import_policy/warnings.py的normalize_warning_records(records, legacy_warnings=())：正确读取TypedDict、count可省略，保持未知合法码，JSON安全输出；无有效结构化项时兼容legacy通用警告，不以中文子串猜机器码。导出函数并提供接线示例。
2. PDF按页、DOCX按块累计UTF-16预算（含拼接分隔符），超限立即停止，不先全部join再判断；仍说明单页解析的CPU/内存不受该计数硬隔离，不能宣称完全防资源耗尽。
3. 修正PDF“无法分离页眉页脚”却输出HEADERS_FOOTERS_SKIPPED的不实语义；PARTIAL_TEXT真实保留，异常页/无文字页不得武断声称是扫描件。保持旧数据结构、正文顺序、标题与表格兼容。
4. 新增真实create_app/TestClient导入矩阵：TXT/MD/PDF/DOCX成功、重启可读、混合页警告、格式伪造/损坏/加密/超限无残留、emoji文本量。复用Codex已加入的3条HTTP成功回归，不删除或跳过。
5. ZIP项数/解包大小/膨胀率、空文档边界保留；依赖变更仅列建议交DSH。

按模块独立测试；报告命令实跑结果与限制。旧R4在途包继续有效，只需与本轮基线三方比对。根相对patch、哈希manifest、files及报告通过用户交DSH。
