# R4-M｜Gemini（Marcin）：可可靠编辑和定位的资料工作台

你从用户提供的 R4 完整快照开始；无法实时读写库，完成包必须经用户交 DeepSeekHarness 上传。先读 ROUND4 固定接口与审阅，不沿用 R3 老包重写最新文件。

## 你独占的范围

src/features/lore-entries/**、src/features/lore-search/**；src/components/documents 的 DocumentUpload.tsx、DocumentViewer.tsx、documents.css、viewerUtils.ts；tests/lore-entries/**、tests/lore-search/**、tests/documents/upload-* 与 viewer-*；新增 tests/ui/**；docs/reports/gemini-marcin-r4.md。

不改 WorldLoreView、App、src/api、全局样式、依赖/锁文件/CI。所需接线建议写报告由 DSH 做。你可以完整整理这两个工作台模块内部结构，但不要换视觉方案或重写 M0。

## 交付块一：正确的来源浏览

- 删除 chunkIndex:0 硬编码。类型按 ROUND4 扩为 chunkId/可选实际 index；引用可用时传服务端真实 chunk_index，没有就传 chunkId 给 resolver；不要推算或虚构。
- DocumentViewer 指定非首批块时，实际把目标块装入展示列表/目标分页并展开、滚动可见。避免只把全文存在 expandedMap 却因列表不含目标而看不到；分页不能因插入目标块后使用 chunks.length 错跳后续页。
- 同一文档再次定位另一来源也响应新 target；emoji 全文 UTF-16 正确，标题/文件名命中只打开块不高亮正文。不存在/软删来源有可理解反馈。
- LoreSearchPanel 真正使用 match_field/null 坐标，标明正文/标题/文档名命中；不把标题关键词标在正文位置或补0。现有 literal 检索不宣传语义理解。
- 草稿中来源候选的 snippet 是搜索摘要，不是已核验的原文摘录，明确标注；保存成功后显示服务器 excerpt。

## 交付块二：草稿、冲突与异步生命周期

- 接受可选 draftStore（ROUND4 已定接口）；默认可以实例内存，但外部注入能跨 tab 卸载恢复。draft+baseRevision 成对保存，输入过程中更新；空标题但改 type/status/source 的草稿也保留。取消不丢，成功仅清本条，明确丢弃才删除。
- 恢复旧草稿不能自动绑定新 revision。409 拉最新仅用于对照，保留用户文字和原基线；显式选择再提交前说明会使用本地编辑覆盖哪些字段。防重复提交。
- 编辑发送真正 PATCH 差异，未改来源不要重发历史来源。ROUND4 后端也支持保留既有失效引用，UI 仍显示复核提醒；不能强迫用户删除证据才能改标题。
- 使用请求 generation/token 与 signal 判断所有读/写结果，涵盖筛选、列表、详情、来源搜索、冲突详情、保存、删除。abort 不等于服务端未保存；旧世界 mutation 完成不能开新世界弹层或清掉新草稿。关闭弹层/卸载/切世界和 StrictMode 均验证。
- 保留中文输入与移动键盘体验，按钮/卡片横滑规则不受破坏。

## 必须是真实测试

旧 entries-panel 测试多是本文件自写 Map/handler，不能证明组件。新增 tests/ui 下真实 React 渲染测试（DSH 配 Vitest+jsdom+RTL），通过注入 api/draftStore 与 deferred Promise 驱动点击，不用复制生产逻辑到测试。

至少覆盖：候选添加与编辑保存；409 对照后原草稿不丢；重挂载恢复 baseRevision；A世界延迟响应晚于B世界；卸载后 mutation 不污染B；第2块和超首批块可见且正确定位；同文档新target；非正文命中无高亮；失效引用编辑标题。选取高价值场景合并测试，别用标签映射/AbortController 自身行为凑数量。

运行现有 npm test/npm run build；配置到位后 npm run test:ui。快照没有依赖时写“UI 测试未运行，待 DSH 配置”，不得伪报通过。报告需要的依赖由 DSH 安装，交付代码与未跑原因仍完整。

## 交包

遵守 ROUND4 manifest/patch/files/报告要求，根相对路径、旧新哈希、实际快照完整 SHA；只打包改动。报告列界面行为、测试实际结果、DSH 接线清单和剩余限制。先在干净快照检查补丁；禁止上传含临时目录前缀的 patch。不要直接写仓库或覆盖其他助手文件。
