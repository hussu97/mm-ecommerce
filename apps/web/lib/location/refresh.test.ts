import { describe, expect, it } from 'vitest';

import { MOVE_THRESHOLD_M, metresBetween, shouldReplaceWithBrowserFix } from './refresh';
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
