/**
 * Render the reel's text overlays as transparent PNGs, plus the end card.
 *
 * Rendered in Chromium rather than with ffmpeg's drawtext because drawtext has
 * no letter-spacing, and the brand's wide-tracked caps are most of its look.
 * This also pulls the site's real Jost + Raleway from Google Fonts, so the
 * overlays and the footage underneath are set in the same typefaces.
 *
 *   node scripts/marketing/render-reel-overlays.mjs
 */
import { chromium } from 'playwright-core';
import fs from 'node:fs';
import path from 'node:path';
import { REPO_ROOT, WORK, resolveChromium } from './lib.mjs';

const OUT = path.join(WORK, 'ov');
fs.mkdirSync(OUT, { recursive: true });

// Inlined: a file:// image will not load into a setContent page.
const LOGO =
  'data:image/jpeg;base64,' +
  fs
    .readFileSync(path.join(REPO_ROOT, 'apps/web/public/images/logos/black_and_white_logo.jpeg'))
    .toString('base64');

const HEAD = `
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Jost:wght@200;300;400;500;600&family=Raleway:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  :root{
    --maroon:#8a5a64; --secondary:#d6acab; --tertiary:#dfbdc1;
    --cream:#f9f2ec; --ink:#2f3237;
  }
  *{margin:0;padding:0;box-sizing:border-box}
  html,body{width:1080px;height:1920px;background:transparent;overflow:hidden}
  body{font-family:'Jost',sans-serif;-webkit-font-smoothing:antialiased}
  .stage{position:relative;width:1080px;height:1920px}

  /* The storefront is cream and brightly lit end to end, so white type needs
     the scrim to carry nearly all of the contrast. Anything lighter than this
     and the copy disappears into the product photography. */
  .scrim{position:absolute;inset:auto 0 0 0;height:1220px;
    background:linear-gradient(to top,
      rgba(18,12,14,.96) 0%,
      rgba(18,12,14,.93) 40%,
      rgba(18,12,14,.78) 62%,
      rgba(18,12,14,.42) 80%,
      rgba(18,12,14,0) 100%)}

  .lower{position:absolute;left:78px;right:78px;bottom:440px;color:#fff}
  .eyebrow{font-family:'Raleway',sans-serif;font-weight:500;font-size:33px;
    letter-spacing:.42em;text-transform:uppercase;color:#eccfd2;margin-bottom:26px;
    text-shadow:0 2px 18px rgba(0,0,0,.6)}
  .line{font-weight:300;font-size:104px;line-height:1.05;letter-spacing:-.015em;
    text-shadow:0 4px 44px rgba(0,0,0,.55)}
  .line b{font-weight:500}
  .line .soft{color:var(--tertiary);font-weight:300}

  /* Promo pill, on screen for most of the reel: the offer should be readable
     in any frame someone happens to pause on. */
  .pill{position:absolute;left:0;right:0;bottom:196px;display:flex;justify-content:center}
  .pill .inner{display:flex;align-items:center;gap:26px;
    background:var(--maroon);border-radius:999px;padding:30px 52px;
    box-shadow:0 18px 60px rgba(0,0,0,.4)}
  .pill .txt{font-family:'Raleway',sans-serif;font-weight:500;font-size:33px;
    letter-spacing:.2em;text-transform:uppercase;color:#fff;white-space:nowrap}
  .pill .code{font-family:'Raleway',sans-serif;font-weight:600;font-size:33px;letter-spacing:.24em;
    color:#fff;border:2px dashed rgba(255,255,255,.85);border-radius:10px;padding:10px 20px}
</style>`;

const cards = {
  t1: `<div class="stage"><div class="scrim"></div>
    <div class="lower">
      <div class="eyebrow">Melting Moments Cakes</div>
      <div class="line">Our new website<br><b>is live.</b></div>
    </div></div>`,

  t2: `<div class="stage"><div class="scrim"></div>
    <div class="lower">
      <div class="eyebrow">Shop the whole menu</div>
      <div class="line">Brownies. Cookies.<br><span class="soft">Cookie melts.</span></div>
    </div></div>`,

  t3: `<div class="stage"><div class="scrim"></div>
    <div class="lower">
      <div class="eyebrow">Order in minutes</div>
      <div class="line">Delivered to all<br><b>7 Emirates.</b></div>
    </div></div>`,

  pill: `<div class="stage"><div class="pill"><div class="inner">
      <span class="txt">15% off your first 3 orders</span>
      <span class="code">NEW</span>
    </div></div></div>`,
};

// `mix-blend-mode: multiply` drops the logo jpeg's white box out against the
// cream — the only version of the mark in the repo has a baked-in background.
const endCard = `
<div class="stage" style="background:var(--cream);display:flex;flex-direction:column;
     align-items:center;justify-content:center;text-align:center;padding:0 90px">

  <img src="${LOGO}" style="width:430px;mix-blend-mode:multiply;margin-bottom:34px">

  <div style="font-family:'Raleway',sans-serif;font-weight:500;font-size:34px;
       letter-spacing:.46em;text-transform:uppercase;color:var(--maroon);margin-bottom:64px">
    The new site is live
  </div>

  <div style="font-weight:500;font-size:200px;line-height:.92;color:var(--maroon);
       letter-spacing:-.02em">15% OFF</div>
  <div style="font-weight:300;font-size:66px;line-height:1.2;color:var(--ink);margin-top:18px">
    your first <b style="font-weight:500">3 orders</b>
  </div>

  <div style="display:flex;align-items:center;gap:28px;margin-top:74px">
    <span style="font-family:'Raleway',sans-serif;font-weight:400;font-size:38px;
          letter-spacing:.26em;text-transform:uppercase;color:var(--ink)">Use code</span>
    <span style="font-family:'Raleway',sans-serif;font-weight:600;font-size:52px;letter-spacing:.26em;
          color:#fff;background:var(--maroon);border-radius:14px;padding:18px 40px">NEW</span>
  </div>
  <div style="font-family:'Raleway',sans-serif;font-weight:400;font-size:29px;letter-spacing:.18em;
       text-transform:uppercase;color:#8b8188;margin-top:26px">at checkout</div>

  <div style="width:220px;height:1px;background:var(--secondary);margin:86px 0 58px"></div>

  <div style="font-weight:400;font-size:52px;color:var(--maroon);letter-spacing:.01em">
    meltingmomentscakes.com
  </div>
  <div style="font-weight:300;font-size:36px;color:#8b8188;margin-top:22px">
    Delivered across all 7 Emirates
  </div>
</div>`;

const browser = await chromium.launch({ executablePath: resolveChromium() });
const page = await browser.newPage({
  viewport: { width: 1080, height: 1920 },
  deviceScaleFactor: 1,
});

async function render(name, body, opaque = false) {
  await page.setContent(`<!doctype html><html><head>${HEAD}</head><body>${body}</body></html>`, {
    waitUntil: 'load',
  });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(900); // webfonts settle; a FOUT here bakes in permanently
  await page.screenshot({ path: path.join(OUT, `${name}.png`), omitBackground: !opaque });
  console.log('rendered', name);
}

for (const [name, body] of Object.entries(cards)) await render(name, body);
await render('endcard', endCard, true);

console.log('->', OUT);
await browser.close();
