'use client';

import { useEffect, useMemo, useState } from 'react';
import type { Schemas } from '@mm/types';
import { inventoryApi } from '@/lib/pos-api';
import { formatCost, formatDate } from '@/lib/utils';

export type LineCostCheck = Schemas['PurchaseLineCostCheck'];
type Comparison = Schemas['PurchaseLineCostComparison'];

/** The fields of a draft PO line the comparison needs, with a stable key. */
export interface CostCheckedLine {
  uid: string;
  item_id: string;
  quantity: string;
  entered_total: string;
}

export interface LineCostChecks {
  /** The latest answer per line `uid` — kept while a newer one is in flight. */
  checks: Record<string, LineCostCheck>;
  loading: boolean;
  failed: boolean;
}

const DEBOUNCE_MS = 350;

/**
 * Ask the API to price every draft line that has an item, as the buyer types.
 *
 * The unit cost, the references and the verdicts all come back from the server
 * (CLAUDE.md §10): this only debounces, and drops an answer that a newer edit
 * has already overtaken.
 */
export function useLineCostChecks(lines: CostCheckedLine[]): LineCostChecks {
  // Only what the server reads, so typing in an unrelated field asks nothing.
  const signature = useMemo(
    () =>
      JSON.stringify(
        lines
          .filter((l) => l.item_id)
          .map((l) => ({
            uid: l.uid,
            item_id: l.item_id,
            // Half-typed or negative input prices as "nothing yet", which still
            // returns the item's references, rather than a 422 for the lot.
            quantity: Math.max(Number(l.quantity) || 0, 0),
            entered_total: Math.max(Number(l.entered_total) || 0, 0),
          })),
      ),
    [lines],
  );
  // The last answer and the signature it answered: loading is simply "the
  // lines have moved on since", so a cancelled request can never strand it.
  const [answer, setAnswer] = useState<{
    signature: string;
    checks: Record<string, LineCostCheck>;
    failed: boolean;
  }>({ signature: '[]', checks: {}, failed: false });

  useEffect(() => {
    const asked: (Schemas['PurchaseLineCostCheckLine'] & { uid: string })[] = JSON.parse(signature);
    if (asked.length === 0) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      inventoryApi
        .checkPurchaseLineCosts(
          asked.map((l) => ({ item_id: l.item_id, quantity: l.quantity, entered_total: l.entered_total })),
        )
        .then((rows) => {
          if (cancelled) return;
          const checks: Record<string, LineCostCheck> = {};
          asked.forEach((line, i) => {
            if (rows[i]) checks[line.uid] = rows[i];
          });
          setAnswer({ signature, checks, failed: false });
        })
        .catch(() => {
          // Keep the previous answer on screen, marked as not refreshed.
          if (!cancelled) setAnswer((prev) => ({ signature, checks: prev.checks, failed: true }));
        });
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [signature]);

  const loading = signature !== '[]' && answer.signature !== signature;
  return { checks: answer.checks, loading, failed: answer.failed && !loading };
}

const VERDICT_STYLE: Record<Comparison['verdict'], { className: string; icon: string; label: string }> = {
  better: { className: 'bg-emerald-50 text-emerald-700 border-emerald-200', icon: 'trending_down', label: 'better' },
  same: { className: 'bg-gray-50 text-gray-600 border-gray-200', icon: 'trending_flat', label: 'same' },
  worse: { className: 'bg-red-50 text-red-700 border-red-200', icon: 'trending_up', label: 'worse' },
};

function Verdict({ comparison, unit }: { comparison: Comparison; unit: string }) {
  const style = VERDICT_STYLE[comparison.verdict];
  const difference = Number(comparison.difference);
  // `change` is the server's fraction; this only renders it as a percentage.
  const pct = comparison.change == null ? null : `${(Math.abs(Number(comparison.change)) * 100).toFixed(1)}%`;
  const sign = difference > 0 ? '+' : difference < 0 ? '−' : '±';
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium ${style.className}`}
      title={`${sign}${formatCost(Math.abs(difference))} per ${unit} against ${formatCost(comparison.reference_cost)}`}
    >
      <span className="material-icons text-[14px]" aria-hidden>{style.icon}</span>
      {pct && comparison.verdict !== 'same' ? `${pct} ${style.label}` : style.label}
      {pct && comparison.verdict === 'same' && <span className="font-normal">({sign === '±' ? '' : sign}{pct})</span>}
    </span>
  );
}

function Reference({
  label,
  cost,
  unit,
  comparison,
  children,
}: {
  label: string;
  cost: string | null | undefined;
  unit: string;
  comparison: Comparison | null | undefined;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 rounded border border-gray-200 bg-white px-2.5 py-1.5">
      <span className="text-[10px] uppercase tracking-widest text-gray-400">{label}</span>
      <span className="tabular-nums text-gray-700">
        {formatCost(cost)}
        <span className="text-gray-400"> / {unit}</span>
      </span>
      {comparison && <Verdict comparison={comparison} unit={unit} />}
      {children}
    </div>
  );
}

/**
 * The row under a PO line: what this item cost the last time it arrived (with
 * that PO one click away, in a new tab) and what it averages on hand now — each
 * with a better / same / worse verdict once the line has a unit cost.
 */
export function LineCostComparison({
  check,
  unit,
  loading,
  failed,
}: {
  check: LineCostCheck | undefined;
  unit: string;
  loading: boolean;
  failed: boolean;
}) {
  if (!check) {
    return (
      <p className="text-xs text-gray-400 font-body">
        {failed
          ? 'Couldn’t load past prices for this item — they’ll retry when you edit the line.'
          : loading
            ? 'Checking past prices…'
            : null}
      </p>
    );
  }
  const last = check.last_purchase;
  return (
    <div className={`flex flex-wrap items-stretch gap-2 text-xs font-body transition-opacity ${loading ? 'opacity-60' : ''}`}>
      {last ? (
        <Reference label="Last purchase" cost={last.unit_cost} unit={unit} comparison={check.vs_last_purchase}>
          <a
            href={`/purchase-orders/${last.purchase_order_id}`}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-0.5 font-medium text-primary hover:underline"
            title="Open this purchase order in a new tab"
          >
            {last.reference}
            <span className="material-icons text-[13px]" aria-hidden>open_in_new</span>
          </a>
          <span className="text-gray-400">
            {[last.purchased_on ? formatDate(last.purchased_on) : null, last.supplier_name, last.branch_name]
              .filter(Boolean)
              .join(' · ')}
          </span>
        </Reference>
      ) : (
        <div className="rounded border border-dashed border-gray-200 px-2.5 py-1.5 text-gray-400">
          No earlier purchase of this item
        </div>
      )}
      {check.average_cost != null ? (
        <Reference
          label="Avg cost now"
          cost={check.average_cost}
          unit={unit}
          comparison={check.vs_average_cost}
        />
      ) : (
        <div className="rounded border border-dashed border-gray-200 px-2.5 py-1.5 text-gray-400">
          No stock on hand to average
        </div>
      )}
      {check.unit_cost == null && (
        <span className="self-center text-gray-400">Enter a quantity and total to compare.</span>
      )}
      {failed && (
        <span className="self-center text-amber-600">Couldn’t refresh — showing the last answer.</span>
      )}
    </div>
  );
}
