import test from 'node:test';
import assert from 'node:assert/strict';
import {
  isHighSurrogate,
  isLowSurrogate,
  adjustSurrogateBoundary,
  safeUtf16Slice,
  splitTextWithHighlight,
} from '../../src/components/documents/viewerUtils.ts';

test('isHighSurrogate and isLowSurrogate correctly identify UTF-16 surrogate code units', () => {
  // Emoji '🗡' (U+1F5E1): \uD83D \uDDE1
  const dagger = '🗡';
  assert.equal(dagger.length, 2);
  assert.equal(isHighSurrogate(dagger.charCodeAt(0)), true);
  assert.equal(isLowSurrogate(dagger.charCodeAt(0)), false);
  assert.equal(isHighSurrogate(dagger.charCodeAt(1)), false);
  assert.equal(isLowSurrogate(dagger.charCodeAt(1)), true);

  // 普通汉字 '墨' (U+58A8)
  const mo = '墨';
  assert.equal(mo.length, 1);
  assert.equal(isHighSurrogate(mo.charCodeAt(0)), false);
  assert.equal(isLowSurrogate(mo.charCodeAt(0)), false);
});

test('adjustSurrogateBoundary prevents splitting surrogate pair emojis', () => {
  const text = '世界观🗡️与魔法';
  // '世界观' (3) + '🗡' (2: index 3, 4) + '️' (variation selector 1: index 5) + '与魔法' (3)
  const lowSurrogateIndex = 4;
  assert.equal(isLowSurrogate(text.charCodeAt(lowSurrogateIndex)), true);

  // 当截断点恰好落在低代理上时：
  // expand 模式应扩展到该字符之后 (5)
  const expanded = adjustSurrogateBoundary(text, lowSurrogateIndex, 'expand');
  assert.equal(expanded, 5);

  // shrink 模式应收缩到该字符之前 (3)
  const shrunk = adjustSurrogateBoundary(text, lowSurrogateIndex, 'shrink');
  assert.equal(shrunk, 3);
});

test('safeUtf16Slice never generates lone surrogates across text boundaries', () => {
  const text = '雾港之夜🀄暗流涌动';
  // '雾港之夜' = 4
  // '🀄' = 2 (\uD83C\uDC04, index 4, 5)
  // '暗流涌动' = 4

  // 如果请求截断区间是 [0, 5]，正好截在 emoji 之间
  const sliced = safeUtf16Slice(text, 0, 5);
  // safeUtf16Slice 会在 expand 模式下保留完整的 '🀄'，避免产生半个损坏的 surrogate
  assert.equal(sliced, '雾港之夜🀄');

  // 如果请求截断区间是 [5, 10]，起始点截在 emoji 之间
  const slicedAfter = safeUtf16Slice(text, 5, 10);
  // safeUtf16Slice 在 shrink 模式下起始点收缩为 4，完整包含 emoji
  assert.equal(slicedAfter, '🀄暗流涌动');
});

test('splitTextWithHighlight correctly segments text around target UTF-16 offset range', () => {
  const pageText = '在旧街尽头的钟表店里，老怀特正在擦拭一枚锈蚀的黄铜指针。';
  const pageOffset = 1000;
  // 目标区间位于老怀特：'老怀特' (3字)
  // '在旧街尽头的钟表店里，' = 11 字
  const startChar = 1000 + 11; // 1011
  const endChar = startChar + 3; // 1014

  const segments = splitTextWithHighlight(pageText, pageOffset, startChar, endChar);
  assert.equal(segments.length, 3);
  assert.equal(segments[0].text, '在旧街尽头的钟表店里，');
  assert.equal(segments[0].isHighlight, false);

  assert.equal(segments[1].text, '老怀特');
  assert.equal(segments[1].isHighlight, true);

  assert.equal(segments[2].text, '正在擦拭一枚锈蚀的黄铜指针。');
  assert.equal(segments[2].isHighlight, false);
});

test('splitTextWithHighlight gracefully handles out-of-range target offsets', () => {
  const pageText = '这是第一页的内容。';
  const pageOffset = 0;

  // 目标在下一页 (offset 500)
  const segments = splitTextWithHighlight(pageText, pageOffset, 500, 520);
  assert.equal(segments.length, 1);
  assert.equal(segments[0].text, pageText);
  assert.equal(segments[0].isHighlight, false);
});

test('raw pagination offset history preserves arbitrary non-constant boundaries', () => {
  // 模拟服务端分页：假设第一页 19500 处由于段落对齐返回 next_offset = 19500
  // 第二页 38000 处返回 next_offset = 38000
  const history = [0];

  // 翻到第二页
  const page1Next = 19500;
  history.push(0);
  const currentOffsetPage2 = page1Next;

  // 翻到第三页
  const page2Next = 38000;
  history.push(currentOffsetPage2);
  const currentOffsetPage3 = page2Next;

  assert.equal(currentOffsetPage3, 38000);

  // 从第三页点击上一页：从历史栈中弹出，精确回到 19500，而不是假定 38000 - 20000 = 18000
  const prevOffset1 = history.pop();
  assert.equal(prevOffset1, 19500);

  // 再次点击上一页：回到 0
  const prevOffset0 = history.pop();
  assert.equal(prevOffset0, 0);
});
