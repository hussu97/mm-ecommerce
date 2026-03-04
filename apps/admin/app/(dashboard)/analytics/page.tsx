'use client';

import { useEffect, useState, useCallback } from 'react';
import dynamic from 'next/dynamic';
import { analyticsApi } from '@/lib/api';
import type { AnalyticsOverview, FunnelData, OrdersPoint, RevenuePoint, TopProduct } from '@/lib/types';
import { formatCurrency } from '@/lib/utils';

// Lazy-load recharts to avoid SSR issues
const LineChart = dynamic(() => import('recharts').then(m => m.LineChart), { ssr: false });
const BarChart = dynamic(() => import('recharts').then(m => m.BarChart), { ssr: false });
const PieChart = dynamic(() => import('recharts').then(m => m.PieChart), { ssr: false });
const Line = dynamic(() => import('recharts').then(m => m.Line), { ssr: false });
const Bar = dynamic(() => import('recharts').then(m => m.Bar), { ssr: false });
const Pie = dynamic(() => import('recharts').then(m => m.Pie), { ssr: false });
const Cell = dynamic(() => import('recharts').then(m => m.Cell), { ssr: false });
const XAxis = dynamic(() => import('recharts').then(m => m.XAxis), { ssr: false });
const YAxis = dynamic(() => import('recharts').then(m => m.YAxis), { ssr: false });
const CartesianGrid = dynamic(() => import('recharts').then(m => m.CartesianGrid), { ssr: false });
const Tooltip = dynamic(() => import('recharts').then(m => m.Tooltip), { ssr: false });
const ResponsiveContainer = dynamic(() => import('recharts').then(m => m.ResponsiveContainer), { ssr: false });
const Legend = dynamic(() => import('recharts').then(m => m.Legend), { ssr: false });

// ─── Brand colors ─────────────────────────────────────────────────────────────
const PRIMARY = '#8a5a64';
const SECONDARY = '#d6acab';
const PIE_COLORS = [PRIMARY, SECONDARY, '#c4958f', '#e8c9c7'];

// ─── Date helpers ─────────────────────────────────────────────────────────────
function toIso(d: Date) {
  return d.toISOString().slice(0, 10);
}

function rangeForPreset(preset: string): { start: string; end: string } {
  const end = new Date();
  const start = new Date();
  if (preset === '7d') start.setDate(end.getDate() - 6);
  else if (preset === '90d') start.setDate(end.getDate() - 89);
  else start.setDate(end.getDate() - 29);
  return { start: toIso(start), end: toIso(end) };
}

// ─── Metric card ─────────────────────────────────────────────────────────────

function MetricCard({
  label, value, sub, growth,
}: {
  label: string;
  value: string;
  sub?: string;
  growth?: number;
}) {
  return (
    <div className="bg-white border border-gray-200 p-4">
      <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-1">{label}</p>
      <p className="font-display text-2xl text-gray-800">{value}</p>
      {sub && <p className="text-xs font-body text-gray-400 mt-0.5">{sub}</p>}
      {growth !== undefined && (
        <p className={`text-xs font-body mt-1 ${growth >= 0 ? 'text-green-600' : 'text-red-500'}`}>
          {growth >= 0 ? '▲' : '▼'} {Math.abs(growth)}% vs prior period
        </p>
      )}
    </div>
  );
}

// ─── Section wrapper ─────────────────────────────────────────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white border border-gray-200 p-4">
      <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-4">{title}</p>
      {children}
    </div>
  );
}

// ─── Page ────────────────────────────────────────────────────────────────────

