# 墨境第二轮集成报告（R2-D）

- **任务**：R2-D 契约、共享接线、集成与回归
- **作者**：DeepSeekHarness
- **分支**：`feat/r2-integration`
- **功能基线**：`c83eccb44734dd16ef6da8e0c2382eff4bed99df`（PR #1 合并后的 main）
- **任务书基线**：`3e900637389b0f9d6ae838d1d62eae0609d4ca34`（第二轮文档提交）
- **首版补充契约**：`docs/contracts/M1_R2_API.md` `1.0-r2`

## 1. 状态总表

| 项 | 状态 | 说明 |
| --- | --- | --- |
| DSH-01/02/03（M1-A/B） | **MERGED** | PR #1 已合并，不重做 |
| R2-D 补充契约 | **WRITTEN** | `docs/contracts/M1_R2_API.md` |
| R2-M（Marcin）包 | **PENDING** | 尚未收到 |
| R2-L（Li）包 | **PENDING** | 尚未收到 |
| R2-A（Ma）包 | **PENDING** | 尚未收到 |
| R2 集成 | **IN PROGRESS** | 共享层修复已提交；三个包到位后逐包 import |
| 线上部署 | **未部署** | 本轮只有本地闭环；静态站点不能跑 Python 后端，不声称后端已上线 |

## 2. 本轮已完成（不依赖三个包的部分）

### 2.1 补充契约

新增 `docs/contracts/M1_R2_API.md`（additive，不改写 v0.2）：

- PDF/DOCX 的 `media_type`、`encoding_semantics`（`source_bytes` / `extracted_text`）、`warnings` 语义
- 新错误码：`PDF_ENCRYPTED` / `PDF_CORRUPTED` / `PDF_SCANNED` / `DOCX_INVALID` / `DOCX_PROTECTED` / `EXTRACTION_LIMIT` / `ARCHIVE_BOMB`
- 限额决策：TXT/MD 保持 2 MiB；PDF/DOCX 8 MiB、≤200 页、解压 ≤100 MiB、文本 ≤2,000,000 UTF-16 单位
- `GET /api/v1/search` 的完整形状：`start_char/end_char` 是**全文**坐标，`match_start/match_end` 是**块内**坐标，`snippet` 是摘要
- 新增 `GET /api/v1/capabilities`，前端只启用后端真实可用的格式
- **取消语义修正**：客户端取消只代表停止等待，服务端可能已提交；取消后必须刷新列表核对，重复上传靠 `content_sha256` 去重

### 2.2 共享层修复（本轮已提交）

| 问题 | 处理 |
| --- | --- |
| `importer.py` 长段被硬切后 `heading_path` 挂错（子块继承了后面的标题） | **已复现并修复**：取"起点不晚于该块的最后一段"；新增回归 `test_long_paragraph_keeps_own_heading_path` |
| UTF-16 分页可能切断代理对 | 新增 `test_utf16_slice_never_splits_surrogate`（现有实现通过，锁定行为） |
| `WorldLoreView` 世界切换无取消，旧响应可能覆盖新世界 | `useEffect` 加 `AbortController`，切世界清空 `worldDetail` 与查看器状态 |
| `#lore` 刷新后丢上下文 | 路由改为 `#lore/<worldId>`，刷新可恢复；返回列表时刷新文档计数 |
| 文案暗示检索已上线 | "当前可检索设定" → "当前可浏览章节切片、核对原文；检索能力将在本轮接入" |
| 降级文案可能暗示用了导入设定 | 改为"开启本地模拟试玩（不会使用本世界的设定）" |
| 缺少可复现 CI | 新增 `.github/workflows/ci.yml`（Node 22.18 + Python 3.11，前端测试/构建 + 后端 pytest）与 `scripts/check.ps1` 本地一键检查 |

### 2.3 DTO 可加性扩展

`src/api/types.ts` 追加（全部可选，旧代码不受影响）：`encoding_semantics`、`warnings`、`page_range`、`DocumentWarning`、`SearchResultItem`、`SearchResponse`、`CapabilitiesResponse`。

## 3. 测试结果（本次实际执行）

| 命令 | 结果 |
| --- | --- |
| `npm test` | 22 passed / 0 failed |
| `npm run build` | 通过，1595 modules，JS 313.42 kB（gzip 98.16 kB） |
| `pytest`（server） | **39 passed**（原 37 + 本轮 2 个 importer 回归） |

> 22 / 37 是上一轮报告的基线；本轮的 39 是加了两条回归用例后的实际数字。旧用例一条没删。

## 4. 待三个包到位后要做

1. 逐包核验 manifest / snapshot SHA / 文件哈希，独立 `import/r2-{marcin,li,ma}` 分支
2. 逐文件三方比对，`git apply --check` 后应用，保留作者
3. Li 的解析模块接入上传入口（扩展 `accept`/`media_type`/`warnings`），旧 TXT/MD 路径不变
4. Ma 的 `search_chunks` 注册到 `/api/v1/search`，前端薄适配层接 `LoreSearchPanel`，来源回跳接 `DocumentViewer.initialSource`
5. Marcin 的查看器/上传可靠性改进接入 `WorldLoreView`
6. 全量验收：四格式导入 → 检索 → 定位原文 → 删除后不可检索 → 重启仍保留；M0 手势与草稿单独回归

## 5. 风险与限制

- **未做真实浏览器与安卓真机验证**：视觉、手势、软键盘仍需用户验收。
- **未部署**：`dist/` 是静态前端，Python 后端没有可用的线上运行环境；本轮交付为本地完整闭环。
- 依赖解析库（Li 包）未安装前，PDF/DOCX 入口必须保持关闭（契约 §1.7 的能力接口即为此设计）。
- `/search` 第一版是字面子串扫描，不承诺分词召回或海量性能。
