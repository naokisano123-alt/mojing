# M3 第二轮协作任务

本轮目标是把首轮的固定演示接成可选的真实模型脉动，同时守住玩家知情范围、游戏时钟和存档隔离。普通叙事在脉动关闭、缺少连接或预算耗尽时仍可继续。真实调用由玩家主动开启，本轮开发与 CI 使用 Fake 或 Mock。

代码基线为合入 PR #24 的 [8a95587a778e3849385bc413632d402ecf09972f](https://github.com/naokisano123-alt/mojing/commit/8a95587a778e3849385bc413632d402ecf09972f)。三位 Gemini 开新窗口时使用同一份**包含本任务书的完整仓库 ZIP**，另记该 ZIP 对应的提交 SHA。交付报告同时记录代码基线与任务书快照 SHA。

| 负责方 | 交付 | 业务文件归属 |
| --- | --- | --- |
| DeepSeekHarness | [独立导包、公共接线和验收](M3_WAVE2_DEEPSEEKHARNESS.md) | 公共入口、`server/app/m3/core/`、冒险与模型网关接线、整体验收 |
| Gemini Marcin | [真实模型的事件推演与权威校验](M3_WAVE2_GEMINI_MARCIN.md) | `server/app/m3/events/` |
| Gemini Li | [付费调用闸门、离线恢复和预算](M3_WAVE2_GEMINI_LI.md) | `server/app/m3/pulse/` |
| Gemini Ma | [知识传播、叙事投影和手机端控制](M3_WAVE2_GEMINI_MA.md) | `server/app/m3/knowledge/`、`src/features/world-knowledge/`、新目录 `src/features/world-pulse/` |

先读 [M3 总纲](../M3_DESIGN_DRAFT.md)、[首轮复审](../reports/codex-m3-wave1-integration.md) 和 [本轮接口约定](../contracts/M3_WAVE2_API.md)。三个模块只用本轮约定和各自 fixture 并行开发，包内以适配边界为准。M3.4 起的 NPC 自主模拟、动态经济、开放行动系统、大存档增量和已删除来源归档另列后续。

## 防覆写与交付

1. 三位 Gemini 分别交付补丁或只含改动文件的 ZIP。报告列明完整文件清单、基线和任务书 SHA、独立启动与测试命令、实际结果及接线需求。删除或重命名单列说明。用户把三个文件包分别转给 DeepSeekHarness。
2. 每包一条 `import/m3-wave2-{marcin,li,ma}` 分支、一个 PR。从最新 `origin/main` 建独立工作树，逐文件比对任务基线与当前主分支，再应用补丁。完整目录包只用于比对，禁止解压覆盖工作树、强推或直接推主分支。出现同文件冲突时保留双方差异并报告，公共接线通过单独 PR 解决。
3. 共享入口、数据库初始化、Android 工程、依赖锁、CI 和旧报告归 DeepSeekHarness 集成。Gemini 在报告中给出建议接线位置。需要调整本轮接口时，先在报告中提出差异并由集成方同步契约。
4. 包内 fixture 使用合成资料、独立临时数据库和假凭据。实际用户数据库、Key、会话标识、APK 和本地环境文件不得提交。分开记录 Fake、Mock、APK 打包、真实服务商和安卓真机证据。

合并顺序由 CI 与验收决定。优先合入三个自测通过的模块，再开公共接线 PR。付费调用入口须在知识隔离、离线和版本竞态通过组合验收后启用。
