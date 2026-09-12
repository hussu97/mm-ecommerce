'use client';

// Report submissions → Transfers & returns. The read-only log of inter-branch
// transfer ORDERS — the admin-raised parents that fan out to many destination
// branches. Each row drills into the parent detail. Its own filter bar (source
// or destination branch / date range / derived status); all filtering is
// client-side over the rows loaded once on mount.

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

// The order's total quantity, rolled up across every item row.
const totalQty = (order: TransferOrder) =>
  order.total_by_item.reduce((sum, line) => sum + Number(line.total_quantity || 0), 0);

// Whether any child leg was received short or over what was sent.
const hasVariance = (order: TransferOrder) =>
  order.children.some((child) =>
    child.items.some((line) => Number(line.received_quantity) !== Number(line.sent_quantity)),
  );

// Whether any child leg SHIPPED with a sending variance — it has been sent and a
// line left in a quantity other than what was requested.
const hasSendingVariance = (order: TransferOrder) =>
  order.children.some((child) =>
    child.sent_transaction_id != null &&
    child.items.some((line) => Number(line.sent_quantity) !== Number(line.quantity)),
  );

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
    { header: 'Source', sortable: true, sortAccessor: (row) => branchName(row.source_branch_id), render: (row) => <span className="text-xs">{branchName(row.source_branch_id)}</span> },
    { header: 'Destinations', sortable: true, sortAccessor: (row) => row.children.length, render: (row) => <span className="text-xs" title={row.children.map((child) => branchName(child.branch_id)).join(', ')}>{row.children.length} branch{row.children.length === 1 ? '' : 'es'}</span> },
    { header: 'Status', sortable: true, sortAccessor: (row) => row.status, render: (row) => <Badge variant={transferStatusVariant(row.status)}>{transferStatusLabel(row.status)}</Badge> },
    { header: 'Items', className: 'text-right', sortable: true, sortAccessor: (row) => row.total_by_item.length, render: (row) => row.total_by_item.length },
    { header: 'Total qty', className: 'text-right', sortable: true, sortAccessor: (row) => totalQty(row), render: (row) => formatQuantity(totalQty(row)) },
    { header: 'Sending variance', sortable: true, sortAccessor: (row) => (hasSendingVariance(row) ? 1 : 0), render: (row) => hasSendingVariance(row) ? <Badge variant="danger">Sent ≠ requested</Badge> : '—' },
    { header: 'Receiving variance', sortable: true, sortAccessor: (row) => (hasVariance(row) ? 1 : 0), render: (row) => hasVariance(row) ? <Badge variant="danger">Received ≠ sent</Badge> : '—' },
    { header: 'Created', sortable: true, sortAccessor: (row) => row.created_at, render: (row) => formatDateTime(row.created_at) },
  ], [branchName]);

  const visible = useMemo(() => {
    const filtered = rows.filter((row) => {
      if (branchId && row.source_branch_id !== branchId && !row.children.some((c) => c.branch_id === branchId)) return false;
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
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-gray-500">Transfer orders across the branches — one source branch fanning out to many. Click a row to open the parent, its per-branch legs and the movement report. A sending-variance flag marks any leg shipped in a different quantity than requested; a receiving-variance flag marks any leg received short or over what was sent.</p>
        <Link href="/inventory/transfers/new" className="inline-flex items-center gap-1.5 px-4 py-2 bg-primary text-white text-xs font-body font-medium uppercase tracking-wider hover:opacity-90 transition-opacity">New transfer order</Link>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <Select label="Branch" value={branchId} onChange={(e) => setBranchId(e.target.value)} placeholder="All branches" className="w-56" options={branches.map((b) => ({ value: b.id, label: b.name }))} />
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
            onRowClick={(row) => router.push(`/inventory/transfers/${row.id}`)}
            empty={<span className="text-sm text-gray-500">No transfer orders match these filters.</span>}
            columns={columns}
            sort={sort}
            onSortChange={setSort}
          />
          <Pagination page={page} pages={pages} total={visible.length} perPage={perPage} onPageChange={setPage} onPerPageChange={setPerPage} label="transfer orders" />
        </>
      )}
    </div>
  );
}
