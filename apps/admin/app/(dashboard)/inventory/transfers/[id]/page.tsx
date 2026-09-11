'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { branchesApi, inventoryApi, type TransferOrder } from '@/lib/pos-api';
import type { Branch } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Spinner } from '@/components/ui';
import { formatDateTime, formatQuantity } from '@/lib/utils';

const num = (v: unknown): number => Number(v ?? 0);

/** Where the transfer is, in words, from its two legs. */
function receivingStatus(t: TransferOrder): { label: string; variant: 'success' | 'warning' | 'neutral' | 'danger' } {
  if (t.received_transaction_id) return { label: 'Received', variant: 'success' };
  if (t.sent_transaction_id) return { label: 'Sent — awaiting receipt', variant: 'warning' };
  if (t.status === 'declined') return { label: 'Declined', variant: 'danger' };
  return { label: t.status.replaceAll('_', ' '), variant: 'neutral' };
}

export default function TransferDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [transfer, setTransfer] = useState<TransferOrder | null>(null);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setTransfer(await inventoryApi.transferOrder(id));
      setError('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load the transfer.');
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void branchesApi.list().then(setBranches).catch(() => setBranches([])); }, []);

  const branchName = useMemo(() => (bid: string | null) => branches.find((b) => b.id === bid)?.name ?? '—', [branches]);

  if (loading) return <div className="p-6"><Spinner /></div>;
  if (error || !transfer) return (
    <div className="p-6 space-y-3">
      <p className="bg-red-50 p-3 text-sm text-red-800">{error || 'Transfer not found.'}</p>
      <Link href="/inventory" className="text-sm text-primary underline">Back to inventory</Link>
    </div>
  );

  const isReturn = transfer.kind === 'return';
  const status = receivingStatus(transfer);
  const hasVariance = transfer.items.some((l) => num(l.received_quantity) !== num(l.sent_quantity) && (transfer.received_transaction_id || transfer.sent_transaction_id));

  return (
    <div className="p-6 max-w-[1100px] space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link href="/inventory" className="text-xs text-gray-400 hover:text-primary">← Inventory</Link>
          <h1 className="font-display text-xl text-primary tracking-wide">{transfer.reference}</h1>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={isReturn ? 'warning' : 'neutral'}>{isReturn ? 'Return' : 'Transfer'}</Badge>
          <Badge variant={status.variant}>{status.label}</Badge>
        </div>
      </div>

      <div className="grid gap-3 border border-gray-200 p-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <Detail label="From" value={branchName(transfer.source_branch_id)} />
        <Detail label="To" value={branchName(transfer.branch_id)} />
        <Detail label="Business date" value={transfer.business_date} />
        <Detail label="Created" value={transfer.created_at ? formatDateTime(transfer.created_at) : '—'} />
        {transfer.template_version != null && (
          <Detail label="Raised from" value={`Template v${transfer.template_version}`} />
        )}
      </div>

      {hasVariance && (
        <p className="bg-amber-50 border border-amber-200 p-3 text-sm text-amber-800">
          Some lines were received short or over what was sent — see the Variance column and reasons below.
        </p>
      )}

      <div className="overflow-x-auto border border-gray-200">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs uppercase tracking-wider text-gray-500">
            <tr>
              <th className="p-2">Item</th>
              <th className="p-2 text-right">Requested</th>
              <th className="p-2 text-right">Sent</th>
              <th className="p-2 text-right">Received</th>
              <th className="p-2 text-right">Variance</th>
              <th className="p-2">Reason</th>
            </tr>
          </thead>
          <tbody>
            {transfer.items.map((line) => {
              const variance = num(line.received_quantity) - num(line.sent_quantity);
              return (
                <tr key={line.id} className="border-t border-gray-100">
                  <td className="p-2 font-medium">{line.item_name ?? line.item_sku ?? line.item_id}</td>
                  <td className="p-2 text-right">{formatQuantity(line.quantity)}</td>
                  <td className="p-2 text-right">{formatQuantity(line.sent_quantity)}</td>
                  <td className="p-2 text-right">{formatQuantity(line.received_quantity)}</td>
                  <td className={`p-2 text-right ${variance < 0 ? 'text-red-600' : variance > 0 ? 'text-amber-600' : 'text-gray-400'}`}>
                    {variance === 0 ? '—' : `${variance > 0 ? '+' : ''}${formatQuantity(variance)}`}
                  </td>
                  <td className="p-2 text-gray-600">{line.variance_reason ?? ''}</td>
                </tr>
              );
            })}
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
