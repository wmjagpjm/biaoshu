/**
 * 模块：Vite 前端构建与开发服配置
 * 用途：React 插件、IPv4 固定端口、/api 反代后端；LAN 显式 bind 与 Host 白名单。
 * 对接：npm run dev；E2E 经 VITE_API_PROXY_TARGET 指向 8010；默认 8000 保持日用；
 *      LAN 由启动真源短暂注入 BIAOSHU_LISTEN_PROFILE / BIAOSHU_LAN_HOST。
 * 二次开发：LAN 禁止 0.0.0.0/通配 allowedHosts；LAN proxy target 必须恒回环；
 *         业务 API 前缀保持 /api。
 */
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const listenProfile = (process.env.BIAOSHU_LISTEN_PROFILE ?? 'loopback')
  .trim()
  .toLowerCase()
const lanHost = (process.env.BIAOSHU_LAN_HOST ?? '').trim()
const isLan = listenProfile === 'lan' && lanHost.length > 0

// LAN：忽略外部 VITE_API_PROXY_TARGET，强制回环；loopback/E2E 保留 8010 覆盖
const apiProxyTarget = isLan
  ? 'http://127.0.0.1:8000'
  : process.env.VITE_API_PROXY_TARGET?.trim() || 'http://127.0.0.1:8000'

const serverHost = isLan ? lanHost : '127.0.0.1'
// LAN：有限精确 Host 白名单；默认 loopback 不设通配
const allowedHosts = isLan
  ? [lanHost, '127.0.0.1', 'localhost']
  : undefined

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 默认固定 IPv4 回环；LAN 精确绑定用户显式 RFC1918
    host: serverHost,
    port: 5173,
    strictPort: true,
    ...(allowedHosts ? { allowedHosts } : {}),
    // 开发期 /api → 后端，前端可继续用默认 VITE_API_BASE_URL=/api
    proxy: {
      '/api': {
        target: apiProxyTarget,
        changeOrigin: true,
      },
    },
  },
})
