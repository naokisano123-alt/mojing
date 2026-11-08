# Gemini（Ma）｜R2-A 中文检索服务与搜索面板
先读 ROUND2.md、审阅记录、契约v0.2、现有db.py schema、documents/chunks软删除语义。你离线工作，不直接上传；包交用户转DeepSeekHarness核验。独占后端lore_search/和前端features/lore-search/，与另外两人不重叠。

## 实现一整块可独立测试的检索能力
1. 后端 search_chunks(conn, world_id, query, limit=20, offset=0) 返回ROUND2中的结构。依赖已有worlds/documents/chunks；参数化SQL；每次明确world_id，排除软删世界/文档。不得返回其他世界内容。
2. 第一版保证中文1–2字短词、人名、普通子串可查。可直接用SQLite instr/安全子串查询完成小规模正确性基线；百分号/下划线/引号/SQL片段都按字面资料处理。不要默认FTS unicode61就能中文分词。
3. 如额外实现FTS5/trigram，必须保留无扩展环境及短词降级，说明索引更新/重建/事务策略，由DSH接入；不能依赖无人维护的索引返回陈旧或已删除结果。FTS是可选增强，不以复杂索引阻塞完整交付。
4. 结果分页稳定、不重复，包含document/chunk来源及摘要snippet；正文命中和标题命中的规则、大小写/空格规范先明确。query空白/超长、limit/offset范围给稳定错误，错误按现有ApiError惯例或清晰的模块异常交DSH映射。
5. 如返回命中起止，按UTF-16，说明是块内还是全文坐标；确保emoji、中文标点下能经chunk坐标回到原文，不把摘要offset当原文offset。

## 独立前端面板
导出 LoreSearchPanel({worldId,search,onOpenSource})，search和AbortSignal通过props注入，不依赖修改公共src/api。支持输入、搜索中、空结果、分页、错误重试、取消、切世界时清空并拒绝旧响应。每项显示文档/标题路径/摘要，有按钮回跳documentId/chunkIndex/startChar/endChar。不能把摘要标成全文。
样式限定本目录CSS和自己的命名空间，不覆盖全局。不要改WorldLoreView/DocumentViewer；给DSH如何拼装的示例。不存在后端时显示不可达，不生成模拟搜索结果。

## 回归与性能证据
临时SQLite + 虚构样本，测试中文单字/双字、emoji、同名跨世界、删除世界/文档、无结果、注入字面、特殊字符、分页稳定、导入后立即可搜。至少记录接近既有20文档×2MiB限额的可复现性能样本与局限，不凭空宣称毫秒级或海量可用。可为慢查询提出限流/索引方案，不擅改现有上传限额。
前端测试必须覆盖竞态与来源点击payload；测试依赖由DSH统一加入。报告 docs/reports/gemini-ma-r2.md，标准包带 ma/R2-A/snapshot SHA，并给路由/客户端适配及错误码的局部建议。
