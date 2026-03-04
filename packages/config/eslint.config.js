// Shared ESLint config — apps extend this
// Usage in app eslint.config.mjs:
//   import baseConfig from "@mm/config/eslint"
//   export default [...baseConfig]

/** @type {import('eslint').Linter.Config[]} */
const config = [
  {
    rules: {
      "no-console": ["warn", { allow: ["warn", "error"] }],
      "no-unused-vars": "off", // handled by TypeScript
    },
  },
];

export default config;
