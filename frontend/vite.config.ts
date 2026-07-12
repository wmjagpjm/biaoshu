/**
 * 模块：Vite 前端构建与开发服配置
 * 用途：React 插件、IPv4 固定端口、/api 反代后端。
 * 对接：npm run dev；E2E 经 VITE_API_PROXY_TARGET 指向 8010；默认 8000 保持日用。
 * 二次开发：勿在默认 target 写死非本机地址；业务 API 前缀保持 /api。
 */
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const apiProxyTarget =
  process.env.VITE_API_PROXY_TARGET?.trim() || 'http://127.0.0.1:8000'

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
        target: apiProxyTarget,
        changeOrigin: true,
      },
    },
  },
})
