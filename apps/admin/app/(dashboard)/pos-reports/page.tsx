'use client';

import { useEffect, useRef, useState } from 'react';
import { branchesApi, posReportsApi } from '@/lib/pos-api';
import type {
  Branch,
  CostOfGoods,
  InventoryValuation,
  MenuEngineeringRow,
  PaymentReportRow,
  SalesBreakdownRow,
  SalesSummary,
  TaxReportRow,
} from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Input, Select, Spinner, TabBar } from '@/components/ui';
import { money } from '@/components/pos/ResourcePage';

type TabKey = 'sales' | 'payments' | 'taxes' | 'menu' | 'inventory';

/** Default window: the last 7 trading days, which is what a manager checks. */
function defaultWindow() {
  const today = new Date();
  const from = new Date(today);
  from.setDate(from.getDate() - 6);
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  return { from: iso(from), to: iso(today) };
}

export default function PosReportsPage() {
  const [tab, setTab] = useState<TabKey>('sales');
  const [branches, setBranches] = useState<Branch[]>([]);
  const [branchId, setBranchId] = useState('');
  const [{ from, to }, setRange] = useState(defaultWindow);

  useEffect(() => {
    void branchesApi.list().then(setBranches).catch(() => setBranches([]));
  }, []);

  const window = {
    branch_id: branchId || undefined,
    date_from: from || undefined,
    date_to: to || undefined,
  };

  return (
    <div>
      <div className="border-b border-gray-200 px-6 pt-5">
        <h1 className="font-display text-xl text-primary tracking-wide mb-1">POS Reports</h1>
        <p className="mb-3 text-xs text-gray-500 font-body">
          Scoped by trading day, so sales after midnight report against the day they belong to.
        </p>
        <div className="mb-3 flex flex-wrap items-end gap-3">
          <Select
            label="Branch"
            value={branchId}
            onChange={(e) => setBranchId(e.target.value)}
            options={branches.map((b) => ({ value: b.id, label: b.name }))}
            placeholder="All branches"
            className="w-52"
          />
          <Input
            label="From"
            type="date"
            value={from}
            onChange={(e) => setRange((r) => ({ ...r, from: e.target.value }))}
            className="w-40"
          />
          <Input
            label="To"
            type="date"
            value={to}
            onChange={(e) => setRange((r) => ({ ...r, to: e.target.value }))}
            className="w-40"
          />
        </div>
        <TabBar
          tabs={[
            { key: 'sales', label: 'Sales' },
            { key: 'payments', label: 'Payments' },
            { key: 'taxes', label: 'Taxes' },
            { key: 'menu', label: 'Menu Engineering' },
            { key: 'inventory', label: 'Inventory' },
          ]}
          active={tab}
          onChange={(k) => setTab(k as TabKey)}
        />
      </div>

      <div className="p-6 max-w-[1400px]">
        {tab === 'sales' && <SalesTab window={window} />}
        {tab === 'payments' && <PaymentsTab window={window} />}
        {tab === 'taxes' && <TaxesTab window={window} />}
        {tab === 'menu' && <MenuTab window={window} />}
        {tab === 'inventory' && <InventoryTab window={window} branchId={branchId} />}
      </div>
    </div>
  );
}

type Window = { branch_id?: string; date_from?: string; date_to?: string };

/**
 * Runs a report fetch and re-runs it whenever `key` changes.
 *
 * The fetcher is held in a ref rather than in the dependency list: it is a fresh
 * closure on every render, so depending on it directly would refetch forever.
 * `key` is the honest dependency — it encodes the filters the report is for.
 */
function useReport<T>(fetcher: () => Promise<T>, key: string) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const fetcherRef = useRef(fetcher);

  // Declared before the fetch effect so it has already refreshed by the time
  // the fetch runs. Mutating a ref during render is not safe under concurrent
  // rendering, so it happens in an effect instead.
  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetcherRef
      .current()
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setError('');
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : 'Failed to load report.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [key]);

  return { data, loading, error };
}

/** Stable cache key for a report window. */
function windowKey(w: Window, ...extra: string[]): string {
  return [w.branch_id ?? '', w.date_from ?? '', w.date_to ?? '', ...extra].join('|');
}

function Panel({
  loading,
  error,
  empty,
  children,
}: {
  loading: boolean;
  error: string;
  empty?: boolean;
  children: React.ReactNode;
}) {
  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner />
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
        {error}
      </div>
    );
  }
  if (empty) {
    return (
      <p className="py-16 text-center text-sm text-gray-400 font-body">
        No data for this period.
      </p>
    );
  }
  return <>{children}</>;
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded border border-gray-200 bg-white p-4">
      <p className="text-[11px] uppercase tracking-widest text-gray-400 font-body">{label}</p>
      <p className="font-display text-xl text-primary mt-1">{value}</p>
      {hint && <p className="text-[11px] text-gray-400 font-body mt-0.5">{hint}</p>}
    </div>
  );
}

