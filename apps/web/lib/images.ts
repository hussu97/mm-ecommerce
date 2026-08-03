/**
 * The banner artwork that ships with the repo is built by
 * `scripts/optimize-images.mjs`, which writes an `.avif` and a `.webp` next to
 * every file under `public/images/banners`. The hero and promo bands render
 * through a hand-written `<picture>` rather than `next/image` — the mobile frame
 * is a different crop, which is art direction the image component cannot express
 * — so nothing negotiates a modern format for them unless the markup does it.
 *
 * The same slots can also be filled from the CMS, where an admin's upload lives
 * on R2 or GCS and has no siblings at all. Pointing a `<source>` at a `.avif`
 * that was never generated makes the browser fall through to the next candidate
 * silently, so it looks like it works while quietly serving nobody the smaller
 * file. Only claim siblings for the paths the build guarantees.
 */
const LOCAL_BANNER = /^\/images\/banners\/[^?#]+\.(jpe?g|png)$/i;

export interface ModernSources {
  avif: string;
  webp: string;
}

/** AVIF/WebP siblings for repo banner artwork, or null for anything else. */
export function modernSources(src: string): ModernSources | null {
  if (!LOCAL_BANNER.test(src)) return null;
  const stem = src.replace(/\.[^.]+$/, '');
  return { avif: `${stem}.avif`, webp: `${stem}.webp` };
}
