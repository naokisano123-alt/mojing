# 墨境 M1-A/B 实施报告（DeepSeekHarness）

- **任务**：DSH-02 最小后端（世界持久化 + TXT/MD 导入）
- **作者**：DeepSeekHarness
- **分支**：`feat/m1-ab-backend`
- **契约**：`docs/contracts/M1_AB_API.md` v0.2
- **契约提交**：`990da90a39e80665377731ed81cb1a75748b7074`
- **上游基线**：`a71342ad8a751ffb98adaaa102fe85691a0f9cf1`（任务书基线 `06088df`）
- **状态**：DSH-02 完成并通过测试；DSH-01 完成；**DSH-03（Gemini 包导入 + 前端接线）等待交付包**

---

## 1. 交付内容

```text
server/
  app/
    __init__.py
    main.py       14 个本批接口、应用工厂 create_app、错误处理、CORS
    db.py         SQLite schema（worlds / documents / chunks）与连接
    importer.py   编码探测、规范化、确定性分块、UTF-16 偏移换算
    config.py     Settings（可被环境变量覆盖）
    errors.py     {"error": {"code","message","details"}} 统一形状
  tests/
    conftest.py           TestClient 与临时库 fixture
    test_worlds.py        9 例
    test_documents.py     19 例
    test_importer.py      9 例
  requirements.txt
  pytest.ini
  README.md
```

依赖（`server/requirements.txt`，实测版本）：fastapi 0.162.2、uvicorn 0.54.0、python-multipart 0.0.33、pytest 9.2.0、httpx 0.28.1。

## 2. 启动

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r server\requirements.txt
cd server
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- Swagger：`http://127.0.0.1:8000/docs`
- 健康检查：`GET /api/v1/health` → `{"ok":true,"version":"0.1.0","db":"ok"}`
- 数据默认落在 `server/data/mojing.db`（已 gitignore）；测试用临时库，互不污染

## 3. 实现决策

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| 持久层 | 标准库 `sqlite3` + 显式 SQL | 3 张表、单方言；少一层依赖，schema 完全可控。不引 SQLAlchemy/SQLModel |
| 上传解析 | 同步，成功 201 | 2 MiB 以内、毫秒到百毫秒；202 + 轮询要引入任务表，本批收益不抵成本 |
| 失败处理 | 4xx，事务回滚，**不落库** | 列表里出现的文档一定是可用的，没有"半成品"状态 |
| 删除 | 软删除（`deleted_at`） | 为 M1-D 的 LoreEntry 来源引用留余地；`include_deleted=true` 可审计 |
| 偏移单位 | UTF-16 code unit | 与浏览器 JS 字符串下标一致，前端可直接 `slice`；服务端用 `len(s.encode("utf-16-le"))//2` 换算 |
| 编码 | BOM → 严格 UTF-8 → 严格 GB18030（默认开）→ Big5（默认关） | Big5 与 GB18030 字节空间重叠，误判产出"看着正常其实错字"的正文 |
| 分块 | 确定性规则，无模型 | Markdown ATX 标题 + 纯文本中文章节行（`第X卷/章/回/节/篇/部`）+ 段落聚合，目标 600–1800、硬上限 4000 |

## 4. 接口示例（真实响应，已脱敏为虚构资料）

```bash
# 建世界
curl -s -X POST http://127.0.0.1:8000/api/v1/worlds \
  -H 'Content-Type: application/json' \
  -d '{"name":"潮汐档案馆","description":"一座建在退潮线上的档案馆。"}'
# → 201 {"id":"ccdb272f-...","name":"潮汐档案馆","document_count":0,...}

# 导入中文 Markdown
curl -s -X POST http://127.0.0.1:8000/api/v1/worlds/{world_id}/documents \
  -F "file=@云山纪.md"
# → 201 {"status":"ready","encoding":"utf-8","chunk_count":5,"text_length":122,...}

# 分块（含全文）
curl -s "http://127.0.0.1:8000/api/v1/documents/{id}/chunks?include_text=true"
# → {"items":[{"chunk_index":0,"heading_path":["云山纪 · 世界总纲"],"start_char":0,"end_char":40,...}]}
```

## 5. 测试结果（实际执行）

```powershell
cd server
..\.venv\Scripts\python.exe -m pytest
```

