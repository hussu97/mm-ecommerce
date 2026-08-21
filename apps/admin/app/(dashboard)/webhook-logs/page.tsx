'use client';

/**
 * Everything anyone has pushed at us, and what we did about it.
 *
 * This exists because of a morning nobody could reconstruct. MM-20260805-007
 * received four noon Send pushes and MM-20260805-006 received one — and the
 * only evidence either way was container stdout, which a restart had already
 * taken. noon Send neither retry nor keep anything to replay from, so a push we
 * could not see was a push that, as far as anyone could tell, never happened.
 *
 * The column that earns the page is `matched`. A push that found no order is
 * either their bug or ours, it is never harmless, and until this screen existed
 * it was completely invisible.
 *
 * The payment gateways were added to it for the same reason, and it applies
 * harder there: a courier push we lose costs tracking, while a payment event we
 * lose is money that moved with the order none the wiser. That has already
 * happened once — an SDK upgrade broke every `payment_intent.succeeded` for
 * three days while Stripe's own dashboard showed nothing but green, because the
 * failures left no record on our side at all. A `signature_valid` of false or a
 * `matched` of false on a Stripe row is the loudest thing on this screen.
 *
 * Rows are purged after LOG_RETENTION_DAYS (7 by default) — which is what makes
 * it affordable to keep every rider-position ping at full payload.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { webhookLogsApi } from '@/lib/api';
import type { WebhookLog, WebhookLogDetail } from '@/lib/types';
import { Badge, Input, Pagination, Select, LoadError, Spinner } from '@/components/ui';
import { DataTable, RowAction } from '@/components/ui/DataTable';

// ─── Constants ────────────────────────────────────────────────────────────────

const PROVIDER_OPTIONS = [
  { value: '', label: 'All Senders' },
  { value: 'noon_send', label: 'noon Send' },
  { value: 'lalamove', label: 'Lalamove' },
  { value: 'slider', label: 'Slider' },
  { value: 'stripe', label: 'Stripe' },
  { value: 'ziina', label: 'Ziina' },
];

// The couriers' two endpoints behave nothing alike — one arrives on a state
// change, the other every twenty seconds. For a payment gateway the value says
// which *mount* the processor is calling; both do identical work, and which URL
// Stripe is actually configured against is otherwise unanswerable without
// asking them.
const ENDPOINT_OPTIONS = [
  { value: '', label: 'All Endpoints' },
  { value: 'status', label: 'Status (courier)' },
  { value: 'tracking', label: 'Tracking (courier)' },
  // Slider's dashboard configures a staging webhook separately, and ours is
  // pointed at production on purpose: those pushes are acknowledged and
  // journalled here and change nothing at all.
  { value: 'staging', label: 'Staging (Slider, inert)' },
  { value: 'payments', label: '/payments/webhooks/…' },
  { value: 'webhooks', label: '/webhooks/…' },
];

const OUTCOME_OPTIONS = [
  { value: '', label: 'All Outcomes' },
  { value: 'matched', label: 'Matched an order' },
  { value: 'unmatched', label: 'Matched nothing' },
  { value: 'errors', label: 'Errors & unmatched' },
];

const PROVIDER_LABEL: Record<string, string> = {
  noon_send: 'noon Send',
  lalamove: 'Lalamove',
  slider: 'Slider',
  stripe: 'Stripe',
  ziina: 'Ziina',
};

/** Seven days back, which is also the whole retention window. */
function defaultDateFrom(): string {
  const d = new Date();
  d.setDate(d.getDate() - 7);
  return d.toISOString().slice(0, 16);
}

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('en-AE', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function WebhookLogsPage() {
  const router = useRouter();
  const [logs, setLogs] = useState<WebhookLog[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [perPage, setPerPage] = useState(50);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');

  // Filters
  const [providerFilter, setProviderFilter] = useState('');
  const [endpointFilter, setEndpointFilter] = useState('');
  const [outcomeFilter, setOutcomeFilter] = useState('');
  const [orderSearch, setOrderSearch] = useState('');
  const [taskSearch, setTaskSearch] = useState('');
  const [dateFrom, setDateFrom] = useState(defaultDateFrom());
  const [dateTo, setDateTo] = useState('');

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [debouncedOrder, setDebouncedOrder] = useState('');
  const [debouncedTask, setDebouncedTask] = useState('');

  // The opened row, and its bodies once they arrive. Fetched one at a time
  // because a page of two thousand payloads is megabytes nobody is reading.
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<WebhookLogDetail | null>(null);
  const [detailError, setDetailError] = useState('');

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setDebouncedOrder(orderSearch);
      setDebouncedTask(taskSearch);
    }, 350);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [orderSearch, taskSearch]);

  useEffect(() => { setPage(1); }, [providerFilter, endpointFilter, outcomeFilter, debouncedOrder, debouncedTask, dateFrom, dateTo]);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      const res = await webhookLogsApi.list({
        provider: providerFilter || undefined,
        endpoint: endpointFilter || undefined,
        matched:
          outcomeFilter === 'matched' ? 'true'
          : outcomeFilter === 'unmatched' ? 'false'
          : undefined,
        errors_only: outcomeFilter === 'errors' ? 'true' : undefined,
        order_number: debouncedOrder || undefined,
        external_id: debouncedTask || undefined,
        date_from: dateFrom ? new Date(dateFrom).toISOString() : undefined,
        date_to: dateTo ? new Date(dateTo).toISOString() : undefined,
        page,
        per_page: perPage,
      });
      setLogs(res.items);
      setTotal(res.total);
      setPages(res.pages);
    } catch (err) {
      setLoadError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [providerFilter, endpointFilter, outcomeFilter, debouncedOrder, debouncedTask, dateFrom, dateTo, page, perPage]);

  useEffect(() => { load(); }, [load]);

  async function toggleRow(id: string) {
    if (expandedId === id) {
      setExpandedId(null);
      setDetail(null);
      return;
    }
    setExpandedId(id);
    setDetail(null);
    setDetailError('');
    try {
      setDetail(await webhookLogsApi.get(id));
    } catch (err) {
      setDetailError((err as Error).message);
    }
  }

  return (
    <div>
      <LoadError message={loadError} onRetry={load} />

      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-display text-2xl text-gray-800">Webhook Logs</h1>
          <p className="text-xs text-gray-400 font-body mt-0.5">
            {total} requests · kept for 7 days
          </p>
        </div>
        <button
          onClick={load}
          className="flex items-center gap-1.5 min-h-11 md:min-h-0 px-1 text-xs text-gray-500 hover:text-primary font-body transition-colors"
          title="Refresh"
        >
          <span className="material-icons text-[16px]">refresh</span>
          Refresh
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-4">
        <div className="w-36">
          <Input
            placeholder="Search order #…"
            value={orderSearch}
            onChange={e => setOrderSearch(e.target.value)}
          />
        </div>
        <div className="w-44">
          <Input
            placeholder="Search task / payment id…"
            value={taskSearch}
            onChange={e => setTaskSearch(e.target.value)}
          />
        </div>
        <div className="w-36">
          <Select
            value={providerFilter}
            onChange={e => setProviderFilter(e.target.value)}
            options={PROVIDER_OPTIONS}
          />
        </div>
        <div className="w-36">
          <Select
            value={endpointFilter}
            onChange={e => setEndpointFilter(e.target.value)}
            options={ENDPOINT_OPTIONS}
          />
        </div>
        <div className="w-44">
          <Select
            value={outcomeFilter}
            onChange={e => setOutcomeFilter(e.target.value)}
            options={OUTCOME_OPTIONS}
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

      {loading ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : (
        <DataTable<WebhookLog>
          rows={logs}
          rowKey={log => log.id}
          empty={
            <p className="py-16 text-center text-sm text-gray-400 font-body">
              No webhooks in this window.
            </p>
          }
          actions={log => (
            <RowAction onClick={() => toggleRow(log.id)}>
              {expandedId === log.id ? 'Hide body' : 'View body'}
            </RowAction>
          )}
          expanded={log =>
            expandedId === log.id ? (
              <>
                {log.error && (
                  <p className="mb-3 px-3 py-2 text-xs font-mono text-red-700 bg-red-50 border border-red-200 whitespace-pre-wrap break-all">
                    {log.error}
                  </p>
                )}
                {detailError ? (
                  <p className="text-xs font-body text-red-500">{detailError}</p>
                ) : !detail ? (
                  <div className="h-16 bg-gray-100 animate-pulse rounded-sm" />
                ) : (
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    <div className="min-w-0">
                      <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-1">
                        What they sent
                      </p>
                      <pre className="text-[11px] font-mono text-gray-700 bg-white border border-gray-200 p-3 overflow-x-auto max-h-72">
                        {JSON.stringify(detail.payload, null, 2)}
                      </pre>
                    </div>
                    <div className="min-w-0">
                      <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-1">
                        What we answered
                      </p>
                      <pre className="text-[11px] font-mono text-gray-700 bg-white border border-gray-200 p-3 overflow-x-auto max-h-72">
                        {JSON.stringify(detail.result, null, 2)}
                      </pre>
                      <p className="text-[11px] font-body text-gray-400 mt-2">
                        from {detail.remote_ip ?? 'an unknown address'}
                        {detail.http_status !== null && ` · HTTP ${detail.http_status}`}
                      </p>
                    </div>
                  </div>
                )}
              </>
            ) : null
          }
          columns={[
            {
              header: 'Event',
              // Their word, never ours — a push that says EXPIRED is evidence,
              // and paraphrasing loses the reason. It is also what identifies
              // the row, so it leads the card.
              priority: 'primary',
              render: log => (
                <span className="text-xs font-mono text-gray-600">{log.event_type ?? '—'}</span>
              ),
            },
            {
              header: 'Courier',
              priority: 'secondary',
              render: log => (
                <>
                  {PROVIDER_LABEL[log.provider] ?? log.provider}
                  <span className="text-gray-400"> / {log.endpoint}</span>
                </>
              ),
            },
            {
              header: 'Outcome',
              className: 'text-center',
              render: log =>
                log.error ? (
                  <Badge variant="danger">error</Badge>
                ) : log.matched === false ? (
                  <Badge variant="warning">no match</Badge>
                ) : log.matched === true ? (
                  <Badge variant="success">matched</Badge>
                ) : (
                  <Badge variant="neutral">received</Badge>
                ),
            },
            {
              header: 'Received',
              className: 'whitespace-nowrap',
              render: log => (
                <span className="text-gray-500">
                  {formatDateTime(log.received_at)}
                  {log.duration_ms !== null && (
                    <span className="text-gray-300"> · {log.duration_ms}ms</span>
                  )}
                </span>
              ),
            },
            {
              header: 'Order #',
              render: log =>
                log.order_number ? (
                  <button
                    onClick={() => router.push(`/orders/${log.order_number}`)}
                    className="inline-flex items-center min-h-11 md:min-h-0 text-xs font-body font-medium text-primary hover:underline"
                  >
                    {log.order_number}
                  </button>
                ) : (
                  <span className="text-gray-300">—</span>
                ),
            },
            {
              header: 'External ID',
              priority: 'desktop',
              render: log => (
                <span className="text-[11px] font-mono text-gray-400">
                  {log.external_id ?? '—'}
                </span>
              ),
            },
            {
              header: 'Key',
              // A fingerprint, never the key. Enough to tell two apart across
              // two systems, which is the only question it answers — and a
              // desk question, so it leaves the card.
              priority: 'desktop',
              render: log => (
                <span className="text-[11px] font-mono text-gray-400">
                  {log.api_key_fingerprint ?? '—'}
                </span>
              ),
            },
          ]}
        />
      )}

      <Pagination
        page={page}
        pages={pages}
        total={total}
        perPage={perPage}
        onPageChange={setPage}
        onPerPageChange={setPerPage}
        label="requests"
      />
    </div>
  );
}
