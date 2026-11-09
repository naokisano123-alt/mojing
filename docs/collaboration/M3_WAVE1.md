# M3 第一轮协作任务

代码基线是主分支完整提交 [9388278f8a334f457e92ed820cc5b2456c76dfd8](https://github.com/naokisano123-alt/mojing/commit/9388278f8a334f457e92ed820cc5b2456c76dfd8)。这就是共同的起点。领取任务时下载含本任务书的完整仓库快照，另外记录该快照的完整提交 SHA。代码基线用于核对历史，任务书快照用于确认这轮的约定。两者都写入交接报告。

先读 [用户总纲](../M3_DESIGN_DRAFT.md)、[技术审查](../reports/codex-m3-architecture-review.md) 和 [集成契约](../contracts/M3_WAVE1_API.md)。总纲里的完整验收是阶段目标，表中范围是本轮交付。

| 负责方 | 任务 | 独占业务路径 |
| --- | --- | --- |
| DeepSeekHarness | [实例底座、接口接线与整体验收](M3_WAVE1_DEEPSEEKHARNESS.md) | `server/app/m3/core/`、公共接线文件、集成测试 |
| Gemini Marcin | [世界事件账本与 AI 初始局势](M3_WAVE1_GEMINI_MARCIN.md) | `server/app/m3/events/` |
| Gemini Li | [持久队列、预算与模拟会话](M3_WAVE1_GEMINI_LI.md) | `server/app/m3/pulse/` |
| Gemini Ma | [人物知识、信息传播与玩家消息](M3_WAVE1_GEMINI_MA.md) | `server/app/m3/knowledge/`、`src/features/world-knowledge/` |

## 独立开发与上传

1. 三个 Gemini 从相同的完整任务书快照开新窗口。各模块按契约使用自己的 SQLite fixture、Fake 或 Mock 独立开发。无须等 DeepSeek 的底座代码，不能为能运行而替换现有后端入口。
2. Gemini 分别提供仅含自己新增或改动文件的补丁或文件包，并附文件清单、代码基线及任务书快照 SHA、接口对照、实际测试命令及结果。逐条标注运行不了或仍待集成的用例。用户将包交给 DeepSeekHarness，后者代传到各自功能分支。
3. 每包一个 PR。DeepSeekHarness 从最新 `origin/main` 建分支，先比对共同基线与当前改动，再检查补丁路径、应用补丁、审查差异。禁止整包覆盖工作树、强推、修改他人 PR 或直接推 `main`。同一路径冲突先报告，保留双方差异，用新的接线提交解决。
4. Gemini 公共接线需求写在自己的报告中。DeepSeekHarness 拥有 `db.py`、`main.py`、现有冒险/检查点/迁移文件、公共模型网关和根页面接线。三个 Gemini 不修改这些文件、依赖锁文件、Android 工程或 CI。新增依赖先提出理由和可替代方案。
5. 本轮报告放在各任务指定的 `docs/reports/` 路径。代码、测试和报告一起进各自 PR。合并前等 CI。若本地用 Mock，报告就写 Mock；真实模型 Key、用户库副本、环境文件和 APK 不进入 Git。

## 集成顺序

三位 Gemini 并行开发。先合入实例底座和事件包，跑通 Fake 初始化、分支与迁移。随后接队列和知识投影，先验证离线、暂停、失效、竞态与知识隔离，再开放脉动开关。玩家入口只显示通过游戏内渠道取得的消息，沿用已有的开局与快进体验。真实付费调用由用户配置并主动开启。

旧协作轮次保留历史，禁止拿旧快照重新覆盖当前成果。
