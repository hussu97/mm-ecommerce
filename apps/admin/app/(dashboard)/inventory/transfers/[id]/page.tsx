'use client';

// Parent transfer-order detail + report. A transfer order is raised from ONE
// source branch and fans out to many destinations; this shows the parent header,
// the allocation grid (item rows × one column per destination child, with the
// `total_by_item` totals), each child's status, and the movement report from
// `transferOrderReport` — including the mini stock-adjustment report posted when
// the admin overrode on-hand at create.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import {
  branchesApi,
  inventoryApi,
  type TransferOrder,
  type TransferOrderReport,
} from '@/lib/pos-api';
import type { Branch } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Spinner } from '@/components/ui';
import { formatCurrency, formatDateTime, formatQuantity } from '@/lib/utils';
import { transferStatusLabel, transferStatusVariant } from '../../_shared';

type TotalLine = TransferOrder['total_by_item'][number];

const num = (v: unknown): number => Number(v ?? 0);

/** Group the total-by-item rows by category — category order (min per bucket) →
 * category name → item name, Uncategorised last. Mirrors the shift-report page. */
function groupByCategory(lines: TotalLine[]): { name: string; lines: TotalLine[] }[] {
  const map = new Map<string, { order: number; lines: TotalLine[] }>();
  for (const line of lines) {
    const name = line.category_name ? String(line.category_name) : 'Uncategorised';
    const order = line.category_order == null ? Number.MAX_SAFE_INTEGER : Number(line.category_order);
    const bucket = map.get(name) ?? { order, lines: [] };
    bucket.order = Math.min(bucket.order, order);
    bucket.lines.push(line);
    map.set(name, bucket);
  }
  const itemName = (l: TotalLine) => l.item_name ?? l.item_sku ?? l.item_id;
  return [...map.entries()]
    .sort((a, b) => a[1].order - b[1].order || a[0].localeCompare(b[0]))
    .map(([name, bucket]) => ({ name, lines: [...bucket.lines].sort((a, b) => itemName(a).localeCompare(itemName(b))) }));
}

