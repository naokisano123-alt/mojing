import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
    // Windows 上后端测试/Python 依赖会写临时文件，避免 Vite 监听到 EBUSY
    watch: {
      ignored: [
        '**/server/**',
        '**/.venv/**',
        '**/.incoming/**',
        '**/.outgoing/**',
        '**/.npm-cache/**',
        '**/.pip-cache/**',
        '**/dist/**',
        '**/node_modules/**',
      ],
    },
  },
});