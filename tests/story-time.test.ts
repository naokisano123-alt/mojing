import test from 'node:test';
import assert from 'node:assert/strict';
import { formatStoryTime, elapsedAt } from '../src/features/adventures/storyTime.ts';

test('reading time uses confirmed calendar days and coarse periods, independent of device timezone', () => {
  const start = '1000-01-01T08:00';
  assert.equal(formatStoryTime(start, 0), '第 1 天 · 上午');
  assert.equal(formatStoryTime(start, 240), '第 1 天 · 下午');
  assert.equal(formatStoryTime(start, 600), '第 1 天 · 夜晚');
  assert.equal(formatStoryTime(start, 960), '第 2 天 · 夜晚');
  assert.equal(formatStoryTime(start, 1440), '第 2 天 · 上午');
  assert.equal(elapsedAt(start, '1000-01-02T12:00'), 1680);
  assert.equal(formatStoryTime(start, 1680), '第 2 天 · 下午');
  assert.equal(formatStoryTime(undefined, 1440), '第 2 天');
});
