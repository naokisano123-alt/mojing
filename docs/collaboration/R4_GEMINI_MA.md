# R4-A｜Gemini（Ma）：条目并发和来源完整性

从同一 R4 快照工作，交付经用户转 DeepSeekHarness。你本轮专注已有人工条目服务，不重新扩展检索、AI抽取或正式状态引擎。

## 独占范围

server/app/lore_entries/**、server/tests/lore_entries/**、docs/reports/gemini-ma-r4.md。服务继续接收调用方 sqlite3 连接，不能自行 commit/rollback、新建正式库或改 main/db/config。共享接线与 HTTP 回归建议交 DSH。

## 交付块一：真正原子的 revision

目前是 SELECT 检查后按 id UPDATE，两个连接都读到 rev1 就都能写 rev2。改为原子比较并交换（UPDATE 条件含 id + expected_revision + active 状态），检查 rowcount。update/update、update/delete、delete/delete 的竞争要有确定语义；成功写后版本正确，过期写409，重复同版本删除保持约定幂等。严禁“只在单线程顺序请求中测试409”。

来源更新应在 revision 占用成功后、同一事务中处理；验证失败不遗留已加 revision 或半份来源。可用嵌套 SAVEPOINT 做模块局部原子性，不 commit 外层事务；不要靠无保护的 SELECT 后写，或让调用者捕获异常后留下局部污染。考虑当前 sqlite3 默认事务开始时点/锁升级，测试并发时真实使用两个连接与 barrier，不以单连接 mock代替；与 DSH 对齐 busy/冲突处理，不把所有 OperationalError 伪装为409。

## 交付块二：引用坐标与历史生命周期

- 严格执行全文绝对 UTF-16 区间；删掉猜相对坐标 fallback。合法式是 c_start <= start < end <= c_end。测试第二块 [1610,3219) 上传[0,1)必须422；包含emoji切开代理对必须422。
- 读来源 additive 增加真实 chunk_index:number|null；缺块为 null，不伪造0；availability 区分文档软删 deleted、缺文档/缺块 unavailable，并核对文档/块关系以免错标 active。
- PATCH 省略 source_links 保留；显式[]移除；显式数组允许保留**当前同一条目中完全相同身份和区间**的非 active 历史来源，保持原服务端 excerpt。新增/挪用他条目无效引用、改了区间仍须active校验，禁止借“历史”穿透跨世界检查。来源全有效时仍重新生成摘录，不信任客户端。
- null 不能静默当[]；revision/坐标只接受真正整数（Python bool 不是有效整数输入），标题摘要计数与前端差异写明并建议统一约定，不擅自大改产品字数。
- 无来源人工条目仍合法；needs_source_review 仍仅在 confirmed 且有非active历史来源为true。来源状态变化不凭空改 confirmed，也不做事实真伪判断。

## 回归要求

新增真实双连接竞争用例：两写同expectedrevision最多一个成功、版本不丢；update/delete不产生幽灵复活；事务失败后外层仍可继续且条目/来源无半写；旧库迁移二次运行不删数据。

引用用例：多块绝对偏移、emoji边界、bool/null非法、跨世界/软删新增拒绝；旧非active引用保留编辑标题成功，删/重排/部分移除历史引用，复制另一条目的失效引用失败；返回实际chunk_index、缺失来源状态。保留旧测试，不通过把旧期望改成宽松成功来掩盖错误；对旧“支持相对偏移”用例标注契约纠偏后更改依据。

模块不改HTTP，但报告给 DSH 明确路由/DTO/事务接线要求和最少HTTP验收场景。完成后跑所属测试，给实际结果；未跑写未跑，不继承127通过数字。

## 打包与防覆写

manifest 写任务、作者、代码基线、实际R4快照完整SHA、契约1.0-r4、旧文件基线哈希和新内容哈希。patch 路径必须 a/server/...、b/server/...；上轮 tmp/repo_test/ 前缀不能重现。files只放改动；在干净同快照检查 git apply --check。由用户交 DSH 核验、import分支三方合并，禁止直接整包覆盖远端。
