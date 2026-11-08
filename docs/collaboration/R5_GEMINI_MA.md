# R5-A｜Ma：独立条目一致性服务

先读ROUND5。独占server/app/lore_entries/**、server/tests/lore_entries/**；报告docs/reports/gemini-ma-r5.md。维持注入sqlite3 conn，不自行commit，不改main/db。

1. 修复SELECT校验后无条件UPDATE的竞态。原子UPDATE必须包含expected_revision和active条件，检查受影响行数；update/update同版本最多一个成功，其余409；update/delete不复活；重复同版本删除维持幂等。
2. 条目和来源变更同一原子单元；使用SAVEPOINT等可嵌套机制，校验失败或来源写入失败不能留下已递增revision/半份来源，即便外层捕获异常继续工作也成立。不得吞掉真正数据库错误伪装为409。
3. 来源只接受全文绝对UTF-16：chunk.start<=start<end<=chunk.end，拒绝猜测相对坐标、bool、代理对中间点。null不能当[]静默清空。
4. PATCH省略source_links保留；显式数组可保留本条目已有且身份/区间完全相同的非active历史引用，保留原摘录；新增或修改的引用必须同世界active。不能复用其他条目的失效引用绕过校验。无来源manual合法。
5. 读取availability准确区分软删与缺失，核对文档/块关系。chunk_index可additive输出，但前端已能按chunkId定位，不等待该字段。
6. 测试必须用两个真实连接与同步屏障覆盖并发；还包括事务回滚、旧库幂等迁移、emoji/非首块绝对坐标、保留失效引用编辑标题、跨世界拒绝。

将HTTP与事务封装需要的最小建议交DSH，模块测试可独立完成。不得重做检索或前端。R4已有代码可继续增量交付；manifest记录真实旧基线，patch路径从server/起，用户转DSH合并。
