"""墨境 M1-A/B 后端：世界持久化 + TXT/MD 导入。

契约：docs/contracts/M1_AB_API.md（v0.2）。
启动：uvicorn app.main:app --host 127.0.0.1 --port 8000  （工作目录 server/）
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import Settings, load_settings
from .db import db, init_db
from .errors import ApiError
from .importer import chunk_text, decode_document, normalize, preview, slice_u16, u16len
from .import_policy.policy import get_max_bytes_for_suffix
from .lore_entries import service as lore_service
from .lore_entries.errors import LoreEntryError
from .lore_search.service import search_chunks
from .lore_search.types import SearchValidationError, SearchWorldNotFoundError
from .parsers.extractor import extract_document
from .parsers.types import ParseError, ParseLimits

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


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or load_settings()
    init_db(cfg.db_path)

    app = FastAPI(title="墨境 M1-A/B API", version="0.1.0")
    app.state.settings = cfg

    if cfg.cors_origins:
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
                "SELECT * FROM documents WHERE world_id = ? AND deleted_at IS NULL ORDER BY created_at ASC",
                (world_id,),
            ).fetchall()
        payload = _world_dict(row, len(docs))
        payload["documents"] = [_document_dict(doc) for doc in docs]
        return payload

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
        return JSONResponse(status_code=204, content=None)

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
            records = getattr(parsed, "warning_records", None) or []
            if records:
                # DocumentWarningRecord is a TypedDict; count is optional.
                warnings = [dict(record) for record in records]
            else:
                warnings = [_warning_payload(item) for item in parsed.warnings]

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
                f"SELECT * FROM documents WHERE {where} ORDER BY created_at ASC, rowid ASC LIMIT ? OFFSET ?",
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
        return JSONResponse(status_code=204, content=None)

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
        return JSONResponse(status_code=204, content=None)

    @app.get("/api/v1/capabilities")
    def capabilities() -> dict[str, Any]:
        return {
            "formats": sorted(ALLOWED_SUFFIXES),
            "max_bytes": {
                ".txt": cfg.max_upload_bytes,
                ".md": cfg.max_upload_bytes,
                ".pdf": cfg.max_doc_bytes,
                ".docx": cfg.max_doc_bytes,
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

    return app


app = create_app()

