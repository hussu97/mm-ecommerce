import { describe, expect, it } from 'vitest';

import { canSeeRestrictedMisc, monthEnd, periodLabel, weekEnd, weekStart } from './purchasing';

describe('periodLabel', () => {
  it('names one whole month', () => {
    expect(periodLabel('2026-09-01', '2026-09-30')).toBe('Sep 2026');
    expect(periodLabel('2028-02-01', '2028-02-29')).toBe('Feb 2028');
  });
  it('names a run of whole months', () => {
    expect(periodLabel('2026-07-01', '2026-09-30')).toBe('Jul – Sep 2026');
    expect(periodLabel('2026-11-01', '2027-10-31')).toBe('Nov 2026 – Oct 2027');
  });
  it('falls back to days', () => {
    expect(periodLabel('2026-09-26', '2026-09-26')).toBe('26 Sep 2026');
    expect(periodLabel('2026-09-21', '2026-09-27')).toBe('21 – 27 Sep 2026');
    expect(periodLabel('2026-09-28', '2026-10-04')).toBe('28 Sep – 4 Oct 2026');
    expect(periodLabel('2026-12-28', '2027-01-03')).toBe('28 Dec 2026 – 3 Jan 2027');
  });
});

describe('week and month bounds', () => {
  it('snaps to Monday–Sunday', () => {
    expect(weekStart('2026-09-26')).toBe('2026-09-21'); // Saturday
    expect(weekStart('2026-09-27')).toBe('2026-09-21'); // Sunday
    expect(weekStart('2026-09-21')).toBe('2026-09-21'); // Monday
    expect(weekEnd('2026-09-21')).toBe('2026-09-27');
    expect(weekEnd('2026-12-30')).toBe('2027-01-03');
  });
  it('knows month ends, leap years included', () => {
    expect(monthEnd(2026, 2)).toBe('2026-02-28');
    expect(monthEnd(2028, 2)).toBe('2028-02-29');
    expect(monthEnd(2026, 12)).toBe('2026-12-31');
  });
});

describe('canSeeRestrictedMisc', () => {
  it('needs the slug or super-admin', () => {
    expect(canSeeRestrictedMisc({ is_superadmin: true, permissions: [] })).toBe(true);
    expect(
      canSeeRestrictedMisc({ permissions: ['inventory.purchase_orders.restricted_misc'] }),
    ).toBe(true);
    expect(canSeeRestrictedMisc({ permissions: ['inventory.purchase_orders.manage'] })).toBe(false);
    expect(canSeeRestrictedMisc(null)).toBe(false);
  });
});
