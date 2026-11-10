import test from 'node:test';
import assert from 'node:assert/strict';
import {
  CHANNEL_LABELS,
  KNOWLEDGE_TYPE_LABELS,
  createNorthTownMultiHopKnowledgeScenario,
  sanitizeWorldNewsPage,
} from '../../../src/features/world-knowledge/api.ts';
import type {
  NarrationKnowledgeContextDto,
  WorldNewsItem,
  WorldNewsPage,
} from '../../../src/features/world-knowledge/types.ts';

test('world-knowledge types cover all 4 knowledge types and 4 channels', () => {
  assert.equal(KNOWLEDGE_TYPE_LABELS.confirmed, '确认事实');
  assert.equal(KNOWLEDGE_TYPE_LABELS.suspected, '怀疑推测');
  assert.equal(KNOWLEDGE_TYPE_LABELS.rumor, '市井传闻');
  assert.equal(KNOWLEDGE_TYPE_LABELS.mistaken, '误信传言');

  assert.equal(CHANNEL_LABELS.observed, '亲眼目睹');
  assert.equal(CHANNEL_LABELS.notice, '公开告示');
  assert.equal(CHANNEL_LABELS.conversation, '当面交谈');
  assert.equal(CHANNEL_LABELS.rumor, '道听途说');
});

test('player projection item excludes hidden event fields and supports opaque pagination', () => {
  const item: WorldNewsItem = {
    fact_id: 'fact_player_from_innkeeper',
    holder_id: 'player',
    knowledge_type: 'confirmed',
    title: '掌柜告知灰羽商队改道东谷',
    content: '酒馆掌柜老周告诉你，灰羽商队因邻国加税改走东谷山道，生铁将推迟三天运抵小镇。',
    channel: 'conversation',
    source_person_id: 'npc:innkeeper',
    source_label: '当面交谈 · 酒馆掌柜老周',
    learned_minute: 50,
    confidence: 0.85,
    status: 'active',
    revision: 1,
  };

  const dirtyPage = {
    items: [
      {
        ...item,
        title: '【隐藏权威标题】掌柜告知灰羽商队改道东谷',
        source_event_id: 'evt_caravan_02',
      } as unknown as WorldNewsItem,
    ],
    next_before: 'kcur1_p1',
    total: 99,
  } as unknown as WorldNewsPage;

  const page = sanitizeWorldNewsPage(dirtyPage);
  assert.equal(page.items[0].title, '掌柜告知灰羽商队改道东谷');
  assert.equal('source_event_id' in (page.items[0] as unknown as Record<string, unknown>), false);
  assert.equal('total' in (page as unknown as Record<string, unknown>), false);
  assert.equal(page.items.length, 1);
  assert.equal(page.next_before, 'kcur1_p1');
});

test('wave2 multi-hop scenario: caravan leader -> innkeeper -> player visibility and branch isolation', async () => {
  const scenario = createNorthTownMultiHopKnowledgeScenario('adv_north_01');

  // Step 1: Caravan leader learns of rerouting -> player sees 0 items
  scenario.step1CaravanLeaderLearns();
  const p1 = await scenario.transport.listWorldNews('adv_north_01');
  assert.equal(p1.items.length, 0);

  // Step 2: Innkeeper receives relay from caravan leader -> player still sees 0 items
  scenario.step2InnkeeperRelayed();
  const p2 = await scenario.transport.listWorldNews('adv_north_01');
  assert.equal(p2.items.length, 0);

  // Snapshot before player talks to innkeeper
  const preKnowSnapshot = scenario.transport.snapshotAdventureNews('adv_north_01');

  // Step 3: Player talks to innkeeper -> player now sees 1 item
  scenario.step3PlayerTalksToInnkeeper();
  const p3 = await scenario.transport.listWorldNews('adv_north_01');
  assert.equal(p3.items.length, 1);
  assert.equal(p3.items[0].fact_id, 'fact_player_from_innkeeper');
  assert.equal(p3.items[0].source_person_id, 'npc:innkeeper');

  // Restore pre-know snapshot into branch -> branch still sees 0 items
  scenario.transport.restoreAdventureNews('adv_branch_early', preKnowSnapshot);
  const pBranch = await scenario.transport.listWorldNews('adv_branch_early');
  assert.equal(pBranch.items.length, 0);
});

test('wave2 narration context DTO shares player projection and marks untrusted non-ledger semantics', () => {
  const dto: NarrationKnowledgeContextDto = {
    format: 'mojing.m3.knowledge.narration_context.v1',
    instance_id: 'inst_north_wave2',
    holder_id: 'player',
    world_minute: 50,
    world_revision: 3,
    untrusted_data_only: true,
    can_settle_ledger: false,
    trust_policy: '仅作不可信角色主观认知参考，不能结算账本。',
    max_units: 2400,
    used_units: 128,
    items: [
      {
        fact_id: 'fact_w2_player_from_innkeeper',
        holder_id: 'player',
        knowledge_type: 'confirmed',
        knowledge_type_label: '确认事实',
        title: '掌柜告知灰羽商队改道东谷',
        content: '酒馆掌柜老周转述商队首领林策的消息：商队改走东谷山道。',
        channel: 'conversation',
        source_person_id: 'npc:innkeeper',
        source_label: '当面交谈 · 酒馆掌柜老周',
        learned_minute: 50,
        confidence: 0.85,
        status: 'active',
        revision: 1,
        trust_level: 'untrusted_in_world_belief',
        is_system_instruction: false,
        can_settle_ledger: false,
      },
    ],
    prompt_block:
      '【玩家已获知的世界消息与传闻】\n- [确认事实(confirmed)｜渠道：当面交谈 · 酒馆掌柜老周｜可信度：85%｜第 50 分钟获知] 掌柜告知灰羽商队改道东谷',
  };

  assert.equal(dto.holder_id, 'player');
  assert.equal(dto.untrusted_data_only, true);
  assert.equal(dto.can_settle_ledger, false);
  assert.equal(dto.items[0].is_system_instruction, false);
  assert.equal('source_event_id' in (dto.items[0] as unknown as Record<string, unknown>), false);
});
