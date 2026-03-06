import { test, expect } from '@playwright/test';

test.describe('Search', () => {
  test('search page renders with query param', async ({ page }) => {
    await page.goto('/search?q=cake');

    // Page should render without error
    await expect(page.locator('body')).toBeVisible();

    // Results section or heading should appear
    const resultsArea = page.locator('main');
    await expect(resultsArea).toBeVisible();
  });

  test('search page shows results heading for common query', async ({ page }) => {
    await page.goto('/search?q=cake');
    await page.waitForLoadState('networkidle');

    // Should show a results or search heading
    const heading = page.locator('h1, h2').filter({ hasText: /cake|result|search/i });
    await expect(heading.first()).toBeVisible({ timeout: 10_000 });
  });

  test('search page renders empty state for unlikely query', async ({ page }) => {
    await page.goto('/search?q=xyzzy99999notaproduct');
    await page.waitForLoadState('networkidle');

    // Should show empty state message
    const emptyState = page.locator('body').filter({
      hasText: /no results|not found|nothing here|no products/i,
    });
    await expect(emptyState).toBeVisible({ timeout: 10_000 });
  });

  test('search page without query shows search prompt', async ({ page }) => {
    await page.goto('/search');
    await page.waitForLoadState('networkidle');

    await expect(page.locator('main')).toBeVisible();
  });
});
