import { describe, it, expect } from 'vitest';
import { categorySlugOf, isLiveLink, liveSlugSet, productPathOf } from './category-links';

/**
 * Hiding every product in a category stopped the API returning that category,
 * which retired its tile — and left a full-width promo band on the home page
 * still inviting customers to shop it. The band carried a hand-typed URL and
 * nothing tied that URL back to the catalogue. This is that tie.
 *
 * Rewritten for migration 120. Category slugs used to be `cat-`-prefixed, so
 * "is this a category link" was a regex; now `/brownies` and `/about` are the
 * same shape and the answer comes from the route table instead.
 */

const LIVE = liveSlugSet([{ slug: 'cookies' }, { slug: 'brownies' }]);

describe('categorySlugOf', () => {
  it('recognises a category route', () => {
    expect(categorySlugOf('/desserts')).toBe('desserts');
    expect(categorySlugOf('/en/desserts')).toBe('desserts');
    expect(categorySlugOf('/ar/desserts')).toBe('desserts');
  });

  it('sees through the ways a human types a URL', () => {
    // Each of these is the same dead link, and matching only the tidy form
    // would leave exactly the hand-authored cases unguarded.
    expect(categorySlugOf('/desserts/')).toBe('desserts');
    expect(categorySlugOf('/desserts?utm_source=ig')).toBe('desserts');
    expect(categorySlugOf('/desserts#top')).toBe('desserts');
    expect(categorySlugOf('/EN/Desserts')).toBe('desserts');
  });

  /**
   * The reserved list doing its job. Without it every one of these would read
   * as a category, fail the liveness check below, and disappear from the home
   * page — a hero slide pointing at the blog would silently stop rendering.
   */
  it('knows a storefront page from a category', () => {
    for (const href of ['/about', '/all-products', '/blog', '/faq', '/en/about', '/ar/search']) {
      expect(categorySlugOf(href), href).toBeNull();
    }
  });

  /**
   * A product link is not a category link. Reading its last segment as a slug
   * would judge `classic-brownie` against the category list and drop a link to
   * a page that is perfectly alive.
   */
  it('has no opinion about anything deeper than one segment', () => {
    for (const href of ['/en/brownies/classic-brownie', '/brownies/classic-brownie', '/en/blog/post']) {
      expect(categorySlugOf(href), href).toBeNull();
    }
  });

  it('has no opinion about a link that leaves the site', () => {
    for (const href of ['https://instagram.com/x', 'https://wa.me/971503687757', 'mailto:hi@x.com']) {
      expect(categorySlugOf(href), href).toBeNull();
    }
  });

  it('treats a missing link as not-a-category', () => {
    expect(categorySlugOf(undefined)).toBeNull();
    expect(categorySlugOf('')).toBeNull();
  });
});

describe('isLiveLink', () => {
  it('keeps a link to a category the storefront is serving', () => {
    expect(isLiveLink('/cookies', LIVE)).toBe(true);
    expect(isLiveLink('/en/brownies', LIVE)).toBe(true);
  });

  it('drops a link to a category that has gone', () => {
    expect(isLiveLink('/desserts', LIVE)).toBe(false);
    expect(isLiveLink('/en/desserts', LIVE)).toBe(false);
  });

  /**
   * The old `cat-` slugs are exactly this case now: after the rename nothing
   * serves `/cat-brownies`, so a hero slide still pointing there is a dead end
   * and stops rendering. The redirect table catches anyone who follows an old
   * link from outside; this stops us publishing new ones.
   */
  it('drops a link still using a pre-rename slug', () => {
    expect(isLiveLink('/cat-brownies', LIVE)).toBe(false);
  });

  it('leaves non-category links alone', () => {
    // We know nothing about these destinations and must not invent an opinion.
    expect(isLiveLink('/all-products', LIVE)).toBe(true);
    expect(isLiveLink('/about', LIVE)).toBe(true);
    expect(isLiveLink('/en/brownies/classic-brownie', LIVE)).toBe(true);
    expect(isLiveLink('https://instagram.com/x', LIVE)).toBe(true);
    expect(isLiveLink(undefined, LIVE)).toBe(true);
  });

  it('drops everything when no category is live', () => {
    expect(isLiveLink('/cookies', new Set())).toBe(false);
  });
});

describe('productPathOf', () => {
  it('returns the normalised /category/product path for a product link', () => {
    expect(productPathOf('/cookiemelt/lotus-cookie-melt')).toBe('/cookiemelt/lotus-cookie-melt');
    expect(productPathOf('/en/cookiemelt/lotus-cookie-melt')).toBe('/cookiemelt/lotus-cookie-melt');
    expect(productPathOf('/AR/CookieMelt/Lotus-Cookie-Melt')).toBe('/cookiemelt/lotus-cookie-melt');
    expect(productPathOf('/cookiemelt/lotus-cookie-melt/?utm=x#top')).toBe('/cookiemelt/lotus-cookie-melt');
  });

  it('is null for anything that is not a product page', () => {
    expect(productPathOf('/cookiemelt')).toBeNull(); // a category
    expect(productPathOf('/account/orders')).toBeNull(); // reserved first segment
    expect(productPathOf('/a/b/c')).toBeNull(); // too deep
    expect(productPathOf('https://instagram.com/x/y')).toBeNull(); // external
    expect(productPathOf(undefined)).toBeNull();
  });
});

describe('isLiveLink with product gating', () => {
  const LIVE_PRODUCTS = new Set(['/cookiemelt/lotus-cookie-melt']);

  it('keeps a product slide only while its product is live', () => {
    expect(isLiveLink('/cookiemelt/lotus-cookie-melt', LIVE, LIVE_PRODUCTS)).toBe(true);
    expect(isLiveLink('/en/cookiemelt/lotus-cookie-melt', LIVE, LIVE_PRODUCTS)).toBe(true);
    expect(isLiveLink('/cookiemelt/retired-cookie-melt', LIVE, LIVE_PRODUCTS)).toBe(false);
  });

  it('still lets product links pass when no product set is supplied', () => {
    expect(isLiveLink('/cookiemelt/lotus-cookie-melt', LIVE)).toBe(true);
  });

  it('still gates categories the same way alongside product gating', () => {
    expect(isLiveLink('/cookies', LIVE, LIVE_PRODUCTS)).toBe(true);
    expect(isLiveLink('/desserts', LIVE, LIVE_PRODUCTS)).toBe(false);
  });
});
