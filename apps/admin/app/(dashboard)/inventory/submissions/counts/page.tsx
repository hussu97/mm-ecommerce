'use client';

// Report submissions → Manual stock counts. ONLY the manual stock-take counts
// uploaded in admin (the CSV audits on the Counts tab, source_type
// `bulk_stock_audit`) — a branch's first one posts as an opening balance, every
// one after it as an inventory count. Count movements that came out of a shift
// report belong on the Shift reports tab, not here, so they are excluded (both
// share the `inventory_count`/`opening_balance` movement type, which is why
// filtering by type alone showed the same rows in both tabs). Its own filter bar
// (branch / date range / kind); all filtering is client-side over the rows
// loaded once on mount.

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { branchesApi, inventoryApi } from '@/lib/pos-api';
import type { Branch, InventoryTransaction } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Input, Pagination, Select, Spinner } from '@/components/ui';
import {
  DataTable,
  sortByAccessor,
  sortKeyOf,
  type DataColumn,
  type SortState,
} from '@/components/ui/DataTable';
import { formatCurrency, formatDateTime, formatQuantity } from '@/lib/utils';

// Within [from, to] inclusive on the YYYY-MM-DD prefix; an empty bound is
// ignored. A missing date is excluded once any bound is set.
function withinRange(date: string | null | undefined, from: string, to: string): boolean {
  if (!from && !to) return true;
  if (!date) return false;
  const d = date.slice(0, 10);
  if (from && d < from) return false;
  if (to && d > to) return false;
  return true;
}

const TYPE_LABELS: Record<string, string> = { inventory_count: 'Count', opening_balance: 'Opening balance' };

const netDelta = (tx: InventoryTransaction) =>
  tx.items.reduce((sum, line) => sum + Number(line.signed_quantity ?? 0), 0);

export default function StockCountsPage() {
  const router = useRouter();
  const [branches, setBranches] = useState<Branch[]>([]);
  const [rows, setRows] = useState<InventoryTransaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [branchId, setBranchId] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [typeFilter, setTypeFilter] = useState('');

  const [sort, setSort] = useState<SortState | null>(null);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);

  useEffect(() => {
    void branchesApi.list().then(setBranches).catch(() => setBranches([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    // The endpoint filters on a single type, so pull both and merge by id.
    Promise.all([
      inventoryApi.transactions({ type: 'inventory_count', limit: 2000 }),
      inventoryApi.transactions({ type: 'opening_balance', limit: 2000 }),
    ])
      .then(([counts, openings]) => {
        if (cancelled) return;
        const byId = new Map<string, InventoryTransaction>();
        for (const tx of [...counts, ...openings]) byId.set(tx.id, tx);
        // A count movement out of a shift report is that report's business, shown
        // on the Shift reports tab; here we want only the manual CSV stock-takes.
        setRows(Array.from(byId.values()).filter((tx) => tx.source_type === 'bulk_stock_audit'));
        setError('');
      })
      .catch((err) => { if (!cancelled) setError(err instanceof ApiError ? err.message : 'Failed to load stock counts.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => { setPage(1); }, [branchId, from, to, typeFilter, sort]);

  const branchName = useMemo(
    () => (id: string) => branches.find((b) => b.id === id)?.name ?? id,
    [branches],
  );

  const columns = useMemo<DataColumn<InventoryTransaction>[]>(() => [
    { header: 'Business date', sortable: true, sortAccessor: (row) => row.business_date, render: (row) => row.business_date },
    { header: 'Posted', sortable: true, sortAccessor: (row) => row.posted_at ?? null, render: (row) => row.posted_at ? formatDateTime(row.posted_at) : '—' },
    { header: 'Branch', sortable: true, sortAccessor: (row) => branchName(row.branch_id), render: (row) => branchName(row.branch_id) },
    { header: 'Posted by', sortable: true, sortAccessor: (row) => row.posted_by_name ?? null, render: (row) => row.posted_by_name ?? '—' },
    { header: 'Reference', priority: 'primary', sortable: true, sortAccessor: (row) => row.reference, render: (row) => <Link href={`/inventory/transactions/${row.id}`} className="text-primary hover:underline" onClick={(e) => e.stopPropagation()}>{row.reference}</Link> },
    { header: 'Items', className: 'text-right', sortable: true, sortAccessor: (row) => row.items.length, render: (row) => row.items.length },
    { header: 'Net delta', className: 'text-right', sortable: true, sortAccessor: (row) => netDelta(row), render: (row) => { const d = netDelta(row); return <span className={d < 0 ? 'text-red-600' : d > 0 ? 'text-green-700' : 'text-gray-400'}>{d === 0 ? '—' : `${d > 0 ? '+' : ''}${formatQuantity(d)}`}</span>; } },
    { header: 'Value impact', className: 'text-right', sortable: true, sortAccessor: (row) => row.total_cost, render: (row) => formatCurrency(row.total_cost) },
  ], [branchName]);

  const visible = useMemo(() => {
    const filtered = rows.filter((row) => {
      if (branchId && row.branch_id !== branchId) return false;
      if (typeFilter && row.type !== typeFilter) return false;
      if (!withinRange(row.business_date ?? row.posted_at, from, to)) return false;
      return true;
    });
    const activeColumn = sort ? columns.find((c) => sortKeyOf(c) === sort.key) : undefined;
    if (sort && activeColumn?.sortAccessor) {
      return sortByAccessor(filtered, activeColumn.sortAccessor, sort.direction);
    }
    const stamp = (tx: InventoryTransaction) => tx.posted_at ?? tx.created_at;
    return [...filtered].sort((a, b) => stamp(b).localeCompare(stamp(a)));
  }, [rows, branchId, from, to, typeFilter, sort, columns]);

  const pages = Math.max(1, Math.ceil(visible.length / perPage));
  const pageRows = visible.slice((page - 1) * perPage, page * perPage);

  return (
    <div className="max-w-[1400px] space-y-4">
      <p className="text-sm text-gray-500">Manual stock-take counts uploaded in admin (the CSV audits on the Counts tab) — a branch&apos;s first count is an opening balance, every count after it a stock count. Counts that came from a shift report show under Shift reports, not here. Click a row to see the counted lines and their value impact.</p>
      <div className="flex flex-wrap items-end gap-3">
        <Select label="Branch" value={branchId} onChange={(e) => setBranchId(e.target.value)} placeholder="All branches" className="w-56" options={branches.map((b) => ({ value: b.id, label: b.name }))} />
        <Input label="From" type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="w-44" />
        <Input label="To" type="date" value={to} onChange={(e) => setTo(e.target.value)} className="w-44" />
        <Select label="Status" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} placeholder="All kinds" className="w-48" options={Object.entries(TYPE_LABELS).map(([value, label]) => ({ value, label }))} />
      </div>
      {error && <p className="bg-red-50 p-2 text-sm text-red-800">{error}</p>}
      {loading ? <Spinner /> : (
        <>
          <DataTable
            rows={pageRows}
            rowKey={(row) => row.id}
            onRowClick={(row) => router.push(`/inventory/transactions/${row.id}`)}
            empty={<span className="text-sm text-gray-500">No stock counts match these filters.</span>}
            columns={columns}
            sort={sort}
            onSortChange={setSort}
          />
          <Pagination page={page} pages={pages} total={visible.length} perPage={perPage} onPageChange={setPage} onPerPageChange={setPerPage} label="counts" />
        </>
      )}
    </div>
  );
}
