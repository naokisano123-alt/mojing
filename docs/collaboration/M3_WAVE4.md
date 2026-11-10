# M3 第四轮 · 从导入设定到 NPC 自主行动

玩家导入自己的世界资料，选择起点，由 AI 建立初始局势。游玩时由 AI 根据人物的目标、关系和所知信息推演行动，程序校验并保存结果。玩家继续输入任意行动，交易、承诺和人物关系的变化随实际剧情发生。

代码基线是 [PR #27 合入提交](https://github.com/naokisano123-alt/mojing/commit/46fb8deb8cdfe725bad5ae241b953d255eb078f1)。三位 Gemini 使用同一份含本任务书的固定仓库 ZIP 开新窗口，交付时填写该 ZIP 的完整提交 SHA。

| 助手 | 任务 | 独占代码目录 |
| --- | --- | --- |
| Gemini Marcin | [导入设定与 AI 初始化](M3_WAVE4_GEMINI_MARCIN.md) | `server/app/m3/world_analysis/`、`server/tests/m3/world_analysis/` |
| Gemini Li | [NPC 自主行动与已确认时间](M3_WAVE4_GEMINI_LI.md) | `server/app/m3/npcs/`、`server/tests/m3/npcs/` |
| Gemini Ma | [手机开局与游玩体验](M3_WAVE4_GEMINI_MA.md) | `src/features/world-start/`、`tests/ui/world-start/` |
| DeepSeekHarness | [接线与组合验收](M3_WAVE4_DEEPSEEKHARNESS.md) | 公共入口、世界脉动运行时、冒险页面和组合测试 |

三包按 [接口约定](../contracts/M3_WAVE4_API.md) 独立开发，各自提供 Fake 或 HTTP Mock。真实世界的开局来自玩家导入的资料，测试世界只作为可重放验收数据。普通叙事、自由行动、查证和存档继续共用现有流程。

## 打包和核验

沿用 [第三轮交付规范](M3_WAVE3.md#打包和核验)，清单的 `round` 改为 `m3-wave4`。每包只交改动文件、测试报告及逐文件 SHA-256 清单。集成方分别导入，先验哈希和目录归属，再对照最新主分支。组合分支附提交、compare 链接和 CI，交 GPT 复审合入。保留真实服务商、安卓真机与自动化结果的证据区分。
