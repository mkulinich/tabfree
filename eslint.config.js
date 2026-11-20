import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [
      "**/dist/**",
      "**/node_modules/**",
      "**/coverage/**",
      ".venv/**",
      "corpus/**",
      "third_party/**",
      "infra/**",
      "docs/**",
      "packages/schemas/**",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.strict,
);
