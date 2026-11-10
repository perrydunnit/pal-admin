import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base '/static/' so the Flask app can serve the build from static/ without
// rewriting any asset URLs.
export default defineConfig({
  plugins: [react()],
  base: '/static/',
  build: { outDir: 'dist', emptyOutDir: true },
  server: {
    proxy: { '/api': 'http://127.0.0.1:8000', '/healthz': 'http://127.0.0.1:8000' },
  },
  test: { environment: 'node', include: ['src/**/*.test.js'] },
})
