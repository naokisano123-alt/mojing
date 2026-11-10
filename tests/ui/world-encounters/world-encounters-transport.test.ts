import test from 'node:test';
import assert from 'node:assert/strict';
import {
  KNOWLEDGE_TYPE_LABELS,
  OPTION_KIND_LABELS,
  OUTCOME_LABELS,
  applyEncounterOptionToComposerDraft,
  createInnkeeperCaravanEncounterScenario,
  sanitizeEncounterEntrypointBundle,
} from '../../../src/features/world-encounters/api.ts';

test('world-encounters labels and option composer helper preserve clock and non-auto-commit invariants', () => {
  assert.equal(OPTION_KIND_LABELS.ask_npc, '当面询问人物');
  assert.equal(OPTION_KIND_LABELS.check_notice, '查阅本地公告');
  assert.equal(OPTION_KIND_LABELS.free_edit, '自由编辑行动');
  assert.equal(OUTCOME_LABELS.clue, '获得线索');
  assert.equal(OUTCOME_LABELS.conflicting, '说法冲突');
  assert.equal(KNOWLEDGE_TYPE_LABELS.mistaken, '误信传言');

  const applied = applyEncounterOptionToComposerDraft('', {
    suggested_action_text: '在霜木镇向玛塔老板娘当面打听灰鬃商队改道的实情。',
    contactable: true,
  });
  assert.equal(applied.error, null);
  assert.equal(applied.advancesClock, false);
  assert.equal(applied.autoCommitsTurn, false);
  assert.match(applied.nextDraft ?? '', /玛塔老板娘/);

  const departedApplied = applyEncounterOptionToComposerDraft('', {
    suggested_action_text: '寻找韩掌柜',
    contactable: false,
  });
  assert.equal(departedApplied.nextDraft, null);
  assert.match(departedApplied.error ?? '', /不在场|动身离开/);
});

test('innkeeper caravan scenario: option -> edit action -> draft -> commit -> message full loop & branch isolation', async () => {
  const { adventureId, transport } = createInnkeeperCaravanEncounterScenario('adv_north_01');

  // 1. Read entrypoint bundle: 1 initial rumor, world_minute = 240
  const bundle0 = await transport.getEntrypointBundle(adventureId);
  const clean0 = sanitizeEncounterEntrypointBundle(bundle0);
  assert.equal(clean0.world_minute, 240);
  assert.equal(clean0.advances_clock, false);
  assert.equal(clean0.auto_commits_turn, false);
  assert.equal(clean0.news_items.length, 1);

  const rumorId = clean0.news_items[0].fact_id;
  const options = clean0.options_by_fact_id[rumorId];
  const askMartaOpt = options.find(o => o.target_npc_id === 'npc_marta_innkeeper');
  assert.ok(askMartaOpt);
  assert.equal(askMartaOpt.contactable, true);
  assert.equal(askMartaOpt.advances_clock, false);

  // 2. Write suggestion to composer and edit action text (clock still 240)
  const draftStep = applyEncounterOptionToComposerDraft('', askMartaOpt);
  const editedAction = `${draftStep.nextDraft}顺便请教白河谷地水陆联运需耗时几日。`;

  // Snapshot before committing turn for branch isolation test
  const preCommitBranchSnap = transport.snapshotBranch(adventureId);

  // 3. Prepare draft: clock_advanced is false, committed is false, news_items still 1
  const draft = await transport.prepareDraft(adventureId, {
    fact_id: rumorId,
    option_kind: 'ask_npc',
    target_npc_id: 'npc_marta_innkeeper',
    action_text: editedAction,
  });
  assert.equal(draft.outcome, 'clue');
  assert.equal(draft.clock_advanced, false);
  assert.equal(draft.committed, false);
  assert.equal(draft.can_settle_ledger, false);
  assert.equal(draft.proposed_transmissions.length, 1);
  assert.equal((await transport.getEntrypointBundle(adventureId)).news_items.length, 1);

  // 4. Commit draft: world_minute advances on turn commit, new message appears in player news
  const committed = await transport.commitDraft(adventureId, draft.draft_id, {
    commit_world_minute: 250,
  });
  assert.equal(committed.committed, true);
  assert.equal(committed.ledger_modified, false);
  assert.equal(committed.world_minute, 250);
  assert.equal(committed.all_player_news.length, 2);
  assert.equal(committed.new_messages[0].knowledge_type, 'confirmed');
  assert.equal(committed.new_messages[0].source_person_id, 'npc_marta_innkeeper');

  // 5. Branch isolation: restored pre-commit branch still has only 1 rumor at minute 240
  transport.restoreBranch('adv_branch_early', preCommitBranchSnap);
  const branchBundle = await transport.getEntrypointBundle('adv_branch_early');
  assert.equal(branchBundle.world_minute, 240);
  assert.equal(branchBundle.news_items.length, 1);
});

