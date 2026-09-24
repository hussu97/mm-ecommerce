'use client';

// Forecast vs actual — the replenishment forecast's shadow history, and its
// settings. Every morning at the snapshot time the forecast for the production
// branch is stored (what to send each branch, what to keep, what to produce),
// and after the day closes it is scored against what was actually requested,
// sent and produced, and what then sold. "Backtest" rows are the same forecast
// replayed for past days. Nothing here creates or changes an order.

import { useEffect, useMemo, useState } from 'react';
import {
  branchesApi,
  inventoryApi,
  replenishmentApi,
  type ReplenishmentAccuracy,
  type ReplenishmentHistory,
  type ReplenishmentHistoryRow,
  type ReplenishmentSettings,
} from '@/lib/pos-api';
import type { Branch, InventoryCategory } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button, Input, LoadError, MultiSelect, Pagination, Select, Spinner, TabBar } from '@/components/ui';
import { useToast } from '@/components/ui/feedback';
import { formatQuantity } from '@/lib/utils';

const isoDay = (d: Date) => d.toLocaleDateString('sv-SE', { timeZone: 'Asia/Dubai' });
const pct = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${Math.round(v * 100)}%`);
const qty = (v: number | null | undefined) => (v === null || v === undefined ? '—' : formatQuantity(v));
// Demand is an estimate — one decimal says as much as it knows.
const est = (v: number | null | undefined) => (v === null || v === undefined ? '—' : (Math.round(v * 10) / 10).toString());

const KIND_LABEL: Record<string, string> = { transfer: 'Transfer', retain: 'Keep at source', production: 'Production' };

export default function ReplenishmentPage() {
  const [tab, setTab] = useState<'history' | 'settings'>('history');
  const [branches, setBranches] = useState<Branch[]>([]);
  useEffect(() => {
    void branchesApi.list().then(setBranches).catch(() => setBranches([]));
  }, []);
  return (
    <div className="max-w-[var(--content-max)] space-y-4">
      <div>
        <h2 className="font-display text-lg text-primary tracking-wide">Replenishment forecast</h2>
        <p className="text-sm text-gray-500">
          The suggested transfer and production quantities shown on the new transfer &amp; production form, stored every morning and scored against what was actually done. A guide only — it never creates an order.
        </p>
      </div>
      <TabBar
        tabs={[{ key: 'history', label: 'Forecast vs actual' }, { key: 'settings', label: 'Settings' }]}
        active={tab}
        onChange={(k) => setTab(k as 'history' | 'settings')}
      />
      {tab === 'history' ? <HistoryTab branches={branches} /> : <SettingsTab branches={branches} />}
    </div>
  );
}

function HistoryTab({ branches }: { branches: Branch[] }) {
  const today = new Date();
  const [from, setFrom] = useState(isoDay(new Date(today.getTime() - 14 * 86400000)));
  const [to, setTo] = useState(isoDay(today));
  const [branchId, setBranchId] = useState('');
  const [kind, setKind] = useState('');
  const [mode, setMode] = useState('scheduled');
  const [search, setSearch] = useState('');
  const [data, setData] = useState<ReplenishmentHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    replenishmentApi
      .history({ date_from: from, date_to: to, branch_id: branchId || undefined, kind: kind || undefined, mode: mode || undefined })
      .then((d) => { if (!cancelled) { setData(d); setError(''); setPage(1); } })
      .catch((err) => { if (!cancelled) setError(err instanceof ApiError ? err.message : 'Failed to load the forecast history.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [from, to, branchId, kind, mode, reload]);

  const rows = useMemo(() => {
    const q = search.trim().toLocaleLowerCase();
    return (data?.rows ?? []).filter((r) => !q || r.item_name.toLocaleLowerCase().includes(q));
  }, [data, search]);
  const pages = Math.max(1, Math.ceil(rows.length / perPage));
  const visible = rows.slice((page - 1) * perPage, page * perPage);

  return (
    <div className="space-y-4">
      <div className="grid gap-3 border border-gray-200 p-3 sm:grid-cols-3 lg:grid-cols-6">
        <Input label="From" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        <Input label="To" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        <Select
          label="Branch"
          value={branchId}
          onChange={(e) => setBranchId(e.target.value)}
          options={[{ value: '', label: 'All branches' }, ...branches.filter((b) => !b.deleted_at).map((b) => ({ value: b.id, label: b.name }))]}
        />
        <Select
          label="Line"
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          options={[
            { value: '', label: 'All lines' },
            { value: 'transfer', label: 'Transfers' },
            { value: 'retain', label: 'Keep at source' },
            { value: 'production', label: 'Production' },
          ]}
        />
        <Select
          label="Run"
          value={mode}
          onChange={(e) => setMode(e.target.value)}
          options={[
            { value: 'scheduled', label: 'Daily snapshot' },
            { value: 'backtest', label: 'Backtest' },
            { value: '', label: 'Both' },
          ]}
        />
        <Input label="Item" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search item" />
      </div>

      <p className="bg-amber-50 border border-amber-200 p-2 text-xs text-amber-800">
        What actually sold was shaped by what was actually sent and made, not by the forecast. Where the forecast suggested <em>less</em> than was done, the leftover stock shows what it would have saved; where it suggested <em>more</em>, the gain is an estimate — the stock-out-adjusted demand below is itself worked out from the hours an item was in stock.
      </p>

      {loading ? (
        <Spinner />
      ) : error ? (
        <LoadError message={error} onRetry={() => setReload((n) => n + 1)} />
      ) : data ? (
        <>
          <div className="grid gap-3 md:grid-cols-2">
            <AccuracyCard title="Transfers (per branch, item and day)" acc={data.transfer} />
            <AccuracyCard title="Production (network, the day it protects)" acc={data.production} />
          </div>
          {rows.length === 0 ? (
            <p className="border border-dashed border-gray-300 p-4 text-sm text-gray-500">
              No forecast rows in this range yet. The daily snapshot is taken at the time set under Settings; a backtest fills past days.
            </p>
          ) : (
            <div className="overflow-x-auto border border-gray-200">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-left text-xs uppercase tracking-wider text-gray-500">
                  <tr>
                    <th className="px-2 py-1">Date</th>
                    <th className="px-2 py-1">Item</th>
                    <th className="px-2 py-1">Branch</th>
                    <th className="px-2 py-1">Line</th>
                    <th className="px-2 py-1 text-right" title="What the forecast suggested at the morning snapshot">Forecast</th>
                    <th className="px-2 py-1 text-right" title="Transfers: requested / sent. Production: planned / produced.">Actual</th>
                    <th className="px-2 py-1 text-right" title="Forecast whole-day demand (production: every branch, the next day)">Fc demand</th>
                    <th className="px-2 py-1 text-right" title="Sales scaled up for the hours the item was out of stock">Est. demand</th>
                    <th className="px-2 py-1 text-right">Sold</th>
                    <th className="px-2 py-1 text-right" title="Open minutes with no stock">Out (min)</th>
                    <th className="px-2 py-1 text-right" title="Stock at the end of the day">Closing</th>
                    <th className="px-2 py-1 text-right" title="Sales the same weekday last week — the naive baseline">Last wk</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((r) => <HistoryRow key={`${r.business_date}-${r.kind}-${r.branch_id}-${r.item_id}-${r.mode}`} row={r} />)}
                </tbody>
              </table>
            </div>
          )}
          <Pagination
            page={page}
            pages={pages}
            total={rows.length}
            perPage={perPage}
            onPageChange={setPage}
            onPerPageChange={(p) => { setPerPage(p); setPage(1); }}
            label="rows"
          />
        </>
      ) : null}
    </div>
  );
}

function HistoryRow({ row }: { row: ReplenishmentHistoryRow }) {
  const isProduction = row.kind === 'production';
  const actual = isProduction
    ? `${qty(row.actual_planned_qty)} / ${qty(row.actual_produced_qty)}`
    : row.kind === 'retain'
      ? qty(row.actual_requested_qty)
      : `${qty(row.actual_requested_qty)} / ${qty(row.actual_sent_qty)}`;
  const ranOut = (row.stockout_minutes ?? 0) > 0;
  return (
    <tr className="border-t border-gray-100">
      <td className="px-2 py-1 whitespace-nowrap tabular-nums">{row.business_date}</td>
      <td className="px-2 py-1">{row.item_name}</td>
      <td className="px-2 py-1 whitespace-nowrap">{row.branch_name}</td>
      <td className="px-2 py-1 whitespace-nowrap">
        {KIND_LABEL[row.kind] ?? row.kind}
        {row.mode === 'backtest' && <Badge variant="info" className="ml-1">backtest</Badge>}
      </td>
      <td className="px-2 py-1 text-right tabular-nums font-medium">
        {qty(row.forecast_qty)}
        {row.shortfall_qty > 0 && <span className="block text-xs text-amber-700">short {qty(row.shortfall_qty)}</span>}
      </td>
      <td className="px-2 py-1 text-right tabular-nums">{actual}</td>
      <td className="px-2 py-1 text-right tabular-nums">{est(row.day_demand_mean)}</td>
      <td className="px-2 py-1 text-right tabular-nums">{est(row.est_demand)}</td>
      <td className="px-2 py-1 text-right tabular-nums">{qty(row.realized_sales)}</td>
      <td className={`px-2 py-1 text-right tabular-nums ${ranOut ? 'text-red-600 font-medium' : 'text-gray-400'}`}>
        {row.stockout_minutes === null || row.stockout_minutes === undefined ? '—' : row.stockout_minutes}
      </td>
      <td className="px-2 py-1 text-right tabular-nums">{qty(row.closing_on_hand)}</td>
      <td className="px-2 py-1 text-right tabular-nums text-gray-500">{qty(row.baseline_demand)}</td>
    </tr>
  );
}

function AccuracyCard({ title, acc }: { title: string; acc: ReplenishmentAccuracy }) {
  const beats = acc.wape !== null && acc.wape !== undefined && acc.baseline_wape !== null && acc.baseline_wape !== undefined
    ? acc.wape <= acc.baseline_wape
    : null;
  return (
    <div className="border border-gray-200 p-3 text-sm">
      <p className="text-xs font-medium uppercase tracking-wide text-gray-500">{title}</p>
      <p className="mt-1 text-xs text-gray-400">{acc.rows} scored rows</p>
      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1">
        <dt className="text-gray-500" title="Σ|forecast − estimated demand| ÷ Σ estimated demand. Lower is better.">Error (WAPE)</dt>
        <dd className="text-right tabular-nums">
          {pct(acc.wape)}
          {beats !== null && <Badge variant={beats ? 'success' : 'warning'} className="ml-2">{beats ? 'beats' : 'trails'} last week {pct(acc.baseline_wape)}</Badge>}
        </dd>
        <dt className="text-gray-500" title="Positive = forecast runs high">Bias</dt>
        <dd className="text-right tabular-nums">{pct(acc.bias)}</dd>
        <dt className="text-gray-500">Rows that ran out</dt>
        <dd className="text-right tabular-nums">{acc.stockout_rows}</dd>
        <dt className="text-gray-500" title="Forecast suggested more than was done, and the branch ran out">Forecast more · ran out</dt>
        <dd className="text-right tabular-nums">{acc.forecast_more_and_ran_out}</dd>
        <dt className="text-gray-500" title="Forecast suggested less, and the day closed with at least that much left">Forecast less · surplus</dt>
        <dd className="text-right tabular-nums">{acc.forecast_less_and_surplus}</dd>
        <dt className="text-gray-500" title="Estimated demand minus sales, on rows that ran out">Est. lost sales</dt>
        <dd className="text-right tabular-nums">{formatQuantity(acc.est_lost_sales)}</dd>
      </dl>
    </div>
  );
}

function SettingsTab({ branches }: { branches: Branch[] }) {
  const toast = useToast();
  const [settings, setSettings] = useState<ReplenishmentSettings | null>(null);
  const [categories, setCategories] = useState<InventoryCategory[]>([]);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    replenishmentApi.settings().then(setSettings).catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load settings.'));
    inventoryApi.categories().then(setCategories).catch(() => setCategories([]));
  }, []);

  if (error) return <LoadError message={error} />;
  if (!settings) return <Spinner />;

  const set = <K extends keyof ReplenishmentSettings>(key: K, value: ReplenishmentSettings[K]) =>
    setSettings((prev) => (prev ? { ...prev, [key]: value } : prev));
  const hhmm = (t: string) => t.slice(0, 5);

  const save = async () => {
    setSaving(true);
    try {
      const saved = await replenishmentApi.updateSettings({
        bucket_hours: settings.bucket_hours as 1 | 2 | 3 | 4 | 6,
        service_level: Number(settings.service_level),
        production_branch_weight: Number(settings.production_branch_weight),
        production_branch_id: settings.production_branch_id,
        same_day_ready_time: hhmm(settings.same_day_ready_time),
        next_day_category_ids: settings.next_day_category_ids,
        snapshot_time: hhmm(settings.snapshot_time),
        half_life_days: Number(settings.half_life_days),
        window_days: Number(settings.window_days),
      });
      setSettings(saved);
      toast.success('Forecast settings saved.');
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not save the settings.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="max-w-2xl space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Select
          label="Production branch"
          value={settings.production_branch_id ?? ''}
          onChange={(e) => set('production_branch_id', e.target.value || null)}
          options={[{ value: '', label: 'None — use the form\'s source' }, ...branches.filter((b) => !b.deleted_at).map((b) => ({ value: b.id, label: b.name }))]}
        />
        <Select
          label="Intraday profile window"
          value={String(settings.bucket_hours)}
          onChange={(e) => set('bucket_hours', Number(e.target.value))}
          options={[1, 2, 3, 4, 6].map((h) => ({ value: String(h), label: `${h} hour${h === 1 ? '' : 's'} from opening` }))}
        />
        <Input
          label="Service level (%)"
          type="number"
          min={51}
          max={99}
          value={Math.round(Number(settings.service_level) * 100)}
          onChange={(e) => set('service_level', Number(e.target.value) / 100)}
          helper="How often the suggested stock should cover the day's demand. Higher means fewer sell-outs and more left over."
        />
        <Input
          label="Production branch priority"
          type="number"
          step="0.05"
          min={1}
          max={5}
          value={settings.production_branch_weight}
          onChange={(e) => set('production_branch_weight', Number(e.target.value))}
          helper="When stock is short, how much more a unit at the production branch counts than elsewhere (1 = equal)."
        />
        <Input
          label="Today's production sellable from"
          type="time"
          value={hhmm(settings.same_day_ready_time)}
          onChange={(e) => set('same_day_ready_time', e.target.value)}
          helper="The source only needs to hold back stock until this time."
        />
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wider text-gray-600">Sold from the next day</p>
          <MultiSelect
            options={categories.filter((c) => !c.deleted_at).map((c) => ({ value: c.id, label: c.name }))}
            value={settings.next_day_category_ids}
            onChange={(ids) => set('next_day_category_ids', ids)}
            placeholder="None"
          />
          <p className="mt-1 text-xs text-gray-400">Categories made in the evening (cookie melt), so the source holds back the whole day.</p>
        </div>
        <Input
          label="Daily snapshot at"
          type="time"
          value={hhmm(settings.snapshot_time)}
          onChange={(e) => set('snapshot_time', e.target.value)}
          helper="When the day's forecast is stored for the forecast-vs-actual history."
        />
        <Input
          label="Recency half-life (days)"
          type="number"
          min={1}
          max={90}
          value={settings.half_life_days}
          onChange={(e) => set('half_life_days', Number(e.target.value))}
          helper="A day this old counts half as much as yesterday."
        />
        <Input
          label="History window (days)"
          type="number"
          min={14}
          max={365}
          value={settings.window_days}
          onChange={(e) => set('window_days', Number(e.target.value))}
        />
      </div>
      <Button onClick={() => void save()} loading={saving}>Save settings</Button>
    </div>
  );
}
