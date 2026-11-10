"""墨境 M1-A/B 后端：世界持久化 + TXT/MD 导入。

契约：docs/contracts/M1_AB_API.md（v0.2）。
启动：uvicorn app.main:app --host 127.0.0.1 --port 8000  （工作目录 server/）
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import importlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, File, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import Settings, load_settings
from .db import db, init_db
from .errors import ApiError
from .importer import chunk_text, decode_document, normalize, preview, slice_u16, u16len
from .import_policy.policy import get_max_bytes_for_suffix
from .import_policy.warnings import normalize_warning_records
from .lore_entries import service as lore_service
from .lore_entries.errors import LoreEntryError
from .narration.lifecycle import await_request_work
from .model_gateway.url import validate_and_normalize_base_url

from .narration.core import (
    ContextSource,
    DraftError,
    FakeModelClient,
    NarrationRequest,
    generate_draft,
    validate_request,
)
from .lore_search.service import search_chunks
from .lore_search.types import SearchValidationError, SearchWorldNotFoundError
from .parsers.extractor import docx_available, extract_document
from .parsers.types import ParseError, ParseLimits

MODEL_REQUEST_TIMEOUT = 60.0

ALLOWED_SUFFIXES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
TEXT_SUFFIXES = {".txt", ".md"}

_WARNING_CODE_HINTS = [
    ("扫描", "PARTIAL_TEXT"),
    ("空页", "EMPTY_PAGES"),
    ("表格", "TABLES_FLATTENED"),
    ("页眉", "HEADERS_FOOTERS_SKIPPED"),
    ("页脚", "HEADERS_FOOTERS_SKIPPED"),
    ("文本框", "TEXTBOXES_SKIPPED"),
    ("栏", "COLUMN_ORDER_UNCERTAIN"),
]


_PARSER_CODE_MAP = {
    "CORRUPT_DOCUMENT": {"pdf": "PDF_CORRUPTED", "docx": "DOCX_INVALID"},
    "ENCRYPTED_DOCUMENT": {"pdf": "PDF_ENCRYPTED", "docx": "DOCX_PROTECTED"},
    "SCANNED_OR_EMPTY_PDF": {"pdf": "PDF_SCANNED"},
    "ZIP_BOMB_DETECTED": {"pdf": "ARCHIVE_BOMB", "docx": "ARCHIVE_BOMB"},
}


def _map_parser_code(code: str, suffix: str) -> str:
    """把解析模块的内部错误码映射成契约 §1.5 的对外错误码。"""
    kind = "pdf" if suffix == ".pdf" else "docx"
    return _PARSER_CODE_MAP.get(code, {}).get(kind, code)


def _import_policy_config(cfg: Settings) -> dict[str, Any]:
    """由服务端配置构造逐格式限额策略（Li 的 import_policy 纯函数消费）。"""
    return {
        "max_bytes_by_format": {
            ".txt": cfg.max_upload_bytes,
            ".md": cfg.max_upload_bytes,
            ".pdf": cfg.max_doc_bytes,
            ".docx": cfg.max_doc_bytes,
        },
        "fallback_max_bytes": cfg.max_upload_bytes,
        "allowed_suffixes": list(ALLOWED_SUFFIXES),
    }


def _warning_payload(message: str) -> dict[str, Any]:
    code = "EXTRACTION_WARNING"
    for hint, candidate in _WARNING_CODE_HINTS:
        if hint in message:
            code = candidate
            break
    return {"code": code, "message": message}
_FORBIDDEN_FILENAME_CHARS = set('\\/:*?"<>|')


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gateway_error(exc: Exception) -> ApiError:
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None) or str(exc) or "模型网关错误。"
    status = getattr(exc, "status", None)
    if isinstance(code, str) and isinstance(status, int):
        return ApiError(status, code, message)
    return ApiError(502, "MODEL_GATEWAY_ERROR", "模型网关调用失败。")


def _context_error(exc: Exception) -> ApiError:
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None) or str(exc) or "上下文构建失败。"
    status = getattr(exc, "status", None)
    if isinstance(code, str) and isinstance(status, int):
        return ApiError(status, code, message)
    return ApiError(502, "CONTEXT_ERROR", "上下文构建失败。")


def _new_id() -> str:
    return str(uuid4())


# ------------------------------------------------------------------ 请求体


class WorldCreate(BaseModel):
    name: str = ""
    description: str = ""


class WorldPatch(BaseModel):
    name: str | None = None
    description: str | None = None


# ------------------------------------------------------------------ 行 → DTO


def _world_dict(row: sqlite3.Row, document_count: int) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "document_count": document_count,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "deleted_at": row["deleted_at"],
    }


_DOCUMENT_METADATA_COLUMNS = (
    "id, world_id, filename, media_type, byte_size, encoding, content_sha256, "
    "text_length, line_count, chunk_count, source_kind, encoding_semantics, warnings, "
    "status, created_at, updated_at, deleted_at"
)


def _document_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "world_id": row["world_id"],
        "filename": row["filename"],
        "media_type": row["media_type"],
        "byte_size": row["byte_size"],
        "encoding": row["encoding"],
        "content_sha256": row["content_sha256"],
        "text_length": row["text_length"],
        "line_count": row["line_count"],
        "chunk_count": row["chunk_count"],
        "source_kind": row["source_kind"],
        "encoding_semantics": row["encoding_semantics"],
        "warnings": json.loads(row["warnings"] or "[]"),
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "deleted_at": row["deleted_at"],
    }


def _chunk_dict(row: sqlite3.Row, include_text: bool) -> dict[str, Any]:
    body = row["text"]
    payload: dict[str, Any] = {
        "id": row["id"],
        "document_id": row["document_id"],
        "world_id": row["world_id"],
        "chunk_index": row["chunk_index"],
        "heading_path": json.loads(row["heading_path"]),
        "start_char": row["start_char"],
        "end_char": row["end_char"],
        "text_length": row["end_char"] - row["start_char"],
        "text_preview": preview(body),
    }
    if include_text:
        payload["text"] = body
    return payload


# ------------------------------------------------------------------ 校验与查询


def _clean_name(raw: str) -> str:
    name = (raw or "").strip()
    if not name or len(name) > 40:
        raise ApiError(
            422,
            "WORLD_NAME_INVALID",
            "世界名称需要在 1–40 个字符之间。",
            {"field": "name", "max_length": 40},
        )
    return name


def _clean_description(raw: str) -> str:
    text = (raw or "").strip()
    if len(text) > 600:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "世界介绍不能超过 600 个字符。",
            {"field": "description", "max_length": 600},
        )
    return text


def _sanitize_filename(raw: str | None) -> str:
    name = (raw or "").replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ch not in _FORBIDDEN_FILENAME_CHARS)
    name = name.strip()
    if not name:
        name = "未命名.txt"
    if len(name) > 120:
        suffix = Path(name).suffix[:12]
        name = name[: 120 - len(suffix)] + suffix
    return name


def _get_world(conn: sqlite3.Connection, world_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM worlds WHERE id = ? AND deleted_at IS NULL", (world_id,)
    ).fetchone()
    if row is None:
        raise ApiError(404, "WORLD_NOT_FOUND", "这个世界不存在，或者已经被删除。")
    return row


def _get_document(conn: sqlite3.Connection, document_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM documents WHERE id = ? AND deleted_at IS NULL", (document_id,)
    ).fetchone()
    if row is None:
        raise ApiError(404, "DOCUMENT_NOT_FOUND", "这份文档不存在，或者已经被删除。")
    return row


def _get_chunk(conn: sqlite3.Connection, chunk_id: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT c.* FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE c.id = ? AND d.deleted_at IS NULL
        """,
        (chunk_id,),
    ).fetchone()
    if row is None:
        raise ApiError(404, "CHUNK_NOT_FOUND", "这个章节块不存在。")
    return row


