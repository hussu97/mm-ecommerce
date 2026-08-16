import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const REPO_ROOT = path.resolve(fileURLToPath(import.meta.url), '../../..');

/**
 * Where the 500+ MB of intermediate PNG frames live. Deliberately outside the
 * repo — `branding/` is gitignored, but half a gigabyte of frames still has no
 * business sitting in a working tree. Override with WORK=/some/dir.
 */
export const WORK = process.env.WORK || path.join(os.tmpdir(), 'mm-reel');

/**
 * Resolve a Chromium that Playwright can drive.
 *
 * `playwright-core` ships no browser and refuses to launch unless the cached
 * build matches the version it expects, which is rarely the one already on the
 * machine. Rather than force a ~150 MB download per version bump, take the
 * newest Chromium already in the Playwright cache — any recent build renders
 * this page identically.
 */
export function resolveChromium() {
  if (process.env.PLAYWRIGHT_CHROMIUM) return process.env.PLAYWRIGHT_CHROMIUM;

  const cache =
    process.platform === 'darwin'
      ? path.join(os.homedir(), 'Library/Caches/ms-playwright')
      : path.join(os.homedir(), '.cache/ms-playwright');

  const rel =
    process.platform === 'darwin'
      ? 'chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'
      : 'chrome-linux/chrome';

  const builds = fs.existsSync(cache)
    ? fs
        .readdirSync(cache)
        .filter((d) => /^chromium-\d+$/.test(d))
        .sort((a, b) => Number(b.split('-')[1]) - Number(a.split('-')[1]))
    : [];

  for (const b of builds) {
    const exe = path.join(cache, b, rel);
    if (fs.existsSync(exe)) return exe;
  }

  throw new Error(
    `No cached Chromium found in ${cache}.\n` +
      `Run "npx playwright install chromium", or point PLAYWRIGHT_CHROMIUM at a Chrome binary.`
  );
}
