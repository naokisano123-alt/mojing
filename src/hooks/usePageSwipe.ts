import { useEffect, useRef, useState } from 'react';
import type { MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent } from 'react';
import type { Page } from '../model';
import { EXIT_WINDOW_MS, resolveSwipe, swipeDirection } from '../interaction';
import type { ExitArm, SwipeDirection } from '../interaction';

// Clickable cards and category tabs participate in swipes; only actual editors
// and explicitly protected surfaces own their gestures.
const BLOCKED = 'input, textarea, select, summary, [contenteditable="true"], [role="dialog"], [data-no-swipe]';
const selectionActive = () => !!window.getSelection()?.toString().trim();

type Start = { id: number; x: number; y: number; time: number; vertical: boolean; horizontal: boolean };

export function usePageSwipe(page: Page, navigate: (page: Page, direction: SwipeDirection) => void) {
  const start = useRef<Start | null>(null);
  const armed = useRef<ExitArm>(null);
  const suppressClickUntil = useRef(0);
  const timeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [hint, setHint] = useState('');
  const [offset, setOffset] = useState(0);

  function resetExit() {
    armed.current = null;
    setHint('');
    if (timeout.current) clearTimeout(timeout.current);
  }
  function cancel() { start.current = null; setOffset(0); resetExit(); }

  useEffect(() => { cancel(); }, [page]);
  useEffect(() => () => { if (timeout.current) clearTimeout(timeout.current); }, []);

  function onPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    suppressClickUntil.current = 0;
    if (start.current || !event.isPrimary) { cancel(); return; }
    const blocked = (event.target as Element).closest(BLOCKED);
    if (event.button !== 0 || blocked || selectionActive() || document.querySelector('[role="dialog"]')) {
      cancel(); return;
    }
    if (!['home', 'story', 'character', 'atlas', 'saves'].includes(page)) return;
    start.current = { id: event.pointerId, x: event.clientX, y: event.clientY, time: performance.now(), vertical: false, horizontal: false };
  }
  function onPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const gesture = start.current;
    if (!gesture || gesture.id !== event.pointerId) return;
    const dx = event.clientX - gesture.x, dy = event.clientY - gesture.y;
    if (!gesture.horizontal && Math.abs(dy) > 14 && Math.abs(dy) > Math.abs(dx)) gesture.vertical = true;
    if (gesture.vertical || selectionActive()) { setOffset(0); resetExit(); return; }
    if (Math.abs(dx) > 14 && Math.abs(dx) > Math.abs(dy) * 1.7) {
      gesture.horizontal = true;
      suppressClickUntil.current = performance.now() + 700;
      if (event.cancelable) event.preventDefault();
      setOffset(Math.max(-48, Math.min(48, dx * .2)));
    }
  }
  function onPointerUp(event: ReactPointerEvent<HTMLDivElement>) {
    const gesture = start.current;
    start.current = null;
    setOffset(0);
    if (!gesture || gesture.id !== event.pointerId || gesture.vertical || selectionActive()) { resetExit(); return; }
    if (gesture.horizontal) {
      // The browser can synthesize a click after pointerup. Consume it even
      // at a page boundary or after a drag too slow to count as navigation.
      suppressClickUntil.current = performance.now() + 700;
      event.preventDefault();
    }
    const direction = swipeDirection(event.clientX - gesture.x, event.clientY - gesture.y, performance.now() - gesture.time);
    if (!direction) { resetExit(); return; }
    const result = resolveSwipe(page, direction, performance.now(), armed.current);
    resetExit();
    armed.current = result.armed;
    if (result.hint) {
      setHint(result.hint);
      timeout.current = setTimeout(resetExit, EXIT_WINDOW_MS);
    }
    if (result.target) navigate(result.target, direction);
  }
  function onClickCapture(event: ReactMouseEvent<HTMLDivElement>) {
    // detail=0 is keyboard/assistive activation, which must remain usable.
    if (event.detail !== 0 && performance.now() < suppressClickUntil.current) {
      event.preventDefault();
      event.stopPropagation();
      suppressClickUntil.current = 0;
    }
  }
  return { bind: { onPointerDownCapture: onPointerDown, onPointerMoveCapture: onPointerMove, onPointerUpCapture: onPointerUp, onPointerCancel: cancel, onClickCapture }, hint, offset, cancel };
}
