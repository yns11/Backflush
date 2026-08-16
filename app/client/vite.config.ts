import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

/**
 * Le bundle est écrit directement dans `app/server/static` : FastAPI le sert
 * tel quel en production, sans étape de copie susceptible d'être oubliée.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    outDir: '../server/static',
    emptyOutDir: true,
    // Le nom des fichiers porte une empreinte : ils sont immuables et peuvent
    // être mis en cache sans limite par le navigateur.
    assetsDir: 'assets',
    sourcemap: false,
    chunkSizeWarningLimit: 700,
  },
  server: {
    port: 5173,
    // En développement, le frontend tourne sur Vite et l'API sur uvicorn :
    // le proxy évite toute configuration CORS côté navigateur.
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