test('npc unknown, departed en route, departed before commit, and conflicting accounts', async () => {
  const { adventureId, transport } = createInnkeeperCaravanEncounterScenario('adv_north_02');
  const rumorId = 'fact_player_initial_rumor';

  // 1. NPC unknown (雷恩队长 does not know caravan reroute details)
  const unknownDraft = await transport.prepareDraft(adventureId, {
    fact_id: rumorId,
    option_kind: 'ask_npc',
    target_npc_id: 'npc_renn_captain',
    action_text: '向雷恩队长打听商队动向。',
  });
  assert.equal(unknownDraft.outcome, 'unknown');
  assert.equal(unknownDraft.reason_code, 'NPC_HAS_NO_KNOWLEDGE');
  assert.equal(unknownDraft.proposed_transmissions.length, 0);

  // 2. NPC departed en route (韩掌柜 left for Cold Iron City)
  const departedDraft = await transport.prepareDraft(adventureId, {
    fact_id: rumorId,
    option_kind: 'ask_npc',
    target_npc_id: 'npc_han_shopkeeper',
    action_text: '去铁料铺寻找韩掌柜。',
  });
  assert.equal(departedDraft.outcome, 'npc_departed');
  assert.equal(departedDraft.reason_code, 'NPC_LEFT_FOR_TRAVEL');
  assert.equal(departedDraft.proposed_transmissions.length, 0);

  // 3. Conflicting accounts: first commit 老巴克's mistaken belief, then ask 玛塔老板娘
  const buckDraft = await transport.prepareDraft(adventureId, {
    fact_id: rumorId,
    option_kind: 'ask_npc',
    target_npc_id: 'npc_buck_porter',
    action_text: '向老巴克询问货栈传言。',
  });
  await transport.commitDraft(adventureId, buckDraft.draft_id, { commit_world_minute: 245 });

  const conflictDraft = await transport.prepareDraft(adventureId, {
    fact_id: rumorId,
    option_kind: 'ask_npc',
    target_npc_id: 'npc_marta_innkeeper',
    action_text: '拿老巴克的说法向玛塔老板娘求证。',
  });
  assert.equal(conflictDraft.outcome, 'conflicting');
  assert.equal(conflictDraft.reason_code, 'CONFLICTING_ACCOUNTS_DETECTED');
  assert.ok(conflictDraft.conflict_note);

  // 4. If 玛塔老板娘 leaves on travel before commitDraft is called, commitDraft rejects with NPC_DEPARTED_BEFORE_COMMIT
  transport.updateNpcState(adventureId, 'npc_marta_innkeeper', {
    location: '白河城',
    current_action_kind: 'travel',
    contactable: false,
  });
  await assert.rejects(
    () => transport.commitDraft(adventureId, conflictDraft.draft_id, { commit_world_minute: 255 }),
    /NPC_DEPARTED_BEFORE_COMMIT/,
  );
});
