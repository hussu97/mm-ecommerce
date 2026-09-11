'use client';

// Report submissions: everything the shop submits for review in one place —
// the shift inventory reports from the register (approved here) AND the
// inter-branch transfer & return documents (the read-only log that used to live
// under the Transfers tab). Two clearly-labelled tables share one branch filter.

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import {
  branchesApi,
  inventoryApi,
  type ShiftInventoryReport,
  type TransferOrder,
} from '@/lib/pos-api';
import type { Branch, InventoryTransaction } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Input, Pagination, Select, Spinner } from '@/components/ui';
import { DataTable } from '@/components/ui/DataTable';
import { formatCurrency, formatDateTime, formatQuantity } from '@/lib/utils';
import {
  reportStatusVariant,
  reportVariance,
  SUBMISSION_STATUSES,
  transferLineVaries,
  transferReceivingStatus,
} from '../_shared';

type SubmissionSortKey = 'business_date' | 'submitted_at' | 'branch' | 'report' | 'status' | 'variance';

function SubmissionSortHeader({ label, col, sort, direction, onSort }: {
  label: string; col: SubmissionSortKey; sort: SubmissionSortKey; direction: 'asc' | 'desc'; onSort: (col: SubmissionSortKey) => void;
}) {
  return (
    <button type="button" onClick={() => onSort(col)} className="inline-flex items-center gap-1 hover:text-primary">
      {label}{sort === col ? (direction === 'asc' ? ' ↑' : ' ↓') : ''}
    </button>
  );
}

export default function SubmissionsPage() {
  const [branches, setBranches] = useState<Branch[]>([]);
  const [branchId, setBranchId] = useState('');

  useEffect(() => {
    void branchesApi.list().then(setBranches).catch(() => setBranches([]));
  }, []);

  const branchName = useMemo(
    () => (id: string) => branches.find((b) => b.id === id)?.name ?? id,
    [branches],
  );

  return (
    <div className="max-w-[1400px] space-y-8">
      <div className="flex flex-wrap items-end gap-3">
        <Select label="Branch" value={branchId} onChange={(e) => setBranchId(e.target.value)} placeholder="All branches" className="w-56" options={branches.map((b) => ({ value: b.id, label: b.name }))} />
      </div>
      <ShiftReportsSection branchId={branchId} />
      <TransferDocumentsSection branchId={branchId} branchName={branchName} />
      <StockCountsSection branchId={branchId} branchName={branchName} />
    </div>
  );
}

// ─── Shift reports ────────────────────────────────────────────────────────────