**结果：37 passed**（`.....................................  [100%]`，耗时 < 5s，退出码 0）。
运行环境：Windows 10 + Python 3.11.9 + 上述依赖版本。唯一警告是 Starlette 提示 `httpx` 未来需换 `httpx2` 的弃用提示，与本实现无关。

覆盖对照（契约 §10 必测清单）：

| 必测项 | 用例 |
| --- | --- |
| 中文 MD/TXT | `test_upload_markdown_creates_ready_document`、`test_chinese_chapter_lines_split_plain_text` |
| 标题与段落顺序 | `test_heading_path_follows_markdown_levels` |
| 块覆盖不变式（拼接 == 全文） | `test_chunks_cover_text_without_gaps` |
| 空文件 | `test_empty_file_leaves_no_document` |
| 损坏 / 不支持编码 | `test_undecodable_file_is_rejected`、`test_gb18030_is_detected_not_mangled` |
| 非法格式 | `test_unsupported_suffix` |
| 超限 | `test_too_large_is_rejected`、`test_document_limit` |
| 同名 / 重复文档 | `test_duplicate_document_is_rejected`、`test_duplicate_allowed_with_flag` |
| 来源定位 | `test_chunk_by_index_and_id`、`test_raw_pagination` |
| 失败状态 | 同上（失败即无文档，列表 total 不变） |
| 重启持久化 | `test_data_survives_restart` |
| 不同世界隔离 | `test_documents_are_isolated_between_worlds`、`test_worlds_are_isolated` |
| 注入文本按资料保存 | `test_injected_instruction_is_stored_as_plain_text` |
| 演示存档不变 | 后端不读写 localStorage；本轮未改 `src/`，M0 代码零改动 |

## 6. 启动冒烟（真实进程，非 TestClient）

用 `uvicorn --port 8011` 起真实进程，临时库，curl 走完整 HTTP：

| 检查 | 结果 |
| --- | --- |
| `GET /api/v1/health` | `{"ok":true,"version":"0.1.0","db":"ok"}` |
| `POST /api/v1/worlds` | 201，返回 UUID |
| `POST .../documents`（中文 MD） | 201，`status=ready`、`chunks=5`、`text_length=122` |
| `GET .../chunks?include_text=true` | 块 0 `start_char=0, end_char=40` |
| 拼接校验 | 块拼接与 `/raw` 全文**逐字符相等**（`True`） |
| UTF-8 往返 | `/raw` 文本与源文件**完全一致**（`True`） |

> 注：PowerShell 5.1 的 `Invoke-RestMethod` 会把无 charset 的 JSON 按 Latin-1 解码，控制台里显示成 `æ½®æ±` 属于显示问题；上面这行用 WebClient + UTF8 复核，确认服务端数据正确。

## 7. 未完成 / 阻塞

| 项 | 状态 |
| --- | --- |
| DSH-03 接收 Gemini GEM-02 包（`git apply --check`、三方比对、`import/gemini-GEM-02` 分支） | **阻塞**：等待用户转交交付包 |
| 前端接线：`src/App.tsx`、`src/pages/WorldPages.tsx`、`src/model.ts`、`vite.config.ts` 代理 | 待 Gemini 模块到位后进行 |
| 前端 `npm test` / `npm run build` | **未运行**（本机 `node_modules` 未安装成功，沙箱 `EPERM: spawn`）；本轮未改 `src/`，不影响既有 17 例基线，但报告不把它算作本轮通过 |
| M1-C（PDF/DOCX）、M1-D（条目 CRUD）、M1-E（检索） | 按计划未做 |

## 8. 迁移与回滚

- **迁移**：无。`server/` 是全新目录，不改动任何前端文件；数据库首次启动自动建表。
- **回滚**：删除 `server/` 与 `.venv` 即可回到 a71342a 状态；M0 的 `localStorage` 数据从未被触碰。
- **数据**：`server/data/mojing.db` 为本地运行产物，不入库。

## 9. 与其他方的同步

- 契约无变化（仍 v0.2），Gemini 可继续按 `990da90` 的契约实现前端模块。
- 后端启动方式与端点已固定，前端联调时只需 `VITE_API_BASE_URL` 或 Vite 代理 `/api → http://127.0.0.1:8000`。
