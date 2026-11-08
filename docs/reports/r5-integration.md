# 墨境第五轮集成报告（R5-D）

- **分支**：`feat/r4-integration`
- **起点**：`1e5ea7776576e4ce4b56b2236c3661ddc81e0971`（PR #5 后 main）
- **契约**：`docs/contracts/M1_R4_API.md` `1.0-r4`

## 1. 状态总表

| 项 | 状态 | 说明 |
| --- | --- | --- |
| R5-D 契约 | **WRITTEN** | 修正 R3 来源坐标笔误；chunk_index、PATCH 三态、原子 revision、严格 DELETE、警告标准化、draftStore |
| UI 测试基础设施 | **DONE** | Vitest 3.2 + jsdom 26 + RTL 16，`npm run test:ui`，CI 同时跑 node:test 与 Vitest |
| 严格 revision 校验 | **DONE** | PATCH/DELETE 拒绝 bool/小数/非数字，`422 VALIDATION_ERROR` |
| DTO 可加性 | **DONE** | `LoreEntrySourceLink.chunk_index` |
| draftStore 注入 | **PENDING** | 等 Marcin R5 面板可选 prop |
| R5-M（Marcin） | **ASSIGNED** | 未收到包 |
| R5-L（Li） | **ASSIGNED** | 未收到包 |
| R5-A（Ma） | **ASSIGNED** | 未收到包 |
| 真机验收 | **未验收** | 见 `docs/M1_ACCEPTANCE.md` |
| 线上部署 | **未部署** | 本地闭环 |

## 2. 本轮完成

| 文件 | 改动 |
| --- | --- |
| `docs/contracts/M1_R4_API.md` | 新增（1.0-r4） |
| `vitest.config.ts` | 新增，include `tests/ui/**/*.test.tsx`、`tests/integration/**/*.test.tsx` |
| `package.json` | 新增 `test:ui`；devDependencies 增加 vitest/jsdom/RTL |
| `.github/workflows/ci.yml` | frontend job 增加 `npm run test:ui` |
| `server/app/main.py` | PATCH/DELETE 严格正整数校验 |
| `src/api/types.ts` | `chunk_index` 可加性 |
| `tests/integration/ui-smoke.test.tsx` | 证明 Vitest + jsdom + RTL 能真实渲染组件 |
| `docs/M1_ACCEPTANCE.md` / `docs/M2_PLAN.md` | 新增 |
| `README.md` / `server/README.md` | 更新当前功能、启动、限制 |

## 3. 回归（本次实际执行）

| 命令 | 结果 |
| --- | --- |
| `pytest` | 见提交时的实际输出（本轮新增严格校验用例前为 130 passed） |
| `npm test` | 60 passed |
| `npm run test:ui` | 基础设施冒烟通过 |
| `npm run build` | 通过 |

> 精确数字见提交记录与 CI；本文件不固化"永远通过"的数量。

## 4. 待收包

按 `ROUND5.md`，还需接收 Marcin / Li / Ma 三个 R5 包：

1. Li：警告标准化纯模块 + 逐步预算 + HTTP 导入矩阵
2. Ma：原子 revision、严格绝对坐标、历史引用可编辑
3. Marcin：草稿/冲突对比/异步生命周期 + 真实组件测试

收包后按 import 分支核验、三方合并，最后跑全量并更新本文件与 `M1_ACCEPTANCE.md`。
