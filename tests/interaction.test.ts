import test from 'node:test';
import assert from 'node:assert/strict';
import { appendSuggestion, resolveSwipe, swipeDirection } from '../src/interaction.ts';

test('left moves forward and right moves back through the four world pages', () => {
  assert.equal(resolveSwipe('story', 'left', 100, null).target, 'character');
  assert.equal(resolveSwipe('character', 'left', 100, null).target, 'atlas');
  assert.equal(resolveSwipe('atlas', 'left', 100, null).target, 'saves');
  assert.equal(resolveSwipe('saves', 'right', 100, null).target, 'atlas');
  assert.equal(resolveSwipe('character', 'right', 100, null).target, 'story');
  assert.equal(resolveSwipe('saves', 'left', 100, null).target, undefined);
});

test('two right swipes in the same boundary within two seconds are needed to exit', () => {
  const first = resolveSwipe('story', 'right', 100, null);
  assert.equal(first.target, undefined);
  assert.equal(first.hint, '再右滑一次退出该世界');
  const second = resolveSwipe('story', 'right', 1500, first.armed);
  assert.equal(second.target, 'home');
  const homeFirst = resolveSwipe('home', 'right', 1600, second.armed);
  assert.equal(homeFirst.target, undefined);
  assert.equal(resolveSwipe('home', 'right', 1700, homeFirst.armed).target, 'cover');
});

test('exit confirmation expires, cannot carry between pages or survive the opposite gesture', () => {
  const first = resolveSwipe('story', 'right', 100, null);
  assert.equal(resolveSwipe('story', 'right', 2100, first.armed).target, undefined);
  assert.equal(resolveSwipe('home', 'right', 200, first.armed).target, undefined);
  const opposite = resolveSwipe('story', 'left', 200, first.armed);
  assert.equal(opposite.armed, null);
  assert.equal(resolveSwipe('story', 'right', 300, opposite.armed).target, undefined);
});

test('vertical reading, tiny drags and slow selection are not page swipes', () => {
  assert.equal(swipeDirection(100, 10, 200), 'right');
  assert.equal(swipeDirection(-100, 10, 200), 'left');
  assert.equal(swipeDirection(10, 100, 200), null);
  assert.equal(swipeDirection(40, 0, 200), null);
  assert.equal(swipeDirection(100, 90, 200), null);
  assert.equal(swipeDirection(100, 0, 1100), null);
});

test('suggestions append without overwriting free text or truncating at the input limit', () => {
  assert.equal(appendSuggestion('', '询问工作'), '询问工作');
  assert.equal(appendSuggestion('先向店主打招呼。', '询问工作'), '先向店主打招呼。\n询问工作');
  assert.equal(appendSuggestion('先向店主打招呼。\n', '询问工作'), '先向店主打招呼。\n询问工作');
  assert.equal(appendSuggestion('a'.repeat(999), '长选项'), null);
});
