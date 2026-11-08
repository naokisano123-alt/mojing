import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// 组件测试配置（Vitest + jsdom + React Testing Library）。
// 与 node:test 分开：
//   npm test        → tests 目录下的 .test.ts（Node 原生测试，纯逻辑）
//   npm run test:ui → tests/ui 与 tests/integration 下的 .test.tsx（真实 React 渲染）
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['tests/ui/**/*.test.tsx', 'tests/integration/**/*.test.tsx'],
    setupFiles: ['tests/setup/jest-dom.ts'],
    restoreMocks: true,
    css: false,
  },
});