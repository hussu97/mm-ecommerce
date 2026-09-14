'use client';

import { useCallback, useMemo, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';

import { Badge, Button, Input, LoadError, Pagination, Select, TabBar } from '@/components/ui';
import { DataTable, type DataColumn, type SortState } from '@/components/ui/DataTable';
import { Modal, StatusBadge } from '@/components/pos/ResourcePage';
import { RecipeEditor } from '@/components/inventory/RecipeEditor';
import { useApiList } from '@/hooks/useApiList';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { inventoryApi, type RecipeOwnerRow } from '@/lib/pos-api';
import { formatQuantity } from '@/lib/utils';

type OwnerKind = 'product' | 'modifier_option' | 'inventory_item';
type ActiveFilter = 'all' | 'active' | 'inactive';
type RecipeFilter = 'all' | 'with' | 'without';

interface RecipeOwnersPageProps {
  ownerKind: OwnerKind;
  /** Singular noun for empty states and the popup title, e.g. "product item". */
  noun: string;
  /** Show the inventory-item kind badge (made / semi-finished). */
  showKind?: boolean;
  /** Placeholder for the search box. */
  searchPlaceholder: string;
}

const RECIPE_FILTER_OPTIONS = [
  { value: 'all', label: 'All recipes' },
  { value: 'with', label: 'Has a recipe' },
  { value: 'without', label: 'No recipe' },
];

/** Read-only glance at a recipe's lines: each inventory item with its quantity. */
function IngredientSummary({ row }: { row: RecipeOwnerRow }) {
  if (row.ingredients.length === 0) {
    return <span className="text-gray-300">—</span>;
  }
  const MAX = 6;
  const shown = row.ingredients.slice(0, MAX);
  const extra = row.ingredients.length - shown.length;
  const basisLabel =
    row.basis === 'batch'
      ? `Per batch of ${formatQuantity(row.batch_yield ?? '0')}`
      : 'Per unit';
  return (
    <div className="space-y-0.5 text-[11px] font-body text-gray-600">
      <div className="mb-1 inline-block rounded bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-gray-500">
        {basisLabel}
      </div>
      {shown.map((ing, i) => (
        <div key={i}>
          <span className="tabular-nums text-gray-800">
            {formatQuantity(ing.quantity)} {ing.unit}
          </span>{' '}
          {ing.name}
        </div>
      ))}
      {extra > 0 && <div className="text-gray-400">+{extra} more</div>}
    </div>
  );
}

function RecipeStatusBadge({ row }: { row: RecipeOwnerRow }) {
  if (row.recipe_status === 'active') {
    return (
      <span className="flex flex-wrap items-center gap-1">
        <Badge variant="success">Active v{row.active_version_number}</Badge>
        {row.draft_version_number != null && (
          <span className="text-[11px] text-amber-600">draft v{row.draft_version_number}</span>
        )}
      </span>
    );
  }
  if (row.recipe_status === 'draft') {
    return <Badge variant="warning">Draft v{row.draft_version_number}</Badge>;
  }
  return <Badge variant="neutral">None</Badge>;
}

export function RecipeOwnersPage({ ownerKind, noun, showKind, searchPlaceholder }: RecipeOwnersPageProps) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();

  const [search, setSearch] = useState('');
  const debouncedSearch = useDebouncedValue(search);
  const [active, setActive] = useState<ActiveFilter>('all');
  const [recipe, setRecipe] = useState<RecipeFilter>('all');
  const [sort, setSort] = useState<SortState>({ key: 'name', direction: 'asc' });
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  // The recipe editor popup, holding a queue of owner ids so a multi-select can
  // step through several recipes without returning to the list between each.
  const [popup, setPopup] = useState<{ queue: string[]; index: number } | null>(() => {
    const open = searchParams.get('open');
    return open ? { queue: [open], index: 0 } : null;
  });

  const fetchOwners = useCallback(
    (page: number, perPage: number) =>
      inventoryApi.recipeOwners(
        ownerKind,
        {
          search: debouncedSearch.trim() || undefined,
          active,
          recipe,
          sort: sort.key,
          sort_dir: sort.direction,
        },
        page,
        perPage,
      ),
    [ownerKind, debouncedSearch, active, recipe, sort.key, sort.direction],
  );

  const { items, total, pages, page, perPage, setPage, setPerPage, loading, loadError, refetch } =
    useApiList<RecipeOwnerRow>({ paginate: 'server', fetch: fetchOwners });

  const labelById = useMemo(() => new Map(items.map((row) => [row.id, row.name])), [items]);
  const openLabel = (id: string) => labelById.get(id) ?? `this ${noun}`;

  const openOne = (id: string) => setPopup({ queue: [id], index: 0 });
  const openSelected = () => {
    const queue = items.filter((row) => selectedIds.has(row.id)).map((row) => row.id);
    if (queue.length) setPopup({ queue, index: 0 });
  };

  const closePopup = () => {
    setPopup(null);
    // Strip a deep-link ?open= so a later refetch cannot reopen the popup.
    if (searchParams.get('open')) router.replace(pathname);
    void refetch();
  };

  const toggleSelect = (id: string) =>
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const allOnPageSelected = items.length > 0 && items.every((row) => selectedIds.has(row.id));
  const toggleSelectAll = () =>
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allOnPageSelected) items.forEach((row) => next.delete(row.id));
      else items.forEach((row) => next.add(row.id));
      return next;
    });

  const columns: DataColumn<RecipeOwnerRow>[] = [
    {
      header: 'select',
      priority: 'desktop',
      className: 'w-8',
      headerRender: () => (
        <input
          type="checkbox"
          checked={allOnPageSelected}
          onChange={toggleSelectAll}
          className="accent-primary"
          aria-label="Select all on page"
        />
      ),
      render: (row) => (
        <input
          type="checkbox"
          checked={selectedIds.has(row.id)}
          onChange={() => toggleSelect(row.id)}
          onClick={(e) => e.stopPropagation()}
          className="accent-primary"
          aria-label={`Select ${row.name}`}
        />
      ),
    },
    {
      header: 'Name',
      priority: 'primary',
      sortable: true,
      sortKey: 'name',
      render: (row) => (
        <div>
          <div className="font-medium text-gray-800">{row.name}</div>
          {row.secondary && <div className="text-[11px] text-gray-400 font-body">{row.secondary}</div>}
          {row.product_names.length > 0 && (
            <div className="mt-0.5 text-[11px] text-gray-500 font-body">
              <span className="text-gray-400">Used in:</span> {row.product_names.join(', ')}
            </div>
          )}
        </div>
      ),
    },
    ...(showKind
      ? [
          {
            header: 'Kind',
            priority: 'meta' as const,
            render: (row: RecipeOwnerRow) =>
              row.kind ? <Badge variant="warning">{row.kind.replaceAll('_', ' ')}</Badge> : null,
          },
        ]
      : []),
    {
      header: 'Status',
      priority: 'meta',
      render: (row) => <StatusBadge active={row.is_active} />,
    },
    {
      header: 'Recipe',
      priority: 'meta',
      sortable: true,
      sortKey: 'recipe_status',
      render: (row) => <RecipeStatusBadge row={row} />,
    },
    {
      header: 'Ingredients',
      priority: 'meta',
      render: (row) => <IngredientSummary row={row} />,
    },
  ];

  const activeOwner = popup ? popup.queue[popup.index] : null;

  return (
    <div>
      <LoadError message={loadError} onRetry={refetch} />

      {/* Filters */}
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="max-w-xs flex-1">
          <Input
            placeholder={searchPlaceholder}
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <div className="w-44">
          <Select
            options={RECIPE_FILTER_OPTIONS}
            value={recipe}
            onChange={(e) => {
              setRecipe(e.target.value as RecipeFilter);
              setPage(1);
            }}
          />
        </div>
      </div>

      {/* Active / Inactive tabs */}
      <TabBar
        tabs={[
          { key: 'all', label: 'All' },
          { key: 'active', label: 'Active' },
          { key: 'inactive', label: 'Inactive' },
        ]}
        active={active}
        onChange={(key) => {
          setActive(key as ActiveFilter);
          setPage(1);
        }}
      />

      {/* Selection bar */}
      {selectedIds.size > 0 && (
        <div className="flex items-center gap-3 bg-primary/10 border border-primary/30 px-4 py-2.5 mb-4">
          <span className="text-xs font-body text-primary font-medium">{selectedIds.size} selected</span>
          <button
            onClick={() => setSelectedIds(new Set())}
            className="text-xs font-body text-gray-500 hover:text-primary underline"
          >
            Clear
          </button>
          <div className="flex-1" />
          <Button size="sm" onClick={openSelected}>
            Open selected ({items.filter((row) => selectedIds.has(row.id)).length})
          </Button>
        </div>
      )}

      {/* Table */}
      {loading ? (
        <div className="space-y-2">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-12 bg-gray-100 animate-pulse" />
          ))}
        </div>
      ) : (
        <DataTable<RecipeOwnerRow>
          columns={columns}
          rows={items}
          rowKey={(row) => row.id}
          stickyFirstColumn={false}
          sort={sort}
          onSortChange={(next) => {
            setSort(next);
            setPage(1);
          }}
          empty={<p className="py-10 text-center text-sm text-gray-400 font-body">No {noun}s match these filters.</p>}
          actions={(row) => (
            <Button variant="ghost" size="sm" onClick={() => openOne(row.id)}>
              {row.has_recipe ? 'Edit recipe' : 'Add recipe'}
            </Button>
          )}
        />
      )}

      <Pagination
        page={page}
        pages={pages}
        total={total}
        perPage={perPage}
        onPageChange={setPage}
        onPerPageChange={(p) => {
          setPerPage(p);
          setPage(1);
        }}
        label={`${noun}s`}
      />

      {/* Recipe editor popup */}
      {popup && activeOwner && (
        <Modal title={`Recipe for ${openLabel(activeOwner)}`} onClose={closePopup} wide>
          <RecipeEditor
            key={activeOwner}
            ownerKind={ownerKind}
            ownerId={activeOwner}
            ownerLabel={openLabel(activeOwner)}
          />
          {popup.queue.length > 1 && (
            <div className="mt-4 flex items-center justify-between border-t border-gray-100 pt-3">
              <span className="text-xs text-gray-500">
                {popup.index + 1} of {popup.queue.length} selected
              </span>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={popup.index === 0}
                  onClick={() => setPopup((p) => (p ? { ...p, index: p.index - 1 } : p))}
                >
                  Previous
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={popup.index === popup.queue.length - 1}
                  onClick={() => setPopup((p) => (p ? { ...p, index: p.index + 1 } : p))}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}
