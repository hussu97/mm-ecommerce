import { describe, expect, it } from 'vitest';

import { buildQs } from './api';

describe('buildQs', () => {
  it('trims what was typed into a search box', () => {
    expect(buildQs({ search: '  dark choc \n' })).toBe('?search=dark+choc');
  });

  it('sends nothing for a box holding only spaces', () => {
    expect(buildQs({ search: '   ', page: 1 })).toBe('?page=1');
  });

  it('still sends false and repeats array keys', () => {
    expect(buildQs({ is_active: false, category: ['a', 'b'] })).toBe(
      '?is_active=false&category=a&category=b',
    );
  });
});
