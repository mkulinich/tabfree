import { defaultClientConditions, defaultServerConditions } from "vite";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // Resolve workspace packages (@tabfree/tablecalc) to their TypeScript sources: no build needed.
  resolve: { conditions: ["source", ...defaultClientConditions] },
  ssr: { resolve: { conditions: ["source", ...defaultServerConditions] } },
  test: {
    environment: "happy-dom",
  },
});