def _count_documents(conn: sqlite3.Connection, world_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM documents WHERE world_id = ? AND deleted_at IS NULL",
        (world_id,),
    ).fetchone()
    return int(row["n"])


# ------------------------------------------------------------------ 应用工厂


def _load_attr(module_name: str, *attrs: str):
    """延迟加载可选模块；缺失时返回 None，路由据此返回明确的未就绪错误。"""
    if module_name.startswith("."):
        # importlib 不接受相对名，补上当前包名
        module_name = (__package__ or "app") + module_name
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None
    for attr in attrs:
        if hasattr(module, attr):
            return getattr(module, attr)
    return None


def create_app(
    settings: Settings | None = None,
    *,
    narration_store=None,
    context_builder=None,
    model_profile=None,
    pulse_transport=None,
) -> FastAPI:
    cfg = settings or load_settings()
    hosted = bool(cfg.public_origin)
    allowed_model_urls: set[str] = set()
    if hosted:
        origin = urlsplit(validate_and_normalize_base_url(cfg.public_origin))
        if origin.scheme != "https" or cfg.public_origin != f"https://{origin.netloc}":
            raise ValueError("MOJING_PUBLIC_ORIGIN must be an HTTPS origin without a path.")
        allowed_model_urls = {
            validate_and_normalize_base_url(url) for url in cfg.allowed_model_base_urls
        }
        if any(not url.startswith("https://") for url in allowed_model_urls):
            raise ValueError("Hosted model endpoints must use HTTPS.")
    init_db(cfg.db_path)
    from .m3.core import pulse_preview
    from .m3.pulse.worker import pulse_worker_lifespan

    pulse_worker = pulse_preview.create_worker(
        cfg.db_path, narration_store=narration_store, http_transport=pulse_transport,
        allowed_base_urls=allowed_model_urls if hosted else None,
        require_https=hosted or cfg.https_model_only,
    )

    @asynccontextmanager
    async def lifespan(_app):
        from .m3.core import adapters as _m3_adapters

        _m3_adapters.load_builtin_adapters()
        try:
            async with pulse_worker_lifespan(pulse_worker, auto_start_loop=False):
                yield
        finally:
            if narration_store is not None and hasattr(narration_store, 'clear'):
                result = narration_store.clear()
                if hasattr(result, '__await__'):
                    await result

    app = FastAPI(
        title="墨境 API", version="0.2.0", lifespan=lifespan,
        openapi_url=None if hosted else "/openapi.json",
    )
    app.state.settings = cfg

    if hosted:
        @app.middleware("http")
        async def require_same_origin_for_writes(request: Request, call_next):
            # Browser Basic Auth credentials can accompany cross-site form submissions.
            if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("origin") != cfg.public_origin:
                error = ApiError(403, "ORIGIN_FORBIDDEN", "请从墨境站点内发起操作。")
                return JSONResponse(status_code=403, content=error.body())
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response

    if not hosted and cfg.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cfg.cors_origins),
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(ApiError)
    async def _api_error_handler(_request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=exc.body())

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        field = ""
        if errors:
            loc = [str(part) for part in errors[0].get("loc", []) if part != "body"]
            field = ".".join(loc)
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "请求参数不合法。",
                    "details": {"field": field},
                }
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(_request, exc: Exception) -> JSONResponse:  # pragma: no cover
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "服务端出现未预期的错误，请稍后重试。",
                }
            },
        )

    # -------------------------------------------------------------- 健康检查

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        try:
            with db(cfg.db_path) as conn:
                conn.execute("SELECT 1").fetchone()
        except sqlite3.Error:
            return JSONResponse(  # type: ignore[return-value]
                status_code=503, content={"ok": False, "db": "error"}
            )
        return {"ok": True, "version": "0.1.0", "db": "ok"}

    # -------------------------------------------------------------- 世界

    @app.post("/api/v1/worlds", status_code=201)
    def create_world(payload: WorldCreate) -> dict[str, Any]:
        name = _clean_name(payload.name)
        description = _clean_description(payload.description)
        now = _now()
        world_id = _new_id()
        with db(cfg.db_path) as conn:
            conn.execute(
                "INSERT INTO worlds (id, name, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (world_id, name, description, now, now),
            )
            row = conn.execute("SELECT * FROM worlds WHERE id = ?", (world_id,)).fetchone()
        return _world_dict(row, 0)

    @app.get("/api/v1/worlds")
    def list_worlds(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        with db(cfg.db_path) as conn:
            total = int(
                conn.execute("SELECT COUNT(*) AS n FROM worlds WHERE deleted_at IS NULL").fetchone()["n"]
            )
            rows = conn.execute(
                """
                SELECT w.*, (SELECT COUNT(*) FROM documents d
                             WHERE d.world_id = w.id AND d.deleted_at IS NULL) AS doc_count
                FROM worlds w WHERE w.deleted_at IS NULL
                ORDER BY w.created_at ASC, w.rowid ASC LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return {
            "items": [_world_dict(row, int(row["doc_count"])) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/v1/worlds/{world_id}")
    def world_detail(world_id: str) -> dict[str, Any]:
        with db(cfg.db_path) as conn:
            row = _get_world(conn, world_id)
            docs = conn.execute(
                f"SELECT {_DOCUMENT_METADATA_COLUMNS} FROM documents WHERE world_id = ? AND deleted_at IS NULL ORDER BY created_at ASC",
                (world_id,),
            ).fetchall()
        payload = _world_dict(row, len(docs))
        payload["documents"] = [_document_dict(doc) for doc in docs]
        return payload

    @app.get("/api/v1/worlds/{world_id}/export")
    def download_world(world_id: str, include_saves: bool = False) -> Response:
        from .world_export import export_world

        with db(cfg.db_path) as conn:
            conn.execute("BEGIN")
            return export_world(conn, _get_world(conn, world_id), include_saves, Path(cfg.db_path).parent)

    @app.get("/api/v1/worlds/{world_id}/transfer")
    def download_world_transfer(world_id: str, part: Literal["world", "saves", "zip"]) -> Response:
        try:
            # M3 第三轮：可移植层支持已删除来源、v5 与历史快照
            from .m3.portability import transfer_export
        except ImportError:
            from .world_transfer import transfer_export

        with db(cfg.db_path) as conn:
            conn.execute("BEGIN")
            return transfer_export(conn, _get_world(conn, world_id), part, Path(cfg.db_path).parent)

    @app.post("/api/v1/world-transfers", status_code=201)
    def restore_world_transfer(files: list[UploadFile] = File(...)) -> dict[str, Any]:
        try:
            from .m3.portability import import_transfer
        except ImportError:
            from .world_transfer import import_transfer

        with db(cfg.db_path) as conn:
            world_id = import_transfer(conn, files, cfg.max_documents_per_world)
            return _world_dict(_get_world(conn, world_id), _count_documents(conn, world_id))

    @app.patch("/api/v1/worlds/{world_id}")
    def update_world(world_id: str, payload: WorldPatch) -> dict[str, Any]:
        with db(cfg.db_path) as conn:
            _get_world(conn, world_id)
            fields: list[str] = []
            values: list[Any] = []
            if payload.name is not None:
                fields.append("name = ?")
                values.append(_clean_name(payload.name))
            if payload.description is not None:
                fields.append("description = ?")
                values.append(_clean_description(payload.description))
            if fields:
                fields.append("updated_at = ?")
                values.append(_now())
                values.append(world_id)
                conn.execute(f"UPDATE worlds SET {', '.join(fields)} WHERE id = ?", values)
            row = conn.execute("SELECT * FROM worlds WHERE id = ?", (world_id,)).fetchone()
            count = _count_documents(conn, world_id)
        return _world_dict(row, count)

    @app.delete("/api/v1/worlds/{world_id}", status_code=204)
    def delete_world(world_id: str) -> JSONResponse:
        now = _now()
        with db(cfg.db_path) as conn:
            row = conn.execute("SELECT * FROM worlds WHERE id = ?", (world_id,)).fetchone()
            if row is None:
                raise ApiError(404, "WORLD_NOT_FOUND", "这个世界不存在，或者已经被删除。")
            conn.execute("UPDATE worlds SET deleted_at = ?, updated_at = ? WHERE id = ?", (now, now, world_id))
            conn.execute(
                "UPDATE documents SET deleted_at = ?, updated_at = ? WHERE world_id = ? AND deleted_at IS NULL",
                (now, now, world_id),
            )
        return Response(status_code=204)

    # -------------------------------------------------------------- 文档

    @app.post("/api/v1/worlds/{world_id}/documents", status_code=201)
    async def upload_document(
        world_id: str,
        file: UploadFile = File(...),
        allow_duplicate: bool = Query(False),
    ) -> dict[str, Any]:
        # 先按扩展名确定该格式的真实上限，再按上限限量读取（B1）
        filename = _sanitize_filename(file.filename)
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise ApiError(
                415,
                "UNSUPPORTED_MEDIA_TYPE",
                "本批支持 TXT、Markdown、PDF 与 DOCX 文件。",
                {"allowed": list(ALLOWED_SUFFIXES)},
            )

        limit = get_max_bytes_for_suffix(suffix, _import_policy_config(cfg))
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise ApiError(
                413,
                "FILE_TOO_LARGE",
                f"文件超过 {limit // 1024 // 1024} MiB 上限。",
                {"max_bytes": limit, "received_bytes": len(data)},
            )

        if suffix in TEXT_SUFFIXES:
            if not data.strip():
                raise ApiError(422, "EMPTY_DOCUMENT", "这个文件是空的，没有可导入的文字。")
            text, encoding = decode_document(data, cfg)
            text = normalize(text)
            media_type = ALLOWED_SUFFIXES[suffix]
            encoding_semantics = "source_bytes"
            warnings: list[dict[str, Any]] = []
        else:
            try:
                parsed = extract_document(
                    data,
                    filename,
                    limits=ParseLimits(
                        max_bytes=cfg.max_doc_bytes,
                        max_pages=cfg.max_pdf_pages,
                        max_extracted_chars=cfg.max_text_units,
                        max_uncompressed_bytes=cfg.max_docx_unzipped_bytes,
                    ),
                )
            except ParseError as exc:
                raise ApiError(
                    exc.status,
                    _map_parser_code(exc.code, suffix),
                    exc.message,
                    exc.details,
                ) from exc
            text = normalize(parsed.text)
            encoding = parsed.encoding
            media_type = parsed.media_type
            encoding_semantics = "extracted_text"
            warnings = normalize_warning_records(
                getattr(parsed, "warning_records", None) or [],
                parsed.warnings or (),
            )

        if not text.strip():
            raise ApiError(422, "EMPTY_DOCUMENT", "这个文件是空的，没有可导入的文字。")

        text_units = u16len(text)
        if text_units > cfg.max_text_units:
            raise ApiError(
                422,
                "EXTRACTION_LIMIT",
                f"提取文本超过 {cfg.max_text_units} 个 UTF-16 单位的上限。",
                {"limit": cfg.max_text_units, "actual": text_units},
            )

        digest = hashlib.sha256(data).hexdigest()
        chunks = chunk_text(text)
        if not chunks:  # pragma: no cover - 理论上不会发生
            raise ApiError(422, "EMPTY_DOCUMENT", "没能从文件里切出任何内容。")

        document_id = _new_id()
        now = _now()
        with db(cfg.db_path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            _get_world(conn, world_id)
            if not allow_duplicate:
                existing = conn.execute(
                    "SELECT id FROM documents WHERE world_id = ? AND content_sha256 = ? AND deleted_at IS NULL",
                    (world_id, digest),
                ).fetchone()
                if existing is not None:
                    raise ApiError(
                        409,
                        "DUPLICATE_DOCUMENT",
                        "这个世界里已经导入过内容相同的文件。",
                        {"existing_document_id": existing["id"], "content_sha256": digest},
                    )
            if _count_documents(conn, world_id) >= cfg.max_documents_per_world:
                raise ApiError(
                    409,
                    "WORLD_DOCUMENT_LIMIT",
                    f"每个世界最多 {cfg.max_documents_per_world} 份文档，请先删除旧文档。",
                    {"limit": cfg.max_documents_per_world},
                )
            conn.execute(
                """
                INSERT INTO documents (id, world_id, filename, media_type, byte_size, encoding,
                                       content_sha256, text, text_length, line_count, chunk_count,
                                       source_kind, encoding_semantics, warnings, status,
                                       created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'user_import', ?, ?, 'ready', ?, ?)
                """,
                (
                    document_id,
                    world_id,
                    filename,
                    media_type,
                    len(data),
                    encoding,
                    digest,
                    text,
                    u16len(text),
                    text.count("\n") + 1,
                    len(chunks),
                    encoding_semantics,
                    json.dumps(warnings, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.executemany(
                """
                INSERT INTO chunks (id, document_id, world_id, chunk_index, heading_path,
                                    start_char, end_char, text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        _new_id(),
                        document_id,
                        world_id,
                        chunk.chunk_index,
                        json.dumps(chunk.heading_path, ensure_ascii=False),
                        chunk.start_char,
                        chunk.end_char,
                        chunk.text,
                    )
                    for chunk in chunks
                ],
            )
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return _document_dict(row)

    @app.get("/api/v1/worlds/{world_id}/documents")
    def list_documents(
        world_id: str,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        include_deleted: bool = Query(False),
    ) -> dict[str, Any]:
        with db(cfg.db_path) as conn:
            _get_world(conn, world_id)
            where = "world_id = ?" if include_deleted else "world_id = ? AND deleted_at IS NULL"
            total = int(conn.execute(f"SELECT COUNT(*) AS n FROM documents WHERE {where}", (world_id,)).fetchone()["n"])
            rows = conn.execute(
                f"SELECT {_DOCUMENT_METADATA_COLUMNS} FROM documents WHERE {where} ORDER BY created_at ASC, rowid ASC LIMIT ? OFFSET ?",
                (world_id, limit, offset),
            ).fetchall()
        return {
            "items": [_document_dict(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/v1/documents/{document_id}")
    def document_detail(document_id: str) -> dict[str, Any]:
        with db(cfg.db_path) as conn:
            row = _get_document(conn, document_id)
        return _document_dict(row)

    @app.get("/api/v1/documents/{document_id}/raw")
    def document_raw(
        document_id: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(20000, ge=1, le=50000),
    ) -> dict[str, Any]:
        with db(cfg.db_path) as conn:
            row = _get_document(conn, document_id)
        text = row["text"]
        total = int(row["text_length"])
        if offset > total:
            raise ApiError(422, "INVALID_RANGE", "起始位置超出正文长度。", {"field": "offset"})
        body, length = slice_u16(text, offset, limit)
        next_offset = offset + length
        return {
            "document_id": document_id,
            "encoding": row["encoding"],
            "offset": offset,
            "length": length,
            "total_length": total,
            "text": body,
            "next_offset": next_offset if next_offset < total else None,
        }

    @app.get("/api/v1/documents/{document_id}/chunks")
    def list_chunks(
        document_id: str,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        include_text: bool = Query(False),
    ) -> dict[str, Any]:
        with db(cfg.db_path) as conn:
            _get_document(conn, document_id)
            total = int(
                conn.execute("SELECT COUNT(*) AS n FROM chunks WHERE document_id = ?", (document_id,)).fetchone()["n"]
            )
            rows = conn.execute(
                "SELECT * FROM chunks WHERE document_id = ? ORDER BY chunk_index ASC LIMIT ? OFFSET ?",
                (document_id, limit, offset),
            ).fetchall()
        return {
            "items": [_chunk_dict(row, include_text) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/v1/documents/{document_id}/chunks/{chunk_index}")
    def chunk_by_index(document_id: str, chunk_index: int) -> dict[str, Any]:
        with db(cfg.db_path) as conn:
            _get_document(conn, document_id)
            row = conn.execute(
                "SELECT * FROM chunks WHERE document_id = ? AND chunk_index = ?",
                (document_id, chunk_index),
            ).fetchone()
        if row is None:
            raise ApiError(404, "CHUNK_NOT_FOUND", "这个章节块不存在。", {"chunk_index": chunk_index})
        return _chunk_dict(row, include_text=True)

    @app.get("/api/v1/chunks/{chunk_id}")
    def chunk_detail(chunk_id: str) -> dict[str, Any]:
        with db(cfg.db_path) as conn:
            row = _get_chunk(conn, chunk_id)
        return _chunk_dict(row, include_text=True)

    @app.delete("/api/v1/documents/{document_id}", status_code=204)
    def delete_document(document_id: str) -> JSONResponse:
        now = _now()
        with db(cfg.db_path) as conn:
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
            if row is None:
                raise ApiError(404, "DOCUMENT_NOT_FOUND", "这份文档不存在，或者已经被删除。")
            if row["deleted_at"] is None:
                conn.execute(
                    "UPDATE documents SET deleted_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, document_id),
                )
        return Response(status_code=204)

    @app.exception_handler(LoreEntryError)
    async def _lore_entry_error_handler(_request, exc: LoreEntryError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=exc.body())

    @app.get("/api/v1/worlds/{world_id}/lore-entries")
    def list_lore_entries(
        world_id: str,
        type: str | None = Query(None),
        status: str | None = Query(None),
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        with db(cfg.db_path) as conn:
            return lore_service.list_entries(
                conn, world_id, type=type, status=status, limit=limit, offset=offset
            )

    @app.post("/api/v1/worlds/{world_id}/lore-entries", status_code=201)
    def create_lore_entry(world_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with db(cfg.db_path) as conn:
            return lore_service.create_entry(conn, world_id, payload)

    @app.get("/api/v1/lore-entries/{entry_id}")
    def get_lore_entry(entry_id: str) -> dict[str, Any]:
        with db(cfg.db_path) as conn:
            return lore_service.get_entry(conn, entry_id)

    @app.patch("/api/v1/lore-entries/{entry_id}")
    def update_lore_entry(entry_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        expected = payload.get("expected_revision")
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
            raise ApiError(
                422,
                "VALIDATION_ERROR",
                "expected_revision 必须是正整数。",
                {"field": "expected_revision"},
            )
        body = {key: value for key, value in payload.items() if key != "expected_revision"}
        with db(cfg.db_path) as conn:
            return lore_service.update_entry(conn, entry_id, body, expected)

    @app.delete("/api/v1/lore-entries/{entry_id}", status_code=204)
    def delete_lore_entry(
        entry_id: str,
        expected_revision: str = Query(...),
    ) -> JSONResponse:
        # 严格正整数：拒绝 true / 1.5 / "1abc"，不让参数强转混过去
        if not re.fullmatch(r"[1-9][0-9]*", expected_revision or ""):
            raise ApiError(
                422,
                "VALIDATION_ERROR",
                "expected_revision 必须是正整数。",
                {"field": "expected_revision"},
            )
        with db(cfg.db_path) as conn:
            lore_service.delete_entry(conn, entry_id, int(expected_revision))
        return Response(status_code=204)

    # -------------------------------------------------------------- M2-A 叙事实验

    def _store():
        nonlocal narration_store
        if narration_store is not None:
            return narration_store
        factory = _load_attr(".model_gateway", "get_store", "default_store")
        if factory is not None:
            narration_store = factory() if callable(factory) else factory
        else:
            store_cls = _load_attr(".model_gateway", "ModelSessionStore")
            if store_cls is None:
                raise ApiError(503, "M2_NOT_READY", "模型网关尚未就绪（缺少 model_gateway 模块）。")
            narration_store = store_cls()
        return narration_store

    def _context_builder():
        nonlocal context_builder
        if context_builder is not None:
            return context_builder
        builder = _load_attr(".narration_context", "build_context")
        if builder is None:
            raise ApiError(503, "M2_NOT_READY", "叙事上下文模块尚未就绪（缺少 narration_context）。")
        context_builder = builder
        return context_builder

    def _map_draft_error(exc: DraftError) -> ApiError:
        code = getattr(exc, "code", "MODEL_OUTPUT_INVALID")
        status = 422 if code in {"ACTION_INVALID", "CONTEXT_INVALID"} else 502
        return ApiError(status, code, str(exc))

    saved_session = None

    if model_profile is not None:
        @app.post("/api/v1/model-profile/restore")
        async def restore_model_profile():
            nonlocal saved_session
            if saved_session is None:
                try:
                    raw = model_profile.load()
                    if raw is not None:
                        profile = json.loads(str(raw))
                        saved_session = {**_store().create(**profile), "remembered": True}
                except Exception:
                    raise ApiError(503, "MODEL_PROFILE_UNAVAILABLE", "无法读取本机密钥。请重新配置或删除本机密钥。") from None
            return {"session": saved_session}

        @app.delete("/api/v1/model-profile", status_code=204)
        async def delete_model_profile():
            nonlocal saved_session
            try:
                model_profile.clear()
            except Exception:
                raise ApiError(503, "MODEL_PROFILE_UNAVAILABLE", "无法删除本机密钥，请重试。") from None
            _store().clear()
            saved_session = None
            return Response(status_code=204)

    @app.post("/api/v1/model-sessions", status_code=201)
    async def create_model_session(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal saved_session
        store = _store()
        base_url = payload.get("base_url")
        model = payload.get("model")
        api_key = payload.get("api_key")
        if not all(isinstance(x, str) and x.strip() for x in (base_url, model, api_key)):
            raise ApiError(422, "VALIDATION_ERROR", "base_url、model、api_key 都必须是非空字符串。")
        try:
            if hosted or cfg.https_model_only:
                base_url = validate_and_normalize_base_url(base_url)
                if cfg.https_model_only and not base_url.startswith("https://"):
                    raise ApiError(422, "MODEL_HTTPS_REQUIRED", "安卓应用的模型 API 地址需要使用 HTTPS。")
                if hosted and base_url not in allowed_model_urls:
                    raise ApiError(422, "MODEL_ENDPOINT_NOT_ALLOWED", "此站点未开放该模型 API 地址，请检查地址或联系部署者。")
            created = store.create(base_url, model, api_key)
            if hasattr(created, "__await__"):
                created = await created
        except Exception as exc:
            raise _gateway_error(exc)
        if model_profile is not None:
            try:
                model_profile.save(json.dumps({
                    "base_url": base_url, "model": model.strip(), "api_key": api_key.strip(),
                }, ensure_ascii=False))
            except Exception:
                store.delete(created["session_id"])
                raise ApiError(503, "MODEL_PROFILE_UNAVAILABLE", "无法加密保存本机密钥，请重试。") from None
            if saved_session is not None:
                store.delete(saved_session["session_id"])
            saved_session = {**created, "remembered": True}
            return saved_session
        return created

    @app.delete("/api/v1/model-sessions/{session_id}", status_code=204)
    async def delete_model_session(session_id: str) -> Response:
        nonlocal saved_session
        store = _store()
        try:
            result = store.delete(session_id)
            if hasattr(result, "__await__"):
                await result
            if saved_session is not None and session_id == saved_session["session_id"]:
                saved_session = None
        except Exception as exc:
            raise _gateway_error(exc)
        return Response(status_code=204)

    @app.post("/api/v1/model-sessions/{session_id}/test")
    async def test_model_session(session_id: str, http_request: Request) -> dict[str, Any]:
        store = _store()
        try:
            result = store.test(session_id)
            if hasattr(result, "__await__"):
                result = await await_request_work(http_request, result, MODEL_REQUEST_TIMEOUT)
        except TimeoutError:
            raise ApiError(504, "MODEL_TIMEOUT", "模型连接测试超时。")
        except Exception as exc:
            raise _gateway_error(exc)
        return result

    @app.post("/api/v1/worlds/{world_id}/narration-preview")
    async def narration_preview(world_id: str, payload: dict[str, Any], http_request: Request) -> dict[str, Any]:
        action = payload.get("action")
        mode = payload.get("mode", "fake")
        session_id = payload.get("session_id")
        if not isinstance(action, str) or not action.strip():
            raise ApiError(422, "ACTION_INVALID", "请先输入行动。")
        if mode not in ("fake", "live"):
            raise ApiError(422, "VALIDATION_ERROR", "mode 只能是 fake 或 live。")

        try:
            validate_request(NarrationRequest(action=action))
        except DraftError as exc:
            raise ApiError(422, exc.code, str(exc))
        builder = _context_builder()
        # 连接只在构造上下文期间持有；模型等待前必须关闭，避免长事务
        with db(cfg.db_path) as conn:
            try:
                bundle = builder(conn, world_id, action)
            except Exception as exc:
                raise _context_error(exc)

        if mode == "live":
            if not isinstance(session_id, str) or not session_id:
                raise ApiError(409, "MODEL_SESSION_EXPIRED", "模型会话无效或已过期。")
            try:
                client = _store().get_client(session_id)
            except Exception as exc:
                raise _gateway_error(exc)
        else:
            client = FakeModelClient()

        request = NarrationRequest(
            action=action,
            sources=tuple(
                ContextSource(source_id=s.source_id, kind=s.kind, text=s.text)
                for s in bundle.sources
            ),
        )
        try:
            draft = await await_request_work(http_request, generate_draft(request, client), MODEL_REQUEST_TIMEOUT)
        except asyncio.TimeoutError:
            raise ApiError(504, "MODEL_TIMEOUT", "模型响应超过 60 秒，已取消。")
        except asyncio.CancelledError:
            raise
        except DraftError as exc:
            raise _map_draft_error(exc)
        except Exception as exc:
            raise _gateway_error(exc)

        return {
            "request_id": _new_id(),
            "mode": mode,
            "narrative": draft.narrative,
            "references": list(draft.references),
            "sources": list(bundle.metadata),
        }


    @app.get("/api/v1/capabilities")
    def capabilities() -> dict[str, Any]:
        formats = sorted(suffix for suffix in ALLOWED_SUFFIXES if suffix != ".docx" or docx_available())
        return {
            "formats": formats,
            "max_bytes": {
                suffix: cfg.max_upload_bytes if suffix in TEXT_SUFFIXES else cfg.max_doc_bytes
                for suffix in formats
            },
            "search": True,
        }

    @app.get("/api/v1/search")
    def search(
        world_id: str = Query(...),
        q: str = Query(""),
        limit: int = Query(20, ge=1, le=50),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        with db(cfg.db_path) as conn:
            try:
                result = search_chunks(conn, world_id, q, limit=limit, offset=offset)
            except SearchWorldNotFoundError:
                raise ApiError(404, "WORLD_NOT_FOUND", "这个世界不存在，或者已经被删除。")
            except SearchValidationError as exc:
                raise ApiError(422, "SEARCH_QUERY_INVALID", str(exc) or "检索关键词不合法。")
        items = [
            {
                "world_id": item["world_id"],
                "document_id": item["document_id"],
                "document_filename": item["filename"],
                "chunk_id": item["chunk_id"],
                "chunk_index": item["chunk_index"],
                "heading_path": item["heading_path"],
                "start_char": item["start_char"],
                "end_char": item["end_char"],
                "snippet": item["snippet"],
                "match_field": item.get("match_field", "text"),
                "match_start": item.get("match_chunk_start_char"),
                "match_end": item.get("match_chunk_end_char"),
            }
            for item in result["items"]
        ]
        return {
            "query": q,
            "items": items,
            "total": result["total"],
            "limit": result["limit"],
            "offset": result["offset"],
        }

    from .adventures.routes import register_routes
    register_routes(app, cfg, _store, _context_builder, pulse_worker)
    pulse_preview.register_routes(app, cfg.db_path, pulse_worker, _store)

    from .adventures.service import get_adventure
    from .m3.core import adapters as m3_adapters
    from .m3.core import service as m3_instances

    @app.get("/api/v1/adventures/{adventure_id}/world-news")
    def world_news(
        adventure_id: str,
        before: str | None = None,
        limit: int = Query(30, ge=1, le=50),
    ) -> dict[str, Any]:
        """玩家消息投影：只返回该实例玩家有渠道得知的消息（模块未接入时 503）。"""
        with db(cfg.db_path) as conn:
            get_adventure(conn, adventure_id)
            instance = m3_instances.for_adventure(conn, adventure_id)
            adapter = m3_adapters.get_adapter("knowledge")
            if adapter is None:
                raise ApiError(503, "M3_NOT_READY", "玩家消息投影尚未接入（缺少 knowledge 模块）。")
            result = adapter.list_known(conn, instance["id"], "player", before=before, limit=limit)
            return {"items": result.get("items", []), "next_before": result.get("next_before")}

    # ---- M3 第三轮：玩家主动查证 / 交谈（Ma） ----
    from .m3 import encounters as m3_encounters

    @app.get("/api/v1/adventures/{adventure_id}/world-encounters")
    def world_encounters(adventure_id: str, location: str | None = None) -> dict[str, Any]:
        with db(cfg.db_path) as conn:
            get_adventure(conn, adventure_id)
            instance = m3_instances.for_adventure(conn, adventure_id)
            return m3_encounters.get_encounter_entrypoint_bundle(
                conn, instance["id"], player_location=location
            )

    @app.post(
        "/api/v1/adventures/{adventure_id}/world-encounters/drafts", status_code=201
    )
    def prepare_world_encounter(adventure_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with db(cfg.db_path) as conn:
            get_adventure(conn, adventure_id)
            instance = m3_instances.for_adventure(conn, adventure_id)
            return m3_encounters.prepare_encounter_draft(conn, instance["id"], payload)

    @app.post(
        "/api/v1/adventures/{adventure_id}/world-encounters/drafts/{draft_id}/commit"
    )
    def commit_world_encounter(
        adventure_id: str, draft_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        minute = payload.get("commit_world_minute")
        if minute is not None and (type(minute) is not int or minute < 0):
            raise ApiError(422, "VALIDATION_ERROR", "commit_world_minute 必须是非负整数。")
        with db(cfg.db_path) as conn:
            get_adventure(conn, adventure_id)
            instance = m3_instances.for_adventure(conn, adventure_id)
            return m3_encounters.commit_encounter_draft(
                conn, instance["id"], draft_id, commit_world_minute=minute
            )

    return app


app = create_app()

