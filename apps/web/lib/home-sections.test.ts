import { describe, expect, it } from 'vitest';
import { SECTION_ORDER, orderedSections } from './home-sections';

describe('orderedSections', () => {
  it('falls back to the canonical order when the CMS has no layout', () => {
    expect(orderedSections(undefined)).toEqual([...SECTION_ORDER]);
    expect(orderedSections({})).toEqual([...SECTION_ORDER]);
  });

  it('honours the CMS order', () => {
    const order = ['cater', 'hero', 'featured', 'usps', 'categories', 'promos', 'baker'];
    expect(orderedSections({ order })).toEqual(order);
  });

  it('appends sections the stored order predates, rather than dropping them', () => {
    // An order written before `promos` and `categories` existed.
    expect(orderedSections({ order: ['hero', 'featured', 'baker'] })).toEqual([
      'hero', 'featured', 'baker', 'usps', 'categories', 'promos', 'cater',
    ]);
  });

  it('ignores keys that name no section', () => {
    expect(orderedSections({ order: ['hero', 'instagram-wall', 'usps'] })).toEqual([
      'hero', 'usps', 'featured', 'categories', 'promos', 'baker', 'cater',
    ]);
  });

  it('never renders a section twice, however the order is stored', () => {
    const result = orderedSections({ order: ['hero', 'hero', 'usps', 'hero'] });
    expect(result).toEqual([...new Set(result)]);
    expect(result[0]).toBe('hero');
  });

  it('drops hidden sections wherever they appear', () => {
    expect(orderedSections({ hidden: ['promos', 'baker'] })).toEqual([
      'hero', 'usps', 'featured', 'categories', 'cater',
    ]);
    expect(orderedSections({ order: ['cater', 'hero'], hidden: ['cater'] })[0]).toBe('hero');
  });

  it('can hide everything without throwing', () => {
    expect(orderedSections({ hidden: [...SECTION_ORDER] })).toEqual([]);
  });
});
