import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// react-map-gl/maplibre needs no special aliasing: import from
// 'react-map-gl/maplibre' and 'maplibre-gl/dist/maplibre-gl.css'. The only
// project-specific bits are the dev proxy (so the browser talks to one origin
// and CORS never matters in development) and NOT pre-bundling maplibre-gl (see optimizeDeps).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: process.env.VITE_BACKEND_URL ?? 'http://localhost:8000', changeOrigin: true },
    },
  },
  // maplibre-gl v6 loads its web worker as a separate ES module that imports a
  // sibling file. Vite's dependency pre-bundling relocates the main file but not
  // the worker, giving "Worker failed to load" and a blank map. Serve it as-is.
  optimizeDeps: { exclude: ['maplibre-gl'] },
  build: { chunkSizeWarningLimit: 1600 },
})
