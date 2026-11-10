# M3 第四轮 · 集成准备（待三包）

- **分支**：`feat/m3-wave4-prep`
- **基线**：main `9117404`（PR #27 验收修复 + PR #28 第四轮任务书）
- **状态**：三包未到；本文记录**不依赖包、已先行完成**的公共接线与验收准备。

## 1. 公共插槽：从资料建立初始局势

`server/app/m3/core/world_analysis.py` 定义 `WorldAnalysisProvider` 协议与注册表：

| 成员 | 语义 |
| --- | --- |
| `analyze(conn, world_id, request)` | 生成**未确认**草案，只读资料，不写正式事件/人物/知识/时间 |
| `get_draft(conn, draft_id)` | 读取草案摘要，不存在返回 None |
| `confirm(conn, draft_id, request)` | 在调用方事务内确认并建立旅程状态 |

HTTP 架子（`main.py`）：

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| POST | `/api/v1/worlds/{id}/analysis-drafts` | 201 草案；provider 缺失 → 503 `M4_NOT_READY` |
| GET | `/api/v1/analysis-drafts/{draft_id}` | 200 草案；不存在 → 404 `DRAFT_NOT_FOUND` |
| POST | `/api/v1/analysis-drafts/{draft_id}/confirm` | `BEGIN IMMEDIATE` 内确认，整笔事务 |

启动时若存在 `app.m3.world_analysis.provider` 自动注册；否则保持未就绪，**不伪造成功**。

## 2. 组合验收素材：两份不同地点与人物的普通导入资料

`server/tests/m3/integration/fixtures/`：

| 文件 | 地点 | 人物 |
| --- | --- | --- |
| `world_a_白石镇.md` | 白石镇（北境官道） | 阿岚、霍五、柳婆婆 |
| `world_b_潮汐港.md` | 潮汐港（南方海岸） | 沈九、阿螺、老鲛 |

两份都走**真实上传接口**（`POST /worlds/{id}/documents`），不是数据库预置：

- 各自分块 ≥ 1；
- 检索隔离：A 世界搜「沈九」为 0，B 世界搜「阿九」…（已验证 A 搜 B 人物为 0）；
- 文档 id、旅程 id 互不相同。

## 3. 已跑验证

| 项 | 结果 |
| --- | --- |
| 新增 `test_world_analysis_seam.py` | 3 passed（503 未就绪、草案生命周期、未知世界 404） |
| 新增 `test_wave4_fixtures.py` | 2 passed（双世界导入与人物隔离） |
| 后端全量 | 见提交对应 CI |
| 前端 / 构建 | 未改前端，沿用 main |

## 4. 等包后的接线计划

1. Marcin `m3/world_analysis/` → 实现 provider（分析、草案、事务内确认）
2. Li `m3/npcs/` → NPC 推演接脉动队列与预算，提交只推进一次实例版本
3. Ma `src/features/world-start/` → 接入世界资料与旅程入口
4. 组合链：两份资料分别「导入 → 分析 → 确认开局 → 推进时间 → NPC 行动 → 再见人物/查证 → 迁移续玩」
