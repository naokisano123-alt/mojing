> 当前任务入口为 [ROUND5](ROUND5.md)。R4在途工作可继续增量交付；文件范围以R5为准，Codex已完成来源导航代码，避免覆盖。

> Codex 后续小修复：R4-01（富文档上传异常）、R4-05（检索null坐标）、R4-06（逐格式前端限额）已处理，见 [修复交接](../reports/codex-r4-hotfix.md)。四助手继续其余任务；DSH合包须保留这些修复，旧快照产物做三方合并。

# 第四轮四助手任务总表（当前生效）

日期：2026-11-08；代码基线 `8296630705a7e61de95fbf532b8a38846dd82c78`。先读 [项目现状](../PROJECT_STATUS.md) 与 [本轮审阅](ROUND4_REVIEW.md)。本文取代 R3 及更早任务的文件所有权；旧文档仅供历史查证。

## 本轮目标

把已经接上的 M1 资料库做成可验证的完整闭环：正常文件能导入，来源能定位，编辑不会悄悄覆盖，错误和切页不会丢失草稿。完成验收后才决定进入 M2；本轮只产出 M2 方案，不接真实 AI，不要求 API Key。

| 助手 | 独立交付 | 任务书 |
| --- | --- | --- |
| DeepSeekHarness | R4-D：先修上传阻断；公共契约/DTO/接线、组件测试基础设施、三包集成、实际验收与阶段说明 | [R4_DEEPSEEKHARNESS](R4_DEEPSEEKHARNESS.md) |
| Gemini（Marcin） | R4-M：资料工作台前端可靠性；来源定位、草稿/冲突/异步状态及真实渲染测试 | [R4_GEMINI_MARCIN](R4_GEMINI_MARCIN.md) |
| Gemini（Li） | R4-L：文档导入管线测试与解析预算；警告标准化纯模块、四格式真实 HTTP 回归 | [R4_GEMINI_LI](R4_GEMINI_LI.md) |
| Gemini（Ma） | R4-A：条目并发与引用完整性；原子 revision、严格全文坐标、历史引用可编辑 | [R4_GEMINI_MA](R4_GEMINI_MA.md) |

## 同一快照与防覆写

1. 三个 Gemini 在新窗口使用**同一个包含 R4 任务书的完整提交快照**；code_baseline 填上方代码 SHA，snapshot_commit 填本次任务书发布后的实际完整 SHA（由用户转发，或在快照中核实）。这两者不同，禁止只填“main/最新版”。DSH 也登记自己的实际起点。
2. Gemini 不因 DSH 后续推送而假装已看到变化；需要的新文件/契约由用户转交。R4 在本文冻结对接接口，可在这个快照直接并行开工；不得等无期限在线交流。
3. 每人独立分支/目录，只改允许路径。共享变更以文字建议交 DSH；不能修改别人的模块后让 DSH 猜。
4. 包结构：manifest.json、patch.diff、files/（只含改动路径）、报告。manifest 含 task_id/author/code_baseline/snapshot_commit/contract_version、逐路径 add/modify/delete、旧 blob SHA 或 SHA256、新文件 SHA256、依赖建议、测试实跑结果。删除必须显式，未知哈希写 UNKNOWN。
5. 补丁路径必须从仓库根开始 a/src/、b/server/ 等，禁止 tmp/repo_test/、绝对路径、上级路径与无关文件。交包前在干净的同 SHA 工作副本执行 git apply --check；记录结果。
6. 用户把三个包转给 DSH。DSH 先核验哈希和基线，再在三个 import 分支分别三方比对、应用、测试；新增文件已存在时先比较，不强行替换。不用 ZIP 覆盖最新仓库，不整文件覆盖重叠修改，不 force push/reset --hard/clean 清理他人工作。
7. 集成按 PR；更新引用保留 expected SHA/非强制保护；通过 Git tree 写入时必须继承完整 base_tree，避免整库丢文件。任何新并发变更先重读，不能移除保护重试。
8. 保留 M0 手势、主题、mojing-m0-v1 数据及旧测试；不全仓格式化、不提交密钥/真实资料/数据库、dist、node_modules 或宿主缓存。

## 文件所有权（含测试）

