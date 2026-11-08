> Codex 后续小修复：R4-01（富文档上传异常）、R4-05（检索null坐标）、R4-06（逐格式前端限额）已处理，见 [修复交接](../reports/codex-r4-hotfix.md)。四助手继续其余任务；DSH合包须保留这些修复，旧快照产物做三方合并。

# 墨境｜当前协作任务入口

**当前生效：[第四轮四助手任务总表](ROUND4.md)**。代码审阅基线：`8296630705a7e61de95fbf532b8a38846dd82c78`；三个 Gemini 从包含本批任务书的同一完整提交快照开始。

先读 [项目当前状态](../PROJECT_STATUS.md) 与 [本轮审阅和复现](ROUND4_REVIEW.md)，再读个人任务书：

- [DeepSeekHarness：公共接线、集成与 M1 验收](R4_DEEPSEEKHARNESS.md)
- [Gemini（Marcin）：资料工作台前端可靠性](R4_GEMINI_MARCIN.md)
- [Gemini（Li）：导入管线、解析预算与真实文件回归](R4_GEMINI_LI.md)
- [Gemini（Ma）：条目原子并发与来源完整性](R4_GEMINI_MA.md)

三个 Gemini 只读指定快照，交付 manifest + patch + 改动文件 + 实跑报告，经用户转 DeepSeekHarness 上传。路径所有权、固定对接接口和防覆写细则全部见 ROUND4。本轮先完成 M1 的正确性和验收，M2 仅规划。

## 历史

[R3 总表](ROUND3.md) / [R3 审阅](ROUND3_REVIEW.md) / [R3 集成报告](../reports/r3-integration.md)。

[R2 总表](ROUND2.md) / [R2 审阅](ROUND2_REVIEW.md) / [R2 集成报告](../reports/r2-integration.md)。

[最初产品/M0 交接](../PROJECT_HANDOFF.md) / [初轮 DeepSeekHarness 任务](DEEPSEEKHARNESS.md) / [初轮 Gemini 任务](GEMINI.md)。

历史文档中的“当前生效”、旧人数、旧文件所有权和测试数量不继续适用；当前状态以 PROJECT_STATUS、ROUND4 和本轮实际报告为准。
