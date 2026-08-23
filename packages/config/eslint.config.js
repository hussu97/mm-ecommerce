// The shared Next.js lint setup for `apps/web` and `apps/admin`.
//
// Both apps used to build this list themselves, identically — except that web
// carried the comment explaining the one rule override and admin carried only
// the override. The rationale lived in one app and the rule in both, which is
// how a reader concludes the second one is arbitrary and deletes it.
//
// Usage:
//   import base from "@mm/config/eslint";
//   export default base;

import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

/** @type {import('eslint').Linter.Config[]} */
const config = [
  ...coreWebVitals,
  ...typescript,
  {
    rules: {
      // react-hooks v7 added this rule but it flags intentional patterns like
      // setMounted(true) in effects (SSR hydration safety) and animation state
      // transitions. Downgrade to warn so CI doesn't hard-fail on valid code.
      "react-hooks/set-state-in-effect": "warn",
      "no-console": ["warn", { allow: ["warn", "error"] }],
      // Handled by TypeScript, which sees the types this rule cannot.
      "no-unused-vars": "off",
    },
  },
  {
    // Command-line tools and end-to-end specs, where stdout is the interface
    // rather than a leftover debug line.
    files: ["scripts/**", "e2e/**", "*.config.{js,mjs,ts}"],
    rules: {
      "no-console": "off",
    },
  },
];

export default config;
