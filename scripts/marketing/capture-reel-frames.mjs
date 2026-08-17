/**
 * Capture the storefront as a frame sequence for the launch reel.
 *
 * Drives the live site in a mobile viewport and screenshots every frame while
 * scrolling, so the footage is genuine browsing rather than a pan across a
 * tall stitched screenshot. Output is frames/f#####.png under WORK, at a size
 * that maps 1:1 onto a 1080x1920 reel.
 *
 *   node scripts/marketing/capture-reel-frames.mjs
 *
 * See README.md in this directory for the full pipeline.
 */
import { chromium } from 'playwright-core';
import fs from 'node:fs';
import path from 'node:path';
import { WORK, resolveChromium } from './lib.mjs';

const OUT = path.join(WORK, 'frames');
fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });

const BASE = process.env.SITE || 'https://meltingmomentscakes.com';
const FPS = 30;

// 360x640 css @ dsf 3 -> 1080x1920 exactly: 9:16 full bleed, no crop, no bars.
const VIEWPORT = { width: 360, height: 640 };
const DSF = 3;

let n = 0;
const frame = async (page) => {
  await page.screenshot({ path: path.join(OUT, `f${String(n++).padStart(5, '0')}.png`) });
};

// easeInOutCubic — a linear scroll starts and stops abruptly and reads as a
// machine scrubbing the page; this reads as a thumb.
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

/**
 * Load a page and force every lazy image to decode before capture starts.
 * Without the pre-scroll the first pass down the page films its own image
 * placeholders popping in.
 */
async function prime(page, url) {
  // Not `networkidle`: analytics keeps a connection open and it never settles.
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForLoadState('load', { timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(2500);

  await page.evaluate(async () => {
    document.documentElement.style.scrollBehavior = 'auto';
    const H = document.body.scrollHeight;
    for (let y = 0; y < H; y += 400) {
      window.scrollTo(0, y);
      await new Promise((r) => setTimeout(r, 60));
    }
    window.scrollTo(0, 0);
  });
  await page.waitForTimeout(1500);

  return page.evaluate(() => document.body.scrollHeight);
}

async function hold(page, seconds) {
  for (let i = 0; i < Math.round(seconds * FPS); i++) await frame(page);
}

async function scrollShot(page, from, to, seconds) {
  const total = Math.round(seconds * FPS);
  for (let i = 0; i < total; i++) {
    const y = from + (to - from) * ease(i / (total - 1));
    await page.evaluate((y) => window.scrollTo(0, y), Math.round(y));
    await frame(page);
  }
}

const browser = await chromium.launch({ executablePath: resolveChromium() });
const ctx = await browser.newContext({
  viewport: VIEWPORT,
  deviceScaleFactor: DSF,
  isMobile: true,
  hasTouch: true,
  userAgent:
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1',
  locale: 'en-AE',
});
const page = await ctx.newPage();

const marks = {};
const mark = (name) => (marks[name] = n / FPS);

// ── 1. Home: hero + promo banner, then down through the bestsellers ─────
mark('home');
await prime(page, `${BASE}/en`);
await hold(page, 2.2);
await scrollShot(page, 0, 1150, 2.2);
await hold(page, 0.9);
await scrollShot(page, 1150, 2500, 2.2);
await hold(page, 0.8);

// ── 2. Brownies category ────────────────────────────────────────────────
mark('brownies');
let H = await prime(page, `${BASE}/en/cat-brownies`);
await hold(page, 1.0);
await scrollShot(page, 0, Math.min(1500, H - 640), 2.6);
await hold(page, 0.6);

// ── 3. Cookie melts ─────────────────────────────────────────────────────
mark('cookiemelt');
H = await prime(page, `${BASE}/en/cat-cookiemelt`);
await hold(page, 0.9);
await scrollShot(page, 0, Math.min(1200, H - 640), 2.2);
await hold(page, 0.6);

// ── 4. Back to the promo banner to hand off to the end card ─────────────
mark('promo');
await prime(page, `${BASE}/en`);
await hold(page, 1.8);

marks._end = n / FPS;
fs.writeFileSync(path.join(WORK, 'marks.json'), JSON.stringify(marks, null, 2));
console.log(`frames: ${n}  duration: ${(n / FPS).toFixed(2)}s  ->  ${OUT}`);
console.log(marks);

await browser.close();
