import { describe, expect, it } from 'vitest';
import { ICON_PATHS } from './icon-paths';

/**
 * `Icon` renders nothing for a name it does not have, on purpose — the CMS lets
 * an admin type any name into a USP row, and the word `local_shipping`
 * appearing on the page is worse than no icon at all.
 *
 * The cost of that choice is silence. Three of the six icons seeded into the
 * home page's USP strip by migration `054` — `favorite`, `spa` and
 * `card_giftcard` — were never added to the extraction list, so the marquee
 * shipped with half its icons missing and nothing anywhere said so. It was
 * spotted by looking at the page.
 *
 * This is the check that would have caught it: every icon name that ships in
 * seeded content has to be one the bundle can actually draw. It is a literal
 * list rather than a parse of the migration, because the migration is Python
 * and the point is to fail here, next to the paths, when somebody adds a row.
 */
const SEEDED_CONTENT_ICONS = [
  // apps/api/alembic/versions/054_seo_content_refresh.py — home `usps.items`
  'local_shipping',
  'schedule',
  'favorite',
  'spa',
  'card_giftcard',
  'place',
];

/** `UspMarquee.SPEED_ICON` — the one item the CMS cannot write. */
const SPEED_ICONS = ['bolt', 'schedule', 'local_shipping'];

describe('ICON_PATHS', () => {
  it.each(SEEDED_CONTENT_ICONS)('can draw the seeded USP icon %s', name => {
    expect(ICON_PATHS[name], `"${name}" is in seeded CMS content but not in the bundle — \
add it to ICON_NAMES in scripts/extract-icons.mjs and re-run the script`).toBeTruthy();
  });

  it.each(SPEED_ICONS)('can draw the delivery-speed icon %s', name => {
    expect(ICON_PATHS[name]).toBeTruthy();
  });

  it('holds real path data, not empty strings', () => {
    for (const [name, d] of Object.entries(ICON_PATHS)) {
      expect(d.length, `${name} has no path data`).toBeGreaterThan(10);
      expect(d.startsWith('M'), `${name} does not start with a moveto`).toBe(true);
    }
  });
});
