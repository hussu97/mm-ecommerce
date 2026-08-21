import { describe, it, expect } from 'vitest';
import { PRODUCT_IMAGE_BLUR } from './image-placeholder';

// The constant is a hand-pasted base64 literal, which is exactly the kind of
// thing that survives a bad edit looking plausible. A malformed one does not
// throw — `next/image` sets it as a background and the tile simply loads from
// nothing, which is the behaviour the placeholder exists to remove and is
// invisible until somebody looks at a phone on a slow connection.
describe('PRODUCT_IMAGE_BLUR', () => {
  it('is a base64 SVG data URI', () => {
    expect(PRODUCT_IMAGE_BLUR).toMatch(/^data:image\/svg\+xml;base64,[A-Za-z0-9+/]+=*$/);
  });

  it('decodes to an SVG carrying the card background tone', () => {
    const svg = atob(PRODUCT_IMAGE_BLUR.split(',')[1]);
    expect(svg).toMatch(/^<svg[\s>]/);
    expect(svg).toContain('</svg>');
    // The tile it sits in is `bg-[#f9f5f0]`; the gradient starts there so the
    // blur-up reads as the tile filling in rather than as a colour change.
    expect(svg).toContain('#f9f5f0');
  });

  it('stays small enough to inline on every tile', () => {
    // Fifty of these ship in one listing page's HTML.
    expect(PRODUCT_IMAGE_BLUR.length).toBeLessThan(1024);
  });
});
