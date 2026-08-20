import { afterEach, describe, expect, it } from 'vitest';

import { BRANCH_COOKIE, readBranch, rememberBranch } from './branch-cookie';

/**
 * The one piece of the location the *server* can see.
 *
 * The pin lives in `localStorage`, which a React Server Component cannot read,
 * and the category and product pages are server-rendered. Without this cookie
 * the only way to match the catalogue to the branch would be to ship HTML full
 * of cakes and delete them on first paint, in front of the customer.
 */
function clear() {
  document.cookie = `${BRANCH_COOKIE}=; Path=/; Max-Age=0`;
}

afterEach(clear);

describe('the branch cookie', () => {
  it('records the kitchen the pin resolved to', () => {
    rememberBranch('2ecc7e57-e543-460a-bf2b-5ab5e4642f3b');

    expect(readBranch()).toBe('2ecc7e57-e543-460a-bf2b-5ab5e4642f3b');
  });

  it('clears when a pin resolves to no kitchen', () => {
    // A pin outside every zone we serve. Leaving the previous branch in place
    // would filter the catalogue by a kitchen that has nothing to do with where
    // the customer now says they are.
    rememberBranch('2ecc7e57-e543-460a-bf2b-5ab5e4642f3b');
    rememberBranch(null);

    expect(readBranch()).toBeNull();
  });

  it('treats an empty cookie as no branch rather than a branch named ""', () => {
    document.cookie = `${BRANCH_COOKIE}=; Path=/`;

    expect(readBranch()).toBeNull();
  });
});
