import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

// 模拟“局域网 http 打开”的非安全上下文：没有 crypto.randomUUID
let counter = 0;
const insecureCrypto = {
  getRandomValues: (array: Uint8Array) => {
    counter += 1;
    for (let i = 0; i < array.length; i += 1) array[i] = (counter * 31 + i) % 256;
    return array;
  },
};

describe('App 启动（非安全上下文）', () => {
  beforeEach(() => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }));
    vi.stubGlobal('crypto', insecureCrypto);
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
  });

  it('没有 crypto.randomUUID 也能渲染首页，而不是白屏', async () => {
    const { default: App } = await import('../../src/App');
    render(<App />);
    expect(await screen.findByText(/你的世界，未完待续/)).toBeTruthy();
    expect(screen.getByText(/雾港来信/)).toBeTruthy();
  });
});