# Gemini（Li）｜R3-L 四格式导入正确性与告警质量
先读ROUND3/审阅；独占parsers及新import_policy，不直接修改main/db/config/importer/公共requirements。本批是有失败样本的修复，不重写已通过的解析器，不引OCR。

## 必做
1. 新import_policy纯函数按suffix、传入的配置选择字节上限；TXT/MD2MiB、PDF/DOCX8MiB的默认由配置决定。未知格式拒绝；给DSH“先判断格式，再限量读取到limit+1”的精确接线建议。仅修改提取器max_bytes不足以解决main先拦2MiB的问题。
2. max_text_units契约按UTF-16：提取后/规范化后累计限制一致，不能用Python len代替。保留旧参数兼容或明确可加性字段；emoji边界、正文/BOM/换行变更用回归样本验证。DSH在共享管线做最终守门，你负责模块级正确。
3. 解析返回结构化warning_records（code/message/count等），旧warnings保留；区分混合扫描/无文本页、空白页、提取异常、页眉页脚、表格、多栏不确定。无法区分时诚实说明“无法确定是否空页/扫描”，不能编造检测结果。对部分提取必须产出PARTIAL_TEXT，不靠路由中文关键字猜。
4. 页数/文本量超限用明确内部错误，DSH映射EXTRACTION_LIMIT，不能都误报文件字节超限；规范化后来源偏移仍由旧chunk流程统一。
5. 检查DOCX ZIP条目数/解压体积/膨胀比边界和坏包异常。新增条目数上限如合理，可给可加性配置与限制说明，不擅抬上传限制。
6. PDF空密码加密处理按产品“拒绝加密”或“允许空密码可读”选定并写清，不能用reader.is_encrypted一直true误判解密状态；保证损坏/混合/扫描样本诚实处理。

## 证据与交付
生成虚构、可重现中文PDF/DOCX。覆盖2–8MiB有效文件、8MiB+1字节、TXT2MiB边界（HTTP接线由DSH跑）、大量emoji计数、页数超限、全扫描、混合页、提取异常、无脚注支持的警告、坏ZIP/条目过多；修改后仍保持正文顺序与UTF-16分块拼接不变式。
不要求本模块读取或写SQLite。依赖建议独立列出，公共requirements由DSH合并。报告docs/reports/gemini-li-r3.md，代码包按ROUND3标准；给主路由及错误码接线最小diff建议但不直接覆盖共享文件。不能运行的案例明确未运行。
