import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Build output is served as static files by docuagent.py, which keeps the Python
// runtime free of third-party dependencies. Relative base so the bundle works from
// any mount path.
export default defineConfig({
  base: "./",
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  build: {
    outDir: "../web-next",
    emptyOutDir: true,
  },
  server: {
    port: 3100,
    proxy: {
      "/api": "http://127.0.0.1:8765",
    },
  },
});
