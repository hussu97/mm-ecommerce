'use client';

import { useCallback, useState } from 'react';

import { Badge, Button, Input, LoadError, Pagination, Select, Spinner } from '@/components/ui';
import { DataTable, type DataColumn } from '@/components/ui/DataTable';
import { useToast } from '@/components/ui/feedback';
import { useApiList } from '@/hooks/useApiList';
import { aggregatorInvoicesApi, ApiError } from '@/lib/api';
import { formatCurrency, formatDateTime } from '@/lib/utils';
import type { Schemas } from '@mm/types';

import { AggregatorTabs } from '../AggregatorTabs';

// The generated contract (rule 8). Money fields are `string | null` on the wire
// (Decimals as strings) and read through formatCurrency below.
type StatementRow = Schemas['AggregatorStatementOut'];

/**
 * The settlement invoices screen.
 *
 * One row per statement a marketplace published — the period it covers, the money
 * it settled (gross, fees, VAT, net) and, where the marketplace issues one, the
 * archived invoice/CSV document to download. Careem, Deliveroo and noon publish a
 * real document; Keeta and Talabat settle with figures but no file, so those rows
 * show the numbers and no download. The download is a one-hour signed URL fetched
 * on click, never a public link.
 */

const CHANNEL_OPTIONS = [
  { value: '', label: 'All channels' },
  { value: 'careem', label: 'Careem' },
  { value: 'deliveroo', label: 'Deliveroo' },
  { value: 'talabat', label: 'Talabat' },
  { value: 'noon', label: 'noon' },
  { value: 'keeta', label: 'Keeta' },
];

const DOC_OPTIONS = [
  { value: '', label: 'All statements' },
  { value: 'true', label: 'With document' },
  { value: 'false', label: 'Without document' },
];

function channelName(c: string): string {
  return CHANNEL_OPTIONS.find(o => o.value === c)?.label ?? c;
}

/** A money field that is allowed to be absent — a dash, not "AED 0.00". */
function money(value: number | string | null | undefined): string {
  return value == null ? '—' : formatCurrency(value);
}

