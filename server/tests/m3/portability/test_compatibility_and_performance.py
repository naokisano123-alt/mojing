"""验证 v1–v4 版本兼容读取、同一世界二次导入隔离、单独导入存档匹配、ZIP 限制与快照性能基准对照。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from app.db import init_db
from app.m3.portability import (
    SAVES_FORMAT,
    SETTINGS_FORMAT,
    SyntheticUploadFile,
    TransferInvalidError,
    TransferLimitExceededError,
    TransferMismatchError,
    TransferSettingsRequiredError,
    build_export_payloads,
    build_zip_bytes,
    import_transfer,
    run_snapshot_performance_benchmark,
    seed_world_with_deleted_sources_and_v4_saves,
)


class TestCompatibilityAndPerformance(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.db_path = f"{self._tmp.name}/compat.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def test_v1_v2_v3_v4_backward_compatibility_and_reimport_isolation(self) -> None:
        """验证 v1、v2、v3、v4 各版本世界观与存档均可正常导入，且同一世界二次导入生成完全独立的副本。"""
        conn = self._connect()
        try:
            # 构造合成 v1 世界观 + v1 存档（无 world_openings、无 adventure_memory_events、无 opening_json、无 world_instances）
            v1_world = {
                "format": SETTINGS_FORMAT,
                "version": 1,
                "world": {
                    "id": "w_legacy_v1",
                    "name": "旧版 v1 世界",
                    "description": "兼容测试",
                    "created_at": "2026-11-01T00:00:00Z",
                    "updated_at": "2026-11-01T00:00:00Z",
                },
                "documents": [],
                "chunks": [],
                "lore_entries": [],
                "lore_entry_sources": [],
                "item_descriptions": [{"name": "铜币", "description": "旧版通用货币"}],
            }
            v1_world_bytes = json.dumps(v1_world, ensure_ascii=False).encode("utf-8")
            v1_digest = hashlib.sha256(v1_world_bytes).hexdigest()

            v1_saves = {
                "format": SAVES_FORMAT,
                "version": 1,
                "world_id": "w_legacy_v1",
                "settings_sha256": v1_digest,
                "adventures": [
                    {
                        "id": "adv_v1_01",
                        "name": "旧版 v1 旅程",
                        "mode": "fake",
                        "revision": 1,
                        "created_at": 100.0,
                        "character_json": None,
                        "origin_checkpoint_id": None,
                    }
                ],
                "adventure_turns": [
                    {
                        "adventure_id": "adv_v1_01",
                        "sequence": 1,
                        "draft_id": "d1",
                        "action": "出发",
                        "result": json.dumps(
                            {"narrative": "踏上旧版旅途。", "sources": [], "references": [], "suggestions": []}
                        ),
                        "created_at": 101.0,
                    }
                ],
                "adventure_checkpoints": [
                    {
                        "id": "cp_v1_01",
                        "adventure_id": "adv_v1_01",
                        "name": "v1 检查点",
                        "revision": 1,
                        "character_json": None,
                        "mode": "fake",
                        "created_at": 102.0,
                    }
                ],
            }
            v1_saves_bytes = json.dumps(v1_saves, ensure_ascii=False).encode("utf-8")

            w_id_v1 = import_transfer(
                conn,
                [
                    SyntheticUploadFile("world.json", v1_world_bytes),
                    SyntheticUploadFile("saves.json", v1_saves_bytes),
                ],
            )
            conn.commit()
            self.assertTrue(w_id_v1)

            # 验证 v2 存档（含 adventure_memory_events，不含 opening_json）与 v3 存档（含 opening_json）
            for ver in (2, 3):
                saves_ver = {
                    **v1_saves,
                    "version": ver,
                    "adventure_memory_events": [],
                    "adventure_checkpoints": [
                        {**v1_saves["adventure_checkpoints"][0], "memory_cursor": 0}
                    ],
                }
                if ver >= 3:
                    saves_ver["adventures"] = [
                        {**v1_saves["adventures"][0], "opening_json": None}
                    ]
                w_id_ver = import_transfer(
                    conn,
                    [
                        SyntheticUploadFile("world.json", v1_world_bytes),
                        SyntheticUploadFile("saves.json", json.dumps(saves_ver).encode("utf-8")),
                    ],
                )
                conn.commit()
                self.assertNotEqual(w_id_ver, w_id_v1)

            # 验证 v4 存档（含已删除历史来源）在同一数据库二次导入完全隔离
            seeded_v4 = seed_world_with_deleted_sources_and_v4_saves(conn, world_id="w_v4_origin")
            conn.commit()
            exp_v4 = build_export_payloads(conn, seeded_v4["world_id"])

            zip_bundle = build_zip_bytes(
                {
                    "world.json": exp_v4["world_bytes"],
                    "saves.json": exp_v4["saves_bytes"],
                }
            )
            first_copy = import_transfer(conn, [SyntheticUploadFile("bundle1.zip", zip_bundle)])
            second_copy = import_transfer(conn, [SyntheticUploadFile("bundle2.zip", zip_bundle)])
            # 同时验证仅上传 saves.json 复用已导入世界设置
            saves_only_copy = import_transfer(
                conn, [SyntheticUploadFile("saves.json", exp_v4["saves_bytes"])]
            )
            conn.commit()

            self.assertEqual(len({seeded_v4["world_id"], first_copy, second_copy, saves_only_copy}), 4)
        finally:
            conn.close()

    def test_zip_security_limits_and_mismatch_errors(self) -> None:
        """验证 ZIP 路径穿越、多余文件、不匹配摘要、缺失世界观及超大文件限制均被准确拦截。"""
        conn = self._connect()
        try:
            seeded = seed_world_with_deleted_sources_and_v4_saves(conn, world_id="w_zip_test")
            conn.commit()
            exp = build_export_payloads(conn, seeded["world_id"])

            # 1. ZIP 包含非法路径 `../world.json`
            bad_zip_traversal = build_zip_bytes(
                {
                    "../world.json": exp["world_bytes"],
                    "saves.json": exp["saves_bytes"],
                }
            )
            with self.assertRaises(TransferInvalidError):
                import_transfer(conn, [SyntheticUploadFile("bad.zip", bad_zip_traversal)])

            # 2. ZIP 包含多余文件
            bad_zip_extra = build_zip_bytes(
                {
                    "world.json": exp["world_bytes"],
                    "saves.json": exp["saves_bytes"],
                    "extra.txt": b"unexpected",
                }
            )
            with self.assertRaises(TransferInvalidError):
                import_transfer(conn, [SyntheticUploadFile("extra.zip", bad_zip_extra)])

            # 3. 世界观与存档 SHA-256 不匹配
            tampered_world = json.loads(exp["world_bytes"].decode("utf-8"))
            tampered_world["world"]["description"] = "被修改的描述"
            with self.assertRaises(TransferMismatchError):
                import_transfer(
                    conn,
                    [
                        SyntheticUploadFile("world.json", json.dumps(tampered_world).encode("utf-8")),
                        SyntheticUploadFile("saves.json", exp["saves_bytes"]),
                    ],
                )

            # 4. 单独导入未知世界的 saves.json 报 WORLD_TRANSFER_SETTINGS_REQUIRED
            unknown_saves = json.loads(exp["saves_bytes"].decode("utf-8"))
            unknown_saves["world_id"] = "world_never_seen_before"
            with self.assertRaises(TransferSettingsRequiredError):
                import_transfer(
                    conn,
                    [SyntheticUploadFile("saves.json", json.dumps(unknown_saves).encode("utf-8"))],
                )

            # 5. 上传大小超过 128 MiB 限制报 413 WORLD_TRANSFER_LIMIT
            oversized = SyntheticUploadFile("huge.json", b"{}")
            oversized.size = 129 * 1024 * 1024
            with self.assertRaises(TransferLimitExceededError):
                import_transfer(conn, [oversized])
        finally:
            conn.close()

    def test_snapshot_performance_benchmark_and_same_minute_multi_revision_restore(self) -> None:
        """运行可重现快照性能基准，验证全量、压缩与增量三方案对照及同分钟不同版本的精确恢复。"""
        bench_report = run_snapshot_performance_benchmark(init_db, num_checkpoints=6)

        self.assertTrue(bench_report["workload"]["same_minute_multi_revision_verified"])
        strategies = bench_report["strategies"]
        self.assertIn("full", strategies)
        self.assertIn("compressed_zlib", strategies)
        self.assertIn("incremental_delta", strategies)

        # 验证压缩方案与增量方案的字节数均显著小于全量快照，且三者均通过同分钟多版本精确恢复校验
        full_bytes = strategies["full"]["total_serialized_bytes"]
        comp_bytes = strategies["compressed_zlib"]["total_serialized_bytes"]
        inc_bytes = strategies["incremental_delta"]["total_serialized_bytes"]

        self.assertGreater(full_bytes, comp_bytes)
        self.assertGreater(full_bytes, inc_bytes)
        self.assertTrue(strategies["full"]["exact_same_minute_multi_revision_verified"])
        self.assertTrue(strategies["compressed_zlib"]["exact_same_minute_multi_revision_verified"])
        self.assertTrue(strategies["incremental_delta"]["exact_same_minute_multi_revision_verified"])
        self.assertFalse(bench_report["recommendation"]["introduce_new_top_level_version"])
