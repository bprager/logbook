import path from "node:path"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { defineConfig } from "vite"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    outDir: "../../src/logbook/static/watch",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/observer": "http://127.0.0.1:8790",
    },
  },
})
