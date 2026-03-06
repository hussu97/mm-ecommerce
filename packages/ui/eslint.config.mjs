import parser from "@typescript-eslint/parser";

/** @type {import('eslint').Linter.Config[]} */
const config = [
  {
    files: ["**/*.ts", "**/*.tsx"],
    languageOptions: { parser },
    rules: {
      "no-unused-vars": "off",
    },
  },
];

export default config;
