# 墨境第五轮集成报告（R5-D）

- **分支**：`feat/r4-integration`
- **起点**：`1e5ea7776576e4ce4b56b2236c3661ddc81e0971`（PR #5 后 main）
- **契约**：`docs/contracts/M1_R4_API.md` `1.0-r4`

## 1. 状态总表

| 项 | 状态 | 说明 |
| --- | --- | --- |
| R5-D 契约 | **DONE** | 修正坐标笔误、chunk_index、PATCH 三态、原子 revision、严格 DELETE、警告标准化、draftStore |
| UI 测试基础设施 | **DONE** | Vitest 3.2 + jsdom 26 + RTL 16 + user-event；CI 同时跑两套 |
| R5-M（Marcin） | **IMPORTED / TESTED** | `import/r5-marcin` @ `f307cf1`，6 文件，7 条组件测试 |
| R5-L（Li） | **IMPORTED / TESTED** | `import/r5-li` @ `7d45cf7`，10 文件，警告标准化 + 逐步预算 + 导入矩阵 |
| R5-A（Ma） | **IMPORTED / TESTED** | `import/r5-ma` @ `907aef2`，5 文件，原子 revision + 严格坐标 |
| 集成 | **TESTED** | 三支已合并；draftStore、警告标准化已接 |
| 真机验收 | **未验收** | 见 `docs/M1_ACCEPTANCE.md` |
| 线上部署 | **未部署** | 本地闭环 |

## 2. 收包核验

| 包 | 分支 | 提交 | 校验 | 备注 |
| --- | --- | --- | --- | --- |
| gemini-marcin-r5-delivery | import/r5-marcin | f307cf1 | 6/6 sha256 一致，apply 通过 | 压缩包内多一层目录，按实际路径核验 |
| gemini-li-R5-1e5ea77 | import/r5-li | 7d45cf7 | 10/10 一致，apply 通过 | |
| gemini-ma-r5-delivery | import/r5-ma | 907aef2 | 5/5 一致，apply 通过 | manifest 用 `entries` 键而非 `files`，按实际结构核验 |

三支 `--no-ff` 合并，作者保留，无覆盖、无强推。

## 3. 共享接线

| 位置 | 改动 |
| --- | --- |
| `server/app/main.py` | 警告改用 Li 的 `normalize_warning_records`，删掉本地 dict 复制与中文关键字猜码 |
| `src/components/documents/WorldLoreView.tsx` | 用稳定 `useRef` Map 注入 `draftStore`（切子页签不丢草稿、按 world key 隔离）；条目面板接收 |
| `tests/integration/world-lore-view.test.tsx` | 新增 3 条装配测试：详情渲染、四页签真实挂载、切世界清空旧数据 |

## 4. 回归（本次实际执行）

| 命令 | 结果 |
| --- | --- |
| `pytest`（server） | **159 passed** |
| `npm test`（node:test） | **62 passed** |
| `npm run test:ui`（Vitest） | **11 passed**（3 文件：Marcin 7 + DSH 装配 3 + 基础设施 1） |
| `npm run build` | 通过，1606 modules，JS 363.46 kB（gzip 111.53 kB） |

## 5. 偏差与遗留

- Marcin 压缩包根目录多一层 `gemini-marcin-r5-delivery/`，不影响 apply，记录备查。
- Ma 的 manifest 使用 `entries` 键、字段为 `base_sha256`/`new_sha256`，与其他人不同；已按实际结构核验，未要求返工。
- 真机（安卓软键盘、手势、320px）未验收。
- 线上 Python 后端未部署。
