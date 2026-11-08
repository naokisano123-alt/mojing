# server｜墨境 M1-A/B 最小后端

FastAPI + SQLite，实现 `docs/contracts/M1_AB_API.md`（v0.2）第 4 节的 14 个本批接口。

## 启动（Windows / PowerShell）

```powershell
# 1. 依赖（在仓库根目录执行，venv 不入库）
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r server\requirements.txt

# 2. 起服务（工作目录必须是 server/）
cd server
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

健康检查：<http://127.0.0.1:8000/api/v1/health> 或 `/docs`（Swagger）。

前端开发期用 Vite 代理 `/api → http://127.0.0.1:8000`，不需要处理 CORS。

## 测试

```powershell
cd server
..\.venv\Scripts\python.exe -m pytest
```

37 个用例覆盖契约第 10 节的必测清单：世界 CRUD、中文 MD/TXT、GB18030、标题与中文章节行、
块覆盖不变式（拼接 == 全文）、分页、重复导入、空文件、超限、错误编码、软删除、跨世界隔离、重启持久化、注入文本按资料保存。

## 目录

```text
server/
  app/
    main.py      路由与应用工厂（create_app）
    db.py        SQLite 建表与连接
    importer.py  解码 / 规范化 / 确定性分块 / UTF-16 偏移换算
    config.py    环境变量与默认限额
    errors.py    契约 §2.6 的错误形状
  tests/         pytest（TestClient，每个用例独立临时库）
  data/          运行时 SQLite（已 gitignore）
  requirements.txt
```

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MOJING_DB_PATH` | `server/data/mojing.db` | SQLite 路径 |
| `MOJING_MAX_UPLOAD_BYTES` | `2097152`（2 MiB） | 单文件上限 |
| `MOJING_MAX_DOCUMENTS_PER_WORLD` | `20` | 每世界文档数上限 |
| `MOJING_ALLOW_GB18030` | `1` | 允许 GB18030 兜底解码 |
| `MOJING_ALLOW_BIG5` | `0` | 默认关，见契约 §5.2 |
| `MOJING_CORS_ORIGINS` | `localhost:5173,127.0.0.1:5173` | 直连时的白名单 |

## 三个实现取舍

1. **不引 ORM**：3 张表、单方言，标准库 `sqlite3` + 显式 SQL 就够，少一层依赖与版本来回。
2. **同步解析、失败不落库**：上传即解析，失败返回 4xx，事务回滚后库里查不到这条文档（不存在"半成品"）。
3. **软删除**：`deleted_at` 置位，行保留；普通查询不可见，`include_deleted=true` 可审计。为 M1-D 的条目来源引用留余地。

## 与契约的对应

| 契约 | 实现位置 |
| --- | --- |
| §4 接口清单 | `app/main.py` |
| §3.3 文档字段 | `documents` 表 + `_document_dict` |
| §3.4 块与不变式 | `importer.chunk_text` + `test_chunks_cover_text_without_gaps` |
| §5.2 编码 | `importer.decode_document` |
| §5.4 分块 | `importer._segments` / `chunk_text` |
| §6 错误码 | `errors.ApiError` + 各处抛出点 |
