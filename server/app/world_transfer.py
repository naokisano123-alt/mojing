"""Portable world and save files. Imports always allocate an independent world."""
from contextlib import ExitStack
import hashlib
import json
import math
from shutil import copyfileobj
import sqlite3
from tempfile import TemporaryFile
from urllib.parse import quote
from uuid import uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile
import zlib

from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from .adventures.memory import validate_event
from .adventures.state import character
from .errors import ApiError
from .world_export import _write_json

VERSION = 1
SAVES_VERSION = 2
SETTINGS_FORMAT = "mojing-world-settings"
SAVES_FORMAT = "mojing-world-saves"
LIMIT = 128 * 1024 * 1024
WORLD_FILE = "world.json"
SAVE_FILE = "saves.json"
WORLD_TABLES = {
    "documents": "id filename media_type byte_size encoding content_sha256 text text_length line_count chunk_count source_kind encoding_semantics warnings status created_at updated_at",
    "chunks": "id document_id chunk_index heading_path start_char end_char",
    "lore_entries": "id type title summary status revision origin created_at updated_at",
    "lore_entry_sources": "id entry_id document_id chunk_id start_char end_char excerpt created_at",
    "item_descriptions": "name description",
}
SAVE_TABLES = {
    "adventures": "id name mode revision created_at character_json origin_checkpoint_id",
    "adventure_turns": "adventure_id sequence draft_id action result created_at",
    "adventure_checkpoints": "id adventure_id name revision character_json mode created_at memory_cursor",
    "adventure_memory_events": "adventure_id sequence turn origin change_json",
}
WORLD_COLUMNS = "id name description created_at updated_at"
ID_FIELDS = {
    "document_id": "documents", "chunk_id": "chunks", "entry_id": "lore_entries",
    "adventure_id": "adventures", "origin_checkpoint_id": "adventure_checkpoints",
}
INTEGER_FIELDS = {"byte_size", "text_length", "line_count", "chunk_count", "chunk_index",
                  "start_char", "end_char", "revision", "sequence", "turn", "memory_cursor"}


def _rows(conn, table, columns, world_id):
    select = ", ".join(f"t.{column}" for column in columns.split())
    joins = ""
    where = "t.world_id = ?"
    if table in {"documents", "lore_entries", "lore_entry_sources"}:
        where += " AND t.deleted_at IS NULL"
    if table in {"chunks", "lore_entry_sources"}:
        joins += " JOIN documents d ON d.id=t.document_id AND d.world_id=t.world_id"
        where += " AND d.deleted_at IS NULL"
    if table == "lore_entry_sources":
        joins += " JOIN lore_entries e ON e.id=t.entry_id AND e.world_id=t.world_id"
        where += " AND e.deleted_at IS NULL"
    if table in {"adventure_turns", "adventure_checkpoints", "adventure_memory_events"}:
        joins += " JOIN adventures a ON a.id=t.adventure_id"
        where = "a.world_id = ?"
    return (dict(row) for row in conn.execute(
        f"SELECT {select} FROM {table} t{joins} WHERE {where} ORDER BY t.rowid", (world_id,)
    ))


def _world_payload(conn, world):
    return {
        "format": SETTINGS_FORMAT, "version": VERSION,
        "world": {key: world[key] for key in WORLD_COLUMNS.split()},
        **{table: _rows(conn, table, columns, world["id"]) for table, columns in WORLD_TABLES.items()},
    }


def _digest(file):
    file.seek(0)
    sha = hashlib.sha256()
    for block in iter(lambda: file.read(65536), b""):
        sha.update(block)
    file.seek(0)
    return sha.hexdigest()


def _too_large():
    return ApiError(413, "WORLD_TRANSFER_LIMIT", "迁移文件的总大小或解压大小超过 128 MiB。")


