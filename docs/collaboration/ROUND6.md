# 第六轮：M2-A 模型叙事实验（当前生效）

代码起点55718d4cb25c56693cfb730fc641eb86e5dd248e。开始前读[当前M2方案](../M2_PLAN.md)和[冻结契约](../contracts/M2_A_API.md)。本轮任务书发布后的同一完整提交快照供三个Gemini使用，记录实际snapshot_commit完整SHA，禁止只写main。

用户于2026-11-08反馈安卓测试完成、一切正常；具体型号/浏览器/逐项用例未登记，因此仅记为用户已测流程正常，不补造验收数据。允许进入M2-A。

Codex本轮已交付纯草案校验与FakeModelClient，尚未接路由或真实服务商。四包从冻结接口独立开发：
| 人员 | 独占路径 | 任务书 |
| --- | --- | --- |
| Gemini Marcin | src/features/narration-preview/**、tests/ui/narration-preview/** | [R6_MARCIN](R6_GEMINI_MARCIN.md) |
| Gemini Li | server/app/model_gateway/**、server/tests/model_gateway/** | [R6_LI](R6_GEMINI_LI.md) |
| Gemini Ma | server/app/narration_context/**、server/tests/narration_context/** | [R6_MA](R6_GEMINI_MA.md) |
| DeepSeekHarness | 公共路由/配置/客户端、WorldLoreView入口、集成测试/依赖/CI | [R6_DSH](R6_DEEPSEEKHARNESS.md) |

每人另拥有自己的docs/reports/*-r6.md。既有M0/M1模块和Codex narration核心只读，必要差异交DSH。Li/Ma不修改main.py，Marcin不修改src/api和页面接线；DSH不得提前代写三包。接口已在契约冻结，不等对方在线才能开工。

继续防覆写：独立分支，三个Gemini完成后用户转包给DSH；manifest包含作者、任务、实际snapshot_commit/contract、每路径旧blob/SHA256与新SHA256、增改删。交patch.diff+files/+实跑报告，根相对路径，无tmp/绝对路径；干净快照git apply --check。DSH独立import分支核验，旧包三方合并，保留作者。禁止ZIP覆盖、强推/reset清理他人工作，Git树必须继承完整base_tree。

第五轮已合并，旧任务不重复开工。已做但未提交的旧修复先核对最新代码再提出差异，不把旧文件覆盖到本轮。M2-A只预览草案，不接管正式角色状态、M0存档或M3规则引擎。

最终验收：Fake闭环和Live mock闭环、真实会话创建/删除/过期、错误/取消无写入、越界引用拒绝、UI不丢输入、不泄露Key、M1回归与手机入口。真实付费请求由用户配置后主动触发，不在自动化测试中使用真实Key。部署与合并分别报告。
