/**
 * Workflow rule W10: every event in `analytics.ts` has a row in the Umami doc.
 *
 * The rule is followed — 71 events, 71 rows, and a changelog that records
 * semantic changes the rule does not even ask for. It has never had a test,
 * which means it has been holding on care alone; the cost of it slipping is a
 * dashboard that silently stops counting something, and nobody notices a
 * number that was never there.
 *
 * The event *names* are checked in full. Payload *fields* are mostly prose and
 * cannot be verified from here, with one exception that has already bitten us
 * (F-WEB-14): `payment_method_selected.method` is an enumerated value, and
 * `apple_pay` was added in code without a doc row — a report segmenting on
 * `method` silently had no bucket for it. That one field is now typed as a union
 * in `analytics.ts` and checked against the `method (…)` list in the doc, so the
 * value half cannot drift there. The "fired from" column is still prose.
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

/** The `method` values the `paymentMethodSelected` helper is typed to emit. */
function paymentMethodsInCode(): Set<string> {
  const src = readFileSync(ANALYTICS, 'utf8');
  const helper = src.match(/paymentMethodSelected:[\s\S]*?method:\s*([^;]+);/);
  if (!helper) return new Set();
  return new Set([...helper[1].matchAll(/'([a-z_]+)'/g)].map((m) => m[1]));
}

/** The `method (a | b | …)` values documented for `payment_method_selected`.
 *  Read off the whole row line — the cell uses escaped pipes (`\|`) inside the
 *  parentheses, so splitting on `|` would cut the list in half. */
function paymentMethodsInDoc(): Set<string> {
  const doc = readFileSync(DOC, 'utf8');
  const row = doc.split('\n').find((l) => l.includes('`payment_method_selected`'));
  const paren = row?.match(/method\s*\(([^)]*)\)/);
  if (!paren) return new Set();
  return new Set([...paren[1].matchAll(/`([a-z_]+)`/g)].map((m) => m[1]));
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

  it('documents every payment method the code can emit', () => {
    const code = paymentMethodsInCode();
    const doc = paymentMethodsInDoc();
    // Guard the matchers: an empty set either side would pass the equality below
    // for the wrong reason.
    expect(code.size, 'could not read the method union from analytics.ts').toBeGreaterThan(1);
    expect(doc.size, 'could not read the method (…) list from the doc').toBeGreaterThan(1);
    expect(
      [...code].sort(),
      "payment_method_selected's `method` values in analytics.ts and the " +
        'doc have drifted — update the Custom Events Reference row and add a ' +
        'Changelog entry (a value the dashboard cannot segment on is invisible)',
    ).toEqual([...doc].sort());
  });
});
