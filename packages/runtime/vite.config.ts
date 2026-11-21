import { resolve } from "node:path";
import { defaultClientConditions, defineConfig } from "vite";

// Two bundles: the frame runtime (one self-contained IIFE, inlined into each dashboard's srcdoc
// because the frame cannot load anything) and the host bridge (an ES module for the portal).
const target = process.env.TABFREE_BUNDLE === "host" ? "host" : "frame";

export default defineConfig({
  // Bundle workspace packages (@tabfree/tablecalc) from their TypeScript sources.
  resolve: { conditions: ["source", ...defaultClientConditions] },
  build: {
    outDir: "dist/bundle",
    emptyOutDir: false,
    sourcemap: true,
    target: "es2022",
    lib:
      target === "frame"
        ? { entry: resolve(import.meta.dirname, "src/frame.ts"), formats: ["iife"], name: "TabfreeRuntime", fileName: () => "frame.js" }
        : { entry: resolve(import.meta.dirname, "src/host.ts"), formats: ["es"], fileName: () => "host.js" },
  },
});