function ShiftReportsSection({ branchId }: { branchId: string }) {
  const router = useRouter();
  const [statusFilter, setStatusFilter] = useState('');
  const [search, setSearch] = useState('');
  const [rows, setRows] = useState<ShiftInventoryReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [sort, setSort] = useState<SubmissionSortKey>('submitted_at');
  const [direction, setDirection] = useState<'asc' | 'desc'>('desc');
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    inventoryApi.shiftReports({ branch_id: branchId || undefined })
      .then((reports) => { if (!cancelled) { setRows(reports); setError(''); } })
      .catch((err) => { if (!cancelled) setError(err instanceof ApiError ? err.message : 'Failed to load submissions.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [branchId]);

  useEffect(() => { setPage(1); }, [branchId]);

  const onSort = (col: SubmissionSortKey) => {
    if (sort === col) setDirection((d) => (d === 'asc' ? 'desc' : 'asc'));
    else { setSort(col); setDirection('asc'); }
    setPage(1);
  };

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const filtered = rows.filter((row) => {
      if (statusFilter && row.status !== statusFilter) return false;
      if (!needle) return true;
      return [row.template_snapshot.name, row.submitted_by_name, row.branch_name, row.business_date]
        .some((field) => String(field ?? '').toLowerCase().includes(needle));
    });
    const dir = direction === 'asc' ? 1 : -1;
    const key = (row: ShiftInventoryReport): string | number => {
      switch (sort) {
        case 'business_date': return row.business_date;
        case 'submitted_at': return row.submitted_at ?? '';
        case 'branch': return row.branch_name ?? '';
        case 'report': return String(row.template_snapshot.name ?? '');
        case 'status': return row.status;
        case 'variance': return reportVariance(row);
      }
    };
    return [...filtered].sort((a, b) => {
      const ka = key(a); const kb = key(b);
      if (ka < kb) return -1 * dir;
      if (ka > kb) return 1 * dir;
      return 0;
    });
  }, [rows, search, statusFilter, sort, direction]);

  const pages = Math.max(1, Math.ceil(visible.length / perPage));
  const pageRows = visible.slice((page - 1) * perPage, page * perPage);

  return (
    <section className="space-y-4">
      <div>
        <h2 className="font-display text-lg text-primary tracking-wide">Shift reports</h2>
        <p className="text-sm text-gray-500">Every inventory report submitted from the register. Click a row to review its counts and, when it is awaiting approval, edit, approve or reject it. The stock ledger updates when a report is approved.</p>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <Select label="Status" value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }} placeholder="All statuses" className="w-48" options={SUBMISSION_STATUSES.map((s) => ({ value: s, label: s.replaceAll('_', ' ') }))} />
        <label className="block flex-1 min-w-52 text-xs uppercase tracking-wider text-gray-500">Search
          <Input aria-label="Search submissions" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} placeholder="Report, branch, or person" className="mt-1" />
        </label>
      </div>
      {error && <p className="bg-red-50 p-2 text-sm text-red-800">{error}</p>}
      {loading ? <Spinner /> : (
        <>
          <DataTable
            rows={pageRows}
            rowKey={(row) => row.id}
            onRowClick={(row) => router.push(`/inventory/reports/${row.id}`)}
            empty={<span className="text-sm text-gray-500">No shift reports match these filters.</span>}
            columns={[
              { header: 'Business date', headerRender: () => <SubmissionSortHeader label="Business date" col="business_date" sort={sort} direction={direction} onSort={onSort} />, render: (row) => row.business_date },
              { header: 'Submitted', headerRender: () => <SubmissionSortHeader label="Submitted" col="submitted_at" sort={sort} direction={direction} onSort={onSort} />, render: (row) => row.submitted_at ? formatDateTime(row.submitted_at) : '—' },
              { header: 'Branch', headerRender: () => <SubmissionSortHeader label="Branch" col="branch" sort={sort} direction={direction} onSort={onSort} />, render: (row) => row.branch_name ?? '—' },
              { header: 'Submitted by', render: (row) => row.submitted_by_name ?? '—' },
              { header: 'Report', priority: 'primary', headerRender: () => <SubmissionSortHeader label="Report" col="report" sort={sort} direction={direction} onSort={onSort} />, render: (row) => String(row.template_snapshot.name ?? row.template_id) },
              { header: 'Status', headerRender: () => <SubmissionSortHeader label="Status" col="status" sort={sort} direction={direction} onSort={onSort} />, render: (row) => <Badge variant={reportStatusVariant(row.status)}>{row.status.replaceAll('_', ' ')}</Badge> },
              { header: 'Progress', render: (row) => `${row.lines.filter((line) => line.confirmed).length}/${row.lines.length}` },
              { header: 'Variance value', headerRender: () => <SubmissionSortHeader label="Variance value" col="variance" sort={sort} direction={direction} onSort={onSort} />, render: (row) => formatCurrency(reportVariance(row)) },
            ]}
          />
          <Pagination page={page} pages={pages} total={visible.length} perPage={perPage} onPageChange={setPage} onPerPageChange={setPerPage} label="reports" />
        </>
      )}
    </section>
  );
}

// ─── Transfer & return documents ──────────────────────────────────────────────

