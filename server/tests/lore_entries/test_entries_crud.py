"""人工设定条目 CRUD、并发与生命周期单元测试。

依据：
- docs/collaboration/ROUND3.md、docs/collaboration/R3_GEMINI_MA.md
- docs/collaboration/ROUND5.md、docs/collaboration/R5_GEMINI_MA.md
- docs/contracts/M1_R3_API.md
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone

try:
    from app.db import SCHEMA
    from app.lore_entries import (
        LoreEntryConflictError,
        LoreEntryNotFoundError,
        LoreEntryValidationError,
        LoreEntryWorldNotFoundError,
        create_entry,
        delete_entry,
        ensure_lore_entries_schema,
        get_entry,
        list_entries,
        update_entry,
    )
except ImportError:
    from server.app.db import SCHEMA
    from server.app.lore_entries import (
        LoreEntryConflictError,
        LoreEntryNotFoundError,
        LoreEntryValidationError,
        LoreEntryWorldNotFoundError,
        create_entry,
        delete_entry,
        ensure_lore_entries_schema,
        get_entry,
        list_entries,
        update_entry,
    )


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TestLoreEntriesCRUD(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        ensure_lore_entries_schema(self.conn)

        self.world_id = "world_test_01"
        now = _iso_now()
        self.conn.execute(
            "INSERT INTO worlds (id, name, description, created_at, updated_at, deleted_at) VALUES (?, ?, ?, ?, ?, ?)",
            (self.world_id, "主测试世界", "世界设定", now, now, None),
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def test_create_and_get_entry_all_types(self) -> None:
        """测试五类设定类型的创建与字段校验。"""
        types = ["character", "location", "faction", "rule", "other"]
        for t in types:
            entry = create_entry(
                self.conn,
                self.world_id,
                {
                    "type": t,
                    "title": f"测试条目_{t}",
                    "summary": f"关于{t}的描述",
                    "status": "draft",
                },
            )
            self.assertEqual(entry["world_id"], self.world_id)
            self.assertEqual(entry["type"], t)
            self.assertEqual(entry["title"], f"测试条目_{t}")
            self.assertEqual(entry["summary"], f"关于{t}的描述")
            self.assertEqual(entry["status"], "draft")
            self.assertEqual(entry["revision"], 1)
            self.assertEqual(entry["origin"], "manual")
            self.assertEqual(entry["source_links"], [])
            self.assertFalse(entry["needs_source_review"])
            self.assertIsNone(entry["deleted_at"])

            # 验证 get_entry 读取一致
            fetched = get_entry(self.conn, entry["id"])
            self.assertEqual(fetched["id"], entry["id"])
            self.assertEqual(fetched["title"], entry["title"])

    def test_validation_errors(self) -> None:
        """测试非法标题、超长摘要、未知类型的错误拦截。"""
        # 1. 标题为空
        with self.assertRaises(LoreEntryValidationError) as ctx:
            create_entry(self.conn, self.world_id, {"type": "character", "title": "   "})
        self.assertEqual(ctx.exception.details["field"], "title")

        # 2. 标题超长 (>80)
        with self.assertRaises(LoreEntryValidationError) as ctx:
            create_entry(self.conn, self.world_id, {"type": "character", "title": "A" * 81})
        self.assertEqual(ctx.exception.details["field"], "title")

        # 3. 摘要超长 (>500)
        with self.assertRaises(LoreEntryValidationError) as ctx:
            create_entry(
                self.conn,
                self.world_id,
                {"type": "character", "title": "有效标题", "summary": "B" * 501},
            )
        self.assertEqual(ctx.exception.details["field"], "summary")

        # 4. 非法类型
        with self.assertRaises(LoreEntryValidationError) as ctx:
            create_entry(
                self.conn,
                self.world_id,
                {"type": "invalid_type", "title": "有效标题"},
            )
        self.assertEqual(ctx.exception.details["field"], "type")

    def test_homonymous_entries_coexist_not_merged(self) -> None:
        """测试同名不同条目合法共存，绝不被服务端悄悄合并。"""
        e1 = create_entry(
            self.conn,
            self.world_id,
            {"type": "character", "title": "陆平", "summary": "青年时期的剑客"},
        )
        e2 = create_entry(
            self.conn,
            self.world_id,
            {"type": "character", "title": "陆平", "summary": "同名同姓的另一位掌柜"},
        )
        self.assertNotEqual(e1["id"], e2["id"])
        self.assertEqual(e1["title"], e2["title"])

        res = list_entries(self.conn, self.world_id)
        self.assertEqual(res["total"], 2)
        titles = [item["title"] for item in res["items"]]
        self.assertEqual(titles, ["陆平", "陆平"])

    def test_update_entry_partial_fields_and_revision_increment(self) -> None:
        """测试 PATCH 部分字段更新：未传字段保持不变，revision 递增。"""
        created = create_entry(
            self.conn,
            self.world_id,
            {
                "type": "character",
                "title": "原始人物名",
                "summary": "原始背景",
                "status": "draft",
            },
        )
        self.assertEqual(created["revision"], 1)

        # 仅更新 title，summary/type/status 保持不变
        updated = update_entry(
            self.conn,
            created["id"],
            {"title": "更新后的人物名"},
            expected_revision=1,
        )
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["title"], "更新后的人物名")
        self.assertEqual(updated["summary"], "原始背景")
        self.assertEqual(updated["type"], "character")
        self.assertEqual(updated["status"], "draft")

        # 再仅更新 status 为 confirmed
        updated2 = update_entry(
            self.conn,
            created["id"],
            {"status": "confirmed"},
            expected_revision=2,
        )
        self.assertEqual(updated2["revision"], 3)
        self.assertEqual(updated2["status"], "confirmed")
        self.assertEqual(updated2["title"], "更新后的人物名")

    def test_optimistic_concurrency_conflict_409(self) -> None:
        """两人从同 revision 更新仅一人成功，后提交者收到 409 LORE_ENTRY_CONFLICT。"""
        created = create_entry(
            self.conn,
            self.world_id,
            {"type": "location", "title": "红枫酒馆", "summary": "旧街酒馆"},
        )
        self.assertEqual(created["revision"], 1)

        # 用户 A 基于 revision 1 更新成功
        update_entry(
            self.conn,
            created["id"],
            {"summary": "用户 A 的描述"},
            expected_revision=1,
        )

        # 用户 B 依然拿着旧 revision 1 尝试更新 -> 必报 409
        with self.assertRaises(LoreEntryConflictError) as ctx:
            update_entry(
                self.conn,
                created["id"],
                {"summary": "用户 B 的滞后描述"},
                expected_revision=1,
            )
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.code, "LORE_ENTRY_CONFLICT")
        self.assertEqual(ctx.exception.details["expected_revision"], 1)
        self.assertEqual(ctx.exception.details["current_revision"], 2)

    def test_delete_entry_revision_conflict_and_idempotency(self) -> None:
        """并发 DELETE 校验：revision 错误报 409 且不抹新版本；成功后重复删除保持幂等。"""
        created = create_entry(
            self.conn,
            self.world_id,
            {"type": "rule", "title": "禁言术", "summary": "三阶法术"},
        )
        # 更新到 revision 2
        update_entry(self.conn, created["id"], {"summary": "四阶法术"}, expected_revision=1)

        # 1. 尝试用旧版本 expected_revision=1 删除 -> 报 409
        with self.assertRaises(LoreEntryConflictError) as ctx:
            delete_entry(self.conn, created["id"], expected_revision=1)
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.details["current_revision"], 2)

        # 条目依然健在
        entry_check = get_entry(self.conn, created["id"])
        self.assertEqual(entry_check["revision"], 2)

        # 2. 正确版本 expected_revision=2 删除 -> 成功
        delete_entry(self.conn, created["id"], expected_revision=2)

        # 3. get_entry 报 404
        with self.assertRaises(LoreEntryNotFoundError) as ctx:
            get_entry(self.conn, created["id"])
        self.assertEqual(ctx.exception.status, 404)

        # 4. 重复以 revision=2 删除保持幂等（不报错，对应 HTTP 204）
        res = delete_entry(self.conn, created["id"], expected_revision=2)
        self.assertIsNone(res)

    def test_database_persistence_and_reopen(self) -> None:
        """测试真实文件数据库下创建/更新/软删在重启后的一致性。"""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            # 阶段 1：写入
            conn1 = sqlite3.connect(db_path)
            conn1.row_factory = sqlite3.Row
            conn1.execute("PRAGMA foreign_keys = ON")
            conn1.executescript(SCHEMA)
            ensure_lore_entries_schema(conn1)

            w_id = "world_disk_01"
            now = _iso_now()
            conn1.execute(
                "INSERT INTO worlds (id, name, description, created_at, updated_at, deleted_at) VALUES (?, ?, ?, ?, ?, ?)",
                (w_id, "持久化世界", "描述", now, now, None),
            )
            e = create_entry(
                conn1,
                w_id,
                {"type": "faction", "title": "苍星会", "summary": "隐秘结社"},
            )
            update_entry(conn1, e["id"], {"summary": "显赫的隐秘结社"}, expected_revision=1)
            conn1.commit()
            conn1.close()

            # 阶段 2：重新开启连接校验
            conn2 = sqlite3.connect(db_path)
            conn2.row_factory = sqlite3.Row
            conn2.execute("PRAGMA foreign_keys = ON")

            fetched = get_entry(conn2, e["id"])
            self.assertEqual(fetched["title"], "苍星会")
            self.assertEqual(fetched["summary"], "显赫的隐秘结社")
            self.assertEqual(fetched["revision"], 2)

            # 软删除并关闭
            delete_entry(conn2, e["id"], expected_revision=2)
            conn2.commit()
            conn2.close()

            # 阶段 3：第三次开启连接校验软删除状态
            conn3 = sqlite3.connect(db_path)
            conn3.row_factory = sqlite3.Row
            conn3.execute("PRAGMA foreign_keys = ON")
            with self.assertRaises(LoreEntryNotFoundError):
                get_entry(conn3, e["id"])
            conn3.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_transaction_rollback_atomicity(self) -> None:
        """测试调用方回滚时，条目与引用同时撤销，不留下半吊子脏数据。"""
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM lore_entries")
        self.assertEqual(cur.fetchone()[0], 0)

        # 开启显式事务并执行创建
        self.conn.execute("BEGIN TRANSACTION")
        create_entry(
            self.conn,
            self.world_id,
            {"type": "other", "title": "未提交的事物", "summary": "回滚测试"},
        )
        # 回滚事务
        self.conn.rollback()

        cur.execute("SELECT COUNT(*) FROM lore_entries")
        self.assertEqual(cur.fetchone()[0], 0)

    def test_savepoint_rollback_on_update_failure(self) -> None:
        """验证 SAVEPOINT 原子性：更新时若校验失败，revision 不得递增，且外层无需回滚即可继续。"""
        entry = create_entry(
            self.conn,
            self.world_id,
            {"type": "character", "title": "守望者", "summary": "初始版本"},
        )
        self.assertEqual(entry["revision"], 1)

        # 尝试更新传入非法的 title 导致校验失败
        with self.assertRaises(LoreEntryValidationError):
            update_entry(
                self.conn,
                entry["id"],
                {"title": "   "},
                expected_revision=1,
            )

        # 验证条目未受影响，版本号仍为 1
        fetched = get_entry(self.conn, entry["id"])
        self.assertEqual(fetched["title"], "守望者")
        self.assertEqual(fetched["revision"], 1)

    def test_dual_connection_concurrent_update_barrier(self) -> None:
        """R4-02 并发回归测试：两个独立真实连接与同步屏障（Barrier）。
        连接 A 与连接 B 均读取到 revision=1。
        并发提交更新，原子 UPDATE 保证恰好只有 1 个成功（进入 rev 2），
        另 1 个必须抛出 409 LoreEntryConflictError，绝不发生后写覆盖前写。
        """
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            init_conn = sqlite3.connect(db_path)
            init_conn.row_factory = sqlite3.Row
            init_conn.execute("PRAGMA journal_mode = WAL")
            init_conn.execute("PRAGMA foreign_keys = ON")
            init_conn.executescript(SCHEMA)
            ensure_lore_entries_schema(init_conn)

            w_id = "world_dual_01"
            now = _iso_now()
            init_conn.execute(
                "INSERT INTO worlds VALUES (?, ?, ?, ?, ?, ?)",
                (w_id, "并发世界", "描述", now, now, None),
            )
            entry = create_entry(
                init_conn,
                w_id,
                {"type": "character", "title": "初始并发条目", "summary": "描述"},
            )
            init_conn.commit()
            init_conn.close()

            entry_id = entry["id"]

            barrier = threading.Barrier(2)
            results: list[dict[str, str | None]] = []

            def worker(tag: str, title: str) -> None:
                conn = sqlite3.connect(db_path, timeout=10.0, isolation_level=None)
                conn.row_factory = sqlite3.Row
                try:
                    row = get_entry(conn, entry_id)
                    assert row["revision"] == 1
                    barrier.wait(timeout=5.0)

                    conn.execute("BEGIN IMMEDIATE")
                    upd = update_entry(
                        conn,
                        entry_id,
                        {"title": title},
                        expected_revision=1,
                    )
                    conn.execute("COMMIT")
                    results.append({"tag": tag, "status": "success", "title": upd["title"]})
                except LoreEntryConflictError as e:
                    try:
                        conn.execute("ROLLBACK")
                    except Exception:
                        pass
                    results.append({"tag": tag, "status": "conflict", "title": None})
                except Exception as e:
                    try:
                        conn.execute("ROLLBACK")
                    except Exception:
                        pass
                    results.append({"tag": tag, "status": "error", "title": str(e)})
                finally:
                    conn.close()

            t1 = threading.Thread(target=worker, args=("Writer_A", "A写入的标题"))
            t2 = threading.Thread(target=worker, args=("Writer_B", "B写入的标题"))

            t1.start()
            t2.start()
            t1.join(timeout=10.0)
            t2.join(timeout=10.0)

            statuses = [r["status"] for r in results]
            self.assertEqual(len(statuses), 2)
            self.assertEqual(statuses.count("success"), 1)
            self.assertEqual(statuses.count("conflict"), 1)

            # 最终检查
            chk_conn = sqlite3.connect(db_path)
            chk_conn.row_factory = sqlite3.Row
            final_entry = get_entry(chk_conn, entry_id)
            self.assertEqual(final_entry["revision"], 2)
            chk_conn.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)
            for ext in ["-wal", "-shm"]:
                if os.path.exists(db_path + ext):
                    os.remove(db_path + ext)

    def test_concurrent_delete_and_update_cannot_resurrect(self) -> None:
        """测试删除与更新竞争：删除成功后，同版本更新绝不复活条目，返回 404。"""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            init_conn = sqlite3.connect(db_path)
            init_conn.row_factory = sqlite3.Row
            init_conn.execute("PRAGMA journal_mode = WAL")
            init_conn.execute("PRAGMA foreign_keys = ON")
            init_conn.executescript(SCHEMA)
            ensure_lore_entries_schema(init_conn)

            w_id = "world_del_resurrect_01"
            now = _iso_now()
            init_conn.execute(
                "INSERT INTO worlds VALUES (?, ?, ?, ?, ?, ?)",
                (w_id, "世界", "描述", now, now, None),
            )
            entry = create_entry(
                init_conn,
                w_id,
                {"type": "faction", "title": "待灭帮派", "summary": "描述"},
            )
            init_conn.commit()
            init_conn.close()

            entry_id = entry["id"]

            conn_del = sqlite3.connect(db_path, timeout=10.0, isolation_level=None)
            conn_del.row_factory = sqlite3.Row
            conn_upd = sqlite3.connect(db_path, timeout=10.0, isolation_level=None)
            conn_upd.row_factory = sqlite3.Row

            # 删除成功
            conn_del.execute("BEGIN IMMEDIATE")
            delete_entry(conn_del, entry_id, expected_revision=1)
            conn_del.execute("COMMIT")

            # 尝试更新同版本已被删除的条目 -> 404 绝不复活
            with self.assertRaises(LoreEntryNotFoundError):
                update_entry(conn_upd, entry_id, {"title": "尝试复活"}, expected_revision=1)

            conn_del.close()
            conn_upd.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)
            for ext in ["-wal", "-shm"]:
                if os.path.exists(db_path + ext):
                    os.remove(db_path + ext)

    def test_concurrent_duplicate_delete_idempotent(self) -> None:
        """测试两个连接并发以同一版本软删除，均返回 204（None），保持幂等。"""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            init_conn = sqlite3.connect(db_path)
            init_conn.row_factory = sqlite3.Row
            init_conn.execute("PRAGMA journal_mode = WAL")
            init_conn.execute("PRAGMA foreign_keys = ON")
            init_conn.executescript(SCHEMA)
            ensure_lore_entries_schema(init_conn)

            w_id = "world_dup_del_01"
            now = _iso_now()
            init_conn.execute(
                "INSERT INTO worlds VALUES (?, ?, ?, ?, ?, ?)",
                (w_id, "世界", "描述", now, now, None),
            )
            entry = create_entry(
                init_conn,
                w_id,
                {"type": "rule", "title": "旧规则", "summary": "描述"},
            )
            init_conn.commit()
            init_conn.close()

            entry_id = entry["id"]

            barrier = threading.Barrier(2)
            results: list[str] = []

            def del_worker() -> None:
                conn = sqlite3.connect(db_path, timeout=10.0, isolation_level=None)
                conn.row_factory = sqlite3.Row
                try:
                    barrier.wait(timeout=5.0)
                    conn.execute("BEGIN IMMEDIATE")
                    delete_entry(conn, entry_id, expected_revision=1)
                    conn.execute("COMMIT")
                    results.append("success")
                except Exception as e:
                    try:
                        conn.execute("ROLLBACK")
                    except Exception:
                        pass
                    results.append(str(type(e).__name__))
                finally:
                    conn.close()

            t1 = threading.Thread(target=del_worker)
            t2 = threading.Thread(target=del_worker)

            t1.start()
            t2.start()
            t1.join(timeout=10.0)
            t2.join(timeout=10.0)

            self.assertEqual(results, ["success", "success"])
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)
            for ext in ["-wal", "-shm"]:
                if os.path.exists(db_path + ext):
                    os.remove(db_path + ext)

    def test_soft_deleted_world_entries_unreachable(self) -> None:
        """测试世界整体软删除后，该世界下所有条目均不可读写（404 WORLD_NOT_FOUND）。"""
        e = create_entry(
            self.conn,
            self.world_id,
            {"type": "character", "title": "苏晚", "summary": "老板娘"},
        )
        self.conn.execute(
            "UPDATE worlds SET deleted_at = ? WHERE id = ?",
            (_iso_now(), self.world_id),
        )
        self.conn.commit()

        # get 404
        with self.assertRaises(LoreEntryWorldNotFoundError):
            get_entry(self.conn, e["id"])

        # list 404
        with self.assertRaises(LoreEntryWorldNotFoundError):
            list_entries(self.conn, self.world_id)

        # update 404
        with self.assertRaises(LoreEntryWorldNotFoundError):
            update_entry(self.conn, e["id"], {"summary": "新描述"}, expected_revision=1)

        # delete 404
        with self.assertRaises(LoreEntryWorldNotFoundError):
            delete_entry(self.conn, e["id"], expected_revision=1)

    def test_pagination_and_stable_sorting(self) -> None:
        """测试分页与稳定排序（updated_at DESC, id ASC）。"""
        for i in range(12):
            create_entry(
                self.conn,
                self.world_id,
                {
                    "type": "location" if i % 2 == 0 else "character",
                    "title": f"地点与人物_{i:02d}",
                    "summary": f"条目编号 {i}",
                },
            )

        # 分页获取
        p1 = list_entries(self.conn, self.world_id, limit=5, offset=0)
        self.assertEqual(p1["total"], 12)
        self.assertEqual(len(p1["items"]), 5)

        p2 = list_entries(self.conn, self.world_id, limit=5, offset=5)
        self.assertEqual(len(p2["items"]), 5)

        ids_1 = {x["id"] for x in p1["items"]}
        ids_2 = {x["id"] for x in p2["items"]}
        self.assertEqual(len(ids_1.intersection(ids_2)), 0)

        # 类型过滤
        loc_res = list_entries(self.conn, self.world_id, type="location")
        self.assertEqual(loc_res["total"], 6)
        self.assertTrue(all(it["type"] == "location" for it in loc_res["items"]))


if __name__ == "__main__":
    unittest.main()
