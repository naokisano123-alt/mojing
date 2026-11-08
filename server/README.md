# server｜墨境 M1 资料库后端

FastAPI + SQLite，实现世界持久化、四格式导入、原文/来源定位、中文子串检索与人工设定条目。

契约演进（均为追加式，旧端点不变）：

- [M1_AB_API v0.2](../docs/contracts/M1_AB_API.md)：世界、文档、分块、原文
- [M1_R2_API 1.0-r2](../docs/contracts/M1_R2_API.md)：PDF/DOCX、/search、/capabilities
- [M1_R3_API 1.0-r3](../docs/contracts/M1_R3_API.md)：人工条目 CRUD
- [M1_R4_API 1.0-r4](../docs/contracts/M1_R4_API.md)：来源绝对坐标、PATCH 三态、原子 revision、警告标准化

## 启动

```powershell
# 仓库根目录
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r server\requirements.txt -r server\requirements-parsers.txt

cd server
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- Swagger：<http://127.0.0.1:8000/docs>
- 健康检查：`GET /api/v1/health`
- 数据默认落在 `server/data/mojing.db`（gitignore）

## 主要端点

| 分组 | 端点 |
| --- | --- |
| 世界 | `GET/POST /api/v1/worlds`、`GET/PATCH/DELETE /api/v1/worlds/{id}` |
| 导入 | `POST /api/v1/worlds/{id}/documents`（TXT/MD/PDF/DOCX）、`GET .../documents` |
| 原文与块 | `GET /api/v1/documents/{id}`、`/raw`、`/chunks`、`/chunks/{index}`、`GET /api/v1/chunks/{id}` |
| 检索 | `GET /api/v1/search?world_id=&q=&limit=&offset=` |
| 能力 | `GET /api/v1/capabilities` |
| 人工条目 | `GET/POST /api/v1/worlds/{id}/lore-entries`、`GET/PATCH/DELETE /api/v1/lore-entries/{id}` |

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MOJING_DB_PATH` | `server/data/mojing.db` | SQLite 路径 |
| `MOJING_MAX_UPLOAD_BYTES` | `2097152` | TXT/MD 单文件上限 |
| `MOJING_MAX_DOC_BYTES` | `8388608` | PDF/DOCX 单文件上限 |
| `MOJING_MAX_PDF_PAGES` | `200` | PDF 页数上限 |
| `MOJING_MAX_TEXT_UNITS` | `2000000` | 提取文本 UTF-16 单位上限 |
| `MOJING_MAX_DOCX_UNZIPPED` | `104857600` | DOCX 解压上限 |
| `MOJING_MAX_DOCUMENTS_PER_WORLD` | `20` | 每世界文档数 |
| `MOJING_ALLOW_GB18030` | `1` | GB18030 兜底解码 |
| `MOJING_ALLOW_BIG5` | `0` | Big5（默认关，见契约 §5.2） |
| `MOJING_CORS_ORIGINS` | `localhost:5173,127.0.0.1:5173` | 直连白名单 |

## 目录

```text
server/
  app/
    main.py          路由与应用工厂
    db.py            schema + 幂等迁移
    importer.py      解码 / 规范化 / 确定性分块 / UTF-16 偏移
    config.py        限额与开关
    errors.py        统一错误形状
    parsers/         PDF/DOCX/TXT/MD 提取（Li）
    import_policy/   逐格式限额与警告标准化（Li）
    lore_search/     中文子串检索（Ma）
    lore_entries/    人工条目服务与 schema（Ma）
  tests/             pytest
```

## 测试

```powershell
cd server
..\.venv\Scripts\python.exe -m pytest
```

测试样本均为虚构资料，不包含真实个人设定。

## 已知限制

- 不做 OCR：整份扫描件返回 `422 PDF_SCANNED`
- 搜索为字面子串（不做分词、拼音、繁简），适合小规模资料库
- 只支持本地单用户；无鉴权，**不要直接暴露公网**
- 线上未部署：静态前端 + Python 后端，需要单独的托管方案
