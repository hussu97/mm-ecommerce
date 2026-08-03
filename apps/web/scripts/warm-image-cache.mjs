/**
 * Requests every product image at every width the storefront's `sizes` can
 * resolve to, so no real visitor is the one who pays for the encode.
 *
 * `/_next/image` caches per (url, width, quality, format) and produces the
 * derivative on the first request for a combination. Measured against
 * production, that first request took between 0.5s and 5.7s to first byte —
 * the encode itself, not the transfer — while a warm one came back in 0.4s.
 * With a 39-product catalogue the whole grid is a few hundred transforms, which
 * is a bounded one-off rather than something that scales with traffic.
 *
 * Run after adding products or changing the artwork:
 *
 *     node scripts/warm-image-cache.mjs https://www.meltingmomentscakes.com
 */
const SITE = (process.argv[2] ?? 'https://www.meltingmomentscakes.com').replace(/\/$/, '');
const API =
  process.argv[3] ?? 'https://api.meltingmomentscakes.com/api/v1';

// The widths a browser can actually land on. Product cards resolve to 384-1080
// across phone and desktop DPRs; the product page's 50vw at 2x is what reaches
// for 1920. Anything outside this is in `deviceSizes` but unreachable from the
// `sizes` the pages declare.
const WIDTHS = [384, 640, 750, 828, 1080, 1200, 1920];
const QUALITY = 75;

// AVIF is what every current browser negotiates and is the expensive one to
// encode. WebP only serves Safari older than 16, so warming it doubles the
// transform count to cover a shrinking tail — leave it to be produced on demand.
const ACCEPT = 'image/avif,image/webp,image/*,*/*';

const CONCURRENCY = 6;

async function productImages() {
  const res = await fetch(`${API}/products?per_page=200`);
  if (!res.ok) throw new Error(`catalogue fetch failed: ${res.status}`);
  const body = await res.json();
  const urls = new Set();
  for (const p of body.items ?? []) for (const u of p.image_urls ?? []) urls.add(u);
  return [...urls];
}

const images = await productImages();
const jobs = images.flatMap((url) =>
  WIDTHS.map((w) => ({
    url,
    w,
    href: `${SITE}/_next/image?url=${encodeURIComponent(url)}&w=${w}&q=${QUALITY}`,
  })),
);

console.log(`${images.length} images x ${WIDTHS.length} widths = ${jobs.length} requests\n`);

let done = 0;
let warmed = 0;
let alreadyWarm = 0;
let failed = 0;
let slowest = 0;

const queue = jobs[Symbol.iterator]();

async function worker() {
  for (const job of queue) {
    const started = performance.now();
    try {
      const res = await fetch(job.href, { headers: { Accept: ACCEPT } });
      await res.arrayBuffer();
      const ms = performance.now() - started;
      slowest = Math.max(slowest, ms);

      if (!res.ok) {
        failed++;
        console.log(`  ${res.status} w=${job.w} ${job.url.split('/').pop()}`);
      } else if ((res.headers.get('x-vercel-cache') ?? '').includes('HIT')) {
        alreadyWarm++;
      } else {
        warmed++;
      }
    } catch (err) {
      failed++;
      console.log(`  ERR w=${job.w} ${job.url.split('/').pop()}: ${err.message}`);
    }
    if (++done % 50 === 0) console.log(`  ${done}/${jobs.length}`);
  }
}

const startedAll = performance.now();
await Promise.all(Array.from({ length: CONCURRENCY }, worker));

console.log(
  `\n${jobs.length} requests in ${((performance.now() - startedAll) / 1000).toFixed(0)}s` +
    ` — ${warmed} newly produced, ${alreadyWarm} already cached, ${failed} failed.` +
    ` Slowest single request ${(slowest / 1000).toFixed(1)}s.`,
);

if (failed) process.exitCode = 1;
