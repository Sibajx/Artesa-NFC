import react, { reactCompilerPreset } from '@vitejs/plugin-react'
import babel from '@rolldown/plugin-babel'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    babel({ presets: [reactCompilerPreset()] })
  ],
  server: {
    // Local development against a local backend (admin/README.md). Without a
    // Cloudflare Access token the API answers 401 or 404; that is expected.
    proxy: { '/api/admin': 'http://127.0.0.1:8000', '/media': 'http://127.0.0.1:8000' },
  },
})
