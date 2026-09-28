import { describe, expect, it } from 'vitest';
import { parseInlineLinks, stripInlineLinks } from './inline-links';

describe('parseInlineLinks', () => {
  it('turns a site path into an internal link between two runs of text', () => {
    expect(parseInlineLinks('Please [contact us](/contact) with a photo.')).toEqual([
      { kind: 'text', text: 'Please ' },
      { kind: 'link', text: 'contact us', href: '/contact', internal: true },
      { kind: 'text', text: ' with a photo.' },
    ]);
  });

  it('keeps https links external', () => {
    expect(parseInlineLinks('[WhatsApp](https://wa.me/1)')).toEqual([
      { kind: 'link', text: 'WhatsApp', href: 'https://wa.me/1', internal: false },
    ]);
  });

  it('leaves an unsafe target as its literal text', () => {
    const source = 'x [click](javascript:alert(1)) y';
    expect(parseInlineLinks(source).every(s => s.kind === 'text')).toBe(true);
    expect(stripInlineLinks(source)).toBe(source);
  });

  it('treats a protocol-relative path as unsafe', () => {
    expect(parseInlineLinks('[a](//evil.test)').every(s => s.kind === 'text')).toBe(true);
  });

  it('returns plain copy untouched', () => {
    expect(parseInlineLinks('No links here.')).toEqual([{ kind: 'text', text: 'No links here.' }]);
  });
});

describe('stripInlineLinks', () => {
  it('keeps the label and drops the markup', () => {
    expect(stripInlineLinks('Please [تواصل معنا](/contact).')).toBe('Please تواصل معنا.');
  });
});
