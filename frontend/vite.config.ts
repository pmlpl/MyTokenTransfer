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
    proxy: {
      // Phase 0-7：dev server 把 /api/* 与 /v1/* 转给 prod nginx 8085
      // 好处：不用动 prod 容器，浏览器同域无 CORS，SSE 流式响应也透传
      '/api': {
        target: 'http://127.0.0.1:8085',
        changeOrigin: true,
      },
      '/v1': {
        target: 'http://127.0.0.1:8085',
        changeOrigin: true,
        // SSE/流式响应需要
        ws: false,
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            // 不缓冲，让 chunked 透传
            delete proxyRes.headers['content-encoding']
          })
        },
      },
    },
  },
})
