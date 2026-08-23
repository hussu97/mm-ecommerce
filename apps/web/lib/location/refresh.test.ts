import { describe, expect, it } from 'vitest';

import type { Address } from '@/lib/types';
import {
  MOVE_THRESHOLD_M,
  firstPinnedAddress,
  metresBetween,
  shouldReplaceWithBrowserFix,
} from './refresh';
import { DEFAULT_LOCATION, type Location } from './types';

const SHARJAH = { latitude: 25.3304139, longitude: 55.3736131 };
const ABU_DHABI = { latitude: 24.4539, longitude: 54.3773 };

function at(source: Location['source'], pin = SHARJAH): Location {
  return { ...pin, source, label: null };
}

describe('metresBetween', () => {
  it('is zero for the same pin', () => {
    expect(metresBetween(SHARJAH, SHARJAH)).toBe(0);
  });

  it('measures Sharjah to Abu Dhabi at roughly 140 km', () => {
    const km = metresBetween(SHARJAH, ABU_DHABI) / 1000;
    expect(km).toBeGreaterThan(130);
    expect(km).toBeLessThan(150);
  });
});

describe('shouldReplaceWithBrowserFix', () => {
  it('replaces the shop placeholder with any real reading', () => {
    expect(shouldReplaceWithBrowserFix(DEFAULT_LOCATION, SHARJAH)).toBe(true);
  });

  it('never overrides an address the customer saved', () => {
    expect(shouldReplaceWithBrowserFix(at('address'), ABU_DHABI)).toBe(false);
  });

  it('never overrides a pin the customer dropped themselves', () => {
    expect(shouldReplaceWithBrowserFix(at('manual'), ABU_DHABI)).toBe(false);
  });

  it('moves an earlier reading once they have actually travelled', () => {
    expect(shouldReplaceWithBrowserFix(at('geolocation'), ABU_DHABI)).toBe(true);
  });

  it('ignores GPS jitter around a reading it already has', () => {
    // ~20 m north of the same spot.
    const jitter = { latitude: SHARJAH.latitude + 0.0002, longitude: SHARJAH.longitude };
    expect(metresBetween(SHARJAH, jitter)).toBeLessThan(MOVE_THRESHOLD_M);
    expect(shouldReplaceWithBrowserFix(at('geolocation'), jitter)).toBe(false);
  });
});

describe('firstPinnedAddress', () => {
  function addr(over: Partial<Address> = {}): Address {
    return {
      id: 'a', user_id: 'u', label: 'Home', first_name: 'A', last_name: 'B',
      phone: '+971500000000', address_line_1: 'Somewhere', address_line_2: null,
      unit_number: null, country: 'AE', is_default: false,
      latitude: SHARJAH.latitude, longitude: SHARJAH.longitude,
      ...over,
    } as Address;
  }

  it('prefers the default when it has a pin', () => {
    const target = addr({ id: 'default', is_default: true });
    expect(firstPinnedAddress([addr({ id: 'other' }), target])?.id).toBe('default');
  });

  it('skips a default saved without map coordinates', () => {
    const unpinned = addr({
      id: 'typed-only',
      is_default: true,
      latitude: null as unknown as number,
      longitude: null as unknown as number,
    });
    expect(firstPinnedAddress([unpinned, addr({ id: 'pinned' })])?.id).toBe('pinned');
  });

  it('treats the null island as no pin at all', () => {
    const nullIsland = addr({ id: 'atlantic', is_default: true, latitude: 0, longitude: 0 });
    expect(firstPinnedAddress([nullIsland])).toBeNull();
  });

  it('is null for an address book with nothing pinned', () => {
    expect(firstPinnedAddress([])).toBeNull();
  });
});
