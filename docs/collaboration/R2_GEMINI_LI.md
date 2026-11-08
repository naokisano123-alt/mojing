# Gemini（Li）｜R2-L PDF / DOCX 文本提取引擎
先读 ROUND2.md、审阅记录、契约v0.2、现有 importer.py 的 normalize/chunk_text 与覆盖不变式。你离线工作；只做自己独占的新 parsers/ 模块与测试，不接管主路由/数据库/旧importer。完成包经用户交DSH上传。

## 独立实现内容
- 统一入口 extract_document(bytes, filename, *, limits) -> ParsedDocument，类型定义在parsers/types.py；返回text/media_type/encoding/warnings。TXT/MD保持旧入口，本模块重点PDF/DOCX，函数签名与ROUND2一致。
- 文本型PDF：按页面顺序提取可读文字；显式页分隔、空页处理；扫描件或缺少可用文本时给可识别错误/警告，严禁假成功和擅自OCR。混合文本/扫描页明确“不完整提取”提示。
- DOCX：正文段落和表格按文档顺序，标题样式尽量保留成可分块结构；声明不支持的页眉脚注/文本框等范围，不能声称完整还原排版。
- 文件内容与格式判定，损坏/加密PDF、无效DOCX、解压炸弹、页数/解压字节/文本量限制；只读内存或受控临时目录并清理，不使用原文件名作为任意路径。
- 提取器不写库、不生成chunk UUID、不二次定义UTF-16管线；DSH接到既有normalize/chunk_text，统一来源是“提取规范化文本”，不是二进制文件字节位置。PDF页码若额外保留，页范围必须有单位定义且与最终文本映射可验证；不强迫本轮实现坐标级高亮。
- 依赖选择一套可维护主方案，给版本/许可证及替代方案说明；不要同时装多套PDF引擎。依赖放server/requirements-parsers.txt建议，由DSH统一确认安装。不修改公共requirements。

## 验收样本和测试
使用可重现、虚构无隐私样本；脚本生成PDF/DOCX或提交小测试夹具，说明中文字体依赖与可移植性。覆盖中文/英文、空页、双栏阅读顺序局限、段落表格顺序、扫描、混合页、加密/损坏、超限、emoji。提取结果进入旧分块后应满足无缝拼接与UTF-16偏移一致；可给共享importer回归用例建议，不直接改旧文件。
报告列“可靠支持/警告支持/拒绝”，严禁文字乱码仍显示成功。单元测试实际运行与否如实写。

## 接线说明
提供最小DSH接线diff建议：上传扩展名/media_type、错误码映射、限额配置、主路由统一调用、前端类型/文件accept能力启用。建议不直接覆盖共享文件；不能自行提高原2MiB上限，先测并由DSH记录配置决策。保持旧TXT/MD通过。
报告路径 docs/reports/gemini-li-r2.md，标准交付包带 li/R2-L/snapshot SHA。新增包如目标已有同名目录，DSH必须三方比对而非覆盖。
