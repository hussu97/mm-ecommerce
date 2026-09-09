import { describe, expectTypeOf, it } from 'vitest';

import type { Schemas } from '@mm/types';
import type { Address } from './types';

/**
 * `Address` must track the API contract, coordinates included (F-WEB-13).
 *
 * These are compile-time assertions — `tsc --noEmit` (the `type-check` script)
 * fails if any drifts, so the guard has teeth without a runtime test. It caught
 * a real bug: `latitude`/`longitude` were typed `number | null` while the API
 * sends required decimal strings, and that is the field delivery is priced from.
 */
describe('Address tracks the AddressResponse contract', () => {
  it('is AddressResponse for every field except the widened coordinates', () => {
    expectTypeOf<Omit<Address, 'latitude' | 'longitude'>>().toEqualTypeOf<
      Omit<Schemas['AddressResponse'], 'latitude' | 'longitude'>
    >();
  });

  it('carries the coordinates as strings — never numbers, the old bug', () => {
    // The web widens the contract's required string to `string | null` (a guest
    // address may have no pin), but string-based either way — not a number.
    expectTypeOf<Address['latitude']>().toEqualTypeOf<string | null>();
    expectTypeOf<Address['longitude']>().toEqualTypeOf<string | null>();
  });

  it('pins the API side as a string, so an API change surfaces here', () => {
    expectTypeOf<Schemas['AddressResponse']['latitude']>().toEqualTypeOf<string>();
    expectTypeOf<Schemas['AddressResponse']['longitude']>().toEqualTypeOf<string>();
  });
});
