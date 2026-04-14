module.exports = {
  // Lint then test when TS/TSX files change
  'apps/web/**/*.{ts,tsx}': [() => 'pnpm --filter web lint', () => 'pnpm --filter web test'],
  'apps/admin/**/*.{ts,tsx}': [() => 'pnpm --filter admin lint', () => 'pnpm --filter admin test'],
  'packages/**/*.{ts,tsx}': () => 'pnpm --filter @mm/types test',
  // Python: run ruff with the staged files
  'apps/api/**/*.py': ['ruff check --fix', 'ruff format'],
};
