import { describe, it, expect } from 'vitest';
import { cn, generateId, formatPrice } from './utils';

describe('cn', () => {
  it('joins class strings', () => {
    expect(cn('a', 'b', 'c')).toBe('a b c');
  });

  it('filters undefined', () => {
    expect(cn('a', undefined, 'b')).toBe('a b');
  });

  it('filters null', () => {
    expect(cn('a', null, 'b')).toBe('a b');
  });

  it('filters false', () => {
    expect(cn('a', false, 'b')).toBe('a b');
  });

  it('returns empty string for all falsy', () => {
    expect(cn(undefined, null, false)).toBe('');
  });
});

describe('generateId', () => {
  it('returns a non-empty string', () => {
    const id = generateId();
    expect(typeof id).toBe('string');
    expect(id.length).toBeGreaterThan(0);
  });

  it('generates unique values', () => {
    const ids = new Set(Array.from({ length: 10 }, () => generateId()));
    expect(ids.size).toBe(10);
  });
});

describe('formatPrice', () => {
  it('formats integer with 2 decimal places', () => {
    expect(formatPrice(100)).toBe('100.00 AED');
  });

  it('formats decimal number', () => {
    expect(formatPrice(29.9)).toBe('29.90 AED');
  });

  it('formats string amount', () => {
    expect(formatPrice('50.00')).toBe('50.00 AED');
  });

  it('formats zero', () => {
    expect(formatPrice(0)).toBe('0.00 AED');
  });
});
