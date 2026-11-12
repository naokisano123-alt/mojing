import asyncio
import sqlite3
from app.m3.world_analysis import (
    WORLD_A_ID, FakeWorldAnalysisModelClient, analyze_world_initial_situation,
    ensure_base_world_tables, extract_and_batch_world_sources, seed_maritime_salt_world,
)


def test_missing_or_truncated_partner_defers_conversation_without_inventing_identity():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    ensure_base_world_tables(conn)
    seed_maritime_salt_world(conn, world_id=WORLD_A_ID, instance_id='analysis-instance')
    extraction = extract_and_batch_world_sources(conn, WORLD_A_ID, '白鹭水驿')
    class MissingPartner(FakeWorldAnalysisModelClient):
        async def analyze_batch(self, *args, **kwargs):
            raw = await super().analyze_batch(*args, **kwargs)
            npc = raw['npcs'][0]
            npc.update(plan_kind='conversation_plan', action_kind='conversation_plan', target_npc_id='')
            return raw
    draft = asyncio.run(analyze_world_initial_situation(extraction, MissingPartner(), instance_id='analysis-instance'))
    waiting = [npc for npc in draft.npcs if npc.current_activity == '等待合适的交谈对象']
    assert waiting and all(npc.action_kind == 'task_progress' and not npc.target_npc_id for npc in waiting)
    assert any(note['action'] == 'deferred_conversation' for note in draft.identity_resolution_log)
    conn.close()
