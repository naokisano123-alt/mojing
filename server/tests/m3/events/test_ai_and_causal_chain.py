"""M3.1 / M3.5 AI 初始局势生成、OpenAI Mock 响应解析、北境贸易因果链与重大转折测试。

覆盖验收项：
1. `FakeEventModelClient` 生成 3～6 条初始活跃事件线与后续到期事件推进；
2. `OpenAIEventModelClient` 配合 `httpx.MockTransport` 解析合法响应、剥离代码块、拦截伪造来源与非 JSON 响应、Key 脱敏；
3. 北境贸易 fixture 完整演进链（邻国关税 -> 商队改道 -> 本地供应受影响 -> 商人调整采购与挂牌价），验证每条后果均有事件来源与版本，且不改动玩家账本；
4. 战争、政权更替等重大转折（`turnpoint_type`）的阶段顺序（`omen -> escalation -> turning_point -> aftermath`）与已登记前置条件（`register_precondition`）验证；
5. 核对随包交付的 `integration_fixture.json` 输入与期望输出同实时运行结果 100% 一致。
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import unittest

import httpx

from app.model_gateway.errors import GatewayError
from app.m3.events import (
    NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
    NORTHERN_TRADE_CANON_SOURCES,
    NORTHERN_TRADE_INSTANCE_ID,
    EventSeedRequest,
    FakeEventModelClient,
    OpenAIEventModelClient,
    SourceFabricatedError,
    TurnpointValidationError,
    apply_proposal,
    build_advance_request_from_db,
    ensure_events_schema,
    ensure_instance_exists,
    generate_event_advancement,
    generate_initial_situation,
    get_event,
    get_northern_trade_integration_bundle,
    register_precondition,
    run_northern_trade_causal_chain,
)


class TestAiAndCausalChain(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_events_schema(self.conn)
        ensure_instance_exists(
            self.conn,
            NORTHERN_TRADE_INSTANCE_ID,
            world_id="world_north",
            adventure_id="adv_north",
            world_minute=0,
        )

    def tearDown(self) -> None:
        self.conn.close()

    async def test_fake_model_seed_and_advance_lifecycle(self) -> None:
        """验证 FakeEventModelClient 初始生成 5 条主线并推进到期事件及衍生临时事件。"""
        fake_client = FakeEventModelClient()
        seed_req = EventSeedRequest(
            instance_id=NORTHERN_TRADE_INSTANCE_ID,
            world_minute=0,
            sources=NORTHERN_TRADE_CANON_SOURCES,
            world_intro="北境小镇与邻国贸易世界设定",
        )
        validated_seed = await generate_initial_situation(seed_req, fake_client)
        self.assertEqual(validated_seed.mode, "seed")
        self.assertEqual(len(validated_seed.operations), 5)

        applied_seed = apply_proposal(
            self.conn,
            NORTHERN_TRADE_INSTANCE_ID,
            validated_seed,
            allowed_source_ids=NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
        )
        self.assertEqual(len(applied_seed["events"]), 5)
        for ev in applied_seed["events"]:
            self.assertEqual(ev["kind"], "thread")
            self.assertEqual(ev["status"], "active")
            self.assertTrue(set(ev["source_ids"]) <= NORTHERN_TRADE_ALLOWED_SOURCE_IDS)

        # 推进时间至 120 分钟，构建受限到期请求并调用 Fake 模型推进
        self.conn.execute(
            "UPDATE world_instances SET world_minute = 120 WHERE id = ?",
            (NORTHERN_TRADE_INSTANCE_ID,),
        )
        adv_req = build_advance_request_from_db(
            self.conn,
            NORTHERN_TRADE_INSTANCE_ID,
            world_minute=120,
            sources=NORTHERN_TRADE_CANON_SOURCES[:2],
        )
        self.assertGreaterEqual(len(adv_req.due_events), 1)
        validated_adv = await generate_event_advancement(adv_req, fake_client)
        applied_adv = apply_proposal(
            self.conn,
            NORTHERN_TRADE_INSTANCE_ID,
            validated_adv,
            allowed_source_ids=NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
        )
        self.assertEqual(applied_adv["events"][0]["revision"], 2)
        self.assertEqual(applied_adv["events"][0]["updated_minute"], 120)

    async def test_openai_event_model_client_with_mock_transport(self) -> None:
        """验证 OpenAIEventModelClient 解析结构化输出、记录 usage、拦截伪造来源且不泄露 Key。"""
        seed_req = EventSeedRequest(
            instance_id=NORTHERN_TRADE_INSTANCE_ID,
            world_minute=0,
            sources=NORTHERN_TRADE_CANON_SOURCES[:3],
        )

        valid_llm_json = {
            "mode": "seed",
            "summary": "Mock 模型生成的三条主线",
            "operations": [
                {
                    "op": "create",
                    "kind": "thread",
                    "title": f"Mock 主线 {i}",
                    "description": f"基于资料生成的第 {i} 条主线描述。",
                    "location": "霜木镇",
                    "scope": "regional",
                    "participants": ["韩掌柜", "加雷特首领"],
                    "stage": "initial",
                    "status": "active",
                    "next_update_minute": 180,
                    "cause_event_ids": [],
                    "source_ids": [NORTHERN_TRADE_CANON_SOURCES[i - 1].source_id],
                }
                for i in range(1, 4)
            ],
        }

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers.get("Authorization"), "Bearer sk-secret-test-key-999")
            body = {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "```json\n" + json.dumps(valid_llm_json, ensure_ascii=False) + "\n```",
                        },
                    }
                ],
                "usage": {"prompt_tokens": 310, "completion_tokens": 220, "total_tokens": 530},
            }
            return httpx.Response(200, json=body)

        client = OpenAIEventModelClient(
            base_url="https://api.example.com/v1",
            model="gpt-4o-mini",
            api_key="sk-secret-test-key-999",
            transport=httpx.MockTransport(mock_handler),
        )
        # 确保 repr 绝不泄露 API Key
        self.assertNotIn("sk-secret-test-key-999", repr(client))

        proposal = await generate_initial_situation(seed_req, client)
        self.assertEqual(len(proposal.operations), 3)
        self.assertEqual(client.last_usage, {"prompt_tokens": 310, "completion_tokens": 220, "total_tokens": 530})

        # 测试模型返回伪造 source_id 时被校验器拦截
        forged_llm_json = {
            **valid_llm_json,
            "operations": [
                valid_llm_json["operations"][0],
                valid_llm_json["operations"][1],
                {**valid_llm_json["operations"][2], "source_ids": ["entry:forged_by_llm"]},
            ],
        }

        async def forged_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps(forged_llm_json, ensure_ascii=False)},
                        }
                    ]
                },
            )

        forged_client = OpenAIEventModelClient(
            base_url="https://api.example.com/v1",
            model="gpt-4o-mini",
            api_key="sk-secret-test-key-999",
            transport=httpx.MockTransport(forged_handler),
        )
        with self.assertRaises(SourceFabricatedError):
            await generate_initial_situation(seed_req, forged_client)

        # 测试模型返回非 JSON 时抛出 GatewayError(MODEL_OUTPUT_NOT_JSON)
        async def invalid_json_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"finish_reason": "stop", "message": {"content": "这不是 JSON"}}]},
            )

        bad_client = OpenAIEventModelClient(
            base_url="https://api.example.com/v1",
            model="gpt-4o-mini",
            api_key="sk-secret-test-key-999",
            transport=httpx.MockTransport(invalid_json_handler),
        )
        with self.assertRaises(GatewayError) as cm_gw:
            await generate_initial_situation(seed_req, bad_client)
        self.assertEqual(cm_gw.exception.code, "MODEL_OUTPUT_NOT_JSON")

    def test_northern_trade_causal_chain_traceability_and_fixture_file(self) -> None:
        """验证北境贸易完整因果链：邻国关税 -> 商队改道 -> 本地供应受影响，每个后果均可追溯，且与 JSON 文件一致。"""
        bundle = run_northern_trade_causal_chain(self.conn, NORTHERN_TRADE_INSTANCE_ID)
        self.assertEqual(len(bundle["steps"]), 4)

        # 检查全部 6 条后果均具备明确的 source_event_id 与 source_event_revision，且无任何玩家账本修改
        consequences = bundle["final_consequences"]
        self.assertEqual(len(consequences), 6)
        expected_sources = [
            ("csq_north_01_tariff", "evt_north_thread_4", 2, "politics"),
            ("csq_north_02_route", "evt_north_thread_2", 2, "trade_route"),
            ("csq_north_03_caravan", "evt_north_inc_1", 1, "trade_route"),
            ("csq_north_04_supply", "evt_north_inc_1", 2, "regional_supply"),
            ("csq_north_05_merchant", "evt_north_inc_2", 1, "npc_inventory"),
            ("csq_north_06_price", "evt_north_inc_2", 1, "market_price"),
        ]
        for actual, (cid, src_eid, src_rev, dom) in zip(consequences, expected_sources):
            self.assertEqual(actual["consequence_id"], cid)
            self.assertEqual(actual["source_event_id"], src_eid)
            self.assertEqual(actual["source_event_revision"], src_rev)
            self.assertEqual(actual["domain"], dom)

        # 验证从末端事件 evt_north_inc_2（霜木镇原材料短缺与商人采购调整）向上追溯可完整找回：
        # 商队改道临时事件 (evt_north_inc_1)、贸易路线主线 (evt_north_thread_2)、邻国关税外交主线 (evt_north_thread_4)
        trace = bundle["causal_trace_for_local_shortage"]
        ancestor_ids = [e["event_id"] for e in trace["ancestors"]]
        self.assertEqual(
            ancestor_ids,
            ["evt_north_thread_2", "evt_north_thread_4", "evt_north_inc_1"],
        )
        self.assertEqual(len(trace["chain_consequences"]), 6)

        # 验证随包附带的 integration_fixture.json 与实时计算完全一致
        standalone_bundle = get_northern_trade_integration_bundle()
        fixture_path = (
            Path(__file__).resolve().parents[3]
            / "app"
            / "m3"
            / "events"
            / "integration_fixture.json"
        )
        self.assertTrue(fixture_path.is_file())
        loaded_fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(loaded_fixture, standalone_bundle)

    def test_major_turnpoint_stages_and_preconditions(self) -> None:
        """测试战争、政权更替等重大转折必须遵循阶段顺序且具备已登记的前置条件与起因链。"""
        run_northern_trade_causal_chain(self.conn, NORTHERN_TRADE_INSTANCE_ID)

        # 1. 尝试直接创建一个处于 turning_point（爆发期）或 completed 的战争事件 -> 拒绝
        with self.assertRaises(TurnpointValidationError) as cm_jump_create:
            apply_proposal(
                self.conn,
                NORTHERN_TRADE_INSTANCE_ID,
                {
                    "mode": "advance",
                    "operations": [
                        {
                            "op": "create",
                            "kind": "thread",
                            "title": "北境与洛兰全面边境战争",
                            "description": "试图毫无铺垫直接爆发战争。",
                            "location": "灰岩边关",
                            "scope": "international",
                            "participants": ["北境王国", "洛兰公国"],
                            "stage": "turning_point",
                            "status": "active",
                            "next_update_minute": 600,
                            "cause_event_ids": ["evt_north_thread_4"],
                            "source_ids": ["entry:diplomacy_loran_tariff_02"],
                            "turnpoint_type": "war",
                        }
                    ],
                },
                world_minute=400,
                allowed_source_ids=NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
            )
        self.assertEqual(cm_jump_create.exception.code, "TURNPOINT_STAGE_INVALID")

        # 2. 在 omen（征兆）阶段合法创建战争主线 evt_war_1
        apply_proposal(
            self.conn,
            NORTHERN_TRADE_INSTANCE_ID,
            {
                "mode": "advance",
                "operations": [
                    {
                        "op": "create",
                        "kind": "thread",
                        "title": "灰岩边关军事对峙与战争危机",
                        "description": "关税摩擦升级后双方边境守军增兵巡逻，战争阴云聚集。",
                        "location": "灰岩边关",
                        "scope": "international",
                        "participants": ["北境王国边防军", "洛兰公国边防军"],
                        "stage": "omen",
                        "status": "active",
                        "next_update_minute": 480,
                        "cause_event_ids": ["evt_north_thread_4"],
                        "source_ids": ["entry:diplomacy_loran_tariff_02"],
                        "turnpoint_type": "war",
                    }
                ],
            },
            world_minute=400,
            allowed_source_ids=NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
            id_factory=lambda: "evt_war_1",
        )

        # 3. 未登记前置条件时尝试推进至 escalation -> 拒绝
        with self.assertRaises(TurnpointValidationError) as cm_no_pre:
            apply_proposal(
                self.conn,
                NORTHERN_TRADE_INSTANCE_ID,
                {
                    "mode": "advance",
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_war_1",
                            "expected_event_revision": 1,
                            "stage": "escalation",
                            "next_update_minute": 600,
                        }
                    ],
                },
                world_minute=480,
                allowed_source_ids=NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
            )
        self.assertEqual(cm_no_pre.exception.code, "TURNPOINT_PRECONDITION_MISSING")

        # 4. 登记已满足的前置条件 pre_border_mobilization
        register_precondition(
            self.conn,
            NORTHERN_TRADE_INSTANCE_ID,
            precondition_id="pre_border_mobilization",
            kind="military_mobilization",
            title="双方边防军完成前线物资与兵力集结",
            participants=["北境王国边防军", "洛兰公国边防军"],
            source_ids=["entry:diplomacy_loran_tariff_02"],
            cause_event_ids=["evt_north_thread_4"],
            registered_minute=450,
        )

        # 5. 尝试从 omen 直接跳到 turning_point（跳过 escalation） -> 拒绝
        with self.assertRaises(TurnpointValidationError) as cm_skip:
            apply_proposal(
                self.conn,
                NORTHERN_TRADE_INSTANCE_ID,
                {
                    "mode": "advance",
                    "operations": [
                        {
                            "op": "update",
                            "event_id": "evt_war_1",
                            "expected_event_revision": 1,
                            "stage": "turning_point",
                            "precondition_ids": ["pre_border_mobilization"],
                            "next_update_minute": 600,
                        }
                    ],
                },
                world_minute=480,
                allowed_source_ids=NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
            )
        self.assertEqual(cm_skip.exception.code, "TURNPOINT_STAGE_INVALID")

        # 6. 按序推进：omen -> escalation (rev 2) -> turning_point (rev 3) -> aftermath & completed (rev 4)
        apply_proposal(
            self.conn,
            NORTHERN_TRADE_INSTANCE_ID,
            {
                "mode": "advance",
                "operations": [
                    {
                        "op": "update",
                        "event_id": "evt_war_1",
                        "expected_event_revision": 1,
                        "stage": "escalation",
                        "precondition_ids": ["pre_border_mobilization"],
                        "next_update_minute": 600,
                    }
                ],
            },
            world_minute=480,
            allowed_source_ids=NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
        )
        apply_proposal(
            self.conn,
            NORTHERN_TRADE_INSTANCE_ID,
            {
                "mode": "advance",
                "operations": [
                    {
                        "op": "update",
                        "event_id": "evt_war_1",
                        "expected_event_revision": 2,
                        "stage": "turning_point",
                        "next_update_minute": 720,
                    }
                ],
            },
            world_minute=600,
            allowed_source_ids=NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
        )
        apply_proposal(
            self.conn,
            NORTHERN_TRADE_INSTANCE_ID,
            {
                "mode": "advance",
                "operations": [
                    {
                        "op": "update",
                        "event_id": "evt_war_1",
                        "expected_event_revision": 3,
                        "stage": "aftermath",
                        "status": "completed",
                        "next_update_minute": None,
                    }
                ],
            },
            world_minute=720,
            allowed_source_ids=NORTHERN_TRADE_ALLOWED_SOURCE_IDS,
        )
        war_ev = get_event(self.conn, NORTHERN_TRADE_INSTANCE_ID, "evt_war_1")
        self.assertEqual(war_ev["stage"], "aftermath")
        self.assertEqual(war_ev["status"], "completed")
        self.assertEqual(war_ev["revision"], 4)


if __name__ == "__main__":
    unittest.main()
