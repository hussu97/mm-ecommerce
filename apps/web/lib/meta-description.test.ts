import { describe, it, expect } from 'vitest';
import {
  META_DESCRIPTION_MAX,
  categoryMetaDescription,
  composeMetaDescription,
  productMetaDescription,
  splitSentences,
} from './meta-description';

// Real storefront blurbs, shortest to longest — the ones Bing flagged.
const BLURBS = [
  'Box of 9 Mix Cookies',
  'Rich Chocolate cookies filled with Nutella',
  'Chewy cookies made with brown butter for a nutty flavour.',
  'Warm gooey brown butter cookie layers with dark chocolate chips and a kinder sauce.',
  'Our bigger and better brownies infused with snickers chunks and topped with a chocolate and caramel drizzle.',
  'Our bigger and better signature brownies rippled with fresh Raspberry sauce giving the best balance of sweet and tart.',
];

describe('composeMetaDescription', () => {
  it('closes the lead with a full stop', () => {
    expect(composeMetaDescription('Box of 9 Mix Cookies')).toBe('Box of 9 Mix Cookies.');
  });

  it('takes the longest alternative in a slot that fits', () => {
    expect(composeMetaDescription('Lead.', [['x'.repeat(200), 'Short one.']], 30)).toBe(
      'Lead. Short one.',
    );
  });

  it('skips a slot where nothing fits and keeps going', () => {
    expect(composeMetaDescription('Lead.', ['x'.repeat(50), 'Tail.'], 20)).toBe('Lead. Tail.');
  });

  it('truncates an overlong lead on a word boundary', () => {
    const out = composeMetaDescription('word '.repeat(60));
    expect(out.length).toBeLessThanOrEqual(META_DESCRIPTION_MAX);
    expect(out.endsWith('word…')).toBe(true);
  });

  it('trades a longer first option for a combination that fits more', () => {
    // Greedy takes "Longer one." (17 with the lead) and has no room for
    // "Tail."; the search finds "Short." plus the tail (18).
    expect(
      composeMetaDescription('Lead.', [['Longer one.', 'Short.'], 'Tail.'], 20),
    ).toBe('Lead. Short. Tail.');
  });

  it('keeps the preferred option when it is as good as any', () => {
    expect(composeMetaDescription('Lead.', [['Aaaa.', 'Bbbb.']], 40)).toBe('Lead. Aaaa.');
  });

  it('would rather fill every slot than win a few characters by dropping one', () => {
    // "Lead. " + the 28-character line alone (34) beats lead + short line +
    // tail (25) on length, but not by the 10 it costs to drop the tail.
    expect(
      composeMetaDescription('Lead.', [['x'.repeat(27) + '.', 'Short line.'], 'A tail.'], 36),
    ).toBe('Lead. Short line. A tail.');
  });

  it('drops a later slot before an earlier one', () => {
    // Either single line fits, not both; the earlier slot is the one kept.
    expect(composeMetaDescription('Lead.', ['First slot.', 'Second slot.'], 20)).toBe(
      'Lead. First slot.',
    );
  });

  it('ignores empty slots', () => {
    expect(composeMetaDescription('Lead.', [null, undefined, ''])).toBe('Lead.');
  });
});

describe('productMetaDescription', () => {
  it.each(BLURBS)('fills "%s" to 140–160 characters', (description) => {
    const out = productMetaDescription({ name: 'X', description, locale: 'en' });
    expect(out.startsWith(description.replace(/\.$/, ''))).toBe(true);
    expect(out.length).toBeGreaterThanOrEqual(140);
    expect(out.length).toBeLessThanOrEqual(META_DESCRIPTION_MAX);
  });

  it('writes Arabic on an Arabic page', () => {
    const out = productMetaDescription({
      name: 'كوكيز',
      description: 'علبة من 3 براونيز مشكلة',
      locale: 'ar',
    });
    expect(out).toMatch(/الشارقة/);
    expect(out).not.toMatch(/[A-Za-z]/);
    expect(out.length).toBeLessThanOrEqual(META_DESCRIPTION_MAX);
    expect(out.length).toBeGreaterThanOrEqual(120);
  });

  it('falls back to the name when there is no blurb', () => {
    const out = productMetaDescription({ name: 'Tiramisu', description: '', locale: 'en' });
    expect(out.startsWith('Order Tiramisu from Melting Moments Cakes.')).toBe(true);
  });
});

describe('categoryMetaDescription', () => {
  it('does not put English around an Arabic category name', () => {
    const out = categoryMetaDescription({ name: 'كوكيز', description: '', locale: 'ar' });
    expect(out).not.toMatch(/[A-Za-z]/);
    expect(out.startsWith('اطلب كوكيز')).toBe(true);
  });

  it('fills the Arabic fallback past 150 characters', () => {
    const out = categoryMetaDescription({ name: 'صناديق مختلطة', description: '', locale: 'ar' });
    expect(out.length).toBeGreaterThanOrEqual(150);
    expect(out.length).toBeLessThanOrEqual(META_DESCRIPTION_MAX);
  });

  it('fills the English fallback past 130 characters', () => {
    const out = categoryMetaDescription({ name: 'Brownies', description: null, locale: 'en' });
    expect(out.startsWith('Order brownies online')).toBe(true);
    expect(out.length).toBeGreaterThanOrEqual(130);
    expect(out.length).toBeLessThanOrEqual(META_DESCRIPTION_MAX);
  });
});

describe('splitSentences', () => {
  it('keeps each sentence with its punctuation', () => {
    expect(splitSentences('One here. Two there! Three')).toEqual(['One here.', 'Two there!', 'Three']);
  });

  it('does not split on a percent or a number', () => {
    expect(splitSentences('15% off your first 3 orders. Code NEW.')).toEqual([
      '15% off your first 3 orders.',
      'Code NEW.',
    ]);
  });
});
