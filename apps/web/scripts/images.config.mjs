/**
 * Encoder settings for `optimize-images.mjs`, plus the fingerprint that lets
 * `check-images.mjs` tell whether `public/images` is still the output of the
 * current `image-src` and the current settings.
 *
 * This lives apart from `optimize-images.mjs` for one reason: the guard has to
 * read the settings without *running* the optimiser. That script does its work
 * at the top level, so importing it re-encodes everything — which is the cost
 * the guard exists to avoid.
 */
import { createHash } from 'node:crypto';
import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const SRC = path.join(ROOT, 'image-src');
export const OUT = path.join(ROOT, 'public', 'images');

/** Kept outside `public/` so it is never served, and never swept by the `rm` of OUT. */
export const MANIFEST = path.join(ROOT, '.images-manifest.json');

/**
 * `modern` marks the directories whose files are referenced by a hand-written
 * `<picture>`, so they need AVIF/WebP siblings on disk. `maxWidth` is chosen
 * from the widest box the layout can put the image in, at 2x.
 */
export const GROUPS = {
  banners: { modern: true, maxWidth: 2000, mobileMaxWidth: 1200 },
  tiles: { modern: false, maxWidth: 1000 },
  photos: { modern: false, maxWidth: 1600 },
  logos: { modern: false, maxWidth: 1024 },
};

export const JPEG = { quality: 78, mozjpeg: true, progressive: true };
export const WEBP = { quality: 74 };
export const AVIF = { quality: 55, effort: 6 };

/** Bumped by hand when the *encoding logic* changes in a way the settings do not describe. */
const ALGORITHM_VERSION = 1;

/**
 * A hash of every input the output depends on: the source bytes, their names,
 * and the settings applied to them. Nothing about `public/images` is read here
 * — the point is to describe what the output *should* be, so a mismatch with
 * the recorded value means it is stale.
 */
export async function fingerprint() {
  const h = createHash('sha256');
  h.update(JSON.stringify({ ALGORITHM_VERSION, GROUPS, JPEG, WEBP, AVIF }));

  for (const group of Object.keys(GROUPS).sort()) {
    const dir = path.join(SRC, group);
    const files = (await readdir(dir)).filter((f) => !f.startsWith('.')).sort();
    for (const file of files) {
      h.update(`${group}/${file}`);
      h.update(createHash('sha256').update(await readFile(path.join(dir, file))).digest());
    }
  }

  return h.digest('hex');
}
