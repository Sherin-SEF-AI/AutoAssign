import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const target = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target, changeOrigin: true },
      "/healthz": { target, changeOrigin: true },
      "/readyz": { target, changeOrigin: true },
    },
  },
  build: {
    sourcemap: true,
    chunkSizeWarningLimit: 1500,
  },
});
