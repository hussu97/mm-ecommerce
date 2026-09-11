'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { branchesApi, inventoryApi, type TransferOrder } from '@/lib/pos-api';
import type { Branch } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button, Spinner } from '@/components/ui';
import { useConfirm, useToast } from '@/components/ui/feedback';
import { formatDateTime, formatQuantity } from '@/lib/utils';

const num = (v: unknown): number => Number(v ?? 0);

type TransferLine = TransferOrder['items'][number];

const lineItemName = (line: TransferLine): string => line.item_name ?? line.item_sku ?? line.item_id;
const lineCategory = (line: TransferLine): string => (line.category_name ? String(line.category_name) : 'Uncategorised');

/** Group lines by category — category order (min per bucket) → category name →
 * item name, Uncategorised last. Mirrors the shift-report detail page. */
function groupByCategory(lines: TransferLine[]): { name: string; lines: TransferLine[] }[] {
  const map = new Map<string, { order: number; lines: TransferLine[] }>();
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

/** Where the transfer is, in words, from its two legs. */
function receivingStatus(t: TransferOrder): { label: string; variant: 'success' | 'warning' | 'neutral' | 'danger' } {
  if (t.received_transaction_id) return { label: 'Received', variant: 'success' };
  if (t.sent_transaction_id) return { label: 'Sent — awaiting receipt', variant: 'warning' };
  if (t.status === 'declined') return { label: 'Declined', variant: 'danger' };
  return { label: t.status.replaceAll('_', ' '), variant: 'neutral' };
}

export default function TransferDetailPage() {
  const { id } = useParams<{ id: string }>();
  const toast = useToast();
  const confirm = useConfirm();
  const [transfer, setTransfer] = useState<TransferOrder | null>(null);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Receive-mode state, keyed by transfer_order_item_id (line.id).
  const [received, setReceived] = useState<Record<string, string>>({});
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [banner, setBanner] = useState<{ text: string; error: boolean } | null>(null);

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

  const awaitingReceipt = !!(transfer && transfer.sent_transaction_id && !transfer.received_transaction_id);

  // Seed each line's Received with its Sent quantity when the transfer becomes
  // receivable, so a clean receipt is one tap on Receive.
  useEffect(() => {
    if (!transfer || !awaitingReceipt) return;
    setReceived(Object.fromEntries(transfer.items.map((line) => [line.id, formatQuantity(line.sent_quantity)])));
    setReasons(Object.fromEntries(transfer.items.map((line) => [line.id, ''])));
  }, [transfer, awaitingReceipt]);

  const lineDiffers = useCallback((line: TransferLine): boolean => {
    const raw = received[line.id];
    if (raw === undefined || raw.trim() === '') return true;
    return num(raw) !== num(line.sent_quantity);
  }, [received]);

  const canSubmit = useMemo(() => {
    if (!transfer || !awaitingReceipt) return false;
    return transfer.items.every((line) => {
      const raw = received[line.id];
      if (raw === undefined || raw.trim() === '' || Number.isNaN(Number(raw))) return false;
      if (num(raw) !== num(line.sent_quantity) && !(reasons[line.id] ?? '').trim()) return false;
      return true;
    });
  }, [transfer, awaitingReceipt, received, reasons]);

  const receive = async () => {
    if (!transfer || !canSubmit) return;
    setSubmitting(true);
    setBanner(null);
    try {
      const lines = transfer.items.map((line) => ({
        transfer_order_item_id: line.id,
        quantity: Number(received[line.id]),
        reason: (reasons[line.id] ?? '').trim() || null,
      }));
      const updated = await inventoryApi.receiveTransferOrder(transfer.id, lines);
      setTransfer(updated);
      setBanner({ text: 'Received. The transfer is now closed.', error: false });
      toast.success('Transfer received.');
    } catch (err) {
      setBanner({ text: err instanceof ApiError ? err.message : 'Could not receive the transfer.', error: true });
    } finally {
      setSubmitting(false);
    }
  };

  const forceReceive = async () => {
    if (!transfer) return;
    if (!(await confirm({
      title: 'Force receive',
      message: 'Receive every line as sent? This closes the transfer.',
      confirmLabel: 'Force receive',
    }))) return;
    setSubmitting(true);
    setBanner(null);
    try {
      const updated = await inventoryApi.receiveTransferOrder(transfer.id, []);
      setTransfer(updated);
      setBanner({ text: 'Received every line as sent. The transfer is now closed.', error: false });
      toast.success('Transfer received.');
    } catch (err) {
      setBanner({ text: err instanceof ApiError ? err.message : 'Could not receive the transfer.', error: true });
    } finally {
      setSubmitting(false);
    }
  };

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
  const groups = groupByCategory(transfer.items);

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

      {banner && (
        <p className={`border p-3 text-sm ${banner.error ? 'border-red-200 bg-red-50 text-red-800' : 'border-green-200 bg-green-50 text-green-800'}`}>{banner.text}</p>
      )}

      {hasVariance && !awaitingReceipt && (
        <p className="bg-amber-50 border border-amber-200 p-3 text-sm text-amber-800">
          Some lines were received short or over what was sent — see the Variance column and reasons below.
        </p>
      )}

      {awaitingReceipt && (
        <p className="text-sm text-gray-600">
          Check each line against what arrived. A short or extra count needs a reason, and stays on the record.
        </p>
      )}

      <div className="overflow-x-auto border border-gray-200">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs uppercase tracking-wider text-gray-500">
            <tr>
              <th className="px-2 py-1">Item</th>
              <th className="px-2 py-1 text-right">Requested</th>
              <th className="px-2 py-1 text-right">Sent</th>
              <th className="px-2 py-1 text-right">Received</th>
              <th className="px-2 py-1 text-right">Variance</th>
              <th className="px-2 py-1">Reason</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => (
              <GroupRows key={group.name} name={group.name} span={6}>
                {group.lines.map((line) => {
                  const receivedQty = awaitingReceipt ? num(received[line.id]) : num(line.received_quantity);
                  const variance = receivedQty - num(line.sent_quantity);
                  const differs = awaitingReceipt && lineDiffers(line);
                  return (
                    <tr key={line.id} className="border-t border-gray-100">
                      <td className="px-2 py-1 font-medium">{lineItemName(line)}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{formatQuantity(line.quantity)}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{formatQuantity(line.sent_quantity)}</td>
                      <td className="px-2 py-1 text-right tabular-nums">
                        {awaitingReceipt ? (
                          <input
                            inputMode="decimal"
                            value={received[line.id] ?? ''}
                            onChange={(e) => setReceived((prev) => ({ ...prev, [line.id]: e.target.value }))}
                            className="w-20 border border-gray-300 px-1 py-0.5 text-right"
                          />
                        ) : (
                          formatQuantity(line.received_quantity)
                        )}
                      </td>
                      <td className={`px-2 py-1 text-right tabular-nums ${variance < 0 ? 'text-red-600' : variance > 0 ? 'text-amber-600' : 'text-gray-400'}`}>
                        {variance === 0 ? '—' : `${variance > 0 ? '+' : ''}${formatQuantity(variance)}`}
                      </td>
                      <td className="px-2 py-1 text-gray-600">
                        {awaitingReceipt ? (
                          differs ? (
                            <input
                              value={reasons[line.id] ?? ''}
                              onChange={(e) => setReasons((prev) => ({ ...prev, [line.id]: e.target.value }))}
                              className="w-48 border border-gray-300 px-1 py-0.5"
                              placeholder="Reason (short / extra / damaged)"
                            />
                          ) : null
                        ) : (
                          line.variance_reason ?? ''
                        )}
                      </td>
                    </tr>
                  );
                })}
              </GroupRows>
            ))}
          </tbody>
        </table>
      </div>

      {awaitingReceipt && (
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => void receive()} loading={submitting} disabled={!canSubmit}>
            {submitting ? 'Receiving…' : 'Receive'}
          </Button>
          <Button variant="outline" onClick={() => void forceReceive()} disabled={submitting}>Force receive</Button>
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
