import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5175,
    strictPort: true,
    watch: {
      // E 盘为 exFAT，文件事件不可靠，改用轮询监听
      usePolling: true,
      interval: 500,
    },
  },
})
