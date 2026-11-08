> 历史任务：R3 已合并。当前生效为 [ROUND4](ROUND4.md)，以下仅保留当时约定，不继续按旧所有权开工。

# 第三轮四助手任务总表（历史）
日期2026-11-08；仓库naokisano123-alt/mojing。先读[本轮审阅](ROUND3_REVIEW.md)、PROJECT_HANDOFF与v0.2/1.0-r2契约。本文件取代R2的人员任务与文件所有权；R2保留历史。

## 基线与开工
功能基线main：87b5b2fed12fad648eaba1930c3968a47a1665df（PR #2已合并）。三个Gemini必须从包含本轮全部任务书的**同一个最新提交完整快照**开新窗口。code_baseline填本SHA，snapshot_commit填实际包含任务书的完整SHA；不要混用，更不能只填“最新版”。

## 目标与分工
| 助手 | 任务 | 独占主要路径 |
|---|---|---|
| Gemini（Marcin） | R3-M：M1-D人工条目前端整模块＋上一轮上传/警告展示缺口 | 新src/features/lore-entries/**、tests/lore-entries/**；既有DocumentUpload.tsx、DocumentViewer.tsx、documents.css及其专用tests/documents/upload-*/viewer-* |
| Gemini（Li） | R3-L：四格式导入策略、结构化警告、UTF-16与解析质量硬化 | server/app/parsers/**、server/tests/parsers/**；新server/app/import_policy/**、server/tests/import_policy/**；报告/样本 |
| Gemini（Ma） | R3-A：人工条目后端/来源引用服务＋检索语义修复 | 新server/app/lore_entries/**、server/tests/lore_entries/**；既有server/app/lore_search/**、server/tests/lore_search/** |
| DeepSeekHarness | R3-D：公共契约、限额入口、迁移/路由/客户端、三包整合与验收 | server/app/main.py/db.py/config.py/errors.py/importer.py、src/api/**、WorldLoreView.tsx/index.ts、App/路由、公共依赖与CI、README与集成报告 |

Codex本轮仅审阅派单，不并行修改上述业务代码。三Gemini只出文件包，经用户转DSH上传；DSH不在各自独占路径预先写第二份同功能。需要移交所有权应明确记录后再动手。

## M1-D 产品规则（本轮冻结目标）
人工维护人物/地点/势力/规则/其他条目，draft/confirmed；分类与确认由用户完成，不做AI自动抽取。支持同名不同条目，不擅自合并。
条目字段沿用v0.2 LoreEntry：id/world_id/type/title/summary/status/source_links/created_at/updated_at，新增revision（初值1，每次成功更新+1）及origin:'manual'。标题1–80、summary≤500；允许无来源的人工条目但明确“人工录入/无文档引用”。
source_links保存document_id/chunk_id/start_char/end_char，**全文UTF-16，半开区间且必须落在指定块内**。服务端核对同世界、有效文档/块和代理对边界；excerpt由服务端从真实文本生成，不信任客户端摘要。
来源被软删除：保留条目及历史引用，source_links派生availability:'active'|'deleted'|'unavailable'；不得悄悄删除引用或把失效引用显示有效。已confirmed可保留历史状态，但显示needs_source_review:true；该字段只提示需复核，不等于模型自动认定事实真伪。无来源manual条目不自动判失效。
PATCH与DELETE带expected_revision；不一致409 LORE_ENTRY_CONFLICT，禁止后提交者默默覆盖先提交者。删除条目为软删除，复删的行为在契约明确；不做purge。
本轮不把条目写入M0图鉴或AI上下文，正式世界资料与演示图鉴继续隔离。

## 并行模块边界
- Ma导出服务函数create_entry(conn,world_id,payload)、list_entries(conn,world_id,...filters)、get_entry(conn,entry_id)、update_entry(conn,entry_id,payload,expected_revision)、delete_entry(conn,entry_id,expected_revision)。调用方控制事务；模块不commit。schema SQL/迁移定义放lore_entries/schema.py，由DSH在db.py调用；不得另改共享db。
- Marcin导出LoreEntriesPanel({worldId,api,onOpenSource})，api注入list/get/create/update/delete以及searchSources；自有types.ts符合上面的字段。searchSources可用R2检索结果选块，默认引用整块；若允许局部选择必须校验UTF-16。onOpenSource带documentId/chunkIndex/startChar/endChar；不直接依赖尚未上线HTTP端点。
- Li提供按suffix取格式字节上限的纯策略函数（参数注入配置，不在模块读取环境）；解析保持extract_document入口兼容。结构化warning_records可加性新增，旧warnings:list[str]保留过渡；DSH优先使用机器code/records，不能再用中文子串猜语义。
- DSH在docs/contracts/M1_R3_API.md记录可加性契约：建议GET/POST /api/v1/worlds/{id}/lore-entries、GET/PATCH/DELETE /api/v1/lore-entries/{entry_id}；不破坏旧端点。接口变化发新SHA和差异给受影响人，不让全部人无故从零重开。

## 防覆写与标准包
全部依旧：独立分支/PR、禁止强推/reset --hard/整包覆盖/部分tree替换全仓库。包名gemini-{marcin|li|ma}-R3-{短snapshotSHA}.zip，包含DELIVERY.md、manifest.json、patch.diff、files/、TEST_RESULTS.md、INTEGRATION.md。manifest写base_commit/snapshot_commit、文件操作add/modify/delete、旧blob/SHA256与新SHA256；未知写UNKNOWN，不伪造。
DSH按每文件基线/最新/产物三方比对；git apply --check失败不强制套用。可用files/人工重建，但须核对manifest、实际最新差异和作者，记录“损坏补丁后人工集成”，不能只校验包内哈希就认定不会覆盖别人。
共享依赖/lock由DSH集中修改；三人不得抢改其文件。旧测试保留；实际执行与未执行分开写。虚构样本，不上传私人资料、密钥、数据库或构建缓存。

## 集成门槛与顺序
1. DSH先修B1与能力/逐格式限额接线的最小阻塞，补HTTP层回归；不等大功能做完才解决旧承诺。
2. Li和Marcin质量包先进入，确保四格式/警告/边界通过；Ma条目服务和Marcin条目UI通过注入接口可分别测试，不互等实现。
3. 再集成条目闭环与来源软删、冲突更新，完整旧库迁移测试；最后合并前端/后端/CI/移动端验收。
4. 使用docs/reports/r3-integration.md记录ASSIGNED/RECEIVED/IMPORTED/TESTED/MERGED/DEPLOYED。本轮现在仅ASSIGNED。
5. 线上Python后端仍无已验证部署；本轮交付本地闭环并准备运行说明即可，不将main合并宣称线上可用。实际部署另列验证，禁止未经鉴权就开放用户资料接口。

任务书：[DSH](R3_DEEPSEEKHARNESS.md)｜[Marcin](R3_GEMINI_MARCIN.md)｜[Li](R3_GEMINI_LI.md)｜[Ma](R3_GEMINI_MA.md)。
