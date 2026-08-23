'use client';

import { Badge } from '@/components/ui';
import { formatCurrency, formatDate } from '@/lib/utils';
import type { DeliveryBatch } from '@/lib/types';


// ── Runs ──────────────────────────────────────────────────────────────────────

const BATCH_VARIANT: Record<DeliveryBatch['status'], 'warning' | 'info' | 'success' | 'danger' | 'neutral'> = {
  pending: 'warning',
  dispatching: 'info',
  dispatched: 'success',
  failed: 'danger',
  cancelled: 'neutral',
};

/** What has gone out together, and what it saved. */
export function RunsTab({
  batches,
  busy,
  onDispatch,
}: {
  batches: DeliveryBatch[];
  busy: boolean;
  onDispatch: (id: string) => void;
}) {
  if (!batches.length) {
    return (
      <div className="bg-white border border-gray-200 p-4 text-xs font-body text-gray-500">
        No runs yet. One appears as soon as an order in a courier zone is packed
        inside a batch window.
      </div>
    );
  }

  return (
    <div className="bg-white border border-gray-200">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-100 bg-gray-50">
            <th className="px-4 py-2 text-left text-[11px] font-body uppercase tracking-widest text-gray-400">Run</th>
            <th className="px-4 py-2 text-left text-[11px] font-body uppercase tracking-widest text-gray-400">Leaves</th>
            <th className="px-4 py-2 text-center text-[11px] font-body uppercase tracking-widest text-gray-400">Drops</th>
            <th className="px-4 py-2 text-right text-[11px] font-body uppercase tracking-widest text-gray-400">Cost</th>
            <th className="px-4 py-2 text-right text-[11px] font-body uppercase tracking-widest text-gray-400">Each</th>
            <th className="px-4 py-2" />
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {batches.map(batch => (
            <tr key={batch.id}>
              <td className="px-4 py-2.5">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-body text-gray-800">
                    {batch.zone_name ?? '—'} · {batch.window_label ?? 'run'}
                  </span>
                  <Badge variant={BATCH_VARIANT[batch.status]}>{batch.status}</Badge>
                </div>
                {batch.order_numbers.length > 0 && (
                  <div className="text-[11px] font-body text-gray-400 mt-0.5">
                    {batch.order_numbers.join(', ')}
                  </div>
                )}
                {batch.last_error && (
                  <div className="text-[11px] font-body text-red-600 mt-0.5">
                    {batch.last_error}
                  </div>
                )}
                {/* Whether this is being handled or is waiting on a person is
                    the only thing worth knowing about a failed run, and it is
                    not something a status badge can say on its own. */}
                {batch.next_attempt_at ? (
                  <div className="text-[11px] font-body text-gray-400 mt-0.5">
                    Attempt {batch.attempt_count} · trying again {formatDate(batch.next_attempt_at)}
                  </div>
                ) : batch.status === 'failed' && batch.attempt_count > 1 ? (
                  <div className="text-[11px] font-body text-gray-400 mt-0.5">
                    Gave up after {batch.attempt_count} attempts
                  </div>
                ) : null}
              </td>
              <td className="px-4 py-2.5 text-xs font-body text-gray-600">
                {formatDate(batch.dispatch_at)}
              </td>
              <td className="px-4 py-2.5 text-center text-xs font-body text-gray-600">
                {batch.stop_count}
              </td>
              <td className="px-4 py-2.5 text-right text-xs font-body text-gray-600">
                {batch.cost_total !== null ? formatCurrency(batch.cost_total) : '—'}
              </td>
              <td className="px-4 py-2.5 text-right text-xs font-body text-gray-800">
                {batch.cost_per_delivery !== null
                  ? formatCurrency(batch.cost_per_delivery)
                  : '—'}
              </td>
              <td className="px-4 py-2.5 text-right whitespace-nowrap">
                {batch.share_link && (
                  <a
                    href={batch.share_link}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[11px] font-body text-primary hover:underline mr-3"
                  >
                    Track
                  </a>
                )}
                {/* A dispatched run with a retry pending is one whose second
                    courier order failed — part of it is on the road and part of
                    it is still in the kitchen, so the button has to stay. */}
                {batch.status !== 'cancelled' &&
                  (batch.status !== 'dispatched' || batch.next_attempt_at) && (
                    <button
                      onClick={() => onDispatch(batch.id)}
                      disabled={busy}
                      className="text-[11px] font-body text-primary hover:underline"
                    >
                      {batch.status === 'pending' ? 'Send now' : 'Retry'}
                    </button>
                  )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