export default function AnalyticsPage() {
  const [preset, setPreset] = useState('30d');
  const [startDate, setStartDate] = useState(() => rangeForPreset('30d').start);
  const [endDate, setEndDate] = useState(() => rangeForPreset('30d').end);

  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [revenue, setRevenue] = useState<RevenuePoint[]>([]);
  const [ordersChart, setOrdersChart] = useState<OrdersPoint[]>([]);
  const [topProducts, setTopProducts] = useState<TopProduct[]>([]);
  const [funnel, setFunnel] = useState<FunnelData | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = { start_date: startDate, end_date: endDate };
      const [ov, rev, oc, tp, fn] = await Promise.all([
        analyticsApi.overview(params),
        analyticsApi.revenue(params),
        analyticsApi.ordersChart(params),
        analyticsApi.topProducts(params),
        analyticsApi.funnel(params),
      ]);
      setOverview(ov);
      setRevenue(rev);
      setOrdersChart(oc);
      setTopProducts(tp);
      setFunnel(fn);
    } catch {
      // silent — API may not be running
    } finally {
      setLoading(false);
    }
  }, [startDate, endDate]);

  useEffect(() => { load(); }, [load]);

  function applyPreset(p: string) {
    setPreset(p);
    const { start, end } = rangeForPreset(p);
    setStartDate(start);
    setEndDate(end);
  }

  // Pie data from funnel
  const pieData = funnel
    ? [
        { name: 'Created', value: funnel.created },
        { name: 'Confirmed', value: funnel.confirmed },
        { name: 'Packed', value: funnel.packed },
        { name: 'Cancelled', value: funnel.cancelled },
      ].filter(d => d.value > 0)
    : [];

  return (
    <div>
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
        <div>
          <h1 className="font-display text-2xl text-gray-800">Analytics</h1>
          <p className="text-xs text-gray-400 font-body mt-0.5">
            {startDate} → {endDate}
          </p>
        </div>

        {/* Date range controls */}
        <div className="flex flex-wrap items-center gap-2">
          {(['7d', '30d', '90d'] as const).map(p => (
            <button
              key={p}
              onClick={() => applyPreset(p)}
              className={`px-3 py-1.5 text-xs font-body uppercase tracking-wider border transition-colors ${
                preset === p
                  ? 'bg-primary text-white border-primary'
                  : 'border-gray-300 text-gray-600 hover:bg-gray-50'
              }`}
            >
              Last {p}
            </button>
          ))}
          <div className="flex items-center gap-1">
            <input
              type="date"
              value={startDate}
              onChange={e => { setPreset('custom'); setStartDate(e.target.value); }}
              className="px-2 py-1.5 text-xs font-body border border-gray-300 focus:outline-none focus:border-primary"
            />
            <span className="text-gray-400 text-xs">→</span>
            <input
              type="date"
              value={endDate}
              onChange={e => { setPreset('custom'); setEndDate(e.target.value); }}
              className="px-2 py-1.5 text-xs font-body border border-gray-300 focus:outline-none focus:border-primary"
            />
          </div>
        </div>
      </div>

      {loading ? (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="bg-white border border-gray-200 p-4 h-24 animate-pulse" />
          ))}
        </div>
      ) : (
        <>
          {/* Overview cards */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
            <MetricCard
              label="Revenue"
              value={formatCurrency(overview?.total_revenue ?? 0)}
              growth={overview?.revenue_growth}
            />
            <MetricCard
              label="Orders"
              value={String(overview?.total_orders ?? 0)}
              growth={overview?.orders_growth}
            />
            <MetricCard
              label="Avg Order Value"
              value={formatCurrency(overview?.avg_order_value ?? 0)}
            />
            <MetricCard
              label="Customers"
              value={String(overview?.total_customers ?? 0)}
              sub="unique registered"
            />
          </div>

          {/* Revenue + Orders charts */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
            <Section title="Revenue Over Time">
              {revenue.length === 0 ? (
                <p className="text-xs text-gray-400 font-body text-center py-8">No data for this period.</p>
              ) : (
                <ResponsiveContainer width="100%" height={220}>
                  <LineChart data={revenue} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 10, fontFamily: 'Jost, sans-serif', fill: '#9ca3af' }}
                      tickFormatter={d => d.slice(5)}
                    />
                    <YAxis
                      tick={{ fontSize: 10, fontFamily: 'Jost, sans-serif', fill: '#9ca3af' }}
                      tickFormatter={v => `${v}`}
                    />
                    <Tooltip
                      formatter={(v: unknown) => [formatCurrency(Number(v)), 'Revenue']}
                      labelStyle={{ fontSize: 10 }}
                      contentStyle={{ fontSize: 11, borderColor: '#e5e7eb' }}
                    />
                    <Line
                      type="monotone"
                      dataKey="revenue"
                      stroke={PRIMARY}
                      strokeWidth={2}
                      dot={false}
                      activeDot={{ r: 4 }}
                    />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </Section>

            <Section title="Orders Over Time">
              {ordersChart.length === 0 ? (
                <p className="text-xs text-gray-400 font-body text-center py-8">No data for this period.</p>
              ) : (
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={ordersChart} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 10, fontFamily: 'Jost, sans-serif', fill: '#9ca3af' }}
                      tickFormatter={d => d.slice(5)}
                    />
                    <YAxis
                      tick={{ fontSize: 10, fontFamily: 'Jost, sans-serif', fill: '#9ca3af' }}
                      allowDecimals={false}
                    />
                    <Tooltip
                      formatter={(v: unknown) => [String(v), 'Orders']}
                      labelStyle={{ fontSize: 10 }}
                      contentStyle={{ fontSize: 11, borderColor: '#e5e7eb' }}
                    />
                    <Bar dataKey="count" fill={SECONDARY} radius={[2, 2, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </Section>
          </div>

          {/* Top products + Funnel */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Section title="Top Products by Revenue">
              {topProducts.length === 0 ? (
                <p className="text-xs text-gray-400 font-body text-center py-8">No data for this period.</p>
              ) : (
                <table className="w-full text-xs font-body">
                  <thead>
                    <tr className="border-b border-gray-100">
                      <th className="pb-2 text-left text-[10px] uppercase tracking-widest text-gray-400">Product</th>
                      <th className="pb-2 text-right text-[10px] uppercase tracking-widest text-gray-400">Qty</th>
                      <th className="pb-2 text-right text-[10px] uppercase tracking-widest text-gray-400">Revenue</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50">
                    {topProducts.map((p, i) => (
                      <tr key={i}>
                        <td className="py-2 text-gray-700">
                          {p.product_name}
                          <span className="text-gray-400 ml-1">({p.variant_name})</span>
                        </td>
                        <td className="py-2 text-right text-gray-500">{p.quantity}</td>
                        <td className="py-2 text-right text-gray-800">{formatCurrency(p.revenue)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Section>

            <Section title="Orders by Status">
              {pieData.length === 0 ? (
                <p className="text-xs text-gray-400 font-body text-center py-8">No data for this period.</p>
              ) : (
                <div>
                  <ResponsiveContainer width="100%" height={200}>
                    <PieChart>
                      <Pie
                        data={pieData}
                        cx="50%"
                        cy="50%"
                        innerRadius={55}
                        outerRadius={85}
                        paddingAngle={2}
                        dataKey="value"
                      >
                        {pieData.map((_, idx) => (
                          <Cell key={idx} fill={PIE_COLORS[idx % PIE_COLORS.length]} />
                        ))}
                      </Pie>
                      <Tooltip
                        formatter={(v: unknown) => [String(v), 'Orders']}
                        contentStyle={{ fontSize: 11, borderColor: '#e5e7eb' }}
                      />
                      <Legend
                        iconSize={8}
                        wrapperStyle={{ fontSize: 10, fontFamily: 'Jost, sans-serif' }}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                  {funnel && (
                    <p className="text-center text-[11px] font-body text-gray-500 mt-1">
                      Completion rate: <span className="text-primary font-medium">{funnel.conversion_rate}%</span>
                    </p>
                  )}
                </div>
              )}
            </Section>
          </div>
        </>
      )}
    </div>
  );
}
