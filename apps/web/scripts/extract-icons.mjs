#!/usr/bin/env node
/**
 * Regenerates `components/ui/icon-paths.ts`.
 *
 * The storefront used to load the Material Icons webfont from Google and let a
 * ligature turn the word "close" into a glyph — 128 KB and two third-party
 * origins for about fifty shapes, none of which appeared until hydration had
 * run. The shapes are now inlined, and they are pulled out of that same font
 * rather than redrawn, so the site looks exactly as it did.
 *
 * Run it when you need an icon that is not in ICON_NAMES yet:
 *
 *     node scripts/extract-icons.mjs
 *
 * It needs Python with `fonttools` and `brotli` — it will make its own
 * virtualenv under the system temp directory the first time. It is deliberately
 * not wired into `pnpm build`: the output is committed, the font changes about
 * once a year, and a build should not depend on Google being reachable.
 */

import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

/**
 * Every icon the storefront can draw.
 *
 * Keep it sorted. `UspMarquee` takes its icon name from the CMS, so anything an
 * admin might reasonably pick belongs here — a name that is missing renders
 * nothing at all, which is quiet but still wrong.
 */
const ICON_NAMES = [
  'add', 'add_location_alt', 'arrow_back', 'arrow_drop_down', 'arrow_forward',
  'bakery_dining', 'bolt', 'bookmark_added', 'cake', 'cancel', 'chat',
  'check_circle', 'chevron_left', 'chevron_right', 'close', 'credit_card',
  'delete_outline', 'error', 'error_outline', 'expand_more', 'info',
  'info_outline', 'inventory_2', 'link', 'local_offer', 'local_shipping',
  'location_off', 'location_on', 'lock', 'login', 'logout', 'mail',
  'mark_email_read', 'menu', 'my_location', 'near_me', 'payments', 'person',
  'person_add', 'person_outline', 'place', 'radio_button_checked',
  'radio_button_unchecked', 'receipt_long', 'remove', 'schedule', 'search',
  'search_off', 'settings', 'shopping_bag', 'shopping_basket', 'storefront',
  'warning', 'wifi_off', 'wrong_location',
];

const here = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(here, '../components/ui/icon-paths.ts');

const work = mkdtempSync(join(tmpdir(), 'mm-icons-'));
const venv = join(tmpdir(), 'mm-icon-venv');

if (!existsSync(venv)) {
  console.log('creating virtualenv for fonttools…');
  execFileSync('python3', ['-m', 'venv', venv], { stdio: 'inherit' });
  execFileSync(join(venv, 'bin', 'pip'), ['install', '-q', 'fonttools', 'brotli'], {
    stdio: 'inherit',
  });
}

// The stylesheet names the current font revision; follow it rather than pinning
// a URL that Google rotates.
//
// The User-Agent has to be a full, believable one. Google serves a different
// stylesheet per browser, and anything it does not recognise gets the TTF
// fallback with no woff2 in it at all.
const UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';
const cssUrl = 'https://fonts.googleapis.com/icon?family=Material+Icons';
const css = execFileSync('curl', ['-sS', '-A', UA, cssUrl], {
  encoding: 'utf8',
});
const fontUrl = css.match(/https:\/\/[^)]*\.woff2/)?.[0];
if (!fontUrl) throw new Error('could not find a woff2 URL in the Material Icons CSS');

const fontPath = join(work, 'material-icons.woff2');
execFileSync('curl', ['-sS', '-o', fontPath, fontUrl]);

writeFileSync(join(work, 'names.json'), JSON.stringify(ICON_NAMES));
writeFileSync(
  join(work, 'extract.py'),
  `
import json, sys
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.misc.transform import Transform

names = json.load(open(sys.argv[1]))
f = TTFont(sys.argv[2])
upem = f['head'].unitsPerEm
cmap = f.getBestCmap()
gs = f.getGlyphSet()

# Ligature table: the font spells "close" as a sequence of ordinary letter
# glyphs and substitutes the icon for it. That substitution is the only way to
# get from a name to a glyph.
lig = {}
for first, entries in f['GSUB'].table.LookupList.Lookup[0].SubTable[0].ligatures.items():
    for e in entries:
        lig[first + ''.join(e.Component)] = e.LigGlyph

def glyph_for(name):
    return lig.get(''.join(cmap[ord(c)] for c in name if ord(c) in cmap))

# Font units to the 24x24 grid, and flip Y — SVG counts downwards, fonts up.
scale = 24.0 / upem
out, missing = {}, []
for n in names:
    g = glyph_for(n)
    if not g:
        missing.append(n)
        continue
    pen = SVGPathPen(gs, ntos=lambda v: f"{round(v, 2):g}")
    gs[g].draw(TransformPen(pen, Transform(scale, 0, 0, -scale, 0, 24)))
    out[n] = pen.getCommands()

if missing:
    print('MISSING: ' + ', '.join(missing), file=sys.stderr)
json.dump(out, open(sys.argv[3], 'w'))
`,
);

execFileSync(
  join(venv, 'bin', 'python'),
  [join(work, 'extract.py'), join(work, 'names.json'), fontPath, join(work, 'paths.json')],
  { stdio: 'inherit' },
);

const paths = JSON.parse(
  execFileSync('cat', [join(work, 'paths.json')], { encoding: 'utf8' }),
);

const entries = Object.keys(paths)
  .sort()
  .map((n) => `  ${/^[A-Za-z_$][\w$]*$/.test(n) ? n : JSON.stringify(n)}: '${paths[n]}',`)
  .join('\n');

writeFileSync(
  OUT,
  `// GENERATED — do not hand-edit. Run \`node scripts/extract-icons.mjs\`.
//
// The outline of every Material Icons glyph the storefront draws, pulled from
// the real font rather than redrawn, so these are the same shapes the site has
// always shown. To add one, put its name in ICON_NAMES in that script and
// re-run it.
//
// Coordinates are the standard 24x24 Material grid.

export const ICON_PATHS: Record<string, string> = {
${entries}
};

export type IconName = keyof typeof ICON_PATHS;
`,
);

console.log(`wrote ${Object.keys(paths).length} icons to ${OUT}`);