export default function AggregatorInvoicesPage() {
  const toast = useToast();
  const [channel, setChannel] = useState('');
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');
  const [hasDoc, setHasDoc] = useState('');
  // The id currently fetching its signed URL, so only that row's button spins.
  const [downloading, setDownloading] = useState<string | null>(null);

  const fetchRows = useCallback(
    async (page: number, perPage: number) => {
      const res = await aggregatorInvoicesApi.list({
        channel: channel || undefined,
        date_from: fromDate || undefined,
        date_to: toDate || undefined,
        has_invoice: hasDoc === '' ? undefined : hasDoc === 'true',
        limit: perPage,
        offset: (page - 1) * perPage,
      });
      return {
        items: res.items,
        total: res.total,
        pages: Math.max(1, Math.ceil(res.total / perPage)),
      };
    },
    [channel, fromDate, toDate, hasDoc],
  );

  const {
    items: rows, total, pages, page, perPage, setPage, setPerPage, loading, loadError, refetch,
  } = useApiList<StatementRow>({ paginate: 'server', fetch: fetchRows });

  // Fetch the short-lived signed URL on click and open it. Opening a blank tab
  // synchronously first keeps Safari from blocking the async pop-up, then we point
  // it at the URL once it arrives (or close it on failure).
  const onDownload = async (row: StatementRow) => {
    setDownloading(row.id);
    const tab = window.open('', '_blank');
    try {
      const res = await aggregatorInvoicesApi.invoiceUrl(row.id);
      if (tab) tab.location.href = res.url;
      else window.location.href = res.url;
    } catch (e) {
      tab?.close();
      toast.error(e instanceof ApiError ? e.message : 'Could not fetch the invoice');
    } finally {
      setDownloading(null);
    }
  };

  const columns: DataColumn<StatementRow>[] = [
    {
      header: 'Period',
      priority: 'primary',
      render: r => (
        <div>
          <div className="font-medium">
            {r.period_start && r.period_end
              ? r.period_start === r.period_end
                ? r.period_start
                : `${r.period_start} → ${r.period_end}`
              : (r.period_end ?? r.period_start ?? '—')}
          </div>
          <div className="font-mono text-xs text-gray-500">{r.statement_id}</div>
        </div>
      ),
    },
    {
      header: 'Channel',
      render: r => <Badge variant="neutral">{channelName(r.channel)}</Badge>,
    },
    {
      header: 'Due',
      priority: 'secondary',
      render: r => <span className="text-gray-600">{r.payment_due_date ?? '—'}</span>,
    },
    {
      header: 'Gross',
      className: 'text-right whitespace-nowrap',
      render: r => <span className="tabular-nums text-gray-700">{money(r.gross_sales)}</span>,
    },
    {
      header: 'Fees',
      className: 'text-right whitespace-nowrap',
      render: r => <span className="tabular-nums text-gray-700">{money(r.total_fees)}</span>,
    },
    {
      header: 'VAT',
      className: 'text-right whitespace-nowrap',
      render: r => <span className="tabular-nums text-gray-700">{money(r.total_vat)}</span>,
    },
    {
      header: 'Net',
      className: 'text-right whitespace-nowrap',
      render: r => <span className="tabular-nums font-medium text-gray-800">{money(r.net_payable)}</span>,
    },
    {
      header: 'Document',
      render: r =>
        r.has_invoice ? (
          <Button
            variant="secondary"
            size="sm"
            loading={downloading === r.id}
            onClick={() => onDownload(r)}
          >
            <span className="material-icons text-[15px] mr-1">download</span>
            {r.invoice_original_filename?.split('.').pop()?.toUpperCase() || 'Download'}
            {r.attachment_count > 0 && (
              <span className="ml-1 text-gray-400">+{r.attachment_count}</span>
            )}
          </Button>
        ) : (
          <span className="text-xs text-gray-400">no file</span>
        ),
    },
    {
      header: 'Archived',
      priority: 'secondary',
      render: r =>
        r.invoice_fetched_at ? (
          <span className="text-xs text-gray-500">{formatDateTime(r.invoice_fetched_at)}</span>
        ) : (
          <span className="text-xs text-gray-400">—</span>
        ),
    },
  ];

  return (
    <div className="space-y-6">
      <AggregatorTabs />

      <div>
        <h1 className="font-display text-2xl text-gray-800">Invoices</h1>
        <p className="text-xs text-gray-400 font-body mt-0.5">
          {total} statement{total === 1 ? '' : 's'} · settlement documents and figures per period
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div className="w-44">
          <Select value={channel} onChange={e => setChannel(e.target.value)} options={CHANNEL_OPTIONS} />
        </div>
        <div className="w-40">
          <Input
            type="date"
            label="Period from"
            value={fromDate}
            max={toDate || undefined}
            onChange={e => setFromDate(e.target.value)}
          />
        </div>
        <div className="w-40">
          <Input
            type="date"
            label="Period to"
            value={toDate}
            min={fromDate || undefined}
            onChange={e => setToDate(e.target.value)}
          />
        </div>
        <div className="w-48">
          <Select value={hasDoc} onChange={e => setHasDoc(e.target.value)} options={DOC_OPTIONS} />
        </div>
      </div>

      {loadError && <LoadError message={loadError} onRetry={refetch} />}

      {loading ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : (
        <>
          <DataTable<StatementRow>
            columns={columns}
            rows={rows}
            rowKey={r => r.id}
            empty={
              <p className="py-16 text-center text-sm text-gray-400 font-body">
                No statements for these filters.
              </p>
            }
          />
          <Pagination
            page={page}
            pages={pages}
            total={total}
            perPage={perPage}
            onPageChange={setPage}
            onPerPageChange={size => {
              setPerPage(size);
              setPage(1);
            }}
            label="statements"
          />
        </>
      )}
    </div>
  );
}
