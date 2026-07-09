import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 固定 IPv4，避免只监听 [::1] 导致 127.0.0.1 打不开
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    // 开发期 /api → 后端，前端可继续用默认 VITE_API_BASE_URL=/api
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
