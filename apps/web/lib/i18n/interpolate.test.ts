import { describe, expect, it } from 'vitest';
import { interpolate } from './interpolate';

describe('interpolate', () => {
  it('fills a single placeholder', () => {
    expect(interpolate('Hello {name}', { name: 'Fatema' })).toBe('Hello Fatema');
  });

  it('fills numbers', () => {
    expect(interpolate('{count} items', { count: 3 })).toBe('3 items');
  });

  it('replaces every occurrence of the same placeholder', () => {
    // The regression the shared version exists for: both private copies used
    // single-occurrence replace, so the second `{code}` shipped verbatim.
    expect(
      interpolate('Code {code} applied — {code} is yours', { code: 'NEW' }),
    ).toBe('Code NEW applied — NEW is yours');
  });

  it('fills several distinct placeholders', () => {
    expect(interpolate('{a} and {b}', { a: 'x', b: 'y' })).toBe('x and y');
  });

  it('leaves unknown placeholders alone', () => {
    expect(interpolate('Hello {name}', { other: 'x' })).toBe('Hello {name}');
  });

  it('returns the template untouched without params', () => {
    expect(interpolate('Hello {name}')).toBe('Hello {name}');
  });

  it('does not treat placeholder names as regex', () => {
    expect(interpolate('{a.b} test', { 'a.b': 'ok' })).toBe('ok test');
  });
});
