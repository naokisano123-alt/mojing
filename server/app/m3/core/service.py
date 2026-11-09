"""世界实例根服务：创建、读取、原子推进、快照与恢复。

所有函数接收调用方已打开的 sqlite3.Connection，**不 commit**；
事务边界与网络/模型调用由调用方控制（契约 §公共身份和时间）。
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any
from uuid import uuid4

from ...errors import ApiError

SNAPSHOT_VERSION = 1


def _value(row: Any, key: str, index: int):
    if row is None:
        return None
    if hasattr(row, "keys"):
        return row[key]
    return row[index]


def _dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        data = {key: row[key] for key in row.keys()}
        # 数据库列名按契约是 revision，对外统一暴露 world_revision
        data["world_revision"] = data.pop("revision", 0)
        return data
    return {
        "id": row[0],
        "world_id": row[1],
        "adventure_id": row[2],
        "world_minute": row[3],
        "world_revision": row[4],
        "canon_version": row[5],
        "created_at": row[6],
        "updated_at": row[7],
    }


def create_instance(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    adventure_id: str | None = None,
    world_minute: int = 0,
    world_revision: int = 0,
    canon_version: str | None = None,
) -> dict[str, Any]:
    if not isinstance(world_id, str) or not world_id:
        raise ApiError(422, "VALIDATION_ERROR", "world_id 无效。")
    if not isinstance(world_minute, int) or isinstance(world_minute, bool) or world_minute < 0:
        raise ApiError(422, "VALIDATION_ERROR", "world_minute 必须是非负整数。")
    existing = None
    if adventure_id is not None:
        existing = conn.execute(
            "SELECT * FROM world_instances WHERE adventure_id = ?", (adventure_id,)
        ).fetchone()
    if existing is not None:
        return _dict(existing)
    now = time.time()
    instance_id = str(uuid4())
    conn.execute(
        """
        INSERT INTO world_instances
            (id, world_id, adventure_id, world_minute, revision, canon_version, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (instance_id, world_id, adventure_id, world_minute, int(world_revision), canon_version, now, now),
    )
    return _dict(conn.execute("SELECT * FROM world_instances WHERE id = ?", (instance_id,)).fetchone())


def get(conn: sqlite3.Connection, instance_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM world_instances WHERE id = ?", (instance_id,)).fetchone()
    if row is None:
        raise ApiError(404, "INSTANCE_NOT_FOUND", "世界实例不存在。")
    return _dict(row)


def for_adventure(conn: sqlite3.Connection, adventure_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM world_instances WHERE adventure_id = ?", (adventure_id,)
    ).fetchone()
    if row is None:
        raise ApiError(404, "INSTANCE_NOT_FOUND", "该旅程尚未绑定世界实例。")
    return _dict(row)


def ensure_for_adventure(conn: sqlite3.Connection, adventure_id: str) -> dict[str, Any]:
    """旧旅程兼容接入：缺失时按当前冒险 revision 补一个实例（分钟从 0 起）。"""
    row = conn.execute(
        "SELECT * FROM world_instances WHERE adventure_id = ?", (adventure_id,)
    ).fetchone()
    if row is not None:
        return _dict(row)
    adventure = conn.execute(
        "SELECT id, world_id, revision FROM adventures WHERE id = ?", (adventure_id,)
    ).fetchone()
    if adventure is None:
        raise ApiError(404, "ADVENTURE_NOT_FOUND", "冒险记录不存在。")
    return create_instance(
        conn,
        _value(adventure, "world_id", 1),
        adventure_id=adventure_id,
        revision=int(_value(adventure, "revision", 2) or 0),
    )


def assert_world(conn: sqlite3.Connection, instance_id: str, world_id: str) -> dict[str, Any]:
    instance = get(conn, instance_id)
    if instance["world_id"] != world_id:
        raise ApiError(409, "INSTANCE_WORLD_MISMATCH", "实例属于另一个世界，拒绝跨世界写入。")
    return instance


def advance(
    conn: sqlite3.Connection,
    instance_id: str,
    *,
    expected_revision: int | None = None,
    minutes: int = 0,
) -> dict[str, Any]:
    """原子推进实例版本（可选增加游戏分钟）。版本不符返回 409。"""
    if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes < 0:
        raise ApiError(422, "VALIDATION_ERROR", "minutes 必须是非负整数。")
    now = time.time()
    if expected_revision is None:
        cur = conn.execute(
            "UPDATE world_instances SET revision = revision + 1, "
            "world_minute = world_minute + ?, updated_at = ? WHERE id = ?",
            (minutes, now, instance_id),
        )
    else:
        cur = conn.execute(
            "UPDATE world_instances SET revision = revision + 1, "
            "world_minute = world_minute + ?, updated_at = ? "
            "WHERE id = ? AND revision = ?",
            (minutes, now, instance_id, int(expected_revision)),
        )
    if cur.rowcount != 1:
        if conn.execute("SELECT 1 FROM world_instances WHERE id = ?", (instance_id,)).fetchone():
            raise ApiError(409, "INSTANCE_CONFLICT", "世界实例版本已变化，请重新读取。")
        raise ApiError(404, "INSTANCE_NOT_FOUND", "世界实例不存在。")
    return get(conn, instance_id)


def snapshot(conn: sqlite3.Connection, instance_id: str) -> dict[str, Any]:
    instance = get(conn, instance_id)
    return {
        "version": SNAPSHOT_VERSION,
        "instance": {
            "world_id": instance["world_id"],
            "world_minute": instance["world_minute"],
            "world_revision": instance["world_revision"],
            "canon_version": instance["canon_version"],
        },
    }


def restore(
    conn: sqlite3.Connection,
    snapshot_data: dict[str, Any],
    *,
    adventure_id: str,
    world_id: str,
) -> dict[str, Any]:
    """从快照恢复出一个**新实例**；跨世界快照直接拒绝。"""
    if not isinstance(snapshot_data, dict) or snapshot_data.get("version") != SNAPSHOT_VERSION:
        raise ApiError(422, "SNAPSHOT_INVALID", "快照格式不支持。")
    data = snapshot_data.get("instance")
    if not isinstance(data, dict):
        raise ApiError(422, "SNAPSHOT_INVALID", "快照缺少实例数据。")
    if data.get("world_id") != world_id:
        raise ApiError(409, "INSTANCE_WORLD_MISMATCH", "快照属于另一个世界，不能恢复。")
    minute = data.get("world_minute", 0)
    revision = data.get("world_revision", 0)
    minutes = int(minute) if isinstance(minute, int) and minute >= 0 else 0
    revision_value = int(revision) if isinstance(revision, int) and revision >= 0 else 0
    # 分支旅程通常已有空实例：就地覆盖为快照值，避免 UNIQUE(adventure_id) 冲突
    existing = conn.execute(
        "SELECT id FROM world_instances WHERE adventure_id = ?", (adventure_id,)
    ).fetchone()
    if existing is not None:
        instance_id = existing["id"] if hasattr(existing, "keys") else existing[0]
        conn.execute(
            "UPDATE world_instances SET world_minute = ?, revision = ?, canon_version = ?, "
            "world_id = ?, updated_at = ? WHERE id = ?",
            (minutes, revision_value, data.get("canon_version"), world_id, time.time(), instance_id),
        )
        return get(conn, instance_id)
    return create_instance(
        conn,
        world_id,
        adventure_id=adventure_id,
        world_minute=minutes,
        world_revision=revision_value,
        canon_version=data.get("canon_version"),
    )