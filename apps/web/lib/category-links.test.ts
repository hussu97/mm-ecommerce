import { describe, it, expect } from 'vitest';
import { categorySlugOf, isLiveLink, liveSlugSet } from './category-links';

/**
 * Hiding every product in a category stopped the API returning that category,
 * which retired its tile — and left a full-width promo band on the home page
 * still inviting customers to shop it. The band carried a hand-typed URL and
 * nothing tied that URL back to the catalogue. This is that tie.
 */

const LIVE = liveSlugSet([{ slug: 'cat-cookies' }, { slug: 'cat-brownies' }]);

describe('categorySlugOf', () => {
  it('recognises a category route', () => {
    expect(categorySlugOf('/cat-desserts')).toBe('cat-desserts');
    expect(categorySlugOf('/en/cat-desserts')).toBe('cat-desserts');
  });

  it('sees through the ways a human types a URL', () => {
    // Each of these is the same dead link, and matching only the tidy form
    // would leave exactly the hand-authored cases unguarded.
    expect(categorySlugOf('/cat-desserts/')).toBe('cat-desserts');
    expect(categorySlugOf('/cat-desserts?utm_source=ig')).toBe('cat-desserts');
    expect(categorySlugOf('/cat-desserts#top')).toBe('cat-desserts');
    expect(categorySlugOf('/EN/Cat-Desserts')).toBe('cat-desserts');
  });

  it('has no opinion about links that are not categories', () => {
    for (const href of ['/all-products', '/about', '/en/blog/post', 'https://instagram.com/x']) {
      expect(categorySlugOf(href)).toBeNull();
    }
  });

  it('treats a missing link as not-a-category', () => {
    expect(categorySlugOf(undefined)).toBeNull();
    expect(categorySlugOf('')).toBeNull();
  });
});

describe('isLiveLink', () => {
  it('keeps a link to a category the storefront is serving', () => {
    expect(isLiveLink('/cat-cookies', LIVE)).toBe(true);
  });

  it('drops a link to a category that has gone', () => {
    expect(isLiveLink('/cat-desserts', LIVE)).toBe(false);
  });

  it('leaves non-category links alone', () => {
    // We know nothing about these destinations and must not invent an opinion.
    expect(isLiveLink('/all-products', LIVE)).toBe(true);
    expect(isLiveLink('/about', LIVE)).toBe(true);
    expect(isLiveLink(undefined, LIVE)).toBe(true);
  });

  it('drops everything when no category is live', () => {
    expect(isLiveLink('/cat-cookies', new Set())).toBe(false);
  });
});
