/**
 * The reporting window every tab is scoped by, and the tab vocabulary.
 *
 * Shared rather than passed down from the page because each tab fetches its
 * own report against the same window — that is what lets a manager change the
 * dates once and have every tab agree.
 */

export type TabKey =
  | 'sales'
  | 'payments'
  | 'taxes'
  | 'menu'
  | 'service'
  | 'branches'
  | 'tables'
  | 'inventory'
  | 'suppliers'
  | 'email';

/** Default window: the last 7 trading days, which is what a manager checks. */
export function defaultWindow() {
  const today = new Date();
  const from = new Date(today);
  from.setDate(from.getDate() - 6);
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  return { from: iso(from), to: iso(today) };
}

export type Window = { branch_id?: string; date_from?: string; date_to?: string };