function TransferDocumentsSection({ branchId, branchName }: {
  branchId: string;
  branchName: (id: string) => string;
}) {
  const router = useRouter();
  const [rows, setRows] = useState<TransferOrder[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);

  useEffect(() => { setPage(1); }, [branchId, statusFilter]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    // "As source or destination": two branch-scoped calls merged by id, since
    // the endpoint filters source and destination separately. No branch → all.
    const load = branchId
      ? Promise.all([
          inventoryApi.transferOrders({ source_branch_id: branchId, limit: 2000 }),
          inventoryApi.transferOrders({ branch_id: branchId, limit: 2000 }),
        ]).then(([outgoing, incoming]) => {
          const byId = new Map<string, TransferOrder>();
          for (const order of [...outgoing, ...incoming]) byId.set(order.id, order);
          return Array.from(byId.values());
        })
      : inventoryApi.transferOrders({ limit: 2000 });
    load
      .then((data) => { if (!cancelled) setRows(data); })
      .catch((err) => { if (!cancelled) setError(err instanceof ApiError ? err.message : 'Failed to load transfers.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [branchId]);

  const statuses = useMemo(() => Array.from(new Set(rows.map((r) => r.status))).sort(), [rows]);

  const visible = useMemo(() => {
    const filtered = statusFilter ? rows.filter((r) => r.status === statusFilter) : rows;
    return [...filtered].sort((a, b) => b.created_at.localeCompare(a.created_at));
  }, [rows, statusFilter]);

  const pages = Math.max(1, Math.ceil(visible.length / perPage));
  const pageRows = visible.slice((page - 1) * perPage, page * perPage);
  const totalOf = (order: TransferOrder, pick: (line: TransferOrder['items'][number]) => string) =>
    order.items.reduce((sum, line) => sum + Number(pick(line) || 0), 0);

  return (
    <section className="space-y-4 border-t border-gray-200 pt-8">
      <div>
        <h2 className="font-display text-lg text-primary tracking-wide">Transfers &amp; returns</h2>
        <p className="text-sm text-gray-500">Transfer and return documents {branchId ? 'this branch has sent or received' : 'across all branches'}. Click a row to open it. A variance flag marks any document whose received quantity differs from what was sent.</p>
      </div>
      <div className="flex flex-wrap items-end gap-3">
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
            columns={[
              { header: 'Reference', priority: 'primary', render: (row) => <Link href={`/inventory/transfers/${row.id}`} className="text-primary hover:underline" onClick={(e) => e.stopPropagation()}>{row.reference}</Link> },
              { header: 'Kind', render: (row) => <Badge variant={row.kind === 'return' ? 'warning' : 'neutral'}>{row.kind}</Badge> },
              { header: 'From → To', render: (row) => <span className="text-xs">{branchName(row.source_branch_id)} → {branchName(row.branch_id)}</span> },
              { header: 'Receiving status', render: (row) => { const s = transferReceivingStatus(row); return <Badge variant={s.variant}>{s.label}</Badge>; } },
              { header: 'Sent', className: 'text-right', render: (row) => formatQuantity(totalOf(row, (line) => line.sent_quantity)) },
              { header: 'Received', className: 'text-right', render: (row) => formatQuantity(totalOf(row, (line) => line.received_quantity)) },
              { header: 'Variance', render: (row) => row.items.some(transferLineVaries) ? <Badge variant="danger">Variance</Badge> : '—' },
              { header: 'Created', render: (row) => formatDateTime(row.created_at) },
            ]}
          />
          <Pagination page={page} pages={pages} total={visible.length} perPage={perPage} onPageChange={setPage} onPerPageChange={setPerPage} label="transfers" />
        </>
      )}
    </section>
  );
}

// ─── Manual stock counts ──────────────────────────────────────────────────────

function StockCountsSection({ branchId, branchName }: {
  branchId: string;
  branchName: (id: string) => string;
}) {
  const router = useRouter();
  const [rows, setRows] = useState<InventoryTransaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);

  useEffect(() => { setPage(1); }, [branchId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    // A branch's first count posts as an opening balance (OPN-…); every count
    // after that posts as an inventory count (CNT-…). The endpoint filters on a
    // single type, so pull both and merge by id.
    Promise.all([
      inventoryApi.transactions({ type: 'inventory_count', branch_id: branchId || undefined, limit: 2000 }),
      inventoryApi.transactions({ type: 'opening_balance', branch_id: branchId || undefined, limit: 2000 }),
    ])
      .then(([counts, openings]) => {
        if (cancelled) return;
        const byId = new Map<string, InventoryTransaction>();
        for (const tx of [...counts, ...openings]) byId.set(tx.id, tx);
        setRows(Array.from(byId.values()));
      })
      .catch((err) => { if (!cancelled) setError(err instanceof ApiError ? err.message : 'Failed to load stock counts.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [branchId]);

  const visible = useMemo(() => {
    const stamp = (tx: InventoryTransaction) => tx.posted_at ?? tx.created_at;
    return [...rows].sort((a, b) => stamp(b).localeCompare(stamp(a)));
  }, [rows]);

  const pages = Math.max(1, Math.ceil(visible.length / perPage));
  const pageRows = visible.slice((page - 1) * perPage, page * perPage);
  const netDelta = (tx: InventoryTransaction) =>
    tx.items.reduce((sum, line) => sum + Number(line.signed_quantity ?? 0), 0);

  return (
    <section className="space-y-4 border-t border-gray-200 pt-8">
      <div>
        <h2 className="font-display text-lg text-primary tracking-wide">Manual stock counts</h2>
        <p className="text-sm text-gray-500">Stock-audit counts posted from the register {branchId ? 'for this branch' : 'across all branches'} — a branch&apos;s first count is an opening balance, every count after it a stock count. Click a row to see the counted lines and their value impact.</p>
      </div>
      {error && <p className="bg-red-50 p-2 text-sm text-red-800">{error}</p>}
      {loading ? <Spinner /> : (
        <>
          <DataTable
            rows={pageRows}
            rowKey={(row) => row.id}
            onRowClick={(row) => router.push(`/inventory/transactions/${row.id}`)}
            empty={<span className="text-sm text-gray-500">No stock counts match these filters.</span>}
            columns={[
              { header: 'Business date', render: (row) => row.business_date },
              { header: 'Posted', render: (row) => row.posted_at ? formatDateTime(row.posted_at) : '—' },
              { header: 'Branch', render: (row) => branchName(row.branch_id) },
              { header: 'Posted by', render: (row) => row.posted_by_name ?? '—' },
              { header: 'Reference', priority: 'primary', render: (row) => <Link href={`/inventory/transactions/${row.id}`} className="text-primary hover:underline" onClick={(e) => e.stopPropagation()}>{row.reference}</Link> },
              { header: 'Items', className: 'text-right', render: (row) => row.items.length },
              { header: 'Net delta', className: 'text-right', render: (row) => { const d = netDelta(row); return <span className={d < 0 ? 'text-red-600' : d > 0 ? 'text-green-700' : 'text-gray-400'}>{d === 0 ? '—' : `${d > 0 ? '+' : ''}${formatQuantity(d)}`}</span>; } },
              { header: 'Value impact', className: 'text-right', render: (row) => formatCurrency(row.total_cost) },
            ]}
          />
          <Pagination page={page} pages={pages} total={visible.length} perPage={perPage} onPageChange={setPage} onPerPageChange={setPerPage} label="counts" />
        </>
      )}
    </section>
  );
}
