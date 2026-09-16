import { describe, expect, it } from 'vitest';

import { metadata as cartMeta } from './cart/layout';
import { metadata as checkoutMeta } from './checkout/layout';
import { metadata as trackMeta } from './track/layout';

/**
 * The private surfaces answer a crawler with `noindex` from a `layout.tsx`
 * beside the page (the page itself is a client component and cannot export
 * metadata). `robots.txt` deliberately does NOT `Disallow` them — a forbidden
 * fetch is indexed as a naked link — so this directive is the only thing keeping
 * them out. `/track` was the one private surface that shipped without it and
 * could be listed (F-WEB-8); this pins all three.
 */
describe('private layouts are noindex', () => {
  it.each([
    ['cart', cartMeta],
    ['checkout', checkoutMeta],
    ['track', trackMeta],
  ])('%s refuses indexing and following', (_name, meta) => {
    expect(meta.robots).toMatchObject({ index: false, follow: false });
  });
});
