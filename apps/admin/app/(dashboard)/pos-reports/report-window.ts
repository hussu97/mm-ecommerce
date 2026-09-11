/**
 * The reporting window every tab is scoped by.
 *
 * Shared rather than passed down from one page because each tab is its own
 * route now and fetches its own report against the same window. The window
 * lives in the URL (`?from=&to=&branch=`) so that whichever tab is showing —
 * and a reload, or a shared link — all read the identical window. That is what
 * lets a manager change the dates once and have every tab agree.
 */

import type { ReadonlyURLSearchParams } from 'next/navigation';

/** Default window: the last 7 trading days, which is what a manager checks. */
export function defaultWindow() {
  const today = new Date();
  const from = new Date(today);
  from.setDate(from.getDate() - 6);
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  return { from: iso(from), to: iso(today) };
}

export type Window = { branch_id?: string; date_from?: string; date_to?: string };

/**
 * Read the reporting window out of the URL search params.
 *
 * A missing `from`/`to` key (a first visit, before `ReportWindow` has seeded
 * the URL) falls back to the default 7-day window, so every tab fetches the
 * same last-week figures the old in-page default used to. A *present but empty*
 * key is honoured as "no bound", which is what clearing a date picker means.
 */
export function windowFromParams(sp: ReadonlyURLSearchParams | URLSearchParams): {
  branch: string;
  from: string;
  to: string;
  window: Window;
} {
  const def = defaultWindow();
  const from = sp.has('from') ? (sp.get('from') ?? '') : def.from;
  const to = sp.has('to') ? (sp.get('to') ?? '') : def.to;
  const branch = sp.get('branch') ?? '';
  return {
    branch,
    from,
    to,
    window: {
      branch_id: branch || undefined,
      date_from: from || undefined,
      date_to: to || undefined,
    },
  };
}
