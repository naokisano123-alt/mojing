# Gemini（Ma）｜R3-A 人工设定条目后端与来源校验
先读ROUND3与现有三表schema、UTF-16工具及软删除规则。你负责完整服务模块与测试，非仅写SQL建议；包交用户转DSH上传。独占lore_entries及lore_search，其他共享文件只给接线建议。

## 人工条目后端
新增server/app/lore_entries/**、server/tests/lore_entries/**。服务函数与字段依ROUND3，连接注入、调用方控制事务，不commit、不私建正式库。
在schema.py提供幂等建表/版本迁移内容供DSH调用。至少entries与source_links，UUID、world外键、软删除、revision乐观并发；迁移保留旧world/document/chunk。不DROP重建；现有库升级及重复运行迁移用临时库验证。
CRUD、五类type、draft/confirmed、同名不误合并、title/summary限制、分页筛选稳定；PATCH部分字段未传不抹除。冲突409；DELETE revision语义清楚，复删不造成新变化。
来源写入先核验条目/world/document/chunk所属一致、资源未删、start/end在块内且全文UTF-16边界合法；拒绝代理对中间位置、负数/越界及伪造excerpt。服务端生成引用摘要，保存真实定位。
来源删除后保留引用并派生availability/needs_source_review；已删世界下条目不可读写，删文档不删除用户手写条目。不做物理清除、AI整理或自动合并。

## 修检索语义
为审阅B6-B8先写失败例：查询“[”/“引号”不应只匹配JSON存储语法；Unicode lower扩展不能导致正文高亮移位；标题/文件名命中无正文命中时match位置应为null而非虚构前80字。
服务返回保留兼容旧字段并用明确match_field；DSH修HTTP与前端适配。匹配范围/排序按R3补充契约统一，可加性文档明确后实施，不擅改老服务语义却不说明。不能因此引FTS索引或删R2测试。

## 测试
临时SQLite测试创建/更新/软删/重启、旧库迁移、同名、跨世界引用拒绝、emoji合法/非法区间、文档软删后状态、两人从同revision更新仅一人成功、并发DELETE不抹新版本、回滚原子性。搜索回归覆盖中文短词、JSON符号、ASCII大小写/Unicode约定、稳定分页和失效来源。
报告docs/reports/gemini-ma-r3.md，标准包带snapshot SHA、schema版本、模块API与例子、具体HTTP接线proposal；不改main/db.py/src/api，也不直接写Marcin前端目录。
