# 墨境第三轮集成报告（R3-D）

- **任务**：R3-D 公共契约、限额入口、迁移/路由/客户端、三包整合与验收
- **作者**：DeepSeekHarness
- **分支**：`feat/r3-integration`
- **功能基线**：`87b5b2fed12fad648eaba1930c3968a47a1665df`（PR #2 合并后 main）
- **任务书基线**：`2540a64`（ROUND3 文档提交）
- **补充契约**：`docs/contracts/M1_R3_API.md` `1.0-r3`

## 1. 状态总表

| 项 | 状态 | 说明 |
| --- | --- | --- |
| R3-D 补充契约 | **WRITTEN** | 条目模型/端点/冲突/来源校验 + B1–B8 契约面 |
| B1 上传按格式限额 | **TESTED** | 先判扩展名再限量读取；HTTP 层三条边界用例 |
| B3 UTF-16 文本量守门 | **TESTED** | 主管线在写入前按 UTF-16 单位校验 |
| B7 match_field | **TESTED（HTTP/适配层）** | 响应携带 `match_field`，非正文命中不补 0 伪造区间；面板显示待 Ma 修复 |
| R3-M（Marcin） | **ASSIGNED** | 未收到包 |
| R3-L（Li） | **ASSIGNED** | 未收到包 |
| R3-A（Ma） | **ASSIGNED** | 未收到包 |
| M1-D 条目闭环 | **PENDING** | 等 Ma 服务 + Marcin UI |
| 线上部署 | **未部署** | 本地闭环；不声称线上可用 |

## 2. B1–B8 处置

| ID | 问题 | 本轮处理 | 状态 |
| --- | --- | --- | --- |
| B1 | 上传先按 2 MiB 读完再判格式，PDF/DOCX 8 MiB 承诺落空 | `main.py` 改为**先判扩展名与上限，再 `read(limit+1)`**；新增三条 HTTP 用例（3 MiB 坏 PDF → 422 而非 413；8 MiB+ → 413 且 `max_bytes=8388608`；TXT 3 MiB → 413 且 `max_bytes=2097152`） | **完成** |
| B2 | 前端用"是否启用 PDF"决定所有格式上限 | 服务端 `/capabilities` 已逐格式给 `max_bytes`；前端逐格式 prop 等 Marcin 的 `maxBytesByFormat` 到位后接入 | **部分** |
| B3 | `max_text_units` 用 Python len 计 | 主管线按 UTF-16 单位最终守门，超限 `422 EXTRACTION_LIMIT`；Li 模块内部计数另行修正 | **完成（主管线）** |
| B4 | 用中文子串猜 warning code | 契约 §2.4 冻结机器码与优先级；等待 Li 的 `warning_records`，再由 DSH 无损适配，禁止长期靠子串猜 | **契约完成，代码待包** |
| B5 | 查看器不显示 warnings / encoding_semantics | 属 Marcin 独占文件；契约 §2.5 已写清要求 | **待包** |
| B6 | 检索对 heading_path JSON 做 lower() 匹配 | 属 Ma 独占模块；契约 §2.3 冻结匹配与排序，待其修复 | **待包** |
| B7 | 适配层固定 `match_field='text'`、缺失坐标补 0 | HTTP 响应新增 `match_field`；前端适配透传真实值，null 不再伪装成正文区间 | **完成（层）** |
| B8 | 排序语义与契约不一致 | 契约 §2.3 冻结：正文 > 标题 > 文件名，再 `document_id, chunk_index`；与 Ma 的实现对齐后再回归 | **契约完成** |

## 3. 本轮已提交内容

| 文件 | 改动 |
| --- | --- |
| `docs/contracts/M1_R3_API.md` | 新增（`1.0-r3`） |
| `server/app/main.py` | B1 限额顺序、B3 UTF-16 守门、B7 `match_field` |
| `server/tests/test_documents.py` | 三条限额边界用例 |
| `src/api/types.ts` | `SearchResultItem.match_field` |
| `src/components/documents/WorldLoreView.tsx` | 适配层透传 `match_field`，null 坐标不补 0 |

## 4. 回归结果（本次实际执行）

| 命令 | 结果 |
| --- | --- |
| `npm test` | **41 passed / 0 failed** |
| `npm run build` | 通过，1599 modules，JS 328.82 kB（gzip 102.68 kB） |
| `pytest`（server） | **85 passed**（R2 的 82 + 本轮 3 条限额边界） |

> 41 / 82 是 R2 报告的历史基线；本轮 85 是新增用例后的实跑数字，旧用例未删。

## 5. 接下来

1. 收 Marcin / Li / Ma 三个 R3 包，逐包三方比对与独立 import 分支
2. 先接质量门槛（Li 的 import_policy + warning_records、Marcin 的 warnings 展示、Ma 的检索语义修复）
3. 再接 M1-D 条目闭环（Ma 服务 + schema → DSH 迁移与路由 → Marcin 工作台 → 来源软删/409 冲突）
4. 全量回归 + CI + PR；真机验收另计
