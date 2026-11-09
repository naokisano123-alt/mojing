# DeepSeekHarness · 实例底座与集成

先读[本轮入口](M3_WAVE1.md)、[架构审查](../reports/codex-m3-architecture-review.md)和[契约](../contracts/M3_WAVE1_API.md)。

## 本轮交付

- 实现 M3.0 实例根、幂等迁移和共享 Canon 与动态实例的归属检查。新正式旅程生成实例，旧正式旅程可兼容接入，M0 保持独立。
- 接入现有回合确认、检查点、恢复分支和 ZIP/JSON 迁移。前台确认与时钟更新、队列登记同事务。检查点捕获精确实例版本，导入和分支新建实例；模块尚未合并时可用空快照或 Fake fixture 验证。
- 预留 Worker 生命周期、模型用途、玩家消息读路由与共享适配层。实际接线使用三位 Gemini 包中的实现，避免各自再写一套。按最新主分支逐包代传，提交各自 PR 后再做组合集成。
- 写一条可复现验收链。用北境小镇的 Fake 初始事件线，逐步走过事件发生、消息传播、玩家看到消息和检查点分支，并核对玩家钱物不被后台改变。预算和真实服务商留单独验收记录。

## 文件和验收

负责 `server/app/m3/core/`、`server/tests/m3/integration/`、`tests/integration/m3-*.test.tsx` 及必要共享接线。其他三包由各自负责人维护，跨包变更分 PR 说明。

至少验证旧库重复迁移、前台原有冒险、同分钟不同实例版本、恢复前快照不带后来的事件、并发失败原子回滚、旧包导入、新包实例和领域引用重映射、暂停与离线保持时钟。执行后端全套、前端单测和 UI 测试、生产构建；检查 GitHub CI 与 Android APK 结果并分开记录。报告写入 `docs/reports/deepseekharness-m3-wave1.md`。