def transfer_export(conn, world, part, scratch_dir):
    with ExitStack() as stack:
        settings = stack.enter_context(TemporaryFile(dir=scratch_dir))
        _write_json(settings, _world_payload(conn, world))
        size = settings.tell()
        if size > LIMIT:
            raise _too_large()
        archive = settings
        if part != "world":
            saves = stack.enter_context(TemporaryFile(dir=scratch_dir))
            _write_json(saves, {
                "format": SAVES_FORMAT, "version": SAVES_VERSION,
                "world_id": world["id"], "settings_sha256": _digest(settings),
                **{table: _rows(conn, table, columns, world["id"]) for table, columns in SAVE_TABLES.items()},
            })
            if size + saves.tell() > LIMIT:
                raise _too_large()
            archive = saves
            if part == "zip":
                archive = stack.enter_context(TemporaryFile(dir=scratch_dir))
                with ZipFile(archive, "w", compression=ZIP_DEFLATED) as zipped:
                    for name, file in ((WORLD_FILE, settings), (SAVE_FILE, saves)):
                        file.seek(0)
                        with zipped.open(name, "w") as member:
                            copyfileobj(file, member)
                if archive.tell() > LIMIT:
                    raise _too_large()

        archive.seek(0, 2)
        size = archive.tell()
        archive.seek(0)
        cleanup = stack.pop_all()

    suffix = {"world": "世界观.json", "saves": "存档.json", "zip": "世界迁移.zip"}[part]
    stem = "".join(c if c.isalnum() or c in " _-." else "_" for c in world["name"]).strip(" .")
    filename = quote(f"{stem or '墨境'}-{suffix}", safe="")
    return StreamingResponse(iter(lambda: archive.read(65536), b""),
                             media_type="application/zip" if part == "zip" else "application/json",
                             background=BackgroundTask(cleanup.close), headers={
        "Content-Disposition": f"attachment; filename=\"mojing-transfer.{suffix.split('.')[-1]}\"; filename*=UTF-8''{filename}",
        "Content-Length": str(size), "Cache-Control": "no-store",
    })


def _invalid():
    return ApiError(422, "WORLD_TRANSFER_INVALID", "迁移文件损坏或格式不受支持，请选择墨境导出的世界观、存档或 ZIP。")


def _read_part(file, parts, remaining):
    raw = file.read(remaining + 1)
    if len(raw) > remaining:
        raise _too_large()
    payload = json.loads(raw.decode("utf-8"), parse_constant=lambda _: None)
    if not isinstance(payload, dict):
        raise _invalid()
    kind = payload.get("format")
    version = payload.get("version")
    versions = (VERSION, SAVES_VERSION) if kind == SAVES_FORMAT else (VERSION,)
    if (kind not in (SETTINGS_FORMAT, SAVES_FORMAT) or kind in parts
            or type(version) is not int or version not in versions):
        raise _invalid()
    parts[kind] = (payload, hashlib.sha256(raw).hexdigest())
    return len(raw)


def _read_files(files):
    parts = {}
    remaining = LIMIT
    for file in files:
        file.seek(0)
        if file.read(4) == b"PK\x03\x04":
            file.seek(0)
            if len(files) != 1:
                raise _invalid()
            with ZipFile(file) as zipped:
                entries = zipped.infolist()
                if len(entries) != 2 or {entry.filename for entry in entries} != {WORLD_FILE, SAVE_FILE}:
                    raise _invalid()
                if sum(entry.file_size for entry in entries) > LIMIT:
                    raise _too_large()
                for entry in entries:
                    if entry.flag_bits & 1:
                        raise _invalid()
                    with zipped.open(entry) as member:
                        remaining -= _read_part(member, parts, remaining)
        else:
            file.seek(0)
            remaining -= _read_part(file, parts, remaining)
    return parts


def _validated(row, table, columns):
    if not isinstance(row, dict):
        raise _invalid()
    values = {key: row[key] for key in columns.split()}
    for key, value in values.items():
        if value is None and key in {"character_json", "origin_checkpoint_id"}:
            continue
        if key in INTEGER_FIELDS:
            if type(value) is not int or not 0 <= value <= 2**53 - 1:
                raise _invalid()
        elif key == "created_at" and table in SAVE_TABLES:
            if type(value) not in (float, int) or not math.isfinite(value):
                raise _invalid()
        elif not isinstance(value, str):
            raise _invalid()
    return values


def _records(payload, tables):
    records = {}
    for table, columns in tables.items():
        if not isinstance(payload[table], list):
            raise _invalid()
        records[table] = [_validated(row, table, columns) for row in payload[table]]
    return records


def _insert(conn, table, row):
    conn.execute(
        f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
        tuple(row.values()),
    )


