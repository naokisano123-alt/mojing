"""M3 Wave 3 可续玩迁移、已删除来源墓碑与快照性能测试用合成 Fixtures。"""

from __future__ import annotations

from io import BytesIO
import json
import sqlite3
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from app.adventures import checkpoints as checkpoint_service, service as adventures_service
from app.m3 import events as world_events, knowledge as world_knowledge
from app.m3.core import adapters as core_adapters, service as instance_service

from .service import build_export_payloads, install_portability_adapters


class SyntheticUploadFile:
    """合成上传文件对象（兼容 FastAPI `UploadFile` 的 `.filename`, `.file`, `.size` 接口）。"""

    def __init__(self, filename: str, content: bytes) -> None:
        self.filename = filename
        self.file = BytesIO(content)
        self.size = len(content)


def build_zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = BytesIO()
    with ZipFile(buf, "w", compression=ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def seed_world_with_deleted_sources_and_v4_saves(
    conn: sqlite3.Connection,
    *,
    world_id: str = "world_deleted_src_001",
    delete_doc_after_event: bool = True,
    delete_entry_after_event: bool = True,
) -> dict[str, Any]:
    """在 SQLite 连接中创建一个包含文档分块、设定条目、正式旅程、M3 事件、玩家知识与检查点的合成世界，
    并在事件与检查点引用这些来源后，将其中的 `doc_del_01`（含 `chunk:chunk_del_01`）与 `entry:entry_del_01` 软删除。
    """
    install_portability_adapters()
    now_iso = "2026-11-10T00:00:00Z"

    conn.execute(
        "INSERT INTO worlds(id, name, description, created_at, updated_at, deleted_at) VALUES (?, ?, ?, ?, ?, NULL)",
        (world_id, "霜木镇边贸古卷", "含活跃资料与后续删除资料的测试世界。", now_iso, now_iso),
    )

    # 1. 创建保留的活跃文档 doc_keep_01 与将被软删除的文档 doc_del_01
    for doc_id, chunk_id, fname, text in (
        ("doc_keep_01", "chunk_keep_01", "北境常备地理志.txt", "霜木镇位于北境山麓要冲，与寒铁城相通。"),
        ("doc_del_01", "chunk_del_01", "旧版灰岩边关密档.txt", "灰岩边关旧税则规定生铁过境暂免附加税。"),
    ):
        u16_len = len(text.encode("utf-16-le")) // 2
        conn.execute(
            """
            INSERT INTO documents (
                id, world_id, filename, media_type, byte_size, encoding, content_sha256,
                text, text_length, line_count, chunk_count, source_kind, encoding_semantics,
                warnings, status, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, 'text/plain', ?, 'utf-8', ?, ?, ?, 1, 1, 'user_import', 'source_bytes', '[]', 'ready', ?, ?, NULL)
            """,
            (doc_id, world_id, fname, len(text.encode("utf-8")), f"sha_{doc_id}", text, u16_len, now_iso, now_iso),
        )
        conn.execute(
            """
            INSERT INTO chunks (id, document_id, world_id, chunk_index, heading_path, start_char, end_char, text)
            VALUES (?, ?, ?, 0, '["总纲"]', 0, ?, ?)
            """,
            (chunk_id, doc_id, world_id, u16_len, text),
        )

    # 2. 创建保留的设定条目 entry_keep_01 与将被软删除的设定条目 entry_del_01
    for eid, title, summary in (
        ("entry_keep_01", "星辉大商会", "常驻寒铁城与霜木镇的跨国商会。"),
        ("entry_del_01", "旧边贸免税令", "五年前签订的临时免税附录，现已废止并从设定库移除。"),
    ):
        conn.execute(
            """
            INSERT INTO lore_entries (
                id, world_id, type, title, summary, status, revision, origin, created_at, updated_at, deleted_at
            ) VALUES (?, ?, 'politics', ?, ?, 'confirmed', 1, 'user', ?, ?, NULL)
            """,
            (eid, world_id, title, summary, now_iso, now_iso),
        )

    # 3. 创建旅程与世界实例，写入引用上述 4 个来源的事件与知识记录
    run = adventures_service.create_adventure(conn, world_id, "霜木镇铁料之旅", "fake")
    adv_id = run["id"]
    inst = instance_service.ensure_for_adventure(conn, adv_id)
    inst_id = inst["id"]

    allowed_sids = {
        "chunk:chunk_keep_01",
        "chunk:chunk_del_01",
        "entry:entry_keep_01",
        "entry:entry_del_01",
    }

    seed_proposal = {
        "instance_id": inst_id,
        "mode": "seed",
        "operations": [
            {
                "op": "create",
                "kind": "thread",
                "title": "灰岩边关旧税则废止风波",
                "description": "边关税务官宣布旧版灰岩边关密档与旧边贸免税令失效，改征三成重税。",
                "location": "灰岩边关",
                "scope": "international",
                "participants": ["克劳德专员", "韩掌柜"],
                "stage": "tariff_shift",
                "status": "active",
                "next_update_minute": 120,
                "cause_event_ids": [],
                "source_ids": ["chunk:chunk_del_01", "entry:entry_del_01"],
                "reason": "开局事件引用了后续会被用户删除的旧密档与旧条目。",
            },
            {
                "op": "create",
                "kind": "thread",
                "title": "霜木镇工坊联合稳价",
                "description": "韩掌柜与霍克铁匠在霜木镇商议应对星辉大商会压价。",
                "location": "霜木镇",
                "scope": "local",
                "participants": ["韩掌柜", "霍克铁匠"],
                "stage": "local_alliance",
                "status": "active",
                "next_update_minute": 180,
                "cause_event_ids": [],
                "source_ids": ["chunk:chunk_keep_01", "entry:entry_keep_01"],
                "reason": "引用活跃保留资料的主线。",
            },
            {
                "op": "create",
                "kind": "thread",
                "title": "寒铁城矿石外运配额收紧",
                "description": "寒铁城矿务司核查出城生铁批文。",
                "location": "寒铁城",
                "scope": "regional",
                "participants": ["维克多矿监"],
                "stage": "quota_check",
                "status": "active",
                "next_update_minute": 240,
                "cause_event_ids": [],
                "source_ids": ["chunk:chunk_keep_01"],
                "reason": "区域主线。",
            },
        ],
    }
    id_seq = iter(["evt_del_ref_01", "evt_keep_ref_02", "evt_keep_ref_03"])
    world_events.apply_proposal(
        conn,
        inst_id,
        seed_proposal,
        world_minute=0,
        allowed_source_ids=allowed_sids,
        id_factory=lambda: next(id_seq),
    )

    world_knowledge.record_transmission(
        conn,
        inst_id,
        {
            "fact_id": "fact_tariff_news_01",
            "holder_id": "player",
            "knowledge_type": "confirmed",
            "title": "灰岩边关废除旧免税令",
            "content": "告示栏贴出通知：旧边贸免税令废止，生铁进口加征三成关税。",
            "source_event_id": "evt_del_ref_01",
            "channel": "notice",
            "learned_minute": 0,
            "confidence": 1.0,
        },
    )

    # 推进 1 回合（30 分钟），并在删除资料前创建检查点 cp_before_delete
    char_after = dict(run["character"])
    char_after["elapsed_minutes"] = 30
    turn_res = {
        "narrative": "你在霜木镇查看了旧免税令废止的告示。",
        "sources": [
            {
                "source_id": "chunk:chunk_del_01",
                "document_id": "doc_del_01",
                "chunk_id": "chunk_del_01",
            },
            {
                "source_id": "entry:entry_keep_01",
                "entry_id": "entry_keep_01",
            },
        ],
        "references": ["chunk:chunk_del_01", "entry:entry_del_01", "entry:entry_keep_01"],
        "suggestions": ["找韩掌柜商议"],
        "state_before": run["character"],
        "state_after": char_after,
    }
    conn.execute(
        """
        INSERT INTO adventure_turns (adventure_id, sequence, draft_id, action, result, created_at)
        VALUES (?, 1, 'd_1', '查看告示栏', ?, 10.0)
        """,
        (adv_id, json.dumps(turn_res, ensure_ascii=False)),
    )
    conn.execute(
        "UPDATE adventures SET revision = 1, character_json = ? WHERE id = ?",
        (json.dumps(char_after, ensure_ascii=False), adv_id),
    )
    conn.execute(
        "UPDATE world_instances SET world_minute = 30, revision = 1 WHERE id = ?",
        (inst_id,),
    )
    conn.execute("UPDATE world_events SET updated_minute = 30 WHERE instance_id = ?", (inst_id,))

    if conn.in_transaction:
        conn.commit()
    cp1 = checkpoint_service.create_checkpoint(conn, adv_id, "删资料前存档点", "req-cp-1", 1)
    if conn.in_transaction:
        conn.commit()
    branch_run = checkpoint_service.restore(conn, cp1["id"], "删资料前派生的旧分支", "req-fork-1")
    if conn.in_transaction:
        conn.commit()

    # 4. 用户在世界资料库中删除了 doc_del_01 与 entry_del_01
    if delete_doc_after_event:
        conn.execute(
            "UPDATE documents SET deleted_at = '2026-11-10T02:00:00Z' WHERE id = 'doc_del_01'"
        )
    if delete_entry_after_event:
        conn.execute(
            "UPDATE lore_entries SET deleted_at = '2026-11-10T02:05:00Z' WHERE id = 'entry_del_01'"
        )

    # 删资料后再创建一个同分钟（30 分钟）但新实例版本（revision=2）的检查点 cp2
    conn.execute("UPDATE world_instances SET revision = 2 WHERE id = ?", (inst_id,))
    if conn.in_transaction:
        conn.commit()
    cp2 = checkpoint_service.create_checkpoint(conn, adv_id, "删资料后同分钟新版本存档点", "req-cp-2", 1)
    if conn.in_transaction:
        conn.commit()

    return {
        "world_id": world_id,
        "adventure_id": adv_id,
        "branch_adventure_id": branch_run["id"],
        "instance_id": inst_id,
        "checkpoint_before_delete": cp1,
        "checkpoint_after_delete": cp2,
        "deleted_source_ids": ["chunk:chunk_del_01", "entry:entry_del_01"],
        "active_source_ids": ["chunk:chunk_keep_01", "entry:entry_keep_01"],
    }
