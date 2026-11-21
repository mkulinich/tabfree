import react from "@vitejs/plugin-react";
import { defaultClientConditions, defineConfig } from "vite";

// The portal is served by the API (apps/api serves dist/ at /); `npm run dev -w @tabfree/portal`
// proxies the API on :8765 for development.
const api = "http://localhost:8765";

export default defineConfig({
  plugins: [react()],
  // Workspace packages (@tabfree/runtime, @tabfree/tablecalc) are bundled from their TypeScript sources.
  resolve: { conditions: ["source", ...defaultClientConditions] },
  build: { outDir: "dist", sourcemap: true, target: "es2022", emptyOutDir: true },
  server: { proxy: { "/api": api, "/auth": api, "/runtime": api } },
});
