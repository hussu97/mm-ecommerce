/**
 * One nav entry lights up, even when one route sits inside another.
 *
 * The sidebar used to decide per entry with `pathname.startsWith(href)`, which
 * was correct for as long as no nav href was a prefix of another. `/analytics`
 * and `/analytics/carts` are: standing on Live Baskets highlighted Analytics as
 * well, and a per-entry rule has no way to express "the more specific one wins"
 * because it never sees the other entries.
 */

import { describe, expect, it } from 'vitest';
import { activeNavHref } from './nav';

describe('activeNavHref', () => {
  it('picks the most specific entry when one route nests inside another', () => {
    expect(activeNavHref('/analytics/carts')).toBe('/analytics/carts');
  });

  it('still lights the parent up on the parent route', () => {
    expect(activeNavHref('/analytics')).toBe('/analytics');
  });

  it('lights a section up from one of its detail pages', () => {
    // `/orders/[orderNumber]` has no nav entry of its own, so the section it
    // belongs to is the answer.
    expect(activeNavHref('/orders/MM-20260820-001')).toBe('/orders');
  });

  it('matches the dashboard exactly, since it is a prefix of everything', () => {
    expect(activeNavHref('/')).toBe('/');
    expect(activeNavHref('/products')).toBe('/products');
  });

  it('does not treat a shared word-start as nesting', () => {
    // `/product-something` is not inside `/products`, and a bare `startsWith`
    // would have said it was.
    expect(activeNavHref('/products-archive')).toBeNull();
  });
});
