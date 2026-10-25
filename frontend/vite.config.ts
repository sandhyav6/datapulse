import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// In development, /api/* is proxied to the deployed site (CloudFront) or to the local API
// (backend/scripts/local_api.py). Set VITE_API_PROXY in frontend/.env.local. In production the
// site and the API share one CloudFront domain, so the browser never makes a cross-origin call.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.VITE_API_PROXY || 'http://localhost:8000'
  return {
    plugins: [react(), tailwindcss()],
    server: {
      proxy: { '/api': { target, changeOrigin: true, secure: true } },
    },
    build: { sourcemap: false, chunkSizeWarningLimit: 900 },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      css: false,
    },
  }
})
