/**
 * The day the new-customer offer is said to end.
 *
 * The admin saves a bare date as midnight UTC and `validate` honours the code
 * up to and including that instant, so "valid until 2027-01-01" means the last
 * day is Dec 31. Stating Jan 1 would promise a day the code does not cover.
 */

import { describe, expect, it } from 'vitest';

import { offerEndDate } from './offer-ends';

describe('offerEndDate', () => {
  it('states the last full day for a date saved from the admin (midnight UTC)', () => {
    expect(offerEndDate('2027-01-01T00:00:00Z', 'en')).toBe('Dec 31');
    expect(offerEndDate('2027-01-01T00:00:00+00:00', 'en')).toBe('Dec 31');
  });

  it('states the same day for a boundary set at midnight UAE time', () => {
    expect(offerEndDate('2026-12-31T20:00:00Z', 'en')).toBe('Dec 31');
  });

  it('writes the Arabic date with Latin digits, like every other figure in the offer', () => {
    expect(offerEndDate('2027-01-01T00:00:00Z', 'ar')).toBe('31 ديسمبر');
  });

  it('says nothing for an open-ended or unreadable boundary', () => {
    expect(offerEndDate(null, 'en')).toBeNull();
    expect(offerEndDate(undefined, 'en')).toBeNull();
    expect(offerEndDate('', 'en')).toBeNull();
    expect(offerEndDate('not a date', 'en')).toBeNull();
  });
});
