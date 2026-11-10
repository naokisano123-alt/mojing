# M3 第三轮协作任务

共同起点是主分支上首次包含本任务书和第二轮集成修复的完整提交。请用交付时提供的固定 ZIP 开新窗口，报告写完整 SHA。先读 [M3 总纲](../M3_DESIGN_DRAFT.md)、[第二轮接口](../contracts/M3_WAVE2_API.md) 和 [第二轮复验](../reports/codex-m3-wave2-completion.md)。

| 助手 | 交付 | 独占目录 |
| --- | --- | --- |
| Gemini Marcin | [NPC 自主行动与模拟深度](M3_WAVE3_GEMINI_MARCIN.md) | `server/app/m3/npcs/`、`server/tests/m3/npcs/` |
| Gemini Li | [可续玩迁移、已删除来源与快照性能](M3_WAVE3_GEMINI_LI.md) | `server/app/m3/portability/`、`server/tests/m3/portability/` |
| Gemini Ma | [玩家主动查证、交谈与消息后续行动](M3_WAVE3_GEMINI_MA.md) | `server/app/m3/encounters/`、`src/features/world-encounters/` 及各自同名测试目录 |
| DeepSeekHarness | [逐包复验、接线及发布](M3_WAVE3_DEEPSEEKHARNESS.md) | 公共入口、数据库迁移、模型网关、Android 和 CI |

三个业务包分别用自己的 SQLite 或 transport fixture 独立开发。Marcin 的 NPC 是世界中的行动方，Ma 的互动由玩家主动发起。两包用实例内稳定 NPC 标识关联，开发阶段不互相导入。Li 的备份模块以快照适配器处理模块载荷，无需等待新的 NPC schema。动态经济和自动结算玩家账本另排阶段。

## 打包和核验

每个 Gemini 交一份**只含新增或修改文件**的 ZIP，附独立测试报告和 `manifest.json`。清单使用仓库相对路径，`sha256` 为 ZIP 内文件原始字节的小写十六进制摘要。清单自身无需哈希。删除项须显式列出；重命名按删除加新增表示。

```json
{
  "round": "m3-wave3",
  "base_sha": "<完整的共同仓库快照 SHA>",
  "files": [
    {"path": "server/app/m3/example/service.py", "action": "add", "sha256": "<64 位 SHA-256>"}
  ]
}
```

操作类型为 `add`、`modify`、`delete`。删除项只列路径和操作。禁止重复路径、绝对路径、`..`、符号链接、越权文件或整个仓库压缩包。报告也作为文件列入清单。

DeepSeekHarness 为三包建独立分支和 PR，先核验哈希与文件归属，再与最新主分支逐文件比较。共享接线另开 PR。若主分支已经修改同一文件，报告冲突并保留双方差异，禁止整包覆盖或强推。真实 Key、会话标识、用户数据库和 APK 不进入 Git。
