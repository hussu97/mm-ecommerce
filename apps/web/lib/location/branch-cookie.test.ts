import { afterEach, describe, expect, it } from 'vitest';

import { ZONE_COOKIE, readZone, rememberZone } from './branch-cookie';

/**
 * The one piece of the location the *server* can see.
 *
 * The pin lives in `localStorage`, which a React Server Component cannot read,
 * and the category and product pages are server-rendered. Without this cookie
 * the only way to match the catalogue to the delivery zone would be to ship
 * HTML full of cakes and delete them on first paint, in front of the customer.
 */
function clear() {
  document.cookie = `${ZONE_COOKIE}=; Path=/; Max-Age=0`;
}

afterEach(clear);

describe('the zone cookie', () => {
  it('records the polygon the pin resolved to', () => {
    rememberZone('2ecc7e57-e543-460a-bf2b-5ab5e4642f3b');

    expect(readZone()).toBe('2ecc7e57-e543-460a-bf2b-5ab5e4642f3b');
  });

  it('clears when a pin resolves to no zone', () => {
    // A pin outside every polygon we serve. Leaving the previous zone in place
    // would filter the catalogue by a polygon that has nothing to do with where
    // the customer now says they are.
    rememberZone('2ecc7e57-e543-460a-bf2b-5ab5e4642f3b');
    rememberZone(null);

    expect(readZone()).toBeNull();
  });

  it('treats an empty cookie as no zone rather than a zone named ""', () => {
    document.cookie = `${ZONE_COOKIE}=; Path=/`;

    expect(readZone()).toBeNull();
  });
});
