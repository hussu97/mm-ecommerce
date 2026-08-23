/**
 * The two admin conventions from CLAUDE.md that had no teeth.
 *
 * Both were followed. That is exactly why they needed a test: a convention
 * nothing checks is followed until the day somebody has a reason not to, and
 * the pagination one had already drifted in coverage — the shared control was
 * used everywhere it appeared, and eleven tables did not appear.
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, sep } from 'node:path';
import { describe, expect, it } from 'vitest';

const ADMIN = join(__dirname, '..');

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry === '.next') continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

const SOURCES = walk(ADMIN).filter((f) => !/\.test\.tsx?$/.test(f));

describe('workflow rule W8 — admin pagination', () => {
  it('offers exactly the standard page sizes, in order', () => {
    const ui = readFileSync(join(ADMIN, 'components/ui/index.tsx'), 'utf8');
    const match = ui.match(/const PER_PAGE_OPTIONS = \[([^\]]+)\]/);

    expect(match, 'PER_PAGE_OPTIONS has moved or been renamed').toBeTruthy();
    expect(match![1].split(',').map((n) => Number(n.trim()))).toEqual([
      50, 100, 200, 500, 1000, 2000,
    ]);
  });

  it('declares those sizes in one place', () => {
    // A second array of page sizes anywhere is the drift this rule exists to
    // stop: two controls offering different options on adjacent screens.
    const offenders = SOURCES.filter((f) => {
      if (f.endsWith(join('components', 'ui', 'index.tsx'))) return false;
      return /\[\s*(?:10|20|25|50)\s*,\s*\d+\s*,\s*\d+/.test(readFileSync(f, 'utf8'));
    }).map((f) => f.slice(ADMIN.length + 1));

    expect(offenders, 'these look like a second page-size list').toEqual([]);
  });
});

describe('convention 9 — one request path', () => {
  it('no screen calls fetch directly', () => {
    // `lib/api.ts` owns `request()`, and with it the 401 refresh-and-retry.
    // Two copies of a CSV download bypassed it and threw on an expired
    // session instead of refreshing; that is what this stops recurring.
    const SANCTIONED = [join('lib', 'api.ts')];
    const offenders: string[] = [];

    for (const file of SOURCES) {
      const rel = file.slice(ADMIN.length + 1);
      if (SANCTIONED.some((s) => rel === s)) continue;
      if (rel.startsWith('scripts' + sep)) continue;
      const body = readFileSync(file, 'utf8');
      // `refetch(` and `prefetch(` are not `fetch(`.
      if (/(^|[^A-Za-z0-9_.])fetch\s*\(/m.test(body)) offenders.push(rel);
    }

    expect(offenders, 'these bypass lib/api.ts request()').toEqual([]);
  });
});
