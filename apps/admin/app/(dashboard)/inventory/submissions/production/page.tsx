'use client';

// Report submissions → Production report. The read-only log of production orders
// — items an admin raised for a source branch to make (alongside a transfer, or
// on their own). Each row drills into the order detail. Its own filter bar
// (source branch / status / business-date range); filtering is client-side over
// the rows loaded once on mount, the same shape as the Transfers & returns tab.

import { useEffect, useMemo, useState } from 'react';
import { branchesApi, inventoryApi } from '@/lib/pos-api';
import type { Branch, ProductionOrderSummary } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Input, Pagination, Select, Spinner } from '@/components/ui';
import {
  DataTable,
  sortByAccessor,
  sortKeyOf,
  type DataColumn,
  type SortState,
} from '@/components/ui/DataTable';
import { formatDateTime } from '@/lib/utils';
import { transferStatusLabel, transferStatusVariant } from '../../_shared';

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

export default function ProductionSubmissionsPage() {
  const [branches, setBranches] = useState<Branch[]>([]);
  const [rows, setRows] = useState<ProductionOrderSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [branchId, setBranchId] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  const [sort, setSort] = useState<SortState | null>(null);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);

  useEffect(() => {
    void branchesApi.list().then(setBranches).catch(() => setBranches([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    inventoryApi.productionOrders()
      .then((data) => { if (!cancelled) { setRows(data); setError(''); } })
      .catch((err) => { if (!cancelled) setError(err instanceof ApiError ? err.message : 'Failed to load production orders.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => { setPage(1); }, [branchId, from, to, statusFilter, sort]);

  const branchName = useMemo(
    () => (id: string) => branches.find((b) => b.id === id)?.name ?? id,
    [branches],
  );

  const statuses = useMemo(() => Array.from(new Set(rows.map((r) => r.status))).sort(), [rows]);

  const columns = useMemo<DataColumn<ProductionOrderSummary>[]>(() => [
    { header: 'Reference', priority: 'primary', sortable: true, sortAccessor: (row) => row.reference, render: (row) => <span className="text-primary">{row.reference}</span> },
    { header: 'Source', sortable: true, sortAccessor: (row) => row.source_branch_name ?? branchName(row.source_branch_id), render: (row) => <span className="text-xs">{row.source_branch_name ?? branchName(row.source_branch_id)}</span> },
    { header: 'Business date', sortable: true, sortAccessor: (row) => row.business_date, render: (row) => row.business_date },
    { header: 'Status', sortable: true, sortAccessor: (row) => row.status, render: (row) => <Badge variant={transferStatusVariant(row.status)}>{transferStatusLabel(row.status)}</Badge> },
    { header: 'Lines', className: 'text-right', sortable: true, sortAccessor: (row) => row.line_count, render: (row) => row.line_count },
    { header: 'Produced', className: 'text-right', sortable: true, sortAccessor: (row) => row.produced_count, render: (row) => row.produced_count },
    { header: 'Pending', className: 'text-right', sortable: true, sortAccessor: (row) => row.pending_count, render: (row) => row.pending_count },
    { header: 'Cancelled', className: 'text-right', sortable: true, sortAccessor: (row) => row.cancelled_count, render: (row) => row.cancelled_count > 0 ? row.cancelled_count : '—' },
    { header: 'Created', sortable: true, sortAccessor: (row) => row.created_at, render: (row) => formatDateTime(row.created_at) },
  ], [branchName]);

  const visible = useMemo(() => {
    const filtered = rows.filter((row) => {
      if (branchId && row.source_branch_id !== branchId) return false;
      if (statusFilter && row.status !== statusFilter) return false;
      if (!withinRange(row.business_date, from, to)) return false;
      return true;
    });
    const activeColumn = sort ? columns.find((c) => sortKeyOf(c) === sort.key) : undefined;
    if (sort && activeColumn?.sortAccessor) {
      return sortByAccessor(filtered, activeColumn.sortAccessor, sort.direction);
    }
    return [...filtered].sort((a, b) => b.created_at.localeCompare(a.created_at));
  }, [rows, branchId, from, to, statusFilter, sort, columns]);

  const pages = Math.max(1, Math.ceil(visible.length / perPage));
  const pageRows = visible.slice((page - 1) * perPage, page * perPage);

  return (
    <div className="space-y-4">
      <p className="text-sm text-gray-500">Production orders raised for a source branch to make — from the transfer-and-production grid or on their own. Click a row to open the order, its planned versus produced quantities and the linked ledger movements.</p>
      <div className="flex flex-wrap items-end gap-3">
        <Select label="Source branch" value={branchId} onChange={(e) => setBranchId(e.target.value)} placeholder="All branches" className="w-56" options={branches.map((b) => ({ value: b.id, label: b.name }))} />
        <Input label="From" type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="w-44" />
        <Input label="To" type="date" value={to} onChange={(e) => setTo(e.target.value)} className="w-44" />
        <Select label="Status" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} placeholder="All statuses" className="w-56" options={statuses.map((s) => ({ value: s, label: transferStatusLabel(s) }))} />
      </div>
      {error && <p className="bg-red-50 p-2 text-sm text-red-800">{error}</p>}
      {loading ? <Spinner /> : (
        <>
          <DataTable
            rows={pageRows}
            rowKey={(row) => row.id}
            getRowHref={(row) => `/inventory/submissions/production/${row.id}`}
            empty={<span className="text-sm text-gray-500">No production orders match these filters.</span>}
            columns={columns}
            sort={sort}
            onSortChange={setSort}
          />
          <Pagination page={page} pages={pages} total={visible.length} perPage={perPage} onPageChange={setPage} onPerPageChange={setPerPage} label="production orders" />
        </>
      )}
    </div>
  );
}
