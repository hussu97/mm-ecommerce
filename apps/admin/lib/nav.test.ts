/**
 * One nav entry lights up, even when a route sits inside another.
 *
 * The sidebar used to decide per entry with `pathname.startsWith(href)`, which
 * was correct only for as long as no nav href was a prefix of another and every
 * route had its own entry. A child route without an entry — `/analytics/carts`
 * (the Live Baskets tab), `/orders/MM-…` — must light its parent, and a
 * per-entry rule has no way to express "the more specific one wins" because it
 * never sees the other entries.
 */

import { describe, expect, it } from 'vitest';
import { activeNavHref } from './nav';

describe('activeNavHref', () => {
  it('lights the parent section from a child route without its own entry', () => {
    // Live Baskets is a tab of Analytics now, not its own nav entry.
    expect(activeNavHref('/analytics/carts')).toBe('/analytics');
  });

  it('still lights the parent up on the parent route', () => {
    expect(activeNavHref('/analytics')).toBe('/analytics');
  });

  it('lights Logs from any of its three tab routes', () => {
    // Email, Webhooks and Audit are tabs of Logs; none has its own nav entry.
    expect(activeNavHref('/logs/email')).toBe('/logs');
    expect(activeNavHref('/logs/webhooks')).toBe('/logs');
    expect(activeNavHref('/logs/audit')).toBe('/logs');
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
