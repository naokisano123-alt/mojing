"""Versioned, portable world export. Access only the selected world's data."""
from collections.abc import Iterator
from datetime import datetime, timezone
import json
from tempfile import TemporaryFile
from urllib.parse import quote

from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from .adventures.checkpoints import checkpoint_dict
from .adventures.service import run_dict


def _rows(conn, sql, parameter, json_columns=()):
    for row in conn.execute(sql, (parameter,)):
        value = dict(row)
        for name in json_columns:
            value[name] = json.loads(value[name])
        yield value


def _write_json(file, value):
    """Write arrays one database row at a time, keeping large exports off the heap."""
    def encode(part):
        return json.dumps(part, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    if isinstance(value, dict):
        file.write(b"{")
        for index, (key, part) in enumerate(value.items()):
            if index:
                file.write(b",")
            file.write(encode(key) + b":")
            _write_json(file, part)
        file.write(b"}")
    elif isinstance(value, Iterator):
        file.write(b"[")
        for index, part in enumerate(value):
            if index:
                file.write(b",")
            file.write(encode(part))
        file.write(b"]")
    else:
        file.write(encode(value))


def export_world(conn, world, include_saves, scratch_dir):
    world_id = world["id"]
    documents = _rows(conn, """
        SELECT id, filename, media_type, encoding, encoding_semantics, text,
               COALESCE(NULLIF(warnings, ''), '[]') AS warnings
        FROM documents WHERE world_id=? AND deleted_at IS NULL ORDER BY created_at, id
    """, world_id, ("warnings",))
    chunks = _rows(conn, """
        SELECT c.id, c.document_id, c.chunk_index, c.heading_path, c.start_char, c.end_char
        FROM chunks c JOIN documents d ON d.id=c.document_id AND d.world_id=c.world_id
        WHERE c.world_id=? AND d.deleted_at IS NULL ORDER BY c.document_id, c.chunk_index
    """, world_id, ("heading_path",))
    entries = _rows(conn, """
        SELECT id, type, title, summary, status, created_at, updated_at
        FROM lore_entries WHERE world_id=? AND deleted_at IS NULL ORDER BY created_at, id
    """, world_id)
    sources = _rows(conn, """
        SELECT s.entry_id, s.document_id, s.chunk_id, s.start_char, s.end_char, s.excerpt
        FROM lore_entry_sources s JOIN lore_entries e ON e.id=s.entry_id AND e.world_id=s.world_id
        JOIN documents d ON d.id=s.document_id AND d.world_id=s.world_id
        WHERE s.world_id=? AND s.deleted_at IS NULL AND e.deleted_at IS NULL AND d.deleted_at IS NULL
        ORDER BY s.entry_id, s.created_at, s.id
    """, world_id)
    notes = _rows(conn, """
        SELECT name, description FROM item_descriptions WHERE world_id=? ORDER BY name
    """, world_id)
    payload = {
        "format": "mojing-world",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "world": {key: world[key] for key in ("id", "name", "description", "created_at", "updated_at")},
        "documents": documents,
        "chunks": chunks,
        "lore_entries": entries,
        "lore_sources": sources,
        "item_descriptions": notes,
    }
    if include_saves:
        runs = (run_dict(row) for row in conn.execute(
            "SELECT * FROM adventures WHERE world_id=? ORDER BY created_at, id", (world_id,)
        ))
        turns = _rows(conn, """
            SELECT t.adventure_id, t.sequence, t.action, t.result, t.created_at
            FROM adventure_turns t JOIN adventures a ON a.id=t.adventure_id
            WHERE a.world_id=? ORDER BY t.adventure_id, t.sequence
        """, world_id, ("result",))
        checkpoints = (checkpoint_dict(row) for row in conn.execute("""
            SELECT c.* FROM adventure_checkpoints c JOIN adventures a ON a.id=c.adventure_id
            WHERE a.world_id=? ORDER BY c.adventure_id, c.created_at, c.id
        """, (world_id,)))
        payload["saves"] = {"adventures": runs, "turns": turns, "checkpoints": checkpoints}

    suffix = "含存档" if include_saves else "世界设定"
    name = "".join(c if c.isalnum() or c in " _-." else "_" for c in world["name"]).strip(" .")
    filename = quote(f"{name or '墨境'}-{suffix}.json", safe="")
    archive = TemporaryFile(dir=scratch_dir)
    try:
        _write_json(archive, payload)
        length = archive.tell()
        archive.seek(0)
    except BaseException:
        archive.close()
        raise
    return StreamingResponse(iter(lambda: archive.read(65536), b""), media_type="application/json",
                             background=BackgroundTask(archive.close), headers={
        "Content-Disposition": f"attachment; filename=\"mojing-world.json\"; filename*=UTF-8''{filename}",
        "Content-Length": str(length),
        "Cache-Control": "no-store",
    })
