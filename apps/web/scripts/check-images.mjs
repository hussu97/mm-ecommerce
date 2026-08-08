/**
 * Fails the build if `public/images` is not the output of the current
 * `image-src` and the current encoder settings.
 *
 * `optimize-images.mjs` used to run as part of `build`, which meant every CI
 * build deleted 45 committed files and re-encoded them into byte-identical
 * copies — 149s of the web app's 171s build step, paid twice per deploy
 * because `vercel build` runs the same npm script. The output is committed, so
 * the work was only ever needed when `image-src` changed.
 *
 * Dropping the step outright would let an edit to `image-src` ship without the
 * matching re-encode, silently serving the old artwork. So the encode is now
 * explicit (`pnpm --filter web images`) and this stands in its place: it reads
 * the source bytes and the settings, and compares them to what was recorded
 * the last time the optimiser ran. Sub-second, and it cannot go stale
 * unnoticed.
 */
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fingerprint, MANIFEST, ROOT } from './images.config.mjs';

const rel = (p) => path.relative(ROOT, p);

const fail = (message) => {
  console.error(`\n✗ public/images is out of date.\n\n${message}\n`);
  console.error('  Regenerate and commit the result:\n');
  console.error('      pnpm --filter web images\n');
  process.exit(1);
};

const expected = await fingerprint();

let recorded;
try {
  recorded = JSON.parse(await readFile(MANIFEST, 'utf8'));
} catch (err) {
  if (err.code === 'ENOENT') fail(`  ${rel(MANIFEST)} is missing.`);
  throw err;
}

if (recorded.fingerprint !== expected) {
  fail(
    `  image-src or the encoder settings changed since the images were built.\n` +
      `    recorded: ${recorded.fingerprint}\n` +
      `    current:  ${expected}`,
  );
}

console.log('public/images is up to date with image-src.');
