# 给 Gemini 的任务书｜静态快照下的 M1 前端协作

请先读 [协作与防覆写协议](README.md) 与 [项目全貌](../PROJECT_HANDOFF.md)。你只能读取用户提供的非实时仓库快照，不能假设已看见最新 main；你的代码由用户转交 DeepSeekHarness 集成。你不直接推送、不宣称已部署。

## 第一件事：确认你实际看到的基线

回复你收到的仓库分支、完整 commit SHA、快照时间和接口契约版本。任务分配时的功能基线是 06088df564033a8903187489d3d1549971ada50d；此后仓库增加了协作文件，务必读取。如果附件/工具不提供实际 SHA，写 UNKNOWN，先请用户提供可识别快照，不得以文件名“最新版”代替版本。

当前 M0 已可用，用户已验收图鉴滑动修复。不要重做应用、删除手势逻辑，或把模拟回合当作已接 AI。

## 任务 GEM-01：可立即开始的审阅

只审阅，不先改业务文件。交付 docs/reviews/gemini-m1-ab-review.md 的内容，包含：

- 对 M1-A/B 数据隔离、前后端契约、原文来源、手机上传与失败恢复的具体风险和建议。
- 中文 TXT/MD 验收样本与预期结果，不包含真实个人资料。
- 需要 DeepSeekHarness 回答的接口问题；每条引用快照中的确切路径或契约字段。
- 对“世界文件已导入但真实冒险尚不可用”的诚实 UI 呈现建议。

不要只说“加强安全/优化体验”，要给触发条件、错误表现和验收方式。

## 任务 GEM-02：契约确定后编写前端独立模块

必须先拿到 DeepSeekHarness 提交的 docs/contracts/M1_AB_API.md 及其确切提交快照。缺契约时可交设计草案，不能自行杜撰 API 并声称完成前端联调。

允许新增：
- src/api/**：与冻结契约一致的 TypeScript DTO、可配置地址、世界/文档请求、错误映射、取消请求支持。真实模式失败应显示失败，禁止偷偷用模拟成功数据兜底。
- src/components/documents/**：文件选择、待导入列表、导入状态、原文与章节来源预览等独立组件。通过 props/callbacks 提供接线点，不直接重写 App 或世界页面。
- tests/documents/**：必要的组件/接口行为测试建议或代码。现有测试方式是 Node test；若需要新测试依赖，单独列出，交给 DeepSeekHarness 统一处理，不改根配置和锁文件。
- docs/reviews/gemini-*.md：评审、接线说明和交付记录。

本批 TXT/MD；PDF/DOCX 在 UI 明确尚未支持，不提供看起来成功的假解析。保留中文阅读、移动安全区、浅深主题，组件尽量复用现有变量和类名。需要新增样式时放自己目录内的 CSS 并明确导入，不覆盖全局 styles.css。

不得直接修改：src/App.tsx、src/pages/WorldPages.tsx、src/model.ts、src/hooks/usePageSwipe.ts、src/interaction.ts、src/pages/Story.tsx、src/components/WorldBar.tsx、src/styles.css、vite.config.ts、package.json、package-lock.json。必要接线以局部 diff/说明交给 DeepSeekHarness；这类建议不代表获得共享文件所有权。

## 必须提供的交付包

1. DELIVERY.md：任务号、作者 Gemini、base_commit、契约 commit/path、范围、功能、限制、依赖与待接线位置。
2. manifest.json：每个路径的操作 add/modify/delete；旧文件 base_blob_sha 或 base_sha256；新文件写明“基线上不存在”。缺哈希就标 UNKNOWN 交集成者核验，不编造。
3. patch.diff：尽量提供标准 unified diff；新增文件也明确路径。完整文件内容放 files/ 供复核，不要求接收者直接覆盖仓库。
4. TEST_RESULTS.md：实际运行过的命令和结果；无法运行就写未运行，列建议验证，不能复制旧测试结果当新结果。
5. INTEGRATION.md：给 DeepSeekHarness 的 props 示例、接口依赖、必要的共享文件最小接线建议、失败及取消行为。

交付包标题必须带任务号和短基线 SHA，例如 gemini-GEM-02-abcdef1。接口或基线变化后重新出包，说明取代哪个旧包；不要让用户靠同名文件猜最新版本。

## 防覆写的具体要求

你输出时的仓库可能已变化。明确提醒 DeepSeekHarness：先读取最新文件，三方比较，再选择性应用；新增文件如已有同名文件必须停止并合并。绝不能将你的旧快照整目录或整文件覆盖实时仓库。遇到冲突，接收用户提供的新快照再修补，不假定“我没看到，所以不存在”。

没有实时仓库更新不妨碍你做模块化工作，但意味着最终集成和“已完成”结论由集成者验证。将提议、代码已写、测试已跑、已上传、已部署分别表达。
