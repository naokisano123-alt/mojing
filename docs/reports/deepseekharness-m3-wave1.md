# M3 第一轮 · DeepSeekHarness 交付（实例底座与共享接线）

- **分支**：`feat/m3-wave1`
- **代码基线**：`9388278`（任务书指定）
- **任务书快照**：`20ba6c6`（含 M3 总纲、架构审查、契约与四份任务书）
- **契约**：`docs/contracts/M3_WAVE1_API.md` v1

## 1. 本轮交付（DSH 负责部分）

| 项 | 实现 |
| --- | --- |
| 实例根 | `server/app/m3/core/schema.py`：`world_instances(id, world_id, adventure_id, world_minute, world_revision, canon_version, created_at, updated_at)` + `m3_schema_meta` 版本位 |
| 幂等迁移 | `ensure_schema()`：建表 + 版本位 + 为所有缺失实例的旧旅程补实例（`world_revision` 取当前 `adventures.revision`，`world_minute` 从 0 起）；重复执行不产生重复行 |
| 领域服务 | `server/app/m3/core/service.py`：`create_instance` / `get` / `for_adventure` / `ensure_for_adventure` / `assert_world` / `advance`（原子版本比较）/ `snapshot` / `restore`（新建实例、跨世界拒绝） |
| 回合接线 | 新旅程创建即生成实例；`adventures.commit()` 在同一事务内推进实例 `world_revision + 1` 与 `world_minute + advance_minutes`，版本不符直接 409 |
| 检查点接线 | 存档时写入实例快照（`instance_snapshot`）；恢复分支时按快照恢复出新实例 |
| 迁移接线 | `import_transfer()` 为每个导入的旅程建立独立实例（版本按存档回填） |

## 2. 关键不变式

- 实例版本与回合确认**同事务**提交；失败整体回滚，不留半个实例
- `world_revision` 与 `adventure_revision` 分离，同一游戏分钟可有多个实例版本
- 领域服务不 commit、不调用模型；网络与大段处理留在事务外
- 跨世界写入/恢复直接 `409 INSTANCE_WORLD_MISMATCH`

## 3. 测试证据（本次实跑）

| 命令 | 结果 |
| --- | --- |
| `pytest tests/m3` | **7 passed**：迁移幂等（连跑 3 次）、旧旅程补实例、新旅程建实例、回合确认推进分钟与版本、版本冲突 409、快照恢复新建实例、跨世界快照拒绝 |
| `pytest tests/m3`（补齐后） | **13 passed**：+ 同分钟多实例、提交失败整事务回滚、Worker 开关与用量、消息路由（Fake 与 503）、Fake 验收链 |
| `pytest`（全量） | **335 passed** |
| `npm test` / `npm run test:ui` / `npm run build` | **62 / 45 / 通过**（本轮实跑） |
| Android APK | 由 PR 触发（workflow 只在 main 与 PR 上跑） |

## 3.1 补齐的接线（第二轮）

| 项 | 实现 |
| --- | --- |
| 跨模块适配层 | `m3/core/adapters.py`：`DomainAdapter` 协议 + 注册表；`snapshot_all` / `restore_all`；启动时尝试加载 `m3.events` / `m3.knowledge` |
| 检查点领域快照 | `instance_snapshot.domains` 保存各模块快照；分支恢复时按 `new_instance_id` 调各模块 `restore` |
| Worker 生命周期骨架 | `m3/core/worker.py`：`pulse_enabled` 持久开关（默认关闭）、`ensure_running` 409 `PULSE_DISABLED`、模型用量表 `m3_model_usage` 与按游戏日汇总 |
| 玩家消息读路由 | `GET /api/v1/adventures/{id}/world-news`：解析实例 → 固定 `player` 持有人 → 有模块走 `list_known`，缺失返回 503 `M3_NOT_READY` |
| Fake 验收链 | `tests/m3/test_wiring.py`：实例 → 30 分钟推进 → 玩家消息 → 检查点 → 恢复分支（新实例、消息随快照恢复、角色钱物不变） |

## 4. 未完成 / 待集成

- 事件账本、初始局势（Marcin / `m3/events`）、脉动队列（Li / `m3/pulse`）、知识投影（Ma / `m3/knowledge`）尚未到包；骨架已就位，等真实实现替换
- `world_transfer` 目前只回填实例版本，**未导入**事件/知识快照（等对应模块的快照接口）
- 玩家消息读路由 `GET /adventures/{id}/world-news` 仅预留，等 Ma 的投影实现
- 检查点快照目前只含实例根，领域快照待接入各模块 `snapshot/restore`
- 真实模型、真实服务商与安卓真机未在本轮验证

## 5. 下一步

按契约收三包：事件（Marcin）→ 脉动（Li）→ 知识（Ma），逐包独立 import 分支核验；每包只含自己路径，DSH 负责接线与组合验收。