def _copy_settings(conn, settings, world_id, records, ids):
    world = _validated(settings["world"], "worlds", WORLD_COLUMNS)
    if not 1 <= len(world["name"].strip()) <= 40:
        raise _invalid()
    _insert(conn, "worlds", {**world, "id": world_id})
    texts = {row["id"]: row["text"].encode("utf-16-le") for row in records["documents"]}
    chunks = {row["id"]: row for row in records["chunks"]}
    for table, rows in records.items():
        for original in rows:
            row = _remap(original, table, ids)
            if table == "documents" and not isinstance(json.loads(row["warnings"] or "[]"), list):
                raise _invalid()
            if table == "chunks":
                headings = json.loads(row["heading_path"])
                if not isinstance(headings, list) or not all(isinstance(heading, str) for heading in headings):
                    raise _invalid()
                start, end = row["start_char"] * 2, row["end_char"] * 2
                text = texts[original["document_id"]]
                if not 0 <= start < end <= len(text):
                    raise _invalid()
                row["text"] = text[start:end].decode("utf-16-le")
            if table == "lore_entry_sources" and chunks[original["chunk_id"]]["document_id"] != original["document_id"]:
                raise _invalid()
            _insert(conn, table, {**row, "world_id": world_id})


def _remap(row, table, ids):
    return {
        key: ids[table][value] if key == "id"
        else ids[ID_FIELDS[key]][value] if key in ID_FIELDS and value is not None
        else value
        for key, value in row.items()
    }


def _remap_result(result, ids):
    # A reference to a source since deleted remains unavailable in the copy.
    def reference(kind, value):
        if not isinstance(value, str):
            raise _invalid()
        return ids[kind].setdefault(value, str(uuid4()))

    def source_id(value):
        kind, _, original = value.partition(":")
        table = {"entry": "lore_entries", "chunk": "chunks"}[kind]
        return f"{kind}:{reference(table, original)}"

    result = json.loads(result)
    if (not isinstance(result["narrative"], str) or not isinstance(result["sources"], list)
            or not isinstance(result["references"], list)):
        raise _invalid()
    suggestions = result.get("suggestions", [])
    if not isinstance(suggestions, list) or not all(isinstance(item, str) for item in suggestions):
        raise _invalid()
    for key in ("state_before", "state_after", "projected_state"):
        if result.get(key) is not None:
            character(result[key])
    for source in result["sources"]:
        source["source_id"] = source_id(source["source_id"])
        for field in ("entry_id", "document_id", "chunk_id"):
            if source.get(field) is not None:
                source[field] = reference(ID_FIELDS[field], source[field])
    result["references"] = [source_id(value) for value in result["references"]]
    return json.dumps(result, ensure_ascii=False, allow_nan=False)


def _copy_saves(conn, world_id, records, ids):
    runs = {run["id"]: run for run in records["adventures"]}
    histories = {run_id: [] for run_id in runs}
    for row in records["adventure_turns"]:
        histories[row["adventure_id"]].append(row["sequence"])
    for run_id, run in runs.items():
        sequence = sorted(histories[run_id])
        if len(sequence) != run["revision"] or any(n != i for i, n in enumerate(sequence, 1)):
            raise _invalid()
    memory = {run_id: [] for run_id in runs}
    for event in records["adventure_memory_events"]:
        memory[event["adventure_id"]].append(event)
    for run_id, events in memory.items():
        last_turn = 0
        for sequence, event in enumerate(sorted(events, key=lambda event: event["sequence"]), 1):
            if (event["sequence"] != sequence or not last_turn <= event["turn"] <= runs[run_id]["revision"]
                    or event["origin"] not in ("turn", "correction") or (event["origin"] == "turn" and event["turn"] == 0)):
                raise _invalid()
            validate_event(json.loads(event["change_json"]))
            last_turn = event["turn"]
        events.sort(key=lambda event: event["sequence"])
    for table, rows in records.items():
        for original in rows:
            row = _remap(original, table, ids)
            if table == "adventures":
                row["world_id"] = world_id
            if table in ("adventures", "adventure_checkpoints"):
                state = json.loads(row["character_json"] or "null")
                if state is not None:
                    character(state)
                if row["mode"] not in {"fake", "live"}:
                    raise _invalid()
            if table == "adventure_turns":
                row["result"] = _remap_result(row["result"], ids)
            if table == "adventure_checkpoints":
                run_id = original["adventure_id"]
                cursor = row["memory_cursor"]
                if (row["revision"] > runs[run_id]["revision"] or cursor > len(memory[run_id])
                        or (cursor and memory[run_id][cursor - 1]["turn"] > row["revision"])):
                    raise _invalid()
                row["request_id"] = row["id"]
            _insert(conn, table, row)


