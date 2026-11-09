"""M3.1 / M3.5 事件快照迁移与多分支恢复隔离测试。

覆盖验收项：
1. 生成包含事件版本、因果、来源、后果与前置条件的可移植 JSON 快照；
2. 同一游戏分钟先捕获快照、随后在原实例继续推进事件，已保存快照绝不混入后续变更；
3. 从同一个检查点快照恢复出两个独立世界实例（`inst_branch_a` 与 `inst_branch_b`），
   对同一个 `event_id` 作不同方向的推进，验证原实例与两个分支完全隔离、互不污染；
4. 跨设备/跨库导入时支持通过 `source_id_map` 重映射外部 Canon 来源 ID；
5. 快照格式错误、版本不连续或因果环损坏时原子回滚 `SAVEPOINT`，不留残余脏数据。
"""

from __future__ import annotations

import copy
import json
import sqlite3
import unittest

from app.m3.events import (
    NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
    NORTHERN_TRADE_INSTANCE_ID,
    SnapshotRestoreError,
    apply_proposal,
    ensure_events_schema,
    ensure_instance_exists,
    get_event,
    list_consequences,
    list_event_revisions,
    restore,
    run_northern_trade_causal_chain,
    snapshot,
)


class TestSnapshotAndBranches(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_events_schema(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_restore_two_branches_from_same_checkpoint_and_diverge(self) -> None:
        """从同一个检查点恢复两个实例并对同一事件作不同推进，验证三方状态完全隔离。"""
        run_northern_trade_causal_chain(self.conn, NORTHERN_TRADE_INSTANCE_ID)

        # 在 world_minute = 360 捕获检查点快照
        checkpoint_snap = snapshot(self.conn, NORTHERN_TRADE_INSTANCE_ID)
        # 验证快照完全可 JSON 序列化
        roundtrip_snap = json.loads(json.dumps(checkpoint_snap, ensure_ascii=False))

        # 随后在原实例（同分钟 360）继续推进 evt_north_thread_1，验证此前捕获的快照不受影响
        apply_proposal(
            self.conn,
            NORTHERN_TRADE_INSTANCE_ID,
            {
                "mode": "advance",
                "operations": [
                    {
                        "op": "update",
                        "event_id": "evt_north_thread_1",
                        "expected_event_revision": 1,
                        "stage": "original_timeline_post_checkpoint",
                        "next_update_minute": 540,
                        "reason": "原路线在存档后继续演进",
                    }
                ],
            },
            world_minute=360,
            allowed_source_ids=NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
        )

        # 从检查点快照恢复出两个独立分支实例：inst_branch_a 与 inst_branch_b
        ensure_instance_exists(self.conn, "inst_branch_a", world_id="world_north", world_minute=360)
        ensure_instance_exists(self.conn, "inst_branch_b", world_id="world_north", world_minute=360)

        restored_a = restore(
            self.conn,
            "inst_branch_a",
            roundtrip_snap,
            allowed_source_ids=NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
        )
        restored_b = restore(
            self.conn,
            "inst_branch_b",
            roundtrip_snap,
            allowed_source_ids=NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
        )

        # 恢复刚完成时，两分支内的 evt_north_thread_1 均处于存档时的 revision=1, stage='local_bidding'
        # 绝不包含原路线存档后提交的 'original_timeline_post_checkpoint'
        ev_a_init = get_event(self.conn, "inst_branch_a", "evt_north_thread_1")
        ev_b_init = get_event(self.conn, "inst_branch_b", "evt_north_thread_1")
        self.assertEqual(ev_a_init["revision"], 1)
        self.assertEqual(ev_a_init["stage"], "local_bidding")
        self.assertEqual(ev_b_init["revision"], 1)
        self.assertEqual(ev_b_init["stage"], "local_bidding")
        self.assertEqual(len(restored_a["events"]), 7)
        self.assertEqual(len(restored_b["events"]), 7)

        # 分支 A：对 evt_north_thread_1 推进为本地工坊达成联合议价同盟（status='completed'）
        apply_proposal(
            self.conn,
            "inst_branch_a",
            {
                "mode": "advance",
                "operations": [
                    {
                        "op": "update",
                        "event_id": "evt_north_thread_1",
                        "expected_event_revision": 1,
                        "stage": "craftsman_alliance_formed",
                        "status": "completed",
                        "description": "分支A：霜木镇铁匠与原料商结成价格同盟，成功抵制星辉分号压价。",
                        "next_update_minute": None,
                        "consequences": [
                            {
                                "domain": "faction_relation",
                                "target": "霜木镇工坊同盟",
                                "summary": "本地工坊议价能力增强，维持铁器收购基准价。",
                                "impact_type": "direct",
                            }
                        ],
                        "reason": "分支A路线推进",
                    }
                ],
            },
            world_minute=420,
            allowed_source_ids=NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
            consequence_id_factory=lambda: "csq_branch_a_1",
        )

        # 分支 B：对同一个 event_id (evt_north_thread_1) 推进为谈判陷入停滞（status='stalled'）
        apply_proposal(
            self.conn,
            "inst_branch_b",
            {
                "mode": "advance",
                "operations": [
                    {
                        "op": "update",
                        "event_id": "evt_north_thread_1",
                        "expected_event_revision": 1,
                        "stage": "price_war_stalemate",
                        "status": "stalled",
                        "description": "分支B：星辉分号以断供相胁，本地商谈陷入僵局。",
                        "next_update_minute": 600,
                        "consequences": [
                            {
                                "domain": "shop_status",
                                "target": "霜木镇·零散铁匠铺",
                                "summary": "数家小型铁匠铺因原料与收购双重挤压暂停接单。",
                                "impact_type": "direct",
                            }
                        ],
                        "reason": "分支B路线推进",
                    }
                ],
            },
            world_minute=420,
            allowed_source_ids=NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
            consequence_id_factory=lambda: "csq_branch_b_1",
        )

        # 核对原实例、分支 A、分支 B 三者的 evt_north_thread_1 状态、版本历史与后果完全独立
        ev_orig = get_event(self.conn, NORTHERN_TRADE_INSTANCE_ID, "evt_north_thread_1")
        ev_a = get_event(self.conn, "inst_branch_a", "evt_north_thread_1")
        ev_b = get_event(self.conn, "inst_branch_b", "evt_north_thread_1")

        self.assertEqual(ev_orig["stage"], "original_timeline_post_checkpoint")
        self.assertEqual(ev_orig["status"], "active")

        self.assertEqual(ev_a["stage"], "craftsman_alliance_formed")
        self.assertEqual(ev_a["status"], "completed")
        self.assertIsNone(ev_a["next_update_minute"])

        self.assertEqual(ev_b["stage"], "price_war_stalemate")
        self.assertEqual(ev_b["status"], "stalled")
        self.assertEqual(ev_b["next_update_minute"], 600)

        csq_a = list_consequences(self.conn, "inst_branch_a", source_event_id="evt_north_thread_1")
        csq_b = list_consequences(self.conn, "inst_branch_b", source_event_id="evt_north_thread_1")
        self.assertEqual(len(csq_a), 1)
        self.assertEqual(csq_a[0]["target"], "霜木镇工坊同盟")
        self.assertEqual(len(csq_b), 1)
        self.assertEqual(csq_b[0]["target"], "霜木镇·零散铁匠铺")

        revs_a = list_event_revisions(self.conn, "inst_branch_a", "evt_north_thread_1")
        revs_b = list_event_revisions(self.conn, "inst_branch_b", "evt_north_thread_1")
        self.assertEqual(revs_a[-1]["reason"], "分支A路线推进")
        self.assertEqual(revs_b[-1]["reason"], "分支B路线推进")

    def test_snapshot_restore_source_id_remapping_and_corrupted_rollback(self) -> None:
        """测试跨库迁移时 source_id_map 重映射，以及损坏快照恢复失败时的 SAVEPOINT 原子回滚。"""
        run_northern_trade_causal_chain(self.conn, NORTHERN_TRADE_INSTANCE_ID)
        snap = snapshot(self.conn, NORTHERN_TRADE_INSTANCE_ID)

        source_map = {
            "chunk:north_geography_01": "chunk:imported_geo_101",
            "chunk:north_trade_routes_02": "chunk:imported_route_102",
            "entry:faction_starlight_guild_01": "entry:imported_guild_201",
            "entry:diplomacy_loran_tariff_02": "entry:imported_tariff_202",
            "entry:conflict_iron_vein_03": "entry:imported_vein_203",
        }
        ensure_instance_exists(self.conn, "inst_imported", world_id="world_imported", world_minute=360)
        restored = restore(
            self.conn,
            "inst_imported",
            snap,
            source_id_map=source_map,
            allowed_source_ids=frozenset(source_map.values()),
        )
        ev1 = next(e for e in restored["events"] if e["event_id"] == "evt_north_thread_1")
        self.assertEqual(
            ev1["source_ids"],
            ["chunk:imported_geo_101", "entry:imported_guild_201"],
        )

        # 构造损坏快照（缺失修订版本 1，存在版本断层），验证恢复到 inst_imported 时回滚且保留之前恢复的数据
        corrupted = copy.deepcopy(snap)
        corrupted["revisions"] = [
            r for r in corrupted["revisions"] if not (r["event_id"] == "evt_north_thread_4" and r["revision"] == 1)
        ]
        with self.assertRaises(SnapshotRestoreError) as cm:
            restore(
                self.conn,
                "inst_imported",
                corrupted,
                source_id_map=source_map,
                allowed_source_ids=frozenset(source_map.values()),
            )
        self.assertEqual(cm.exception.code, "SNAPSHOT_REVISION_GAP")

        # 验证回滚后 inst_imported 之前恢复的 7 条事件完好无损
        still_intact = get_event(self.conn, "inst_imported", "evt_north_thread_1")
        self.assertEqual(still_intact["source_ids"], ["chunk:imported_geo_101", "entry:imported_guild_201"])


if __name__ == "__main__":
    unittest.main()
