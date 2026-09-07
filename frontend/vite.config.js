import { defineConfig } from 'vite' // bundler / `npm run dev`
import vue from '@vitejs/plugin-vue' // Vue 3 SFC compiler plugin

// /api is proxied to FastAPI (backend/) when that server is running.
export default defineConfig({
  plugins: [vue()],
  server: {
    host: true,
    port: 5173,
    allowedHosts: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
