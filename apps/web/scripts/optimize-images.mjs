/**
 * Generates `public/images` from the pristine artwork in `image-src`.
 *
 * Two different consumers, two different outputs:
 *
 *  - `banners/` is drawn by `HeroCarousel` and `PromoBanners` through a raw
 *    `<picture>`, because the mobile frame is a different crop and that is art
 *    direction `next/image` cannot express. Nothing re-encodes those bytes for
 *    us, so we emit AVIF and WebP siblings here and let the markup negotiate.
 *
 *  - `tiles/`, `photos/` and `logos/` go through `next/image`, which already
 *    negotiates AVIF/WebP per request. Emitting siblings for them would be dead
 *    weight; all they need is a source that is not several times larger than any
 *    size the page will ever ask for, since the optimiser has to fetch and
 *    decode the whole thing on every cold transform.
 *
 * This is NOT part of `build`. It takes ~150s on a CI runner and its output is
 * committed, so running it on every build re-encoded 45 files into
 * byte-identical copies for nothing. `check-images.mjs` runs in the build's
 * place and fails if the two have drifted.
 *
 * Run after changing anything in `image-src`, and commit the result:
 *
 *     pnpm --filter web images
 */
import sharp from 'sharp';
import { mkdir, readdir, readFile, writeFile, rm, stat } from 'node:fs/promises';
import path from 'node:path';
import {
  AVIF,
  GROUPS,
  JPEG,
  MANIFEST,
  OUT,
  SRC,
  WEBP,
  fingerprint,
} from './images.config.mjs';

const kb = (n) => `${(n / 1024).toFixed(0)} KB`;

let totalBefore = 0;
let totalAfter = 0;

await rm(OUT, { recursive: true, force: true });

for (const [group, cfg] of Object.entries(GROUPS)) {
  const dir = path.join(SRC, group);
  const files = (await readdir(dir)).filter((f) => !f.startsWith('.')).sort();
  await mkdir(path.join(OUT, group), { recursive: true });

  console.log(`\n${group}/`);

  for (const file of files) {
    const srcPath = path.join(dir, file);
    const src = await readFile(srcPath);
    const meta = await sharp(src).metadata();
    totalBefore += src.length;

    // The `-mobile` frames are portrait crops shown only under `sm`, so they are
    // never asked to cover a wide viewport and do not need the wide cap.
    const cap =
      cfg.mobileMaxWidth && /-mobile\./.test(file) ? cfg.mobileMaxWidth : cfg.maxWidth;

    const base = sharp(src)
      .rotate() // bake EXIF orientation in before the tag is stripped
      .resize({ width: cap, withoutEnlargement: true });

    // Formats and filenames are left exactly as they are. The two `person_shot`
    // PNGs are opaque photographs and would be a fraction of the size as JPEG,
    // but they go through `next/image`, which re-encodes to AVIF regardless — so
    // the source format only affects what the optimiser has to decode, never what
    // a visitor downloads. Meanwhile the live CMS rows point at those exact
    // `.png` paths, and the storefront serves whatever the CMS says. Renaming
    // them here would 404 the about page in production to save bytes nobody
    // was going to transfer.
    const isPng = meta.format === 'png';
    const ext = path.extname(file).toLowerCase();
    const stem = file.replace(/\.[^.]+$/, '');
    const written = [];

    const fallback = await (isPng
      ? base.clone().png({ palette: true, quality: 82, effort: 9 })
      : base.clone().jpeg(JPEG)
    ).toBuffer();
    await writeFile(path.join(OUT, group, stem + ext), fallback);
    written.push([stem + ext, fallback.length]);
    totalAfter += fallback.length;

    if (cfg.modern) {
      for (const [suffix, buf] of [
        ['.avif', await base.clone().avif(AVIF).toBuffer()],
        ['.webp', await base.clone().webp(WEBP).toBuffer()],
      ]) {
        await writeFile(path.join(OUT, group, stem + suffix), buf);
        written.push([stem + suffix, buf.length]);
        totalAfter += buf.length;
      }
    }

    const out = await sharp(fallback).metadata();
    console.log(
      `  ${file}  ${meta.width}x${meta.height} ${kb(src.length)}` +
        ` -> ${out.width}x${out.height}  ` +
        written.map(([n, s]) => `${path.extname(n).slice(1)} ${kb(s)}`).join(' | '),
    );
  }
}

console.log(
  `\nimage-src ${(totalBefore / 1024 / 1024).toFixed(2)} MB` +
    ` -> public/images ${(totalAfter / 1024 / 1024).toFixed(2)} MB`,
);

// The banner markup asks for a sibling by swapping the extension. If one is ever
// missing the browser silently falls through to the next <source>, which is easy
// to not notice, so fail the build here instead.
for (const [group, cfg] of Object.entries(GROUPS)) {
  if (!cfg.modern) continue;
  for (const f of await readdir(path.join(OUT, group))) {
    if (/\.(avif|webp)$/.test(f)) continue;
    for (const ext of ['.avif', '.webp']) {
      const sibling = path.join(OUT, group, f.replace(/\.[^.]+$/, ext));
      await stat(sibling).catch(() => {
        throw new Error(`missing ${ext} sibling for ${group}/${f}`);
      });
    }
  }
}
console.log('every banner has its avif and webp sibling.');

// Written last, so it only records success. `check-images.mjs` compares this to
// a freshly computed fingerprint on every build; if you changed `image-src` and
// did not re-run this script, the build stops instead of shipping stale art.
await writeFile(
  MANIFEST,
  JSON.stringify(
    {
      comment:
        'Generated by scripts/optimize-images.mjs. Commit alongside public/images.',
      fingerprint: await fingerprint(),
    },
    null,
    2,
  ) + '\n',
);
console.log(`recorded fingerprint in ${path.basename(MANIFEST)}.`);
