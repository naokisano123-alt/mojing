# 墨境｜第二轮四助手任务总表（当前生效）
日期：2026-11-08。产品负责人：用户。本轮执行助手：DeepSeekHarness、Gemini（Marcin）、Gemini（Li）、Gemini（Ma）。Codex 本轮只审阅与分派，不占用四人的实现目录。

## 入口与基线
先读本文、[最新仓库审阅](ROUND2_REVIEW.md)、[项目全貌](../PROJECT_HANDOFF.md)，再读各自任务书。本文取代上一轮“一位 Gemini + DeepSeekHarness”的人员及文件所有权安排；旧文档保留历史，不再按旧范围开工。
本轮审阅固定于 main 的 c83eccb44734dd16ef6da8e0c2382eff4bed99df，PR #1 已合并。M1-A/B 不要重做。已有契约 docs/contracts/M1_AB_API.md v0.2；后端 app/main.py 与前端资料库已接通，但不是 AI 冒险。

三个 Gemini 均从**包含本轮所有任务书的同一个完整提交快照**开新窗口。code_baseline 填上述功能基线；snapshot_commit 填用户实际下载快照的完整 SHA（含本轮文档提交），二者不同是正常的。不能将 c83eccb44734dd16ef6da8e0c2382eff4bed99df 冒充更新文档后的完整快照，也不能用文件夹名“最新版”代替 SHA。

