import { describe, it, expect, vi, afterEach } from 'vitest';

/**
 * The longer TTLs are keyed on the purge secret, so a deployment that has not
 * been given it behaves exactly as before: nothing can purge it, so nothing may
 * be held longer than the minute that used to be the whole mechanism.
 */

async function policy() {
  vi.resetModules();
  return import('./cache-policy');
}

describe('cache policy', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('keeps today\'s TTLs when the storefront cannot be purged', async () => {
    vi.stubEnv('STOREFRONT_REVALIDATE_SECRET', '');
    const p = await policy();
    expect(p.ON_DEMAND_PURGE).toBe(false);
    expect(p.CONTENT_TTL).toBe(60);
    expect(p.BLOG_TTL).toBe(300);
    expect(p.LANGUAGES_TTL).toBe(300);
    expect(p.UNPURGED_TTL).toBe(60);
  });

  it('lengthens only purged content once the API can purge it', async () => {
    vi.stubEnv('STOREFRONT_REVALIDATE_SECRET', 'shared');
    const p = await policy();
    expect(p.ON_DEMAND_PURGE).toBe(true);
    expect(p.CONTENT_TTL).toBe(600);
    expect(p.BLOG_TTL).toBe(3600);
    expect(p.LANGUAGES_TTL).toBe(3600);
    // Today's opening hours turn over at midnight with nothing to purge them.
    expect(p.UNPURGED_TTL).toBe(60);
  });

  it('never holds purged content past the route-segment ceiling', async () => {
    vi.stubEnv('STOREFRONT_REVALIDATE_SECRET', 'shared');
    const p = await policy();
    // `export const revalidate = 600` in the product and blog-post routes.
    expect(p.CONTENT_TTL).toBeLessThanOrEqual(600);
  });
});
