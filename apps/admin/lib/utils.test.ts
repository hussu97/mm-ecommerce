import { describe, it, expect } from 'vitest';
import { cn, slugify, formatCurrency, formatDate, formatQuantity } from './utils';

describe('cn', () => {
  it('joins class strings', () => {
    expect(cn('a', 'b')).toBe('a b');
  });

  it('filters falsy values', () => {
    expect(cn('a', undefined, null, false, 'b')).toBe('a b');
  });

  it('returns empty string for no valid classes', () => {
    expect(cn(undefined, false)).toBe('');
  });
});

describe('slugify', () => {
  it('lowercases the string', () => {
    expect(slugify('HELLO')).toBe('hello');
  });

  it('replaces spaces with hyphens', () => {
    expect(slugify('hello world')).toBe('hello-world');
  });

  it('removes special characters', () => {
    expect(slugify('hello@world!')).toBe('helloworld');
  });

  it('trims leading and trailing hyphens', () => {
    expect(slugify('-hello-')).toBe('hello');
  });

  it('collapses multiple spaces', () => {
    expect(slugify('hello   world')).toBe('hello-world');
  });
});

describe('formatCurrency', () => {
  it('formats number with AED prefix', () => {
    expect(formatCurrency(100)).toBe('AED 100.00');
  });

  it('formats decimal', () => {
    expect(formatCurrency(29.9)).toBe('AED 29.90');
  });

  it('formats string input', () => {
    expect(formatCurrency('50')).toBe('AED 50.00');
  });

  it('groups thousands', () => {
    expect(formatCurrency(1234.5)).toBe('AED 1,234.50');
    expect(formatCurrency(1000000)).toBe('AED 1,000,000.00');
  });

  it('treats null and undefined as zero (the shape money() accepted)', () => {
    expect(formatCurrency(null)).toBe('AED 0.00');
    expect(formatCurrency(undefined)).toBe('AED 0.00');
  });
});

describe('formatQuantity', () => {
  it('drops the trailing zeros of a high-scale ledger string', () => {
    expect(formatQuantity('8.37500000')).toBe('8.375');
    expect(formatQuantity('1.000000')).toBe('1');
    expect(formatQuantity('12.500000')).toBe('12.5');
  });

  it('leaves an integer string untouched', () => {
    expect(formatQuantity('42')).toBe('42');
  });

  it('keeps a fractional string lossless (no rounding)', () => {
    expect(formatQuantity('0.00000010')).toBe('0.0000001');
  });

  it('rounds a computed number to the ledger scale before trimming', () => {
    // 0.1 + 0.2 carries float noise; the figure a running net produces.
    expect(formatQuantity(0.1 + 0.2)).toBe('0.3');
    expect(formatQuantity(5)).toBe('5');
    expect(formatQuantity(-2.5)).toBe('-2.5');
  });

  it('shows an em dash for a missing value', () => {
    expect(formatQuantity(null)).toBe('—');
    expect(formatQuantity(undefined)).toBe('—');
    expect(formatQuantity('')).toBe('—');
  });
});

describe('formatDate', () => {
  it('returns a non-empty string', () => {
    const result = formatDate('2024-01-15T00:00:00Z');
    expect(typeof result).toBe('string');
    expect(result.length).toBeGreaterThan(0);
  });

  it('contains the year', () => {
    const result = formatDate('2024-01-15T00:00:00Z');
    expect(result).toContain('2024');
  });
});