export default function TransferOrderDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [order, setOrder] = useState<TransferOrder | null>(null);
  const [report, setReport] = useState<TransferOrderReport | null>(null);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [o, r] = await Promise.all([
        inventoryApi.transferOrder(id),
        inventoryApi.transferOrderReport(id).catch(() => null),
      ]);
      setOrder(o);
      setReport(r);
      setError('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load the transfer order.');
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void branchesApi.list().then(setBranches).catch(() => setBranches([])); }, []);

  const branchName = useMemo(
    () => (bid: string | null) => branches.find((b) => b.id === bid)?.name ?? bid ?? '—',
    [branches],
  );

  // For each child, the allocated quantity per item — so the grid can show a
  // column per destination against the item rows.
  const childQty = useMemo(() => {
    const map = new Map<string, Map<string, number>>();
    for (const child of order?.children ?? []) {
      const byItem = new Map<string, number>();
      for (const line of child.items) byItem.set(line.item_id, num(line.quantity));
      map.set(child.id, byItem);
    }
    return map;
  }, [order]);

  if (loading) return <div className="p-6"><Spinner /></div>;
  if (error || !order) return (
    <div className="p-6 space-y-3">
      <p className="bg-red-50 p-3 text-sm text-red-800">{error || 'Transfer order not found.'}</p>
      <Link href="/inventory/submissions/transfers" className="text-sm text-primary underline">Back to transfers</Link>
    </div>
  );

  const isReturn = order.kind === 'return';
  const groups = groupByCategory(order.total_by_item);
  const children = order.children;
  const grandTotal = order.total_by_item.reduce((sum, l) => sum + num(l.total_quantity), 0);
  const colCount = 2 + children.length + 1; // item, unit, children…, total

  return (
    <div className="p-6 max-w-[1600px] space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link href="/inventory/submissions/transfers" className="text-xs text-gray-400 hover:text-primary">← Transfers &amp; returns</Link>
          <h1 className="font-display text-xl text-primary tracking-wide">{order.reference}</h1>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={isReturn ? 'warning' : 'neutral'}>{isReturn ? 'Return' : 'Transfer'}</Badge>
          <Badge variant={transferStatusVariant(order.status)}>{transferStatusLabel(order.status)}</Badge>
        </div>
      </div>

      <div className="grid gap-3 border border-gray-200 p-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <Detail label="Source" value={branchName(order.source_branch_id)} />
        <Detail label="Destinations" value={`${children.length} branch${children.length === 1 ? '' : 'es'}`} />
        <Detail label="Business date" value={order.business_date} />
        <Detail label="Created" value={order.created_at ? formatDateTime(order.created_at) : '—'} />
        {order.required_date && <Detail label="Required by" value={order.required_date} />}
        {order.template_version != null && <Detail label="Raised from" value={`Template v${order.template_version}`} />}
        {order.adjustment_group_id && <Detail label="Overrides" value="Posted a shortfall top-up — see the report below" />}
        {order.notes && <Detail label="Notes" value={order.notes} />}
      </div>

      {/* Allocation grid: item rows × one column per destination child, with the
          parent's total-across-branches on the right. */}
      <div>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wider text-gray-600">Allocation by branch</h2>
        <div className="overflow-x-auto border border-gray-200">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs uppercase tracking-wider text-gray-500">
              <tr>
                <th className="px-2 py-1 sticky left-0 bg-gray-50">Item</th>
                <th className="px-2 py-1">Unit</th>
                {children.map((child) => (
                  <th key={child.id} className="px-2 py-1 text-right whitespace-nowrap">
                    <Link href={`/inventory/transfers/${order.id}`} className="hover:underline">{branchName(child.branch_id)}</Link>
                    <div className="mt-0.5"><Badge variant={transferStatusVariant(child.status)}>{transferStatusLabel(child.status)}</Badge></div>
                  </th>
                ))}
                <th className="px-2 py-1 text-right">Total</th>
              </tr>
            </thead>
            <tbody>
              {groups.map((group) => (
                <GroupRows key={group.name} name={group.name} span={colCount}>
                  {group.lines.map((line) => (
                    <tr key={line.item_id} className="border-t border-gray-100">
                      <td className="px-2 py-1 font-medium sticky left-0 bg-white">{line.item_name ?? line.item_sku ?? line.item_id}</td>
                      <td className="px-2 py-1 text-gray-500">{line.unit}</td>
                      {children.map((child) => {
                        const qty = childQty.get(child.id)?.get(line.item_id);
                        return (
                          <td key={child.id} className="px-2 py-1 text-right tabular-nums">
                            {qty ? formatQuantity(qty) : <span className="text-gray-300">—</span>}
                          </td>
                        );
                      })}
                      <td className="px-2 py-1 text-right font-medium tabular-nums">{formatQuantity(line.total_quantity)}</td>
                    </tr>
                  ))}
                </GroupRows>
              ))}
              <tr className="border-t-2 border-gray-300 bg-gray-50 font-medium">
                <td className="px-2 py-1 sticky left-0 bg-gray-50" colSpan={2}>Grand total</td>
                {children.map((child) => {
                  const total = order.total_by_item.reduce((sum, l) => sum + (childQty.get(child.id)?.get(l.item_id) ?? 0), 0);
                  return <td key={child.id} className="px-2 py-1 text-right tabular-nums">{formatQuantity(total)}</td>;
                })}
                <td className="px-2 py-1 text-right tabular-nums">{formatQuantity(grandTotal)}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* The parent report: per-branch legs with movement values, then any
          shortfall adjustments posted at create. */}
      <div>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wider text-gray-600">Movement report</h2>
        {report ? (
          <div className="overflow-x-auto border border-gray-200">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase tracking-wider text-gray-500">
                <tr>
                  <th className="px-2 py-1">Destination</th>
                  <th className="px-2 py-1">Reference</th>
                  <th className="px-2 py-1">Status</th>
                  <th className="px-2 py-1 text-right">Items</th>
                  <th className="px-2 py-1 text-right">Sent</th>
                  <th className="px-2 py-1 text-right">Received</th>
                  <th className="px-2 py-1 text-right">Sent value</th>
                  <th className="px-2 py-1 text-right">Received value</th>
                </tr>
              </thead>
              <tbody>
                {report.children.map((child) => (
                  <tr key={child.transfer_id} className="border-t border-gray-100">
                    <td className="px-2 py-1 font-medium">{child.destination_branch_name ?? branchName(child.destination_branch_id)}</td>
                    <td className="px-2 py-1 text-gray-600">{child.reference}</td>
                    <td className="px-2 py-1"><Badge variant={transferStatusVariant(child.status)}>{transferStatusLabel(child.status)}</Badge></td>
                    <td className="px-2 py-1 text-right tabular-nums">{child.item_count}</td>
                    <td className="px-2 py-1 text-right tabular-nums">{formatQuantity(child.total_sent)}</td>
                    <td className="px-2 py-1 text-right tabular-nums">{formatQuantity(child.total_received)}</td>
                    <td className="px-2 py-1 text-right tabular-nums">{formatCurrency(child.sent_value)}</td>
                    <td className="px-2 py-1 text-right tabular-nums">{formatCurrency(child.received_value)}</td>
                  </tr>
                ))}
                {report.children.length === 0 && (
                  <tr><td colSpan={8} className="px-2 py-2 text-center text-sm text-gray-400">No legs.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="border border-dashed border-gray-300 p-3 text-sm text-gray-500">The report could not be loaded.</p>
        )}
      </div>

      {report && report.adjustments.length > 0 && (
        <div>
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wider text-gray-600">Stock adjustments (override top-up)</h2>
          <p className="mb-2 text-xs text-gray-500">
            Allocating more than the source held wrote the shortfall off at create. Each line is a posted stock adjustment tied to this order.
          </p>
          <div className="overflow-x-auto border border-gray-200">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase tracking-wider text-gray-500">
                <tr>
                  <th className="px-2 py-1">Item</th>
                  <th className="px-2 py-1">Reference</th>
                  <th className="px-2 py-1 text-right">Quantity</th>
                  <th className="px-2 py-1 text-right">Unit cost</th>
                  <th className="px-2 py-1 text-right">Value</th>
                </tr>
              </thead>
              <tbody>
                {report.adjustments.map((adj) => (
                  <tr key={adj.transaction_id} className="border-t border-gray-100">
                    <td className="px-2 py-1 font-medium">{adj.item_name ?? adj.item_id}</td>
                    <td className="px-2 py-1 text-gray-600">
                      <Link href={`/inventory/transactions/${adj.transaction_id}`} className="text-primary hover:underline">{adj.transaction_reference}</Link>
                    </td>
                    <td className="px-2 py-1 text-right tabular-nums">{formatQuantity(adj.quantity)}</td>
                    <td className="px-2 py-1 text-right tabular-nums">{formatCurrency(adj.unit_cost)}</td>
                    <td className="px-2 py-1 text-right tabular-nums">{formatCurrency(adj.value)}</td>
                  </tr>
                ))}
                <tr className="border-t-2 border-gray-300 bg-gray-50 font-medium">
                  <td className="px-2 py-1" colSpan={4}>Adjustment total</td>
                  <td className="px-2 py-1 text-right tabular-nums">{formatCurrency(report.adjustment_total)}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wider text-gray-400">{label}</p>
      <p className="text-gray-800">{value}</p>
    </div>
  );
}

function GroupRows({ name, span, children }: { name: string; span: number; children: React.ReactNode }) {
  return (
    <>
      <tr className="bg-gray-100/70">
        <td colSpan={span} className="px-2 py-1 text-xs font-medium uppercase tracking-wide text-gray-600">{name}</td>
      </tr>
      {children}
    </>
  );
}
