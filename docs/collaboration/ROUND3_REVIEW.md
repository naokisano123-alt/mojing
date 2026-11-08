# 第三轮仓库审阅
日期：2026-11-08；固定main：87b5b2fed12fad648eaba1930c3968a47a1665df。本次为静态代码、契约、报告和GitHub CI状态审阅；未在本环境重新执行整个测试集，也未代表安卓真机验收。

## 已确认
- PR #2 已合并，读取时没有打开的PR；最新main的Actions run 42462753663为completed/success。
- R2-M/L/A三包已集成；后端PDF/DOCX、/capabilities、/search、数据库补列，前端上传格式探测、检索页签、来源跳转均有代码。
- 集成报告§8记录41前端/82后端通过；§3的22/39和§4“待三包”是较早阶段，需明确标历史。
- 有CI和scripts/check.ps1，但线上完整后端仍未部署，不能让原静态原型的使用者以为已经用到这些本地后端能力。
- 人工LoreEntry CRUD尚无实现，可作为下一块M1-D，不必现在进入M2。

## 有代码证据的缺口
| ID | 证据 | 影响/负责人 |
|---|---|---|
| B1 | main.py上传先file.read(cfg.max_upload_bytes+1)，超过cfg.max_upload_bytes就413，之后才判断suffix | 默认PDF/DOCX超过2MiB先被拦，8MiB能力承诺未兑现。DSH入口接线，Li提供限额策略和测试 |
| B2 | WorldLoreView把maxBytes统一设为rich formats?8MiB:2MiB，未用capabilities.max_bytes逐格式值 | TXT也会被前端放到8MiB；非默认环境限额失配。Marcin做逐格式组件，DSH传capability |
| B3 | parsers中用len(full_text)执行max_extracted_chars，主路由把max_text_units传入该字段 | 契约宣称UTF-16，但非BMP字符计为1而非2；需边界测试与一致化。Li/DSH |
| B4 | _warning_payload用中文关键词猜code；PDF部分页警告实际“图片或空白页”，不含“扫描” | 不能可靠生成PARTIAL_TEXT；多栏通用警告也可能先匹配页眉。Li提供结构化记录，DSH无损适配 |
| B5 | DocumentViewer未读取/渲染warnings和encoding_semantics，虽已存在于公共DTO | 导入不完整的资料仍可能看不到提示。Marcin |
| B6 | search service查lower(c.heading_path)的JSON序列化字符串；Python侧lower与SQLite默认lower规则不同 | JSON符号可能伪命中；Unicode大小写扩展可能偏移不准。Ma回归与修复 |
| B7 | search adapter未把match_field传到HTTP，WorldLoreView把结果固定text，并将缺失坐标变0 | 标题/文件名命中被当成正文区间。Ma提交有类型语义，DSH公共契约/适配 |
| B8 | R2契约规定按文档created_at排序、正文匹配，服务实际正文/标题/文件名优先+document_id排序 | 语义偏差需选定真实规则、兼容记录并测试，不两端各自理解。DSH契约，Ma服务 |

上述为静态可见问题，不冒充全部已动态复现。本轮任务要求用失败用例证明后修复，不能把全绿CI理解为没有边界缺口。

## 本轮目标
先关闭B1-B5等已有路径问题；同时完成M1-D人工条目与原文引用的前后端闭环。沿用现有三表、sqlite3和UTF-16体系，不重做R2、不引AI、OCR、云同步/多人或复杂世界状态机。旧资料和M0进度必须保留。
