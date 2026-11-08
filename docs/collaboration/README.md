# 墨境｜协作任务入口

**当前生效：[第二轮四助手任务总表](ROUND2.md)**。人员为 DeepSeekHarness、Gemini（Marcin）、Gemini（Li）、Gemini（Ma）；三个 Gemini 使用同一完整快照，文件包均经用户交 DeepSeekHarness 核验集成。先读总表再读个人任务书。

- [本轮仓库审阅](ROUND2_REVIEW.md)
- [DeepSeekHarness](R2_DEEPSEEKHARNESS.md)
- [Gemini（Marcin）](R2_GEMINI_MARCIN.md)
- [Gemini（Li）](R2_GEMINI_LI.md)
- [Gemini（Ma）](R2_GEMINI_MA.md)

---

以下为上一轮协议存档；人员、阶段、文件所有权如与本轮冲突，以 ROUND2.md 为准。禁止从旧快照整包覆盖最新代码。

# 墨境｜三方协作与防覆写协议

日期：2026-11-08。代码仓库：naokisano123-alt/mojing（公开）。分配任务时的 GitHub 功能基线：06088df564033a8903187489d3d1549971ada50d。

先阅读 [项目全貌](../PROJECT_HANDOFF.md)，再阅读本文及自己的任务书。本文根据用户最新安排，更新旧交接文档中“两位 AI”的一般建议；产品目标与既定手机交互继续有效。任务书是交接，不代表各 AI 已实际开始工作。

## 分工和本轮范围

| 参与者 | 能力与职责 | 第一批交付 |
| --- | --- | --- |
| DeepSeekHarness | 可实时读写；后端主实现者、Gemini 代码的集成上传者 | M1-A/B 后端、接口契约、验证和 PR |
| Gemini | 只能读取用户提供的非实时仓库快照；输出代码包，不直接上传 | 契约审阅、前端 API 适配层与文档导入预览组件 |
| Codex | 按用户安排做产品约束审阅、关键代码复核、发布支持 | 不抢占上述文件；未另行指派时不并行修改业务代码 |
| 用户 | 传递快照/产物、决定产品方向、真机验收 | 保留每次交付对应的基线与任务号 |

本轮只实施 M1-A/B：最小世界数据持久化、TXT/MD 真实导入、原文及来源预览。PDF/DOCX、语义抽取、真实 AI/API Key、正式冒险状态机和云同步留到以后。M0 演示必须继续可用；真实导入结果不能被说成已能驱动 AI 剧情。

## 推荐执行顺序

1. DeepSeekHarness 从最新 main 创建工作分支，登记实际 HEAD；先提交 docs/contracts/M1_AB_API.md，包含接口、请求/响应示例、错误码、来源定位和演示/正式数据隔离方案。
2. Gemini 可先独立审阅既有源码，列风险和验收样本。契约确认前不凭空编写依赖假接口的完整前端。
3. 用户把契约所在的确切提交/完整快照发给 Gemini；Gemini 回报同一 SHA。DeepSeekHarness 开发 server/，Gemini 开发自己拥有的前端新增文件。
4. Gemini 经用户将交付包转给 DeepSeekHarness；后者按本文验证基线、审查差异并在独立分支应用，不直接覆盖工作区。
5. DeepSeekHarness 完成前端页面接线、全量回归、PR 和交付报告。用户真机验收；在线站点发布与代码合并分开报告，不能上传后就声称已部署。

## 文件所有权

- DeepSeekHarness：server/**、docs/contracts/**、docs/reports/deepseekharness-*.md；负责最终改动 src/App.tsx、src/pages/WorldPages.tsx、src/model.ts、vite.config.ts、package.json/package-lock.json、README.md 等共享接线文件。
- Gemini：新增 src/api/**、src/components/documents/**、tests/documents/**（若需调整测试框架，先说明，由 DeepSeekHarness处理配置）、docs/reviews/gemini-*.md。当前尚无这些模块，仍应先确认最新仓库有无同名文件。
- src/hooks/usePageSwipe.ts、src/interaction.ts、src/pages/Story.tsx、src/components/WorldBar.tsx、src/styles.css 视为本批受保护文件。遇到确实需要修改的地方，先说明具体原因、提交局部建议，由用户或集成者分配所有权，不能自行整体重写。
- 所有权是减少冲突的约定，不是永久权限。任务变更时更新记录；不要用“对方没及时回复”自行认定已移交。

## 防覆写：所有 AI 必须遵守

1. 开始前读取最新分支 HEAD、git status、目标文件。工作区脏时保留差异，使用新 worktree/分支；不要 reset --hard、clean -fd、强推或删除别人的分支来清理。
2. 每个任务独立分支。代码通过 PR 集成，不能两个写入者同时直推 main。本文这次仅新增交接文件，不修改任何业务代码。
3. 禁止把旧 ZIP 整包覆盖最新仓库；禁止把 Gemini 给出的整份文件直接覆盖现有文件；禁止用不带 base_tree 的“只含改动文件”的 tree 替换整棵仓库。
4. Gemini 产物必须带 base_commit、目标分支/契约版本、文件清单、每个旧文件的基线 Git blob SHA 或 SHA-256、新增文件声明及补丁。未知基线写 UNKNOWN，不能伪造 SHA。
5. 接收方先 fetch，比较最新目标文件与交付基线。即使总体 HEAD 不同，也要逐文件/逐改动块判断；无关变更可以人工审阅后合并，有重叠则三方合并或请 Gemini基于新快照重做，不以“最后上传者”为准。
6. 补丁先 git apply --check（适用时），再应用到专用分支。新增文件如已存在，立即停止该文件的写入并报告冲突。完整内容仅作为审阅备份，不可默认强制覆盖。
7. 如果通过 GitHub contents API 写已有文件，携带刚读到的 blob SHA；更新分支引用采用 expected_sha/非强制更新。发生版本不匹配时重新读取并合并，不去掉保护重试。
8. 不改变或重置 mojing-m0-v1 演示数据；不提交真实世界资料、密钥、.env、数据库、node_modules、dist、宿主缓存或发布凭据。
9. 不顺手格式化全仓库、重写锁文件或替换 UI；只改分配范围。必要依赖变更由集成者集中处理，保留可审阅差异。
10. 测试必须报告实际命令与结果。未运行写“未运行”，不能继承旧报告当成本次通过。既有基线为 17 项测试通过，不要求永远恰好 17 项，但不能删掉旧用例来获得绿灯。

## 统一交付清单

任务号、作者、base_commit、契约版本、分支、修改/新增/删除路径、功能与限制、迁移/回退说明、测试命令及实际结果、已知冲突、commit/PR（有则填写）。Gemini 必须另外提供 patch.diff 和文件内容包；DeepSeekHarness 必须记录原作者来源及最终集成提交。

## 阅读入口

- [DeepSeekHarness 任务书](DEEPSEEKHARNESS.md)
- [Gemini 任务书](GEMINI.md)

提示：公开 GitHub 代码库不等于在线原型公开；GitHub 更新也不等于 Sites 已同步。最终交付分别说明代码提交、构建和线上部署状态。
