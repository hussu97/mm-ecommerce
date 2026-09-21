'use client';

// Report submissions → Production report → one order. Read-only: what the admin
// asked the source branch to make, and what the till actually produced. Lines are
// grouped by category the way the shift-report detail groups them; a produced qty
// that differs from the plan is flagged "modified", and each produced line links
// out to its PRODUCTION movement reference in the ledger.

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';

import { inventoryApi } from '@/lib/pos-api';
import type { ProductionLine, ProductionOrder } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Spinner } from '@/components/ui';
import { formatDateTime, formatQuantity, interactiveRowClass } from '@/lib/utils';
import { transferStatusLabel, transferStatusVariant } from '../../../_shared';

const num = (value: unknown): number => Number(value ?? 0);

// A line's quantity in its recipe basis: batches show "N batches (= M units)",
// units show "M units". Falls back to owner units on legacy rows with no basis
// count. `unitQty` is the owner-unit truth; `basisQty` is the entered basis count.
// The item's real unit (g/kg/piece), resolved server-side; falls back to the
// abstract kind on legacy rows the API served before `display_unit` existed.
const unitLabel = (line: ProductionLine): string => line.display_unit ?? line.unit;

const basisText = (line: ProductionLine, basisQty: number | null, unitQty: number | null): string => {
  if (unitQty == null) return '—';
  if (line.basis === 'batch') {
    const b = basisQty ?? unitQty;
    return `${formatQuantity(b)} batch${b === 1 ? '' : 'es'} (= ${formatQuantity(unitQty)} ${unitLabel(line)})`;
  }
  return `${formatQuantity(unitQty)} ${unitLabel(line)}`;
};

