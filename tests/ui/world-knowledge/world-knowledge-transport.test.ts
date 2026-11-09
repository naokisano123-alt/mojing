import test from 'node:test';
import assert from 'node:assert/strict';
import type {
  KnowledgeChannel,
  KnowledgeType,
  WorldNewsItem,
  WorldNewsPage,
} from '../../../src/features/world-knowledge/types.ts';

const TYPE_LABELS: Record<KnowledgeType, string> = {
  confirmed: '确认事实',
  suspected: '怀疑推测',
  rumor: '市井传闻',
  mistaken: '误信传言',
};

const CHANNEL_LABELS: Record<KnowledgeChannel, string> = {
  observed: '亲眼目睹',
  notice: '公开告示',
  conversation: '当面交谈',
  rumor: '道听途说',
};

test('world-knowledge types cover all 4 knowledge types and 4 channels', () => {
  assert.equal(TYPE_LABELS.confirmed, '确认事实');
  assert.equal(TYPE_LABELS.suspected, '怀疑推测');
  assert.equal(TYPE_LABELS.rumor, '市井传闻');
  assert.equal(TYPE_LABELS.mistaken, '误信传言');

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

  const page: WorldNewsPage = {
    items: [item],
    next_before: 'kcur1_p1',
  };

  assert.equal('source_event_id' in (item as unknown as Record<string, unknown>), false);
  assert.equal('total' in (page as unknown as Record<string, unknown>), false);
  assert.equal(page.items.length, 1);
  assert.equal(page.next_before, 'kcur1_p1');
});

test('checkpoint snapshot and branch restore isolate post-checkpoint player news', () => {
  const branchStore = new Map<string, WorldNewsItem[]>();
  branchStore.set('adv_main', []);

  // Snapshot before player talks to innkeeper
  const preKnowSnapshot = (branchStore.get('adv_main') ?? []).map(x => ({ ...x }));

  // Player learns news on main branch
  branchStore.set('adv_main', [
    {
      fact_id: 'fact_player_from_innkeeper',
      knowledge_type: 'confirmed',
      title: '掌柜告知灰羽商队改道东谷',
      content: '生铁推迟三日运达。',
      channel: 'conversation',
      source_label: '当面交谈 · 酒馆掌柜老周',
      learned_minute: 50,
      confidence: 0.85,
      revision: 1,
    },
  ]);

  // Restore pre-know snapshot into branch
  branchStore.set('adv_branch', preKnowSnapshot.map(x => ({ ...x })));

  assert.equal(branchStore.get('adv_main')?.length, 1);
  assert.equal(branchStore.get('adv_branch')?.length, 0);
});
