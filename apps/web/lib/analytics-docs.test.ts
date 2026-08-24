/**
 * Workflow rule W10: every event in `analytics.ts` has a row in the Umami doc.
 *
 * The rule is followed — 71 events, 71 rows, and a changelog that records
 * semantic changes the rule does not even ask for. It has never had a test,
 * which means it has been holding on care alone; the cost of it slipping is a
 * dashboard that silently stops counting something, and nobody notices a
 * number that was never there.
 *
 * Only the *names* are checked. The doc's "fired from" column is prose and
 * cannot be verified from here — it has drifted before, and closing the name
 * half is what is cheaply possible.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const ANALYTICS = join(__dirname, 'analytics.ts');
const DOC = join(__dirname, '..', '..', '..', 'docs', 'umami-analytics-setup.md');

/** Event names passed to `track(...)`. */
function eventsInCode(): Set<string> {
  const src = readFileSync(ANALYTICS, 'utf8');
  return new Set([...src.matchAll(/\btrack\(\s*'([a-z0-9_]+)'/g)].map((m) => m[1]));
}

/** Event names in the reference table's first column. */
function eventsInDoc(): Set<string> {
  const doc = readFileSync(DOC, 'utf8');
  return new Set([...doc.matchAll(/^\|\s*`([a-z0-9_]+)`\s*\|/gm)].map((m) => m[1]));
}

describe('analytics events and their documentation', () => {
  it('documents every event the code fires', () => {
    const undocumented = [...eventsInCode()].filter((e) => !eventsInDoc().has(e)).sort();

    expect(
      undocumented,
      'add these to the Custom Events Reference table in docs/umami-analytics-setup.md, ' +
        'with a Changelog row — an event the dashboard does not know about is not counted',
    ).toEqual([]);
  });

  it('does not document events the code no longer fires', () => {
    const code = eventsInCode();
    const stale = [...eventsInDoc()].filter((e) => !code.has(e)).sort();

    expect(
      stale,
      'these are in the doc and fire nowhere. A goal or funnel built on one of ' +
        'them reports zero forever, which reads as a broken funnel rather than a stale doc',
    ).toEqual([]);
  });

  it('fires a plausible number of events, so a broken matcher is visible', () => {
    // Both sets being empty would pass the two tests above.
    expect(eventsInCode().size).toBeGreaterThan(50);
    expect(eventsInDoc().size).toBeGreaterThan(50);
  });
});
