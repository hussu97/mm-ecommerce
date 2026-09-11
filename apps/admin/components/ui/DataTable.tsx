'use client';

import { Fragment, useMemo, useState } from 'react';

import { cn } from '@/lib/utils';

/**
 * One list, two shapes: a table where there is room and cards where there is not.
 *
 * **The problem this exists for.** Every list in the console was a `<table>`
 * inside `overflow-x-auto`, which is correct in the sense that nothing spills
 * onto the page — and useless on a phone. Measured at 390px before this
 * existed: Translations hid 80% of its width behind a sideways drag, Staff 71%,
 * Branches 65%, Devices 63%, Admin Users 56%. What you actually saw was two of
 * seven columns, with the name column squeezed until "Chocolate Fudge
 * Celebration Cake" wrapped into six lines and made every row 250px tall. The
 * status, the actions and the numbers were all off-screen to the right, and
 * nothing on screen said so.
 *
 * A horizontal scrollbar is a reasonable answer for a *wide artefact* — a map,
 * a chart, a code block. It is the wrong answer for a data table, because the
 * columns are not one object: they are separate facts about a row, and a phone
 * can show them stacked perfectly well. So below `md` a row becomes a card.
 *
 * **Columns declare their own importance**, once, and both shapes read it:
 *
 *   `primary`    the row's identity — the card's title. One per table.
 *   `secondary`  a qualifier under the title: a slug, an email, a reference.
 *                Unlabelled, because a subtitle explains itself.
 *   `meta`       a labelled line in the card body. The default.
 *   `desktop`    dropped from the card entirely. For columns that are scanning
 *                aids in a wide grid and noise in a narrow one — a display-order
 *                handle, a row checkbox, a duplicate of the title.
 *
 * The alternative was a per-screen `hidden md:table-cell` on each column, which
 * is what a codebase ends up with when this component does not exist: the same
 * decision re-made 20 times, differently, and re-litigated every time a column
 * is added. Here a new column is one word.
 *
 * **The card is not a second implementation of the row.** Both shapes call the
 * same `render`, so a cell that shows a badge, a formatted price or a link
 * cannot drift between them.
 */

export type ColumnPriority = 'primary' | 'secondary' | 'meta' | 'desktop';

export type SortDirection = 'asc' | 'desc';
/** The active column (by its `sortKey`, defaulting to `header`) and direction. */
export interface SortState {
  key: string;
  direction: SortDirection;
}

export interface DataColumn<T> {
  /** Label, card field name, and React key. Unique within the table. */
  header: string;
  /**
   * A header cell that is itself a control — a select-all checkbox. Desktop
   * only, because a card list has no header row to hang it on; a screen whose
   * bulk actions must work on a phone needs a control of its own above the
   * list, not a column heading.
   */
  headerRender?: () => React.ReactNode;
  render: (row: T) => React.ReactNode;
  /** Applied to the `<th>`/`<td>` on desktop only — a card has no columns. */
  className?: string;
  /** How this column behaves on a phone. Defaults to `meta`. */
  priority?: ColumnPriority;
  /**
   * Turns the header into a sort control — a button with an ↑/↓ arrow. On a
   * client-side table (no `onSortChange` on the table) the rows are sorted in
   * place by `sortAccessor`; on a server-paginated one the table stays
   * controlled and only reports the click through `onSortChange`.
   */
  sortable?: boolean;
  /** Identity of this column in the sort state. Defaults to `header`. On a
   *  server-sorted table this is the value handed to `onSortChange` (e.g. the
   *  API field name). */
  sortKey?: string;
  /** The comparable value for client-side sorting. Numbers sort numerically,
   *  everything else by locale string; null/undefined always sort last. */
  sortAccessor?: (row: T) => string | number | null | undefined;
}

interface DataTableProps<T> {
  columns: DataColumn<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  /** Rendered right-aligned in the last cell, and as a footer row on a card. */
  actions?: (row: T) => React.ReactNode;
  /** Shown in place of the whole table when there are no rows. */
  empty?: React.ReactNode;
  /** Highlight, strike through, fade — whatever the screen means by it. */
  rowClassName?: (row: T) => string | undefined;
  /**
   * Detail revealed under a row — a payload, a diff, a set of line items.
   *
   * Return `null` for a row with nothing to show. On desktop it becomes a
   * full-width row beneath; on a phone it is drawn inside the card, which is
   * the only shape that makes sense there and the reason this belongs here
   * rather than being re-invented per log screen.
   */
  expanded?: (row: T) => React.ReactNode;
  /**
   * Opens the row. Applied to the whole row and the whole card.
   *
   * A card is a much better click target than a table row — it is the size of
   * a thumb rather than the height of one line — so this is where a list with
   * a detail page earns most of its mobile usability. Anything inside that has
   * its own click must stop propagation.
   */
  onRowClick?: (row: T) => void;
  className?: string;
  /**
   * Controlled sort — pass this with `onSortChange` when the rows are one
   * server-fetched page and sorting has to go back to the API. Leave both off
   * for a client-side table: mark columns `sortable` with a `sortAccessor` and
   * the table sorts itself, seeded by `defaultSort`.
   */
  sort?: SortState | null;
  onSortChange?: (sort: SortState) => void;
  /** Initial sort for an uncontrolled (client-side) table. */
  defaultSort?: SortState;
}

