import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    port: Number(process.env.PORT) || 5174,
    strictPort: true,
    proxy: {
      // 前端请求 /api/xxx，开发时转发给本地 FastAPI（阶段1起在 8010 端口），并去掉 /api 前缀
      '/api': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
