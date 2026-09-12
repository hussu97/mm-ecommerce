'use client';

// Report submissions → Shift reports (the default sub-tab; no redirect).
// Every inventory report submitted from the register, approved here. Its own
// filter bar (branch / date range / status / search); all filtering is
// client-side over the rows loaded once on mount.

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { branchesApi, inventoryApi, type ShiftInventoryReport } from '@/lib/pos-api';
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
import { formatCurrency, formatDateTime } from '@/lib/utils';
import { reportStatusVariant, reportVariance, SUBMISSION_STATUSES } from '../_shared';

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

export default function ShiftReportsPage() {
  const router = useRouter();
  const [branches, setBranches] = useState<Branch[]>([]);
  const [rows, setRows] = useState<ShiftInventoryReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [branchId, setBranchId] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [search, setSearch] = useState('');

  const [sort, setSort] = useState<SortState | null>(null);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);

  useEffect(() => {
    void branchesApi.list().then(setBranches).catch(() => setBranches([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    inventoryApi.shiftReports({})
      .then((reports) => { if (!cancelled) { setRows(reports); setError(''); } })
      .catch((err) => { if (!cancelled) setError(err instanceof ApiError ? err.message : 'Failed to load submissions.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => { setPage(1); }, [branchId, from, to, statusFilter, search, sort]);

  const columns = useMemo<DataColumn<ShiftInventoryReport>[]>(() => [
    { header: 'Business date', sortable: true, sortAccessor: (row) => row.business_date, render: (row) => row.business_date },
    { header: 'Submitted', sortable: true, sortAccessor: (row) => row.submitted_at ?? null, render: (row) => row.submitted_at ? formatDateTime(row.submitted_at) : '—' },
    { header: 'Branch', sortable: true, sortAccessor: (row) => row.branch_name ?? null, render: (row) => row.branch_name ?? '—' },
    { header: 'Submitted by', sortable: true, sortAccessor: (row) => row.submitted_by_name ?? null, render: (row) => row.submitted_by_name ?? '—' },
    { header: 'Report', priority: 'primary', sortable: true, sortAccessor: (row) => String(row.template_snapshot.name ?? row.template_id), render: (row) => String(row.template_snapshot.name ?? row.template_id) },
    { header: 'Status', sortable: true, sortAccessor: (row) => row.status, render: (row) => <Badge variant={reportStatusVariant(row.status)}>{row.status.replaceAll('_', ' ')}</Badge> },
    { header: 'Progress', render: (row) => `${row.lines.filter((line) => line.confirmed).length}/${row.lines.length}` },
    { header: 'Variance value', className: 'text-right', sortable: true, sortAccessor: (row) => reportVariance(row), render: (row) => formatCurrency(reportVariance(row)) },
  ], []);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const filtered = rows.filter((row) => {
      if (branchId && row.branch_id !== branchId) return false;
      if (statusFilter && row.status !== statusFilter) return false;
      if (!withinRange(row.business_date, from, to)) return false;
      if (!needle) return true;
      return [row.template_snapshot.name, row.submitted_by_name, row.branch_name, row.business_date]
        .some((field) => String(field ?? '').toLowerCase().includes(needle));
    });
    const activeColumn = sort ? columns.find((c) => sortKeyOf(c) === sort.key) : undefined;
    if (sort && activeColumn?.sortAccessor) {
      return sortByAccessor(filtered, activeColumn.sortAccessor, sort.direction);
    }
    return [...filtered].sort((a, b) => (b.submitted_at ?? '').localeCompare(a.submitted_at ?? ''));
  }, [rows, branchId, from, to, statusFilter, search, sort, columns]);

  const pages = Math.max(1, Math.ceil(visible.length / perPage));
  const pageRows = visible.slice((page - 1) * perPage, page * perPage);

  return (
    <div className="space-y-4">
      <p className="text-sm text-gray-500">Every inventory report submitted from the register. Click a row to review its counts and, when it is awaiting approval, edit, approve or reject it. The stock ledger updates when a report is approved.</p>
      <div className="flex flex-wrap items-end gap-3">
        <Select label="Branch" value={branchId} onChange={(e) => setBranchId(e.target.value)} placeholder="All branches" className="w-56" options={branches.map((b) => ({ value: b.id, label: b.name }))} />
        <Input label="From" type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="w-44" />
        <Input label="To" type="date" value={to} onChange={(e) => setTo(e.target.value)} className="w-44" />
        <Select label="Status" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} placeholder="All statuses" className="w-48" options={SUBMISSION_STATUSES.map((s) => ({ value: s, label: s.replaceAll('_', ' ') }))} />
        <label className="block flex-1 min-w-52 text-xs uppercase tracking-wider text-gray-500">Search
          <Input aria-label="Search submissions" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Report, branch, or person" className="mt-1" />
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
            columns={columns}
            sort={sort}
            onSortChange={setSort}
          />
          <Pagination page={page} pages={pages} total={visible.length} perPage={perPage} onPageChange={setPage} onPerPageChange={setPerPage} label="reports" />
        </>
      )}
    </div>
  );
}
