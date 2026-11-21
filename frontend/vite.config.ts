import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In development /api is proxied to FastAPI, so the frontend works without CORS setup.
// Override the target with VITE_API_PROXY (e.g. http://api:8000 in Docker Compose).
const backend = { target: process.env.VITE_API_PROXY ?? 'http://127.0.0.1:8000', changeOrigin: true };

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': backend,
      '/docs': backend,
      '/openapi.json': backend,
    },
  },
});
