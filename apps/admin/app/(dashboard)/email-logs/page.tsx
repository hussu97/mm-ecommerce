'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { emailLogsApi } from '@/lib/api';
import type { EmailLog, EmailLogStatus } from '@/lib/types';
import { Badge, Input, Pagination, Select } from '@/components/ui';

// ─── Constants ────────────────────────────────────────────────────────────────

const STATUS_OPTIONS = [
  { value: '', label: 'All Statuses' },
  { value: 'sent', label: 'Sent' },
  { value: 'failed', label: 'Failed' },
  { value: 'skipped', label: 'Skipped' },
];

const TEMPLATE_OPTIONS = [
  { value: '', label: 'All Templates' },
  { value: 'order_confirmation', label: 'Order Confirmation' },
  { value: 'order_packed', label: 'Order Packed' },
  { value: 'order_cancelled', label: 'Order Cancelled' },
  { value: 'welcome', label: 'Welcome' },
  { value: 'password_reset', label: 'Password Reset' },
];

const TEMPLATE_LABELS: Record<string, string> = {
  order_confirmation: 'Order Confirmation',
  order_packed: 'Order Packed',
  order_cancelled: 'Order Cancelled',
  welcome: 'Welcome',
  password_reset: 'Password Reset',
};

const STATUS_VARIANT: Record<EmailLogStatus, 'success' | 'danger' | 'warning'> = {
  sent: 'success',
  failed: 'danger',
  skipped: 'warning',
};

// Default date_from = 7 days ago
function defaultDateFrom(): string {
  const d = new Date();
  d.setDate(d.getDate() - 7);
  return d.toISOString().slice(0, 16); // "YYYY-MM-DDTHH:MM"
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

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function EmailLogsPage() {
  const router = useRouter();
  const [logs, setLogs] = useState<EmailLog[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [perPage, setPerPage] = useState(50);
  const [loading, setLoading] = useState(true);

  // Filters
  const [statusFilter, setStatusFilter] = useState('');
  const [templateFilter, setTemplateFilter] = useState('');
  const [recipientSearch, setRecipientSearch] = useState('');
  const [orderSearch, setOrderSearch] = useState('');
  const [dateFrom, setDateFrom] = useState(defaultDateFrom());
  const [dateTo, setDateTo] = useState('');

  // Debounced text searches
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [debouncedRecipient, setDebouncedRecipient] = useState('');
  const [debouncedOrder, setDebouncedOrder] = useState('');

  // Expanded error row
  const [expandedId, setExpandedId] = useState<string | null>(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setDebouncedRecipient(recipientSearch);
      setDebouncedOrder(orderSearch);
    }, 350);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [recipientSearch, orderSearch]);

  useEffect(() => { setPage(1); }, [statusFilter, templateFilter, debouncedRecipient, debouncedOrder, dateFrom, dateTo]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await emailLogsApi.list({
        status: statusFilter || undefined,
        template: templateFilter || undefined,
        recipient: debouncedRecipient || undefined,
        order_number: debouncedOrder || undefined,
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
  }, [statusFilter, templateFilter, debouncedRecipient, debouncedOrder, dateFrom, dateTo, page, perPage]);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-display text-2xl text-gray-800">Email Logs</h1>
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
        <div className="w-44">
          <Input
            placeholder="Search recipient email…"
            value={recipientSearch}
            onChange={e => setRecipientSearch(e.target.value)}
          />
        </div>
        <div className="w-36">
          <Input
            placeholder="Search order #…"
            value={orderSearch}
            onChange={e => setOrderSearch(e.target.value)}
          />
        </div>
        <div className="w-36">
          <Select
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value)}
            options={STATUS_OPTIONS}
          />
        </div>
        <div className="w-48">
          <Select
            value={templateFilter}
            onChange={e => setTemplateFilter(e.target.value)}
            options={TEMPLATE_OPTIONS}
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
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500">Sent At</th>
              <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500">Status</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 hidden md:table-cell">Template</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500">Recipient</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 hidden lg:table-cell">Order #</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 hidden xl:table-cell">Subject</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 hidden xl:table-cell">Resend ID</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500">Error / Info</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading ? (
              Array.from({ length: 10 }).map((_, i) => (
                <tr key={i}>
                  {Array.from({ length: 8 }).map((_, j) => (
                    <td key={j} className="px-4 py-3">
                      <div className="h-4 bg-gray-100 animate-pulse rounded-sm" />
                    </td>
                  ))}
                </tr>
              ))
            ) : logs.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center text-sm text-gray-400 font-body">
                  No email logs found.
                </td>
              </tr>
            ) : (
              logs.map(log => (
                <>
                  <tr
                    key={log.id}
                    className="hover:bg-gray-50 transition-colors"
                  >
                    <td className="px-4 py-3 text-xs font-body text-gray-500 whitespace-nowrap">
                      {formatDateTime(log.sent_at)}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <Badge variant={STATUS_VARIANT[log.status]}>
                        {log.status}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 hidden md:table-cell">
                      <span className="text-xs font-body text-gray-600">
                        {TEMPLATE_LABELS[log.template] ?? log.template}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span className="text-xs font-body text-gray-700">{log.recipient}</span>
                    </td>
                    <td className="px-4 py-3 hidden lg:table-cell">
                      {log.order_number ? (
                        <button
                          onClick={() => router.push(`/orders/${log.order_number}`)}
                          className="text-xs font-body font-medium text-primary hover:underline"
                        >
                          {log.order_number}
                        </button>
                      ) : (
                        <span className="text-xs text-gray-300 font-body">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 hidden xl:table-cell max-w-xs">
                      <span className="text-xs font-body text-gray-500 truncate block" title={log.subject}>
                        {log.subject}
                      </span>
                    </td>
                    <td className="px-4 py-3 hidden xl:table-cell">
                      {log.resend_id ? (
                        <span className="text-[11px] font-mono text-gray-400" title={log.resend_id}>
                          {log.resend_id.slice(0, 20)}…
                        </span>
                      ) : (
                        <span className="text-xs text-gray-300 font-body">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {log.error ? (
                        <button
                          onClick={() => setExpandedId(expandedId === log.id ? null : log.id)}
                          className="flex items-center gap-1 text-xs font-body text-red-500 hover:text-red-700 transition-colors"
                          title={log.error}
                        >
                          <span className="material-icons text-[14px]">error_outline</span>
                          <span className="hidden sm:inline truncate max-w-[140px]">{log.error.slice(0, 40)}{log.error.length > 40 ? '…' : ''}</span>
                          <span className="material-icons text-[12px]">
                            {expandedId === log.id ? 'expand_less' : 'expand_more'}
                          </span>
                        </button>
                      ) : (
                        <span className="text-xs text-gray-300 font-body">—</span>
                      )}
                    </td>
                  </tr>

                  {/* Expanded error row */}
                  {expandedId === log.id && log.error && (
                    <tr key={`${log.id}-error`} className="bg-red-50">
                      <td colSpan={8} className="px-4 py-3">
                        <p className="text-xs font-mono text-red-700 whitespace-pre-wrap break-all">
                          {log.error}
                        </p>
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
        label="logs"
      />
    </div>
  );
}