| 所有者 | 可修改路径 |
| --- | --- |
| Marcin | src/features/lore-entries/**、src/features/lore-search/**；DocumentUpload.tsx、DocumentViewer.tsx、documents.css、viewerUtils.ts；tests/lore-entries/**、tests/lore-search/**、tests/documents/upload-*、tests/documents/viewer-*；新增 tests/ui/**；本人 r4 报告 |
| Li | server/app/parsers/**、server/app/import_policy/**；server/tests/parsers/**、server/tests/import_policy/**；新增 server/tests/import_pipeline/**（含自己的局部 fixtures）；本人 r4 报告 |
| Ma | server/app/lore_entries/**；server/tests/lore_entries/**；本人 r4 报告。lore_search 本轮不再扩展，发现问题仅报告 |
| DSH | main.py/db.py/config.py/importer.py、src/api/**、WorldLoreView.tsx 与 App 接线、契约、根 README/server README、依赖/锁文件/测试配置/CI；现有 server/tests/test_*.py 与 tests/documents/api.test.ts、新增 tests/integration/**；r4-integration、M1_ACCEPTANCE、M2_PLAN |

受保护：M0 Story、WorldBar、usePageSwipe、interaction、model 数据结构、全局 styles 不改；必要改动先具体交 DSH 记录授权和最小差异。DSH 可以审查所有文件，修改 Gemini 独占文件须在该包交付后记录 integration_fix，避免实时双写。

## R4 固定对接接口

契约名 `M1_R4_API.md / 1.0-r4` 由 DSH 整理，以下已是执行依据，不需要等待 DSH 写完才开工。

- Source write 坐标**只用全文绝对 UTF-16 半开区间**：chunk.start_char <= start_char < end_char <= chunk.end_char，且不能切开代理对。禁止猜测块内坐标。R3 契约中 end <= chunk_length 的公式笔误按此修正。
- Ma 的读取来源 additive 增加 `chunk_index: number | null`（来自真实 chunks；缺失为 null），不改已有字段。新写引用必须存在且同世界 active；摘录由服务端算。
- PATCH 不带 source_links 保留全部引用。显式带数组时，可保留当前条目中完全相同 document_id/chunk_id/start/end 的历史非 active 引用，不能借此新增或改写无效引用；[] 显式移除全部；null 为422，禁止静默清空。
- Marcin 的来源跳转类型扩为 `{documentId:string; chunkId?:string; chunkIndex?:number; startChar?:number; endChar?:number}`。旧 callback 接口做 additive 演进；有真实 chunk_index 才传，没有则传 chunkId 由 DSH resolver 获取真实块再打开，不能回退为0。
- 标题/文件名命中：match_field 保留，坐标保持 null，打开对应真实块而不高亮正文。仅正文有完整有效区间才传 start/end。独立检索面板 DTO 支持 number|null，不靠双重 as unknown 绕类型。
- 条目草稿不含 Key，只在当前前端会话保存。Marcin 暴露可选 `draftStore` prop，Map 风格 `get(key)/set(key,value)/delete(key)`；value=`{draft:LoreEntryDraft,baseRevision:number|null}`；key=worldId+":"+entryId（新建用 new）。DSH 在 WorldLoreView 用稳定 useRef 注入，面板卸载切 tab 后仍能恢复；刷新/离开整个资料库不承诺持久。
- 恢复旧草稿必须保留 baseRevision，不能自动绑定新 revision。409 读取远端只供对比；用户明确选择以本地编辑为基础再提交，才更新基线，不静默覆盖。
- Li 新 `server/app/import_policy/warnings.py` 暴露 `normalize_warning_records(records, legacy_warnings=()) -> list[dict]`：接受当前 dict 结构，count 缺省不报错；输出 code/message/可选 count 的 JSON 安全值；保留未知合法码、纯字符串兼容回退；不按中文子串推断机器码。DSH 在上传路由调用。此前可先局部修 dict 读取止血。
- 测试基础设施默认 Vitest + jsdom + React Testing Library（由 DSH 唯一修改依赖/锁/配置，使用适配 Node22 的固定兼容版本）；保留原 node:test 命令，新增 npm run test:ui，CI 两套都运行。Marcin 输出 tests/ui/**/*.test.tsx 与真实模块测试，不改依赖；暂未装时明确写未运行并给安装后命令。

## 执行与验收顺序

DSH 先修 R4-01 并加入正常 DOCX/PDF HTTP 冒烟；同时落公共契约、组件测试框架及 DTO。三 Gemini 在上述接口基础上独立完成；Li 的新 HTTP 回归在旧代码上允许明确复现红灯，最终合并必须转绿。接包按 Li→Ma→Marcin，最后 DSH 接线。

完成条件：四格式成功导入与重启后可读；混合 PDF PARTIAL_TEXT 可见；异常无残留数据；多块/超首屏来源真实定位与 emoji 高亮；标题/文件名命中不伪造正文；同 revision 并发最多一写成功，其余409且来源写入原子；软删来源仍可修改条目文字/保留历史；切 tab 草稿可恢复；陈旧异步响应不污染当前世界；原 M0 回归保留；安卓人工检查单与执行状态如实记录。

本轮交付是待用户转发的任务书，不代表四个外部 AI 已自动收到或执行。测试、真机、部署分别报告；本轮不自动部署线上服务。
