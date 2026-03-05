module.exports = {
  // Run vitest in the correct app directory (function = no file args appended)
  'apps/web/**/*.{ts,tsx}': () => 'pnpm --filter web test',
  'apps/admin/**/*.{ts,tsx}': () => 'pnpm --filter admin test',
  'packages/**/*.{ts,tsx}': () => 'pnpm --filter @mm/types test',
  // Python: run ruff with the staged files
  'apps/api/**/*.py': ['ruff check --fix', 'ruff format'],
};
