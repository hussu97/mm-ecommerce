'use client';

// Inventory transaction detail: one posted transaction and its lines. Reached
// from the "Manual stock counts" table on the Report submissions tab and from
// the stock ledger's Source column, which deep-links audit rows here.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { branchesApi, inventoryApi } from '@/lib/pos-api';
import type { Branch, InventoryTransaction } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Spinner } from '@/components/ui';
import { formatCurrency, formatDateTime, formatQuantity } from '@/lib/utils';

const num = (v: unknown): number => Number(v ?? 0);

type CountLine = InventoryTransaction['items'][number];

const lineItemName = (line: CountLine): string => line.item_name ?? line.item_sku ?? line.item_id;
const lineCategory = (line: CountLine): string => (line.category_name ? String(line.category_name) : 'Uncategorised');

/** Group lines by category — category order (min per bucket) → category name →
 * item name, Uncategorised last. Mirrors the shift-report detail page. */
function groupByCategory(lines: CountLine[]): { name: string; lines: CountLine[] }[] {
  const map = new Map<string, { order: number; lines: CountLine[] }>();
  for (const line of lines) {
    const name = lineCategory(line);
    const order = line.category_order == null ? Number.MAX_SAFE_INTEGER : Number(line.category_order);
    const bucket = map.get(name) ?? { order, lines: [] };
    bucket.order = Math.min(bucket.order, order);
    bucket.lines.push(line);
    map.set(name, bucket);
  }
  return [...map.entries()]
    .sort((a, b) => a[1].order - b[1].order || a[0].localeCompare(b[0]))
    .map(([name, bucket]) => ({ name, lines: [...bucket.lines].sort((a, b) => lineItemName(a).localeCompare(lineItemName(b))) }));
}

/** "inventory_count" → "Inventory count". */
function humanizeType(type: string): string {
  const words = type.replaceAll('_', ' ').trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : type;
}

export default function TransactionDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [transaction, setTransaction] = useState<InventoryTransaction | null>(null);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setTransaction(await inventoryApi.getTransaction(id));
      setError('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load the transaction.');
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void branchesApi.list().then(setBranches).catch(() => setBranches([])); }, []);

  const branchName = useMemo(() => (bid: string | null) => branches.find((b) => b.id === bid)?.name ?? '—', [branches]);

  if (loading) return <div className="p-6"><Spinner /></div>;
  if (error || !transaction) return (
    <div className="p-6 space-y-3">
      <p className="bg-red-50 p-3 text-sm text-red-800">{error || 'Transaction not found.'}</p>
      <Link href="/inventory" className="text-sm text-primary underline">Back to inventory</Link>
    </div>
  );

  return (
    <div className="p-6 max-w-[1100px] space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link href="/inventory" className="text-xs text-gray-400 hover:text-primary">← Inventory</Link>
          <h1 className="font-display text-xl text-primary tracking-wide">{transaction.reference}</h1>
        </div>
        <Badge>{humanizeType(transaction.type)}</Badge>
      </div>

      <div className="grid gap-3 border border-gray-200 p-4 text-sm sm:grid-cols-2 lg:grid-cols-5">
        <Detail label="Branch" value={branchName(transaction.branch_id)} />
        <Detail label="Business date" value={transaction.business_date} />
        <Detail label="Posted" value={transaction.posted_at ? formatDateTime(transaction.posted_at) : '—'} />
        <Detail label="Posted by" value={transaction.posted_by_name ?? '—'} />
        <Detail label="Total value impact" value={formatCurrency(transaction.total_cost)} />
      </div>

      <div className="overflow-x-auto border border-gray-200">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs uppercase tracking-wider text-gray-500">
            <tr>
              <th className="px-2 py-1">Item</th>
              <th className="px-2 py-1 text-right">Expected</th>
              <th className="px-2 py-1 text-right">Counted</th>
              <th className="px-2 py-1 text-right">Delta</th>
              <th className="px-2 py-1 text-right">Value</th>
              <th className="px-2 py-1">Remark</th>
            </tr>
          </thead>
          <tbody>
            {groupByCategory(transaction.items).map((group) => (
              <GroupRows key={group.name} name={group.name} span={6}>
                {group.lines.map((line) => {
                  const delta = num(line.signed_quantity);
                  return (
                    <tr key={line.id} className="border-t border-gray-100">
                      <td className="px-2 py-1 font-medium">{lineItemName(line)}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{line.expected_quantity == null ? '—' : formatQuantity(line.expected_quantity)}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{line.balance_after_quantity == null ? '—' : formatQuantity(line.balance_after_quantity)}</td>
                      <td className={`px-2 py-1 text-right tabular-nums ${delta < 0 ? 'text-red-600' : delta > 0 ? 'text-green-700' : 'text-gray-400'}`}>
                        {delta === 0 ? '—' : `${delta > 0 ? '+' : ''}${formatQuantity(delta)}`}
                      </td>
                      <td className="px-2 py-1 text-right tabular-nums">{formatCurrency(line.total_cost)}</td>
                      <td className="px-2 py-1 text-gray-600">{line.notes ?? '—'}</td>
                    </tr>
                  );
                })}
              </GroupRows>
            ))}
          </tbody>
        </table>
      </div>
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

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wider text-gray-400">{label}</p>
      <p className="text-gray-800">{value}</p>
    </div>
  );
}
