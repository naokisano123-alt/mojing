import type { Page } from './model.ts';

export const WORLD_PAGES: Page[] = ['story', 'character', 'atlas', 'saves'];
export const EXIT_WINDOW_MS = 2000;
export const ACTION_LIMIT = 1000;
export type SwipeDirection = 'left' | 'right';
export type ExitArm = { page: 'story' | 'home'; time: number } | null;

// Share the threshold rules between the gesture handler and behavioral checks.
export function swipeDirection(dx: number, dy: number, elapsed: number): SwipeDirection | null {
  if (elapsed > 900 || Math.abs(dx) < 58 || Math.abs(dx) < Math.abs(dy) * 1.7) return null;
  return dx > 0 ? 'right' : 'left';
}

export function resolveSwipe(page: Page, direction: SwipeDirection, now: number, armed: ExitArm): {
  target?: Page; armed: ExitArm; hint?: string;
} {
  if (direction === 'right' && (page === 'story' || page === 'home')) {
    if (armed?.page === page && now >= armed.time && now - armed.time < EXIT_WINDOW_MS) {
      return { target: page === 'story' ? 'home' : 'cover', armed: null };
    }
    return {
      armed: { page, time: now },
      hint: page === 'story' ? '再右滑一次退出该世界' : '再右滑一次返回封面',
    };
  }
  const index = WORLD_PAGES.indexOf(page);
  const next = index + (direction === 'left' ? 1 : -1);
  return { target: index >= 0 && next >= 0 && next < WORLD_PAGES.length ? WORLD_PAGES[next] : undefined, armed: null };
}

export function appendSuggestion(draft: string, suggestion: string): string | null {
  const value = draft ? `${draft}${draft.endsWith('\n') ? '' : '\n'}${suggestion}` : suggestion;
  return value.length <= ACTION_LIMIT ? value : null;
}