export const sortKeyOf = <T,>(c: DataColumn<T>) => c.sortKey ?? c.header;

/**
 * Sort a copy of `rows` by a column accessor. Numbers compare numerically, the
 * rest by locale string, and null/undefined always sink to the bottom whichever
 * way the column points. Shared so an externally-paginated table (which must
 * sort the whole list before slicing a page) orders rows exactly as the table
 * would if it held them all.
 */
export function sortByAccessor<T>(
  rows: T[],
  accessor: (row: T) => string | number | null | undefined,
  direction: SortDirection,
): T[] {
  const dir = direction === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = accessor(a);
    const vb = accessor(b);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir;
    return String(va).localeCompare(String(vb)) * dir;
  });
}

/** The next sort state when a sortable header is clicked. */
function nextSort(current: SortState | null | undefined, key: string): SortState {
  if (current && current.key === key) {
    return { key, direction: current.direction === 'asc' ? 'desc' : 'asc' };
  }
  return { key, direction: 'asc' };
}

function SortArrow({ active, direction }: { active: boolean; direction: SortDirection }) {
  return (
    <span className={cn('ml-1 text-[10px]', active ? 'text-primary' : 'text-gray-300')} aria-hidden>
      {active ? (direction === 'asc' ? '↑' : '↓') : '↕'}
    </span>
  );
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  actions,
  empty,
  rowClassName,
  expanded,
  onRowClick,
  className,
  sort,
  onSortChange,
  defaultSort,
}: DataTableProps<T>) {
  const controlled = onSortChange !== undefined;
  const [internalSort, setInternalSort] = useState<SortState | null>(defaultSort ?? null);
  const activeSort = controlled ? (sort ?? null) : internalSort;

  const sortableColumns = columns.filter(c => c.sortable);

  const applySort = (key: string) => {
    const next = nextSort(activeSort, key);
    if (controlled) onSortChange(next);
    else setInternalSort(next);
  };

  // Client-side tables sort their own rows; a controlled (server-sorted) table
  // renders whatever page it was handed.
  const sortedRows = useMemo(() => {
    if (controlled || !activeSort) return rows;
    const col = columns.find(c => sortKeyOf(c) === activeSort.key);
    if (!col?.sortAccessor) return rows;
    return sortByAccessor(rows, col.sortAccessor, activeSort.direction);
  }, [controlled, activeSort, rows, columns]);

  if (sortedRows.length === 0 && empty !== undefined) {
    return <>{empty}</>;
  }

  const primary = columns.find(c => c.priority === 'primary');
  const secondary = columns.filter(c => c.priority === 'secondary');
  // Anything not otherwise spoken for is a labelled line on the card. A column
  // with no `priority` is the common case and lands here, which is the right
  // default: showing a fact with its label is never wrong, it is only verbose.
  const meta = columns.filter(
    c => c.priority !== 'primary' && c.priority !== 'secondary' && c.priority !== 'desktop',
  );

  return (
    <div className={className}>
      {/* ── Sort control for the card list, below md ─────────────────────
          The cards have no header row to hang an arrow on, so a table that is
          sortable on the desktop keeps a compact select here rather than
          becoming unsortable on a phone. */}
      {sortableColumns.length > 0 && (
        <div className="md:hidden mb-2">
          <label className="flex items-center gap-2 text-[11px] font-body uppercase tracking-widest text-gray-400">
            Sort
            <select
              className="flex-1 rounded border border-gray-200 bg-white px-2 py-1.5 text-xs font-body text-gray-700"
              value={activeSort ? `${activeSort.key}:${activeSort.direction}` : ''}
              onChange={e => {
                const value = e.target.value;
                if (!value) return;
                const [key, direction] = value.split(':') as [string, SortDirection];
                if (controlled) onSortChange({ key, direction });
                else setInternalSort({ key, direction });
              }}
            >
              <option value="">Default</option>
              {sortableColumns.map(c => {
                const key = sortKeyOf(c);
                return (
                  <Fragment key={key}>
                    <option value={`${key}:asc`}>{c.header} ↑</option>
                    <option value={`${key}:desc`}>{c.header} ↓</option>
                  </Fragment>
                );
              })}
            </select>
          </label>
        </div>
      )}

      {/* ── Cards, below md ─────────────────────────────────────────────── */}
      <ul className="md:hidden space-y-2">
        {sortedRows.map(row => (
          <li
            key={rowKey(row)}
            onClick={onRowClick ? () => onRowClick(row) : undefined}
            className={cn(
              'rounded border border-gray-200 bg-white px-3.5 py-3',
              onRowClick && 'cursor-pointer active:bg-gray-50',
              rowClassName?.(row),
            )}
          >
            {primary && (
              <div className="text-sm font-body font-medium text-gray-800 break-words">
                {primary.render(row)}
              </div>
            )}
            {secondary.map(c => (
              <div key={c.header} className="mt-0.5 text-xs font-body text-gray-400 break-words">
                {c.render(row)}
              </div>
            ))}

            {meta.length > 0 && (
              // Two columns rather than a stack: the label is short and the
              // value is short, and stacking them doubled the card's height
              // for no gain. `minmax(0,1fr)` on the value is what lets a long
              // one wrap instead of pushing the card wide.
              <dl
                className={cn(
                  'grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1',
                  (primary || secondary.length > 0) && 'mt-2.5 border-t border-gray-100 pt-2.5',
                )}
              >
                {meta.map(c => (
                  <div key={c.header} className="contents">
                    <dt className="text-[11px] font-body uppercase tracking-widest text-gray-400 pt-0.5">
                      {c.header}
                    </dt>
                    <dd className="text-xs font-body text-gray-700 min-w-0 break-words">
                      {c.render(row)}
                    </dd>
                  </div>
                ))}
              </dl>
            )}

            {actions && (
              <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-gray-100 pt-2">
                {actions(row)}
              </div>
            )}

            {expanded?.(row) ? (
              <div className="mt-2.5 border-t border-gray-100 pt-2.5">{expanded(row)}</div>
            ) : null}
          </li>
        ))}
      </ul>

      {/* ── Table, md and up ──────────────────────────────────────────────
          The sideways scroll here is the deliberate one: at a desk a very wide
          table is better dragged than folded, and below `md` the card list
          above has already replaced it. `data-scroll-intent` says so to
          `scripts/mobile-audit.mjs`, which treats every unmarked horizontal
          scroller on a phone as a defect. */}
      <div
        data-scroll-intent="table"
        className="hidden md:block overflow-x-auto rounded border border-gray-200 bg-white"
      >
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50">
              {columns.map(c => {
                const key = sortKeyOf(c);
                const active = activeSort?.key === key;
                return (
                  <th
                    key={c.header}
                    aria-sort={
                      c.sortable
                        ? active
                          ? activeSort?.direction === 'asc'
                            ? 'ascending'
                            : 'descending'
                          : 'none'
                        : undefined
                    }
                    className={cn(
                      'px-3 py-2 text-left text-[11px] uppercase tracking-widest text-gray-500 font-body',
                      c.className,
                    )}
                  >
                    {c.headerRender ? (
                      c.headerRender()
                    ) : c.sortable ? (
                      <button
                        type="button"
                        onClick={() => applySort(key)}
                        className="inline-flex items-center uppercase tracking-widest hover:text-primary"
                      >
                        {c.header}
                        <SortArrow active={!!active} direction={active && activeSort ? activeSort.direction : 'asc'} />
                      </button>
                    ) : (
                      c.header
                    )}
                  </th>
                );
              })}
              {actions && <th className="px-3 py-2 w-40" />}
            </tr>
          </thead>
          <tbody>
            {sortedRows.map(row => {
              const detail = expanded?.(row);
              return (
                <Fragment key={rowKey(row)}>
                  <tr
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                    className={cn(
                      'border-b border-gray-100 hover:bg-gray-50',
                      !detail && 'last:border-0',
                      onRowClick && 'cursor-pointer',
                      rowClassName?.(row),
                    )}
                  >
                    {columns.map(c => (
                      <td key={c.header} className={cn('px-3 py-2 align-middle', c.className)}>
                        {c.render(row)}
                      </td>
                    ))}
                    {actions && (
                      <td className="px-3 py-2 text-right whitespace-nowrap">
                        <div className="flex items-center justify-end gap-2">{actions(row)}</div>
                      </td>
                    )}
                  </tr>
                  {detail ? (
                    <tr className="bg-gray-50">
                      <td colSpan={columns.length + (actions ? 1 : 0)} className="px-3 py-2">
                        {detail}
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/**
 * A text button inside a row or card — "Edit", "Delete", "Resend".
 *
 * Its own component because the console had this markup written out about forty
 * times, at `text-xs` with no height, which on a phone is a 16px-tall target
 * sitting 8px from its neighbour. Here it keeps the same compact look on
 * desktop and grows to a real target on a phone.
 */
export function RowAction({
  onClick,
  danger,
  disabled,
  children,
}: {
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'text-xs font-body hover:underline disabled:opacity-40 disabled:no-underline',
        'inline-flex items-center justify-center min-h-11 min-w-11 md:min-h-0 md:min-w-0',
        danger ? 'text-red-500' : 'text-primary',
      )}
    >
      {children}
    </button>
  );
}
