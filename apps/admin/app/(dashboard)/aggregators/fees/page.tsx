'use client';

import { useEffect, useState } from 'react';

import { Input, LoadError, Select, Spinner } from '@/components/ui';
import { Badge } from '@/components/ui';
import { DataTable, type DataColumn } from '@/components/ui/DataTable';
import { aggregatorFeesApi } from '@/lib/api';
import { formatCurrency } from '@/lib/utils';
import type { Schemas } from '@mm/types';

import { AggregatorTabs } from '../AggregatorTabs';

// The generated contract (rule 8). Money is `string | null` on the wire.
type FeesSummary = Schemas['AggregatorFeesSummaryOut'];
type FeesRow = Schemas['AggregatorFeesRow'];

/**
 * The Fees & VAT screen — the high-level commission/VAT picture for a date range.
 *
 * One row per marketplace: what they took (commission), the VAT on it where the
 * marketplace declares it, gross sales, net paid out, and the effective rate.
 * The commission figure is genuinely split by marketplace — some settle it on a
 * detailed statement, some only on the order feed — so the API merges both and
 * picks one source per channel; this screen just renders the result. Every fee is
 * a positive magnitude ("what they charged"), not a signed ledger entry. A blank
 * VAT is a channel that never itemises it, not a zero.
 */

const CHANNEL_OPTIONS = [
  { value: '', label: 'All channels' },
  { value: 'careem', label: 'Careem' },
  { value: 'deliveroo', label: 'Deliveroo' },
  { value: 'talabat', label: 'Talabat' },
  { value: 'noon', label: 'noon' },
  { value: 'keeta', label: 'Keeta' },
];

function channelName(c: string): string {
  return c === 'all' ? 'All' : CHANNEL_OPTIONS.find(o => o.value === c)?.label ?? c;
}

/** A money field that is allowed to be absent — a dash, not "AED 0.00". */
function money(value: number | string | null | undefined): string {
  return value == null ? '—' : formatCurrency(value);
}

/** A fraction (0.152) as a percent ("15.2%"), dash when unknown. */
function rate(value: number | null | undefined): string {
  return value == null ? '—' : `${(value * 100).toFixed(1)}%`;
}

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-white border border-gray-200 p-4">
      <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-1">{label}</p>
      <p className="font-display text-2xl text-gray-800">{value}</p>
      {sub && <p className="text-xs font-body text-gray-400 mt-0.5">{sub}</p>}
    </div>
  );
}

// Default the range to the last 30 days (Dubai-agnostic — these are calendar
// dates the API compares lexicographically against the stored business dates).
function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

export default function AggregatorFeesPage() {
  const [channel, setChannel] = useState('');
  const [fromDate, setFromDate] = useState(isoDaysAgo(30));
  const [toDate, setToDate] = useState(isoDaysAgo(0));

  const [summary, setSummary] = useState<FeesSummary | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    void (async () => {
      try {
        const data = await aggregatorFeesApi.summary({
          channel: channel || undefined,
          date_from: fromDate || undefined,
          date_to: toDate || undefined,
        });
        if (!active) return;
        setSummary(data);
        setError('');
      } catch (e) {
        if (!active) return;
        setSummary(null);
        setError(e instanceof Error ? e.message : 'Could not load the fees summary');
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [channel, fromDate, toDate, reload]);

  const t = summary?.totals;
  const columns: DataColumn<FeesRow>[] = [
    {
      header: 'Channel',
      priority: 'primary',
      render: r => <Badge variant="neutral">{channelName(r.channel)}</Badge>,
    },
    {
      header: 'Orders',
      className: 'text-right whitespace-nowrap',
      render: r => <span className="tabular-nums text-gray-600">{r.orders}</span>,
    },
    {
      header: 'Gross sales',
      className: 'text-right whitespace-nowrap',
      render: r => <span className="tabular-nums text-gray-700">{money(r.gross_sales)}</span>,
    },
    {
      header: 'Commission',
      className: 'text-right whitespace-nowrap',
      render: r => <span className="tabular-nums font-medium text-gray-800">{money(r.commission)}</span>,
    },
    {
      header: 'VAT',
      className: 'text-right whitespace-nowrap',
      render: r => <span className="tabular-nums text-gray-700">{money(r.vat)}</span>,
    },
    {
      header: 'Other fees',
      priority: 'secondary',
      className: 'text-right whitespace-nowrap',
      render: r => <span className="tabular-nums text-gray-700">{money(r.other_fees)}</span>,
    },
    {
      header: 'Net payout',
      className: 'text-right whitespace-nowrap',
      render: r => <span className="tabular-nums text-gray-700">{money(r.net_payable)}</span>,
    },
    {
      header: 'Eff. rate',
      className: 'text-right whitespace-nowrap',
      render: r => <span className="tabular-nums text-gray-600">{rate(r.effective_rate)}</span>,
    },
  ];

  return (
    <div className="space-y-6">
      <AggregatorTabs />

      <div>
        <h1 className="font-display text-2xl text-gray-800">Fees &amp; VAT</h1>
        <p className="text-xs text-gray-400 font-body mt-0.5">
          Commission, VAT and net payout per marketplace over the chosen range
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div className="w-44">
          <Select value={channel} onChange={e => setChannel(e.target.value)} options={CHANNEL_OPTIONS} />
        </div>
        <div className="w-40">
          <Input
            type="date"
            label="From"
            value={fromDate}
            max={toDate || undefined}
            onChange={e => setFromDate(e.target.value)}
          />
        </div>
        <div className="w-40">
          <Input
            type="date"
            label="To"
            value={toDate}
            min={fromDate || undefined}
            onChange={e => setToDate(e.target.value)}
          />
        </div>
      </div>

      {error && <LoadError message={error} onRetry={() => setReload(n => n + 1)} />}

      {loading ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : (
        <>
          {t && (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
              <StatCard label="Gross sales" value={money(t.gross_sales)} sub={`${t.orders} orders`} />
              <StatCard
                label="Commission"
                value={money(t.commission)}
                sub={`eff. ${rate(t.effective_rate)}`}
              />
              <StatCard label="VAT" value={money(t.vat)} />
              <StatCard label="Other fees" value={money(t.other_fees)} />
              <StatCard label="Net payout" value={money(t.net_payable)} />
            </div>
          )}

          <DataTable<FeesRow>
            columns={columns}
            rows={summary?.by_channel ?? []}
            rowKey={r => r.channel}
            empty={
              <p className="py-16 text-center text-sm text-gray-400 font-body">
                No fee data for this range.
              </p>
            }
          />
          <p className="text-xs text-gray-400 font-body">
            Commission is drawn from each marketplace&rsquo;s settled statement lines
            where it publishes them (Deliveroo, Keeta, noon) and from the order feed
            otherwise (Talabat). VAT appears only where the marketplace itemises it.
          </p>
        </>
      )}
    </div>
  );
}
