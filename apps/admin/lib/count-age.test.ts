import { describe, expect, it } from 'vitest';
import { countAge, latestStamp, matchesCountFilter } from './count-age';

// 2026-11-08 10:00 in Dubai (UTC+4).
const NOW = new Date('2026-11-08T06:00:00Z');

describe('countAge', () => {
  it('bands by whole shop-calendar days', () => {
    expect(countAge(null, NOW)).toEqual({ days: null, band: 'never', label: 'never' });
    expect(countAge('2026-11-08T01:00:00Z', NOW)).toMatchObject({ band: 'fresh', label: 'today' });
    expect(countAge('2026-11-05T06:00:00Z', NOW)).toMatchObject({ days: 3, band: 'fresh' });
    expect(countAge('2026-11-04T06:00:00Z', NOW)).toMatchObject({ days: 4, band: 'due', label: '4d' });
    expect(countAge('2026-11-01T06:00:00Z', NOW)).toMatchObject({ days: 7, band: 'due' });
    expect(countAge('2026-10-31T06:00:00Z', NOW)).toMatchObject({ days: 8, band: 'overdue' });
  });

  it('uses the Dubai day, not UTC: 23:30 Dubai yesterday is 1d, not today', () => {
    // 2026-11-07 19:30Z = 23:30 Dubai on the 7th.
    expect(countAge('2026-11-07T19:30:00Z', NOW).label).toBe('1d');
    // 2026-11-07 20:30Z = 00:30 Dubai on the 8th.
    expect(countAge('2026-11-07T20:30:00Z', NOW).label).toBe('today');
  });
});

describe('matchesCountFilter', () => {
  it('treats never-counted as stale for every "not counted in" band', () => {
    expect(matchesCountFilter('over:7', null, NOW)).toBe(true);
    expect(matchesCountFilter('over:7', '2026-11-01T06:00:00Z', NOW)).toBe(false);
    expect(matchesCountFilter('over:7', '2026-10-31T06:00:00Z', NOW)).toBe(true);
    expect(matchesCountFilter('fresh', null, NOW)).toBe(false);
    expect(matchesCountFilter('never', '2026-10-01T06:00:00Z', NOW)).toBe(false);
    expect(matchesCountFilter('', null, NOW)).toBe(true);
  });
});

describe('latestStamp', () => {
  it('keeps the newer stamp and tolerates nulls', () => {
    expect(latestStamp(null, null)).toBeNull();
    expect(latestStamp('2026-11-01T00:00:00Z', null)).toBe('2026-11-01T00:00:00Z');
    expect(latestStamp('2026-11-01T00:00:00Z', '2026-11-03T00:00:00Z')).toBe('2026-11-03T00:00:00Z');
  });
});
