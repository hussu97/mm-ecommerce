'use client';

import { Fragment } from 'react';

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
}: DataTableProps<T>) {
  if (rows.length === 0 && empty !== undefined) {
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
      {/* ── Cards, below md ─────────────────────────────────────────────── */}
      <ul className="md:hidden space-y-2">
        {rows.map(row => (
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
              {columns.map(c => (
                <th
                  key={c.header}
                  className={cn(
                    'px-3 py-2 text-left text-[11px] uppercase tracking-widest text-gray-500 font-body',
                    c.className,
                  )}
                >
                  {c.headerRender ? c.headerRender() : c.header}
                </th>
              ))}
              {actions && <th className="px-3 py-2 w-40" />}
            </tr>
          </thead>
          <tbody>
            {rows.map(row => {
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
