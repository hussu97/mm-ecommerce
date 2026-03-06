import { test, expect } from '@playwright/test';

test.describe('Browse to Cart', () => {
  test('homepage loads and shows featured products', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/Melting Moments/i);

    // Featured products section should be visible
    const featured = page.getByRole('heading', { name: /featured/i });
    await expect(featured).toBeVisible();
  });

  test('navigating to a category shows products', async ({ page }) => {
    await page.goto('/cakes');

    // Category page should render a product grid
    const productCards = page.locator('[data-testid="product-card"], article, .product-card');
    await expect(productCards.first()).toBeVisible({ timeout: 10_000 });
  });

  test('product detail page renders from category', async ({ page }) => {
    await page.goto('/');

    // Click the first product link on the page
    const firstProductLink = page.locator('a[href*="/"][href$=""]').filter({ hasText: /aed/i }).first();
    const productLinks = page.locator('a').filter({ hasText: /view|shop|order/i });

    // Navigate directly to a known-good path shape
    await page.goto('/cakes');
    await page.waitForLoadState('networkidle');

    const productLink = page.locator('a[href^="/cakes/"]').first();
    const href = await productLink.getAttribute('href');
    if (href) {
      await page.goto(href);
      await expect(page.locator('h1')).toBeVisible();
    }
  });

  test('add to cart button is present on product detail page', async ({ page }) => {
    await page.goto('/cakes');
    await page.waitForLoadState('networkidle');

    const productLink = page.locator('a[href^="/cakes/"]').first();
    const href = await productLink.getAttribute('href');

    if (href) {
      await page.goto(href);
      const addToCartBtn = page.getByRole('button', { name: /add to cart|order on whatsapp/i });
      await expect(addToCartBtn.first()).toBeVisible();
    }
  });
});
