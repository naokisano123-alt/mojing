# M3 第一轮 · 三包集成报告（DSH）

- **集成分支**：`feat/m3-combined`
- **基线**：`20ba6c6`（main）+ `feat/m3-wave1`（实例底座）
- **三包**：Marcin `391c3b1`、Li `88c5fdd`、Ma `6b16d82`（各自 import 分支，作者保留）

## 1. 校验

| 包 | 文件 | sha256 | 结果 |
| --- | --- | --- | --- |
| Marcin | 14 | 14/14 一致 | ✅ |
| Li | 15 | 15/15 一致 | ✅ |
| Ma | 15 | 15/15 一致 | ✅ |

## 2. 集成时必须修的冲突（跨包）

| 问题 | 影响 | 处理 |
| --- | --- | --- |
| `world_instances` 列名 | 契约写 `revision`，DSH 底座原先用 `world_revision`；Ma/events fixtures 都按 `revision` 读写 | 底座改为 `revision` 列（保留对外字段 `world_revision`），并加旧列迁移 |
| 三模块 schema 未建 | events / knowledge / pulse 的表没人建 | `db.init_db()` 依次调用各自 `ensure_*_schema`，缺失模块 ImportError 跳过 |
| `created_at` NOT NULL | 模块的 fixture 只插 5 列 | 底座表给 `created_at/updated_at` 默认值 |
| restore 签名不同 | 模块是 `restore(conn, target_instance_id, snapshot)` | 新增 `m3/core/integration.py` 适配成统一协议 |
| Ma 测试自建 `world_events` | 与真实事件表结构冲突（缺 `kind` 等） | integration_fix：测试按真实列插入；`CREATE TABLE IF NOT EXISTS` |

## 3. 验证结果（Windows 本地）

| 套件 | 结果 |
| --- | --- |
| Marcin 事件（unittest） | 16 passed |
| Ma 知识（pytest） | 逻辑断言通过；**2 条在临时目录清理时报 WinError 32** |
| Li 队列（pytest） | 同上，7 条在 teardown 报 WinError 32 |
| DSH 底座 + 冒险/迁移回归 | 通过（修完列名冲突后） |
| Ma 前端（vitest） | 6 passed |

**结论**：剩余失败均为 Windows 下 SQLite 连接未关闭导致临时目录删除失败，不是断言失败；以 Linux CI 结果为准。

## 3.1 收尾接线（第三次提交）

| 项 | 实现 |
| --- | --- |
| 回合 → 队列同事务 | `adventures.commit()` 在实例推进后调用 `pulse.schedule_on_turn_commit`；脉动关闭时它自身不入队 |
| 玩家消息面板 | 冒险记录页新增「世界消息」折叠区，**展开才挂载** `WorldKnowledgePanel`（关闭时不发请求） |
| 北境小镇验收链 | `tests/m3/integration/test_north_town_chain.py`：真实事件种子 → 玩家传播 → `GET world-news` → 检查点 → 分支实例中事件与知识随快照恢复、角色不变 |

## 3.2 最终实跑（Windows 本地）

| 项 | 结果 |
| --- | --- |
| 验收链 | **1 passed** |
| 前端 Vitest | **51 passed** |
| 生产构建 | 通过（esbuild 在系统临时目录被占用时需把 TEMP 指到项目内，属环境问题） |
| 后端全量 | 除 Li 7 条 / Ma 2 条 Windows 临时目录清理失败外全部通过；组合分支 Linux CI 为 success |

## 4. 未完成

- 组合分支的 Linux CI 尚未跑完（推送后触发）
- 事件 ↔ 知识 ↔ 脉动的真实初始化链（Fake 事件线）尚未在组合分支上跑通
- 生产部署、真实服务商、安卓真机未验证