def _local_digest(conn, world):
    with TemporaryFile() as file:
        _write_json(file, _world_payload(conn, world))
        return _digest(file)


def _existing_settings(conn, source_id, digest):
    candidates = conn.execute("""
        SELECT w.*, NULL AS local_digest, NULL AS id_map FROM worlds w
        WHERE w.id=? AND w.deleted_at IS NULL
        UNION ALL
        SELECT w.*, p.local_digest, p.id_map FROM worlds w
        JOIN world_transfer_sources p ON p.world_id=w.id
        WHERE p.source_id=? AND p.source_digest=? AND w.deleted_at IS NULL
    """, (source_id, source_id, digest))
    for world in candidates:
        if _local_digest(conn, world) == (world["local_digest"] or digest):
            settings = _world_payload(conn, world)
            for table in WORLD_TABLES:
                settings[table] = list(settings[table])
            return settings, json.loads(world["id_map"]) if world["id_map"] else None
    raise ApiError(422, "WORLD_TRANSFER_SETTINGS_REQUIRED", "找不到匹配版本，请选择这份存档对应的世界观文件一起导入。")


def import_transfer(conn, uploads, max_documents):
    if not 1 <= len(uploads) <= 2:
        raise _invalid()
    if sum(file.size or 0 for file in uploads) > LIMIT:
        raise _too_large()
    try:
        parts = _read_files([file.file for file in uploads])
        settings_part = parts.get(SETTINGS_FORMAT)
        save_part = parts.get(SAVES_FORMAT)
        saves = save_part[0] if save_part else None
        previous_ids = None
        conn.execute("BEGIN IMMEDIATE")
        if settings_part:
            settings, digest = settings_part
            source_id = settings["world"]["id"]
            if saves is not None and (saves["settings_sha256"] != digest or saves["world_id"] != source_id):
                raise ApiError(422, "WORLD_TRANSFER_MISMATCH", "世界观和存档版本不同，请选择同一次导出的文件。")
        else:
            source_id, digest = saves["world_id"], saves["settings_sha256"]
            settings, previous_ids = _existing_settings(conn, source_id, digest)

        world_records = _records(settings, WORLD_TABLES)
        if len(world_records["documents"]) > max_documents:
            raise ApiError(422, "WORLD_DOCUMENT_LIMIT", "这份世界观的文档数超出当前部署上限。", {"limit": max_documents})
        if saves is not None and saves["version"] == VERSION:
            saves["adventure_memory_events"] = []
            for checkpoint in saves["adventure_checkpoints"]:
                checkpoint["memory_cursor"] = 0
        save_records = _records(saves, SAVE_TABLES) if saves is not None else {}
        ids = {
            table: {row["id"]: str(uuid4()) for row in rows if "id" in row}
            for table, rows in {**world_records, **save_records}.items()
        }
        world_id = str(uuid4())
        _copy_settings(conn, settings, world_id, world_records, ids)
        source_ids = {table: ids[table].copy() for table in ("documents", "chunks", "lore_entries")}
        if previous_ids is not None:
            source_ids = {
                table: {original: ids[table][local] for original, local in mapping.items()}
                for table, mapping in previous_ids.items()
            }
        world = conn.execute("SELECT * FROM worlds WHERE id=?", (world_id,)).fetchone()
        _insert(conn, "world_transfer_sources", {
            "world_id": world_id, "source_id": source_id, "source_digest": digest,
            "local_digest": _local_digest(conn, world), "id_map": json.dumps(source_ids),
        })
        if saves is not None:
            _copy_saves(conn, world_id, save_records, {**ids, **source_ids})
        return world_id
    except (BadZipFile, zlib.error, ValueError, sqlite3.IntegrityError,
            RuntimeError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise _invalid() from exc
