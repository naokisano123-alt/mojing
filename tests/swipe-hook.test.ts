import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

// Exercise the actual hook event handlers with isolated refs and a minimal DOM.
// No browser or timing override is needed to reproduce a swipe starting on a card.
const fakeReact = 'export const useRef=v=>({current:v});export const useState=v=>[v,()=>{}];export const useEffect=()=>{};';
const reactUrl = `data:text/javascript;base64,${Buffer.from(fakeReact).toString('base64')}`;
const interactionUrl = new URL('../src/interaction.ts', import.meta.url).href;
const source = readFileSync(new URL('../src/hooks/usePageSwipe.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  .replace("from 'react'", `from '${reactUrl}'`).replace("from '../interaction'", `from '${interactionUrl}'`);
const { usePageSwipe } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
Object.assign(globalThis, { window: { getSelection: () => ({ toString: () => '' }) }, document: { querySelector: () => null } });
function event(x: number, target: string) {
  return { pointerId: 1, isPrimary: true, button: 0, clientX: x, clientY: 10, cancelable: true,
    target: { closest: (selector: string) => selector.split(',').map(s=>s.trim()).includes(target) ? {} : null }, preventDefault() {} };
}
for (const target of ['button', '.tabs', 'svg', 'div']) {
  test(`horizontal swipe starting on ${target} changes page without activating a control`, () => {
    const pages: string[] = [];
    const { bind } = usePageSwipe('atlas', (p: string) => pages.push(p));
    bind.onPointerDownCapture(event(250, target));
    bind.onPointerMoveCapture(event(120, target));
    bind.onPointerUpCapture(event(100, target));
    assert.deepEqual(pages, ['saves']);
    let prevented = false, stopped = false;
    bind.onClickCapture({ detail: 1, preventDefault() { prevented = true; }, stopPropagation() { stopped = true; } });
    assert.equal(prevented && stopped, true);
  });
}
test('a short tap on a card still activates its click', () => {
  const { bind } = usePageSwipe('atlas', () => assert.fail('tap must not navigate'));
  bind.onPointerDownCapture(event(100, 'button')); bind.onPointerUpCapture(event(102, 'button'));
  bind.onClickCapture({ detail: 1, preventDefault() { assert.fail('tap must not be consumed'); }, stopPropagation() {} });
});
test('editing fields retain their own gestures', () => {
  const { bind } = usePageSwipe('atlas', () => assert.fail('input must not navigate'));
  bind.onPointerDownCapture(event(250, 'input')); bind.onPointerMoveCapture(event(100, 'input')); bind.onPointerUpCapture(event(100, 'input'));
});