function BreakdownTable({ rows, unitLabel }: { rows: SalesBreakdownRow[]; unitLabel: string }) {
  return (
    <div className="overflow-x-auto rounded border border-gray-200 bg-white">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-200 bg-gray-50 text-[11px] uppercase tracking-widest text-gray-500 font-body">
            <th className="px-3 py-2 text-left">{unitLabel}</th>
            <th className="px-3 py-2 text-right">{rows[0]?.quantity !== undefined ? 'Qty' : 'Orders'}</th>
            <th className="px-3 py-2 text-right">Discounts</th>
            <th className="px-3 py-2 text-right">Net sales</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-b border-gray-100 last:border-0">
              <td className="px-3 py-2 font-medium">{r.label}</td>
              <td className="px-3 py-2 text-right">{r.quantity ?? r.orders ?? 0}</td>
              <td className="px-3 py-2 text-right text-gray-500">{money(r.discounts)}</td>
              <td className="px-3 py-2 text-right">{money(r.net_sales)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SalesTab({ window }: { window: Window }) {
  const [dimension, setDimension] = useState('product');
  const summary = useReport<SalesSummary>(
    () => posReportsApi.salesSummary(window),
    windowKey(window),
  );
  const breakdown = useReport<SalesBreakdownRow[]>(
    () => posReportsApi.salesBy(dimension, window),
    windowKey(window, dimension),
  );

  return (
    <div className="space-y-6">
      <Panel loading={summary.loading} error={summary.error}>
        {summary.data && (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Net sales" value={money(summary.data.net_sales)} />
            <Stat label="Orders" value={String(summary.data.orders_count)} />
            <Stat label="Average order" value={money(summary.data.average_order_value)} />
            <Stat label="VAT collected" value={money(summary.data.taxes)} />
            <Stat label="Discounts" value={money(summary.data.discounts)} />
            <Stat label="Charges" value={money(summary.data.charges)} />
            <Stat label="Returns" value={money(summary.data.returns)} />
            <Stat
              label="Voided"
              value={money(summary.data.voided_value)}
              hint={`${summary.data.voided_orders} order(s)`}
            />
          </div>
        )}
      </Panel>

      <div>
        <div className="mb-3 flex items-end gap-3">
          <Select
            label="Break down by"
            value={dimension}
            onChange={(e) => setDimension(e.target.value)}
            options={[
              { value: 'product', label: 'Product' },
              { value: 'category', label: 'Category' },
              { value: 'order_type', label: 'Order type' },
              { value: 'source', label: 'Source' },
              { value: 'business_date', label: 'Day' },
              { value: 'staff', label: 'Staff' },
              { value: 'hour', label: 'Hour of day' },
            ]}
            className="w-52"
          />
        </div>
        <Panel
          loading={breakdown.loading}
          error={breakdown.error}
          empty={!breakdown.data?.length}
        >
          {breakdown.data && <BreakdownTable rows={breakdown.data} unitLabel="Name" />}
        </Panel>
      </div>
    </div>
  );
}

function PaymentsTab({ window }: { window: Window }) {
  const { data, loading, error } = useReport<PaymentReportRow[]>(
    () => posReportsApi.payments(window),
    windowKey(window),
  );

  return (
    <Panel loading={loading} error={error} empty={!data?.length}>
      <div className="overflow-x-auto rounded border border-gray-200 bg-white">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50 text-[11px] uppercase tracking-widest text-gray-500 font-body">
              <th className="px-3 py-2 text-left">Method</th>
              <th className="px-3 py-2 text-left">Type</th>
              <th className="px-3 py-2 text-right">Count</th>
              <th className="px-3 py-2 text-right">Collected</th>
              <th className="px-3 py-2 text-right">Refunds</th>
              <th className="px-3 py-2 text-right">Net</th>
              <th className="px-3 py-2 text-right">Tips</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((r) => (
              <tr key={r.payment_method_id} className="border-b border-gray-100 last:border-0">
                <td className="px-3 py-2 font-medium">{r.name}</td>
                <td className="px-3 py-2 capitalize text-gray-500">{r.type.replace('_', ' ')}</td>
                <td className="px-3 py-2 text-right">{r.transactions}</td>
                <td className="px-3 py-2 text-right">{money(r.amount)}</td>
                <td className="px-3 py-2 text-right text-gray-500">{money(r.refunds)}</td>
                <td className="px-3 py-2 text-right font-medium">{money(r.net)}</td>
                <td className="px-3 py-2 text-right">{money(r.tips)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function TaxesTab({ window }: { window: Window }) {
  const { data, loading, error } = useReport<TaxReportRow[]>(
    () => posReportsApi.taxes(window),
    windowKey(window),
  );

  return (
    <Panel loading={loading} error={error} empty={!data?.length}>
      <div className="overflow-x-auto rounded border border-gray-200 bg-white">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50 text-[11px] uppercase tracking-widest text-gray-500 font-body">
              <th className="px-3 py-2 text-left">Tax</th>
              <th className="px-3 py-2 text-right">Rate</th>
              <th className="px-3 py-2 text-right">Taxable amount</th>
              <th className="px-3 py-2 text-right">Tax collected</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((r) => (
              <tr key={`${r.name}-${r.rate}`} className="border-b border-gray-100 last:border-0">
                <td className="px-3 py-2 font-medium">{r.name}</td>
                <td className="px-3 py-2 text-right">{r.rate_percent}%</td>
                <td className="px-3 py-2 text-right">{money(r.taxable_amount)}</td>
                <td className="px-3 py-2 text-right font-medium">{money(r.tax_amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

const CLASSIFICATION_STYLE: Record<
  MenuEngineeringRow['classification'],
  { label: string; variant: 'success' | 'info' | 'warning' | 'danger' }
> = {
  star: { label: 'Star', variant: 'success' },
  plough_horse: { label: 'Plough horse', variant: 'info' },
  puzzle: { label: 'Puzzle', variant: 'warning' },
  dog: { label: 'Dog', variant: 'danger' },
};

function MenuTab({ window }: { window: Window }) {
  const { data, loading, error } = useReport<MenuEngineeringRow[]>(
    () => posReportsApi.menuEngineering(window),
    windowKey(window),
  );

  return (
    <Panel loading={loading} error={error} empty={!data?.length}>
      <p className="mb-3 text-xs text-gray-500 font-body">
        Items are classified against the period average: popular and profitable is a star,
        popular but thin is a plough horse, profitable but slow is a puzzle.
      </p>
      <div className="overflow-x-auto rounded border border-gray-200 bg-white">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50 text-[11px] uppercase tracking-widest text-gray-500 font-body">
              <th className="px-3 py-2 text-left">Product</th>
              <th className="px-3 py-2 text-right">Qty</th>
              <th className="px-3 py-2 text-right">Net sales</th>
              <th className="px-3 py-2 text-right">Cost</th>
              <th className="px-3 py-2 text-right">Margin</th>
              <th className="px-3 py-2 text-right">Margin %</th>
              <th className="px-3 py-2 text-left">Class</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((r) => {
              const style = CLASSIFICATION_STYLE[r.classification];
              return (
                <tr key={r.key} className="border-b border-gray-100 last:border-0">
                  <td className="px-3 py-2 font-medium">{r.label}</td>
                  <td className="px-3 py-2 text-right">{r.quantity ?? 0}</td>
                  <td className="px-3 py-2 text-right">{money(r.net_sales)}</td>
                  <td className="px-3 py-2 text-right text-gray-500">{money(r.cost)}</td>
                  <td className="px-3 py-2 text-right">{money(r.margin)}</td>
                  <td className="px-3 py-2 text-right">{(r.margin_percent * 100).toFixed(1)}%</td>
                  <td className="px-3 py-2">
                    <Badge variant={style.variant}>{style.label}</Badge>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function InventoryTab({ window, branchId }: { window: Window; branchId: string }) {
  const valuation = useReport<InventoryValuation>(
    () => posReportsApi.inventoryValuation(branchId || undefined),
    branchId,
  );
  const cogs = useReport<CostOfGoods>(
    () => posReportsApi.costOfGoods(window),
    windowKey(window),
  );

  return (
    <div className="space-y-6">
      <Panel loading={cogs.loading} error={cogs.error}>
        {cogs.data && (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Cost of goods" value={money(cogs.data.cost_of_goods)} />
            <Stat label="Net sales (excl. VAT)" value={money(cogs.data.net_sales_excl_tax)} />
            <Stat label="Gross margin" value={money(cogs.data.gross_margin)} />
            <Stat label="Margin %" value={`${cogs.data.gross_margin_percent}%`} />
          </div>
        )}
      </Panel>

      <Panel loading={valuation.loading} error={valuation.error}>
        {valuation.data && (
          <>
            <div className="mb-4 grid gap-3 sm:grid-cols-3">
              <Stat label="Items tracked" value={String(valuation.data.items_tracked)} />
              <Stat label="Stock value" value={money(valuation.data.total_value)} />
              <Stat
                label="Below minimum"
                value={String(valuation.data.below_minimum_count)}
                hint="Needs reordering"
              />
            </div>
            {valuation.data.below_minimum.length > 0 && (
              <div className="overflow-x-auto rounded border border-gray-200 bg-white">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-200 bg-gray-50 text-[11px] uppercase tracking-widest text-gray-500 font-body">
                      <th className="px-3 py-2 text-left">SKU</th>
                      <th className="px-3 py-2 text-left">Item</th>
                      <th className="px-3 py-2 text-right">On hand</th>
                      <th className="px-3 py-2 text-right">Minimum</th>
                      <th className="px-3 py-2 text-right">Order to par</th>
                    </tr>
                  </thead>
                  <tbody>
                    {valuation.data.below_minimum.map((row) => (
                      <tr key={row.item_id} className="border-b border-gray-100 last:border-0">
                        <td className="px-3 py-2">
                          <code className="text-xs text-gray-500">{row.sku}</code>
                        </td>
                        <td className="px-3 py-2 font-medium">{row.name}</td>
                        <td className="px-3 py-2 text-right">
                          {row.quantity} <span className="text-xs text-gray-400">{row.unit}</span>
                        </td>
                        <td className="px-3 py-2 text-right text-gray-500">{row.minimum_level}</td>
                        <td className="px-3 py-2 text-right font-medium">{row.shortfall}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </Panel>
    </div>
  );
}
