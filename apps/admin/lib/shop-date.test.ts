import { describe, expect, it } from 'vitest';
import {
  addUtcDays,
  isoDay,
  shopDaysAgo,
  shopTodayAnchor,
  todayInShopTz,
} from './utils';

describe('shop-time date helpers', () => {
  it('isoDay renders a UTC-anchored date as YYYY-MM-DD', () => {
    expect(isoDay(new Date(Date.UTC(2026, 0, 5)))).toBe('2026-01-05');
  });

  it('addUtcDays shifts whole days on the UTC anchor', () => {
    const base = new Date(Date.UTC(2026, 2, 1)); // 1 Mar 2026
    expect(isoDay(addUtcDays(base, -1))).toBe('2026-02-28');
    expect(isoDay(addUtcDays(base, 30))).toBe('2026-03-31');
  });

  it('shopTodayAnchor is anchored at UTC midnight', () => {
    const a = shopTodayAnchor();
    expect(a.getUTCHours()).toBe(0);
    expect(a.getUTCMinutes()).toBe(0);
    expect(a.getUTCSeconds()).toBe(0);
  });

  it('todayInShopTz is a YYYY-MM-DD string', () => {
    expect(todayInShopTz()).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it('shopDaysAgo(0) equals today', () => {
    expect(shopDaysAgo(0)).toBe(todayInShopTz());
  });

  it('shopDaysAgo(n) is exactly n calendar days before today', () => {
    const today = new Date(`${todayInShopTz()}T00:00:00Z`);
    const seven = new Date(`${shopDaysAgo(7)}T00:00:00Z`);
    const diffDays = (today.getTime() - seven.getTime()) / 86_400_000;
    expect(diffDays).toBe(7);
  });
});
