'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { auditLogsApi } from '@/lib/api';
import type { AuditAction, AuditLog } from '@/lib/types';
import { Badge, Input, Pagination, Select } from '@/components/ui';

// ─── Constants ────────────────────────────────────────────────────────────────

const ACTION_OPTIONS = [
  { value: '', label: 'All Actions' },
  { value: 'CREATE', label: 'Create' },
  { value: 'UPDATE', label: 'Update' },
  { value: 'DELETE', label: 'Delete' },
  { value: 'STATUS_CHANGE', label: 'Status Change' },
];

const ENTITY_OPTIONS = [
  { value: '', label: 'All Entities' },
  { value: 'product', label: 'Product' },
  { value: 'category', label: 'Category' },
  { value: 'order', label: 'Order' },
  { value: 'promo_code', label: 'Promo Code' },
];

const ACTION_VARIANT: Record<AuditAction, 'success' | 'danger' | 'warning' | 'neutral'> = {
  CREATE: 'success',
  UPDATE: 'neutral',
  DELETE: 'danger',
  STATUS_CHANGE: 'warning',
};

function defaultDateFrom(): string {
  const d = new Date();
  d.setDate(d.getDate() - 7);
  return d.toISOString().slice(0, 16);
}

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('en-AE', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function formatChanges(changes: Record<string, unknown> | null): string {
  if (!changes) return '—';
  return JSON.stringify(changes, null, 2);
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function AuditLogsPage() {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [perPage, setPerPage] = useState(50);
  const [loading, setLoading] = useState(true);

  // Filters
  const [actionFilter, setActionFilter] = useState('');
  const [entityTypeFilter, setEntityTypeFilter] = useState('');
  const [search, setSearch] = useState('');
  const [dateFrom, setDateFrom] = useState(defaultDateFrom());
  const [dateTo, setDateTo] = useState('');

  // Debounced search
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [debouncedSearch, setDebouncedSearch] = useState('');

  // Expanded changes row
  const [expandedId, setExpandedId] = useState<string | null>(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setDebouncedSearch(search), 350);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [search]);

  useEffect(() => { setPage(1); }, [actionFilter, entityTypeFilter, debouncedSearch, dateFrom, dateTo]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await auditLogsApi.list({
        action: actionFilter || undefined,
        entity_type: entityTypeFilter || undefined,
        search: debouncedSearch || undefined,
        date_from: dateFrom ? new Date(dateFrom).toISOString() : undefined,
        date_to: dateTo ? new Date(dateTo).toISOString() : undefined,
        page,
        per_page: perPage,
      });
      setLogs(res.items);
      setTotal(res.total);
      setPages(res.pages);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, [actionFilter, entityTypeFilter, debouncedSearch, dateFrom, dateTo, page, perPage]);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-display text-2xl text-gray-800">Audit Logs</h1>
          <p className="text-xs text-gray-400 font-body mt-0.5">{total} records</p>
        </div>
        <button
          onClick={load}
          className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-primary font-body transition-colors"
          title="Refresh"
        >
          <span className="material-icons text-[16px]">refresh</span>
          Refresh
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-4">
        <div className="w-52">
          <Input
            placeholder="Search entity or admin email…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <div className="w-40">
          <Select
            value={actionFilter}
            onChange={e => setActionFilter(e.target.value)}
            options={ACTION_OPTIONS}
          />
        </div>
        <div className="w-40">
          <Select
            value={entityTypeFilter}
            onChange={e => setEntityTypeFilter(e.target.value)}
            options={ENTITY_OPTIONS}
          />
        </div>
        <div className="flex items-center gap-2">
          <input
            type="datetime-local"
            value={dateFrom}
            onChange={e => setDateFrom(e.target.value)}
            className="h-9 px-2 text-xs font-body border border-gray-200 rounded-none text-gray-700 focus:outline-none focus:border-primary"
            title="From date"
          />
          <span className="text-xs text-gray-400 font-body">—</span>
          <input
            type="datetime-local"
            value={dateTo}
            onChange={e => setDateTo(e.target.value)}
            className="h-9 px-2 text-xs font-body border border-gray-200 rounded-none text-gray-700 focus:outline-none focus:border-primary"
            title="To date"
          />
        </div>
      </div>

      {/* Table */}
      <div className="bg-white border border-gray-200 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50">
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500">Timestamp</th>
              <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500">Action</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 hidden sm:table-cell">Entity Type</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500">Entity</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 hidden md:table-cell">Admin</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 hidden lg:table-cell">IP</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500">Changes</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading ? (
              Array.from({ length: 10 }).map((_, i) => (
                <tr key={i}>
                  {Array.from({ length: 7 }).map((_, j) => (
                    <td key={j} className="px-4 py-3">
                      <div className="h-4 bg-gray-100 animate-pulse rounded-sm" />
                    </td>
                  ))}
                </tr>
              ))
            ) : logs.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center text-sm text-gray-400 font-body">
                  No audit log entries found.
                </td>
              </tr>
            ) : (
              logs.map(log => (
                <>
                  <tr key={log.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-4 py-3 text-xs font-body text-gray-500 whitespace-nowrap">
                      {formatDateTime(log.created_at)}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <Badge variant={ACTION_VARIANT[log.action as AuditAction] ?? 'neutral'}>
                        {log.action.replace('_', ' ')}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 hidden sm:table-cell">
                      <span className="text-xs font-mono text-gray-500 bg-gray-100 px-1.5 py-0.5 rounded-sm">
                        {log.entity_type}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span className="text-xs font-body text-gray-700">{log.entity_label}</span>
                      <span className="block text-[10px] font-mono text-gray-400 mt-0.5">{log.entity_id}</span>
                    </td>
                    <td className="px-4 py-3 hidden md:table-cell">
                      <span className="text-xs font-body text-gray-600">{log.admin_email}</span>
                    </td>
                    <td className="px-4 py-3 hidden lg:table-cell">
                      <span className="text-[11px] font-mono text-gray-400">
                        {log.ip_address ?? '—'}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      {log.changes ? (
                        <button
                          onClick={() => setExpandedId(expandedId === log.id ? null : log.id)}
                          className="flex items-center gap-1 text-xs font-body text-primary hover:text-primary/80 transition-colors"
                        >
                          <span className="material-icons text-[14px]">data_object</span>
                          <span className="hidden sm:inline">View</span>
                          <span className="material-icons text-[12px]">
                            {expandedId === log.id ? 'expand_less' : 'expand_more'}
                          </span>
                        </button>
                      ) : (
                        <span className="text-xs text-gray-300 font-body">—</span>
                      )}
                    </td>
                  </tr>

                  {/* Expanded changes row */}
                  {expandedId === log.id && log.changes && (
                    <tr key={`${log.id}-changes`} className="bg-gray-50">
                      <td colSpan={7} className="px-4 py-3">
                        <pre className="text-xs font-mono text-gray-700 whitespace-pre-wrap break-all bg-white border border-gray-200 p-3 rounded-sm max-h-60 overflow-y-auto">
                          {formatChanges(log.changes)}
                        </pre>
                      </td>
                    </tr>
                  )}
                </>
              ))
            )}
          </tbody>
        </table>
      </div>

      <Pagination
        page={page}
        pages={pages}
        total={total}
        perPage={perPage}
        onPageChange={setPage}
        onPerPageChange={setPerPage}
        label="entries"
      />
    </div>
  );
}
