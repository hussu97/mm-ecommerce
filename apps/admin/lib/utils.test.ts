import { describe, it, expect } from 'vitest';
import { cn, slugify, formatCurrency, formatDate } from './utils';

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