## 分工
| 助手 | 任务号与大模块 | 独占路径 |
|---|---|---|
| Gemini（Marcin） | R2-M：资料查看/导入组件可靠性及真实行为测试 | 既有 DocumentViewer.tsx、DocumentUpload.tsx、DocumentList.tsx、documents.css；新增同目录内 viewer/upload 辅助模块；tests/documents/viewer-*、upload-*；docs/reports/gemini-marcin-* |
| Gemini（Li） | R2-L：PDF/DOCX 文本提取独立引擎及解析样本 | 新增 server/app/parsers/**、server/tests/parsers/**、server/requirements-parsers.txt、docs/reports/gemini-li-* |
| Gemini（Ma） | R2-A：中文资料检索服务与独立搜索面板 | 新增 server/app/lore_search/**、server/tests/lore_search/**、src/features/lore-search/**、tests/lore-search/**、docs/reports/gemini-ma-* |
| DeepSeekHarness | R2-D：契约、共享接线、迁移/构建、接收三个包、全量回归与 PR | 其余必要共享入口、配置、公共 DTO/客户端、契约、CI、集成报告；不在三人独占目录并行实现同一模块 |

Marcin 不改 WorldLoreView.tsx/index.ts、src/api/**；Li/Ma 不改 server/app/main.py、db.py、config.py、importer.py、server/requirements.txt。这些共同文件都由 DSH 接线。任何额外文件改动先在 DELIVERY 中提案，不能趁集成整片接管他人的目录。Gemini 可自行分配自己模块内部实现，但不能直接上传仓库。

## 本轮边界
补牢 M1-A/B 并扩展 M1-C（PDF/DOCX）及一个小范围 M1-E（中文资料检索）闭环。M1-D 正式条目 CRUD 放下一轮；不接模型、不收 Key、不实现 OCR、云同步、正式剧情/记忆引擎。资料库来源与演示数据继续隔离。

三人都可从同一快照立即实现独立模块，不必等待互相上传。以本文的本地模块边界和原 v0.2 接口作为稳定输入；新增 HTTP 字段与路由由 DSH 制订可加性的补充契约，不擅改旧端点语义。若新增契约有实质变化，DSH 将差异连同新 SHA 经用户回传受影响 Gemini；未影响的独立模块继续做，不要求所有人从零重开。

## 拼装接口（本轮目标，不表示已经上线）
- Li 导出 extract_document(data: bytes, filename: str, *, limits: ParseLimits) -> ParsedDocument；ParsedDocument 包含 text、media_type、encoding、warnings，具体类定义放 parsers/types.py。PDF/DOCX encoding 采用 utf-8 表示提取出的文本表示，不把它误写成原二进制文件编码；在补充契约中写清语义。模块不访问数据库。统一 normalize/chunk/UTF-16 偏移仍由 DSH 接入现有 importer 管线。
- Ma 后端导出 search_chunks(conn, world_id, query, limit=20, offset=0) -> dict。连接由调用方提供；只查询未删除世界/文档的当前 chunks，不私建正式数据库、不在导入期间提交事务。结果返回 items/total/limit/offset；每项含 world_id/document_id/chunk_id/chunk_index/filename/heading_path/start_char/end_char/snippet，snippet 明确是摘要。匹配区间若返回，必须声明 UTF-16 单位。
- Ma UI 导出 LoreSearchPanel({worldId, search, onOpenSource})。search 为注入的异步函数，支持 AbortSignal；组件不依赖尚未上线的 HTTP 端点。onOpenSource 接收 documentId/chunkIndex/startChar/endChar。类型全放自身目录，由 DSH 编写薄适配层对接服务，避免与 src/api/types.ts 同时修改。
- Marcin 保持现有组件导出与 props 兼容；DocumentViewer 可增加可选 initialSource（UTF-16 起止区间）以高亮来源；由 DSH 接到 Ma 回跳。不强迫其他页面新增必填 props。
- DSH 建议对外追加 GET /api/v1/search?world_id=...&q=...&limit=...&offset=...；不改变旧 14 个接口。PDF/DOCX 仍用同一上传入口，扩展允许类型、DTO/错误码/限制说明。v0.2 老客户端可继续 TXT/MD。

## 防覆写与交付（强制约定）
1. 所有修改经分支/PR；三个 Gemini 各出完整文件包，经用户统一交 DSH 上传，不直接并发推 main。DSH 创建 feat/r2-integration，必要时每包独立 import/r2-* 分支。
2. 包名 gemini-{marcin|li|ma}-R2-任务号-{snapshot短SHA}.zip；内容 DELIVERY.md、manifest.json、patch.diff、files/、TEST_RESULTS.md、INTEGRATION.md。manifest 每项列路径、add/modify/delete、旧 blob SHA 或 SHA256、新内容 SHA256。未知写 UNKNOWN，交接时显式提示，禁止伪造。
3. 交付注明 snapshot_commit、code_baseline、contract_version、任务号、允许目录、依赖和可选接线建议。测试不能运行就写未运行；不能把旧 22/37 当本次结果。
4. DSH 先检查工作区与最新目标分支，再比对每个基线文件和远端当前内容。git apply --check 仅是第一步，不是可安全覆盖的证明；有重叠做三方合并，无关新增保留，同名新增先停。
5. 禁止旧 ZIP 整包覆盖仓库、force push、reset --hard/clean -fd 清理他人工作、用不带 base_tree 的局部 tree 替换仓库。GitHub API 更新用 blob SHA/expected_sha；遇到竞态重新取最新再合并，不去掉保护。
6. 依赖和锁文件由 DSH 集中修改；三人提供依赖清单，不各自生成一份根 lock。只提交虚构测试资料；不交真实设定、Key、数据库、构建产物、宿主缓存。
7. 保留 M0 已确认交互、图鉴滑动、草稿和 mojing-m0-v1；不自行重写全局 UI。DSH 审阅后才能应用超范围改动。
8. 集成建议先 Marcin 可靠性包，再 Li 格式解析包，最后 Ma 搜索包。最终验证“建资料库→导入四格式→检索→定位原文→删除后不可检索→重启仍保留”，M0 单独回归。
9. 本轮任务状态现在都是 ASSIGNED，不代表已实现。DSH 以 docs/reports/r2-integration.md 记录 RECEIVED/REJECTED/IMPORTED/TESTED/MERGED/DEPLOYED，代码合并和线上部署分开写。静态 Sites 托管不能直接运行 Python 后端；若无可用后端部署，本轮交付本地完整闭环，在线版本不得假报后端已就绪。
10. Codex 后续如被要求审阅，也先取最新 main。用户决定产品范围，DSH 是集成执行者，不意味着可以自行扩大所有权。

## 任务书
- [DeepSeekHarness](R2_DEEPSEEKHARNESS.md)
- [Gemini（Marcin）](R2_GEMINI_MARCIN.md)
- [Gemini（Li）](R2_GEMINI_LI.md)
- [Gemini（Ma）](R2_GEMINI_MA.md)
