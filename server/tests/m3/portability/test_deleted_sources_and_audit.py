"""验证 v4 存档引用已删除资料后的迁移恢复、历史来源墓碑不可被误用为当前 Canon、不恢复为可搜索条目、跨世界借用同名 ID 拦截与伪造引用拦截。"""

from __future__ import annotations

import copy
import json
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from app.adventures import checkpoints as checkpoint_service
from app.db import init_db
from app.lore_search import search_chunks
from app.m3 import events as world_events, knowledge as world_knowledge
from app.m3.core import service as instance_service
from app.m3.portability import (
    CrossWorldSourceBorrowError,
    ForgedHistoricalReferenceError,
    HistoricalSourceNotCanonError,
    SyntheticUploadFile,
    TransferInvalidError,
    assert_source_usable_as_current_canon,
    build_export_payloads,
    import_transfer,
    list_historical_source_refs,
    seed_world_with_deleted_sources_and_v4_saves,
)


class TestDeletedSourcesAndAudit(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.src_db = f"{self._tmp.name}/src.db"
        self.dst_db = f"{self._tmp.name}/dst.db"
        self.third_db = f"{self._tmp.name}/third.db"
        init_db(self.src_db)
        init_db(self.dst_db)
        init_db(self.third_db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _connect(self, path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def test_v4_deleted_sources_migrate_cleanly_preserve_history_and_never_become_canon_or_searchable(self) -> None:
        """验证 v4 存档引用已软删除资料后成功迁移、保留事件与知识历史、不恢复为可搜索条目、不可用作当前 Canon，且支持二次回导与旧分支续玩。"""
        conn_src = self._connect(self.src_db)
        conn_dst = self._connect(self.dst_db)
        conn_third = self._connect(self.third_db)
        try:
            seeded = seed_world_with_deleted_sources_and_v4_saves(conn_src)
            conn_src.commit()

            exported = build_export_payloads(conn_src, seeded["world_id"])
            self.assertEqual(exported["saves_json"]["version"], 4)

            # 1. 导入到新库 dst_db（此前 world_transfer.py 会因 SNAPSHOT_SOURCE_UNRESOLVED 报错失败，现平滑通过）
            migrated_world_id = import_transfer(
                conn_dst,
                [
                    SyntheticUploadFile("world.json", exported["world_bytes"]),
                    SyntheticUploadFile("saves.json", exported["saves_bytes"]),
                ],
            )
            conn_dst.commit()

            # 2. 验证已删除的文档与条目绝未进入目标库的 documents / chunks / lore_entries 表
            docs_in_dst = conn_dst.execute(
                "SELECT filename FROM documents WHERE world_id = ?", (migrated_world_id,)
            ).fetchall()
            self.assertEqual([r[0] for r in docs_in_dst], ["北境常备地理志.txt"])

            entries_in_dst = conn_dst.execute(
                "SELECT title FROM lore_entries WHERE world_id = ?", (migrated_world_id,)
            ).fetchall()
            self.assertEqual([r[0] for r in entries_in_dst], ["星辉大商会"])

            # 3. 验证已删除内容无法通过资料库搜索命中
            search_res = search_chunks(conn_dst, migrated_world_id, "免税", limit=10)
            self.assertEqual(len(search_res["items"]), 0)

            # 4. 验证可审计历史引用表 m3_historical_source_refs 存在 2 条墓碑记录，且不可被误用为当前 Canon
            tombstones = list_historical_source_refs(conn_dst, migrated_world_id)
            self.assertEqual(len(tombstones), 2)
            for t in tombstones:
                self.assertFalse(t["selectable_as_canon"])
                self.assertFalse(t["searchable"])
                self.assertEqual(t["status"], "deleted_historical")
                with self.assertRaises(HistoricalSourceNotCanonError):
                    assert_source_usable_as_current_canon(conn_dst, migrated_world_id, t["source_id"])

            # 5. 验证 events.select_canon_sources 只选取保留的活跃 Canon，绝不混入历史墓碑
            canon_sel = world_events.select_canon_sources(conn_dst, migrated_world_id)
            tombstone_ids = {t["source_id"] for t in tombstones}
            self.assertTrue(tombstone_ids.isdisjoint(set(canon_sel["selected_source_ids"])))
            self.assertEqual(len(canon_sel["selected_source_ids"]), 2)

            # 6. 验证事件与玩家知识历史完整保留，且旧分支（删资料前派生的分支）与检查点恢复后仍可正常续玩
            adv_rows = conn_dst.execute(
                "SELECT id, name FROM adventures WHERE world_id = ? ORDER BY created_at ASC",
                (migrated_world_id,),
            ).fetchall()
            self.assertEqual(len(adv_rows), 2)
            main_adv_id = next(r["id"] for r in adv_rows if r["name"] == "霜木镇铁料之旅")
            branch_adv_id = next(r["id"] for r in adv_rows if r["name"] == "删资料前派生的旧分支")

            main_inst = instance_service.for_adventure(conn_dst, main_adv_id)
            main_events = world_events.list_events(conn_dst, main_inst["id"])["items"]
            self.assertEqual(len(main_events), 3)
            main_news = world_knowledge.list_known(conn_dst, main_inst["id"], "player")["items"]
            self.assertEqual(len(main_news), 1)
            self.assertIn("旧边贸免税令", main_news[0]["content"])

            # 从迁移后的检查点恢复出新分支，验证可继续恢复与推进
            cps_dst = conn_dst.execute(
                "SELECT id, name FROM adventure_checkpoints WHERE adventure_id = ? ORDER BY created_at ASC",
                (main_adv_id,),
            ).fetchall()
            self.assertEqual(len(cps_dst), 2)
            restored_from_migrated = checkpoint_service.restore(
                conn_dst, cps_dst[0]["id"], "迁移后再派生的分支", "req-after-migrate"
            )
            conn_dst.commit()
            self.assertEqual(restored_from_migrated["revision"], 1)
            self.assertNotEqual(restored_from_migrated["id"], branch_adv_id)

            # 7. 验证新格式回导（Re-export & Re-import 到第三台设备 third_db），历史墓碑与事件继续保持完整
            re_exported = build_export_payloads(conn_dst, migrated_world_id)
            third_world_id = import_transfer(
                conn_third,
                [
                    SyntheticUploadFile("world.json", re_exported["world_bytes"]),
                    SyntheticUploadFile("saves.json", re_exported["saves_bytes"]),
                ],
            )
            conn_third.commit()
            third_tombs = list_historical_source_refs(conn_third, third_world_id)
            self.assertEqual(len(third_tombs), 2)

        finally:
            conn_src.close()
            conn_dst.close()
            conn_third.close()

    def test_forged_reference_and_cross_world_borrowed_id_rejected_with_atomic_rollback(self) -> None:
        """验证伪造历史来源引用与跨世界借用同名 ID 均被拒绝，且数据库完整回滚无残留。"""
        conn_src = self._connect(self.src_db)
        conn_dst = self._connect(self.dst_db)
        try:
            seeded = seed_world_with_deleted_sources_and_v4_saves(conn_src)
            conn_src.commit()
            exported = build_export_payloads(conn_src, seeded["world_id"])

            # 在目标库先预置另一个独立世界 other_world_999，拥有 `chunk:chunk_foreign_secret`
            now_iso = "2026-11-10T00:00:00Z"
            conn_dst.execute(
                "INSERT INTO worlds(id, name, description, created_at, updated_at, deleted_at) VALUES ('other_world_999', '异界秘档', '', ?, ?, NULL)",
                (now_iso, now_iso),
            )
            conn_dst.execute(
                """
                INSERT INTO documents (
                    id, world_id, filename, media_type, byte_size, encoding, content_sha256,
                    text, text_length, line_count, chunk_count, source_kind, encoding_semantics,
                    warnings, status, created_at, updated_at, deleted_at
                ) VALUES ('doc_foreign_999', 'other_world_999', '异界.txt', 'text/plain', 12, 'utf-8', 'sha_f', '异界机密', 4, 1, 1, 'user_import', 'source_bytes', '[]', 'ready', ?, ?, NULL)
                """,
                (now_iso, now_iso),
            )
            conn_dst.execute(
                """
                INSERT INTO chunks (id, document_id, world_id, chunk_index, heading_path, start_char, end_char, text)
                VALUES ('chunk_foreign_secret', 'doc_foreign_999', 'other_world_999', 0, '["机密"]', 0, 4, '异界机密')
                """
            )
            conn_dst.commit()

            # 1. 构造篡改了事件 source_ids 为从未存在过的伪造引用 `entry:forged_nonexistent_888`
            forged_saves = copy.deepcopy(exported["saves_json"])
            forged_saves["world_instances"][0]["snapshot"]["domains"]["events"]["events"][0]["source_ids"].append(
                "entry:forged_nonexistent_888"
            )
            with self.assertRaises(ForgedHistoricalReferenceError):
                import_transfer(
                    conn_dst,
                    [
                        SyntheticUploadFile("world.json", exported["world_bytes"]),
                        SyntheticUploadFile(
                            "saves.json",
                            json.dumps(forged_saves, ensure_ascii=False).encode("utf-8"),
                        ),
                    ],
                )

            # 2. 构造试图跨世界借用 `chunk:chunk_foreign_secret`（甚至伪造墓碑条目）的存档
            borrowed_saves = copy.deepcopy(exported["saves_json"])
            ev_dom = borrowed_saves["world_instances"][0]["snapshot"]["domains"]["events"]
            ev_dom["events"][0]["source_ids"].append("chunk:chunk_foreign_secret")
            ev_dom.setdefault("historical_sources", []).append(
                {
                    "source_id": "chunk:chunk_foreign_secret",
                    "original_source_id": "chunk:chunk_foreign_secret",
                    "kind": "chunk",
                    "title": "试图借用异界分块",
                    "deleted_at": "2026-11-10T03:00:00Z",
                    "status": "deleted_historical",
                    "selectable_as_canon": False,
                    "searchable": False,
                }
            )
            with self.assertRaises(CrossWorldSourceBorrowError):
                import_transfer(
                    conn_dst,
                    [
                        SyntheticUploadFile("world.json", exported["world_bytes"]),
                        SyntheticUploadFile(
                            "saves.json",
                            json.dumps(borrowed_saves, ensure_ascii=False).encode("utf-8"),
                        ),
                    ],
                )

            # 3. 构造篡改历史墓碑 `selectable_as_canon=True` 的非法存档，验证被拦截
            illegal_flag_saves = copy.deepcopy(exported["saves_json"])
            illegal_flag_saves["world_instances"][0]["snapshot"]["domains"]["events"]["historical_sources"][0][
                "selectable_as_canon"
            ] = True
            with self.assertRaises(TransferInvalidError):
                import_transfer(
                    conn_dst,
                    [
                        SyntheticUploadFile("world.json", exported["world_bytes"]),
                        SyntheticUploadFile(
                            "saves.json",
                            json.dumps(illegal_flag_saves, ensure_ascii=False).encode("utf-8"),
                        ),
                    ],
                )

            # 验证目标库中除了预置的 other_world_999 外，没有任何半途写入的世界、旅程或事件记录
            world_count = conn_dst.execute("SELECT COUNT(*) FROM worlds").fetchone()[0]
            self.assertEqual(world_count, 1)
            adv_count = conn_dst.execute("SELECT COUNT(*) FROM adventures").fetchone()[0]
            self.assertEqual(adv_count, 0)
        finally:
            conn_src.close()
            conn_dst.close()

    def test_saves_only_import_after_source_deleted_in_local_world(self) -> None:
        """验证当本地世界存在已软删除资料且用户仅上传 saves.json 时，仍可基于源世界软删除记录生成可审计墓碑并成功恢复。"""
        conn_src = self._connect(self.src_db)
        try:
            seeded = seed_world_with_deleted_sources_and_v4_saves(conn_src, world_id="w_local_del")
            conn_src.commit()
            exported = build_export_payloads(conn_src, seeded["world_id"])

            # 模拟旧版导出的 v4 saves.json（不含显式 historical_sources 扩展字段，但本地源世界已有软删除记录）
            legacy_v4_saves = copy.deepcopy(exported["saves_json"])
            for inst_item in legacy_v4_saves.get("world_instances", []):
                inst_item["snapshot"]["domains"]["events"].pop("historical_sources", None)
            for cp_item in legacy_v4_saves.get("adventure_checkpoints", []):
                if cp_item.get("instance_snapshot"):
                    cp_snap = json.loads(cp_item["instance_snapshot"])
                    cp_snap["domains"]["events"].pop("historical_sources", None)
                    cp_item["instance_snapshot"] = json.dumps(cp_snap, ensure_ascii=False)

            new_world_id = import_transfer(
                conn_src,
                [
                    SyntheticUploadFile(
                        "saves.json",
                        json.dumps(legacy_v4_saves, ensure_ascii=False).encode("utf-8"),
                    )
                ],
            )
            conn_src.commit()
            self.assertNotEqual(new_world_id, seeded["world_id"])
            tombs = list_historical_source_refs(conn_src, new_world_id)
            self.assertEqual(len(tombs), 2)
        finally:
            conn_src.close()
