import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      // In dev, proxy /api calls to the backend so you don't need CORS config
      '/api': 'http://localhost:8000',
    },
  },
})
