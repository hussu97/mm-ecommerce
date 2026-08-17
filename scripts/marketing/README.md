# Launch reel

Builds a 1080x1920 / 30fps promo reel for Instagram Reels, TikTok and Stories
out of the live storefront — real mobile footage, not a slideshow of
screenshots.

## Run it

Needs `ffmpeg` (`brew install ffmpeg`) and a Chromium in the Playwright cache
(`npx playwright install chromium`).

`playwright-core` is intentionally **not** a workspace dependency: adding it
would touch `pnpm-lock.yaml`, which the deploy workflow's path filter treats as
a web + admin change and would redeploy production for a marketing script. It
lives in this directory's own `package.json` instead, outside the pnpm
workspace globs (`apps/*`, `packages/*`):

```bash
cd scripts/marketing && npm i
```

Node resolves it from the script's own directory upward, so run the scripts
from the repo root as normal:

```bash
node scripts/marketing/capture-reel-frames.mjs
node scripts/marketing/render-reel-overlays.mjs
./scripts/marketing/build-reel.sh
```

Result: `branding/marketing/melting-moments-launch-reel.mp4` (~13 MB, 21.9s).
`branding/` is gitignored — the video is a build output, not a source asset.

Intermediates (~500 MB of PNG frames) go to `$TMPDIR/mm-reel`; set `WORK` to
put them elsewhere, and `SITE` to film a staging URL instead of production.

## How it works

| Step | What it does |
|------|--------------|
| `capture-reel-frames.mjs` | Drives the site at 360x640 @3x — exactly 1080x1920, so no crop or letterbox — and screenshots every frame while scrolling on an ease-in-out curve. |
| `render-reel-overlays.mjs` | Renders the text cards as transparent PNGs and the end card as an opaque one, in Chromium with the site's own Jost + Raleway. |
| `build-reel.sh` | Composites the overlays over the footage with alpha fades, then crossfades into the end card. |

Two things that look like detours but aren't:

- **Overlays are rendered in a browser, not by ffmpeg.** `drawtext` has no
  letter-spacing, and the brand's wide-tracked caps are most of its look.
- **The scrim behind the copy is very dark** (0.96 at the base). The storefront
  is cream and brightly lit throughout; at gentler values the white type sinks
  into the product photography. Verified frame by frame — if you re-time the
  reel, re-check contrast on the category shots, which are the worst case.

## The cut

| Time | Shot | Copy |
|------|------|------|
| 0.0–8.3s | Home hero + promo banner, into bestsellers | "Our new website **is live.**" |
| 8.3–12.5s | Brownies category | "Brownies. Cookies. Cookie melts." |
| 12.5–16.2s | Cookie melts | "Delivered to all **7 Emirates.**" |
| 4.3–17.6s | — | Persistent pill: *15% off your first 3 orders — NEW* |
| 18.0–21.9s | End card | Logo, **15% OFF**, code **NEW**, domain |

Every claim in the copy is one the storefront already makes: the 15%/code NEW
banner and delivery to all 7 Emirates.
**Before posting, confirm the `NEW` promo code is actually live** — the site
banner advertising it and a usable row in `promo_codes` are two different
things, and the reel is only honest if both are true.

Timings in `build-reel.sh` are tied to the shot boundaries in
`capture-reel-frames.mjs`; change the shot list and you must re-time the
overlays. The capture writes `marks.json` to the work dir with the real segment
start times to re-time against.
