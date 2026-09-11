'use client';

// Report submissions → Transfers & returns. The read-only log of inter-branch
// transfer and return documents. Its own filter bar (branch / date range /
// status); all filtering is client-side over the rows loaded once on mount.

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { branchesApi, inventoryApi, type TransferOrder } from '@/lib/pos-api';
import type { Branch } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Input, Pagination, Select, Spinner } from '@/components/ui';
import {
  DataTable,
  sortByAccessor,
  sortKeyOf,
  type DataColumn,
  type SortState,
} from '@/components/ui/DataTable';
import { formatDateTime, formatQuantity } from '@/lib/utils';
import { transferLineVaries, transferReceivingStatus } from '../../_shared';

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

const totalOf = (order: TransferOrder, pick: (line: TransferOrder['items'][number]) => string) =>
  order.items.reduce((sum, line) => sum + Number(pick(line) || 0), 0);

export default function TransfersPage() {
  const router = useRouter();
  const [branches, setBranches] = useState<Branch[]>([]);
  const [rows, setRows] = useState<TransferOrder[]>([]);
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
    inventoryApi.transferOrders({ limit: 2000 })
      .then((data) => { if (!cancelled) { setRows(data); setError(''); } })
      .catch((err) => { if (!cancelled) setError(err instanceof ApiError ? err.message : 'Failed to load transfers.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => { setPage(1); }, [branchId, from, to, statusFilter, sort]);

  const branchName = useMemo(
    () => (id: string) => branches.find((b) => b.id === id)?.name ?? id,
    [branches],
  );

  const statuses = useMemo(() => Array.from(new Set(rows.map((r) => r.status))).sort(), [rows]);

  const columns = useMemo<DataColumn<TransferOrder>[]>(() => [
    { header: 'Reference', priority: 'primary', sortable: true, sortAccessor: (row) => row.reference, render: (row) => <Link href={`/inventory/transfers/${row.id}`} className="text-primary hover:underline" onClick={(e) => e.stopPropagation()}>{row.reference}</Link> },
    { header: 'Kind', sortable: true, sortAccessor: (row) => row.kind, render: (row) => <Badge variant={row.kind === 'return' ? 'warning' : 'neutral'}>{row.kind}</Badge> },
    { header: 'From → To', sortable: true, sortAccessor: (row) => branchName(row.source_branch_id), render: (row) => <span className="text-xs">{branchName(row.source_branch_id)} → {branchName(row.branch_id)}</span> },
    { header: 'Receiving status', sortable: true, sortAccessor: (row) => transferReceivingStatus(row).label, render: (row) => { const s = transferReceivingStatus(row); return <Badge variant={s.variant}>{s.label}</Badge>; } },
    { header: 'Sent', className: 'text-right', sortable: true, sortAccessor: (row) => totalOf(row, (line) => line.sent_quantity), render: (row) => formatQuantity(totalOf(row, (line) => line.sent_quantity)) },
    { header: 'Received', className: 'text-right', sortable: true, sortAccessor: (row) => totalOf(row, (line) => line.received_quantity), render: (row) => formatQuantity(totalOf(row, (line) => line.received_quantity)) },
    { header: 'Variance', sortable: true, sortAccessor: (row) => (row.items.some(transferLineVaries) ? 1 : 0), render: (row) => row.items.some(transferLineVaries) ? <Badge variant="danger">Variance</Badge> : '—' },
    { header: 'Created', sortable: true, sortAccessor: (row) => row.created_at, render: (row) => formatDateTime(row.created_at) },
  ], [branchName]);

  const visible = useMemo(() => {
    const filtered = rows.filter((row) => {
      if (branchId && row.source_branch_id !== branchId && row.branch_id !== branchId) return false;
      if (statusFilter && row.status !== statusFilter) return false;
      if (!withinRange(row.created_at, from, to)) return false;
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
    <div className="max-w-[1400px] space-y-4">
      <p className="text-sm text-gray-500">Transfer and return documents across the branches. Click a row to open it. A variance flag marks any document whose received quantity differs from what was sent.</p>
      <div className="flex flex-wrap items-end gap-3">
        <Select label="Branch" value={branchId} onChange={(e) => setBranchId(e.target.value)} placeholder="All branches" className="w-56" options={branches.map((b) => ({ value: b.id, label: b.name }))} />
        <Input label="From" type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="w-44" />
        <Input label="To" type="date" value={to} onChange={(e) => setTo(e.target.value)} className="w-44" />
        <Select label="Status" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} placeholder="All statuses" className="w-56" options={statuses.map((s) => ({ value: s, label: s.replaceAll('_', ' ') }))} />
      </div>
      {error && <p className="bg-red-50 p-2 text-sm text-red-800">{error}</p>}
      {loading ? <Spinner /> : (
        <>
          <DataTable
            rows={pageRows}
            rowKey={(row) => row.id}
            onRowClick={(row) => router.push(`/inventory/transfers/${row.id}`)}
            empty={<span className="text-sm text-gray-500">No transfers or returns match these filters.</span>}
            columns={columns}
            sort={sort}
            onSortChange={setSort}
          />
          <Pagination page={page} pages={pages} total={visible.length} perPage={perPage} onPageChange={setPage} onPerPageChange={setPerPage} label="transfers" />
        </>
      )}
    </div>
  );
}
