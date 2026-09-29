// ArtesaNFC web — ESLint flat config.
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import { defineConfig } from "eslint/config";
import astro from "eslint-plugin-astro";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";

export default defineConfig(
  {
    ignores: [
      "dist/",
      ".astro/",
      "node_modules/",
      "playwright-report/",
      "test-results/",
      "reports/",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.strict,
  ...astro.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    plugins: { "jsx-a11y": jsxA11y, "react-hooks": reactHooks },
    languageOptions: { globals: { ...globals.browser } },
    rules: {
      ...jsxA11y.flatConfigs.strict.rules,
      ...reactHooks.configs.recommended.rules,
      // Lists styled with list-style:none lose their semantics in
      // Safari/VoiceOver; the explicit role="list" is deliberate.
      "jsx-a11y/no-redundant-roles": ["error", { ul: ["list"] }],
      // The token of /c/{token} and API payloads must never reach the
      // console; the only allowed channel is console.warn without data.
      "no-console": ["error", { allow: ["warn"] }],
    },
  },
  {
    files: ["scripts/**/*.mjs", "*.config.{js,mjs,ts}", "tests/**/*.ts"],
    languageOptions: { globals: { ...globals.node } },
    rules: { "no-console": "off" },
  },
);