export default function ProductionOrderDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [order, setOrder] = useState<ProductionOrder | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    inventoryApi.productionOrder(id)
      .then((data) => { if (!cancelled) { setOrder(data); setError(''); } })
      .catch((err) => { if (!cancelled) setError(err instanceof ApiError ? err.message : 'Failed to load the production order.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [id]);

  if (loading) return <div className="p-6"><Spinner /></div>;
  if (error || !order) return (
    <div className="p-6 space-y-3">
      <p className="bg-red-50 p-3 text-sm text-red-800">{error || 'Production order not found.'}</p>
      <Link href="/inventory/submissions/production" className="text-sm text-primary underline">Back to production report</Link>
    </div>
  );

  // Group lines by category, categories in display order, items by name — the same
  // order the register presents production in.
  const grouped = (() => {
    const map = new Map<string, { order: number; lines: ProductionLine[] }>();
    for (const line of order.lines) {
      const name = line.category_name ?? 'Uncategorised';
      const catOrder = line.category_order == null ? Number.MAX_SAFE_INTEGER : line.category_order;
      const bucket = map.get(name) ?? { order: catOrder, lines: [] };
      bucket.order = Math.min(bucket.order, catOrder);
      bucket.lines.push(line);
      map.set(name, bucket);
    }
    return [...map.entries()]
      .sort((a, b) => a[1].order - b[1].order || a[0].localeCompare(b[0]))
      .map(([name, bucket]) => ({ name, lines: [...bucket.lines].sort((a, b) => (a.item_name ?? '').localeCompare(b.item_name ?? '')) }));
  })();

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link href="/inventory/submissions/production" className="text-xs text-gray-400 hover:text-primary">← Production report</Link>
          <h1 className="font-display text-xl text-primary tracking-wide">{order.reference}</h1>
        </div>
        <Badge variant={transferStatusVariant(order.status)}>{transferStatusLabel(order.status)}</Badge>
      </div>

      <div className="grid gap-3 border border-gray-200 p-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
        <Detail label="Source branch" value={order.source_branch_name ?? '—'} />
        <Detail label="Business date" value={order.business_date} />
        <Detail label="Created at" value={formatDateTime(order.created_at)} />
        {order.transfer_order_id ? (
          <div>
            <p className="text-xs uppercase tracking-wider text-gray-400">Transfer order</p>
            <Link href={`/inventory/transfers/${order.transfer_order_id}`} className="text-primary hover:underline">Raised with a transfer</Link>
          </div>
        ) : (
          <Detail label="Transfer order" value="Production only" />
        )}
        {order.notes && <Detail label="Notes" value={order.notes} />}
      </div>

      <div className="overflow-x-auto border border-gray-200">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs uppercase tracking-wider text-gray-500">
            <tr>
              <th className="px-2 py-1">Item</th>
              <th className="px-2 py-1">Unit</th>
              <th className="px-2 py-1 text-right">Requested</th>
              <th className="px-2 py-1 text-right">Produced</th>
              <th className="px-2 py-1 text-right">Difference</th>
              <th className="px-2 py-1">Status</th>
              <th className="px-2 py-1">Ledger reference</th>
              <th className="px-2 py-1">Note</th>
            </tr>
          </thead>
          <tbody>
            {grouped.map((group) => (
              <GroupRows key={group.name} name={group.name} span={8}>
                {group.lines.map((line) => {
                  const produced = line.produced_quantity;
                  const modified = line.status === 'produced' && produced != null && num(produced) !== num(line.planned_quantity);
                  const diff = produced == null ? null : num(produced) - num(line.planned_quantity);
                  return (
                    <tr key={line.id} className={`border-t border-gray-100 ${interactiveRowClass}`}>
                      <td className="px-2 py-1 font-medium">
                        {line.item_name ?? line.item_id}
                        {line.item_sku && <span className="ml-1 text-xs text-gray-400">{line.item_sku}</span>}
                      </td>
                      <td className="px-2 py-1 text-gray-500">{line.basis === 'batch' ? 'batch' : unitLabel(line)}</td>
                      <td className="px-2 py-1 text-right tabular-nums whitespace-nowrap">{basisText(line, line.planned_basis_quantity, line.planned_quantity)}</td>
                      <td className="px-2 py-1 text-right tabular-nums whitespace-nowrap">
                        {produced == null ? <span className="text-gray-300">—</span> : basisText(line, line.produced_basis_quantity, produced)}
                        {modified && <Badge variant="warning" className="ml-2">Modified</Badge>}
                      </td>
                      <td className={`px-2 py-1 text-right tabular-nums ${diff != null && diff !== 0 ? 'text-amber-700' : 'text-gray-500'}`}>
                        {diff == null ? <span className="text-gray-300">—</span> : `${diff > 0 ? '+' : ''}${formatQuantity(diff)}`}
                      </td>
                      <td className="px-2 py-1"><Badge variant={transferStatusVariant(line.status)}>{transferStatusLabel(line.status)}</Badge></td>
                      <td className="px-2 py-1 text-xs text-gray-600">{line.production_reference ?? <span className="text-gray-300">—</span>}</td>
                      <td className="px-2 py-1 text-xs text-gray-600">{line.cancel_note ?? ''}</td>
                    </tr>
                  );
                })}
              </GroupRows>
            ))}
            {(() => {
              const requested = order.lines.reduce((s, l) => s + num(l.planned_quantity), 0);
              const produced = order.lines.reduce((s, l) => s + num(l.produced_quantity), 0);
              const diff = produced - requested;
              return (
                <tr className="border-t-2 border-gray-300 bg-gray-50 font-medium">
                  <td className="px-2 py-1" colSpan={2}>Branch total</td>
                  <td className="px-2 py-1 text-right tabular-nums">{formatQuantity(requested)}</td>
                  <td className="px-2 py-1 text-right tabular-nums">{formatQuantity(produced)}</td>
                  <td className={`px-2 py-1 text-right tabular-nums ${diff !== 0 ? 'text-amber-700' : ''}`}>{`${diff > 0 ? '+' : ''}${formatQuantity(diff)}`}</td>
                  <td className="px-2 py-1" colSpan={3} />
                </tr>
              );
            })()}
          </tbody>
        </table>
      </div>
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
