'use client';

import { Button, Input, LoadError, Pagination, Spinner } from '@/components/ui';
import { DataTable, type DataColumn, type SortState } from '@/components/ui/DataTable';
import { Page } from '@/components/ui/Page';

/**
 * The one shape every list screen in the console wears.
 *
 * Three patterns used to draw a list — the config-driven `ResourcePage`, a
 * hand-wired `useApiList` + `DataTable` (orders, customers), and a couple of
 * fully hand-rolled ones — and each drew its own header, search box, error
 * banner, empty state and pagination, slightly differently. This is the single
 * chrome they now share: title, a right-aligned controls cluster (filters,
 * search, a primary action), an optional full-width filter row, the standard
 * loading / error / empty states, the table, and pagination.
 *
 * It is deliberately presentational — it owns no data. The caller decides where
 * the rows come from (a `useApiList` page, or a client-side slice) and hands in
 * `rows`, `pagination`, `loading`/`error`, and the column defs. That keeps it
 * usable by both the server-paginated pages and `ResourcePage`'s client-side
 * slice without a mode flag.
 */

export interface ListPagePagination {
  page: number;
  pages: number;
  total: number;
  perPage: number;
  onPageChange: (page: number) => void;
  onPerPageChange: (perPage: number) => void;
  /** Noun for the count line, e.g. "orders". Defaults to "items". */
  label?: string;
}

export interface ListPageProps<T> {
  title: string;
  description?: string;
  /** A search box in the header controls cluster. Omit for lists with no search. */
  search?: { value: string; onChange: (value: string) => void; placeholder?: string };
  /** Extra controls in the header cluster, left of the search box (filter selects). */
  toolbar?: React.ReactNode;
  /** The page's primary action, right of search — usually a "New" button. */
  primaryAction?: React.ReactNode;
  /** A full-width filter row rendered under the header (see `FilterBar`). */
  filterBar?: React.ReactNode;

  loading?: boolean;
  /** Inline error banner (a mutation or a client-side load failure). */
  error?: string;
  /** List-load failure, rendered through the shared `<LoadError>` with a retry. */
  loadError?: string;
  onRetry?: () => void;

  // ── DataTable passthrough ──
  columns: DataColumn<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  actions?: (row: T) => React.ReactNode;
  expanded?: (row: T) => React.ReactNode;
  onRowClick?: (row: T) => void;
  rowClassName?: (row: T) => string | undefined;
  sort?: SortState | null;
  onSortChange?: (sort: SortState) => void;
  defaultSort?: SortState;
  stickyHeader?: boolean;
  /** A custom empty state; otherwise `emptyMessage` renders the standard one. */
  empty?: React.ReactNode;
  emptyMessage?: string;

  /** Omit for an unpaginated list. */
  pagination?: ListPagePagination | null;

  maxWidth?: 'full' | 'reading';
  /** Rendered after the table/pagination — a contextual panel. */
  belowTable?: React.ReactNode;
  /** Rendered last, outside the flow — modals, dialogs. */
  children?: React.ReactNode;
}

export function ListPage<T>({
  title,
  description,
  search,
  toolbar,
  primaryAction,
  filterBar,
  loading = false,
  error,
  loadError,
  onRetry,
  columns,
  rows,
  rowKey,
  actions,
  expanded,
  onRowClick,
  rowClassName,
  sort,
  onSortChange,
  defaultSort,
  stickyHeader,
  empty,
  emptyMessage = 'Nothing here yet.',
  pagination,
  maxWidth = 'full',
  belowTable,
  children,
}: ListPageProps<T>) {
  const hasControls = Boolean(search || toolbar || primaryAction);
  return (
    <Page maxWidth={maxWidth}>
      <header className="mb-5 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="font-display text-xl text-primary tracking-wide">{title}</h1>
          {description && <p className="text-xs text-gray-500 font-body mt-1">{description}</p>}
        </div>
        {hasControls && (
          <div className="flex items-center gap-2">
            {toolbar}
            {search && (
              <Input
                placeholder={search.placeholder ?? 'Search…'}
                value={search.value}
                onChange={(e) => search.onChange(e.target.value)}
                className="w-full sm:w-48"
              />
            )}
            {primaryAction}
          </div>
        )}
      </header>

      {filterBar}

      {error && (
        <div className="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          {error}
        </div>
      )}
      {loadError && <LoadError message={loadError} onRetry={onRetry} />}

      {loading ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : (
        <DataTable<T>
          columns={columns}
          rows={rows}
          rowKey={rowKey}
          actions={actions}
          expanded={expanded}
          onRowClick={onRowClick}
          rowClassName={rowClassName}
          sort={sort}
          onSortChange={onSortChange}
          defaultSort={defaultSort}
          stickyHeader={stickyHeader}
          empty={
            empty ?? (
              <p className="py-16 text-center text-sm text-gray-400 font-body">{emptyMessage}</p>
            )
          }
        />
      )}

      {pagination && !loading && (
        <Pagination
          page={pagination.page}
          pages={pagination.pages}
          total={pagination.total}
          perPage={pagination.perPage}
          onPageChange={pagination.onPageChange}
          onPerPageChange={pagination.onPerPageChange}
          label={pagination.label}
        />
      )}

      {belowTable}
      {children}
    </Page>
  );
}

// `Button` is re-exported for the common `primaryAction` so callers can build a
// "New" action without a second import path.
export { Button as ListPageAction };
