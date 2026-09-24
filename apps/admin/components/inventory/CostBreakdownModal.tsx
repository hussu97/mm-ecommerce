'use client';

// What an item's stock is worth, and how it got there — FIFO costing v3.
//
// "Current stock" is the FIFO layers that make up what is on the shelf now,
// oldest (next to be used) first; they always add up to the stock on hand, and
// the popup says so loudly on the day they do not. "Costing history" is every
// movement at one branch with what it is worth *now* beside what it was booked
// at — a count found before its PO arrived, or a sale that ran ahead of its
// delivery, is re-costed when the price lands. Every figure is quoted by the
// API; nothing here re-derives a cost.

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import type { Schemas } from '@mm/types';

import { Badge, Pagination, Select, Spinner, TabBar } from '@/components/ui';
import { Modal } from '@/components/pos/ResourcePage';
import { ApiError } from '@/lib/api';
import { inventoryApi } from '@/lib/pos-api';
import type { Branch, InventoryItem } from '@/lib/pos-types';
import { formatCost, formatQuantity } from '@/lib/utils';

type CostLayers = Schemas['ItemCostLayersResponse'];
type CostHistory = Schemas['ItemCostHistoryResponse'];

const MOVEMENT_LABELS: Record<string, string> = {
  purchasing: 'Purchase',
  transfer_send: 'Transfer out',
  transfer_receive: 'Transfer in',
  quantity_adjustment: 'Adjustment',
  return_to_supplier: 'Return to supplier',
  production: 'Produced',
  consumption_from_production: 'Used in production',
  consumption_from_orders: 'Sold',
  return_from_orders: 'Returned by customer',
  waste_from_orders: 'Order waste',
  waste_from_production: 'Production waste',
  cost_adjustment: 'Cost adjustment',
  inventory_count: 'Count',
  opening_balance: 'Opening balance',
  internal_use: 'Internal use',
  extra_production_use: 'Extra production use',
};

function EstimateBadge() {
  return (
    <span title="Estimate — this stock is waiting on its next priced receipt, which will re-cost it.">
      <Badge variant="warning">est.</Badge>
    </span>
  );
}

/** A purchase order reference that opens the PO's detail page in a new tab, so
 *  the popup (and the costing it explains) stays open behind it. */
function PurchaseOrderLink({ id, reference }: { id: string; reference: string }) {
  return (
    <Link
      href={`/purchase-orders/${id}`}
      target="_blank"
      rel="noopener noreferrer"
      title={`Open ${reference}`}
      className="text-primary underline decoration-dotted underline-offset-2 hover:decoration-solid"
    >
      {reference}
    </Link>
  );
}

// The branch the popup opens on, remembered per device. '' means "All branches".
const BRANCH_STORAGE_KEY = 'mm-admin-cost-breakdown-branch';

function readSavedBranch(): string | null {
  try {
    return localStorage.getItem(BRANCH_STORAGE_KEY);
  } catch {
    return null;
  }
}

function saveBranch(branchId: string) {
  try {
    localStorage.setItem(BRANCH_STORAGE_KEY, branchId);
  } catch {
    // Private mode or blocked storage: the choice just isn't remembered.
  }
}

/** The last branch picked on this device, if it is still active; else the
 *  Sharjah kitchen (where the stock is made and costed); else the first. */
export function defaultBranchId(branches: Branch[]): string {
  const saved = readSavedBranch();
  if (saved === '' || (saved && branches.some((branch) => branch.id === saved))) return saved;
  const kitchen =
    branches.find((branch) => /sharjah/i.test(branch.name) && branch.type === 'kitchen') ??
    branches.find((branch) => /sharjah/i.test(branch.name)) ??
    branches.find((branch) => branch.type === 'kitchen');
  return (kitchen ?? branches[0])?.id ?? '';
}

export function CostBreakdownModal({
  item,
  branches,
  onClose,
}: {
  item: InventoryItem;
  branches: Branch[];
  onClose: () => void;
}) {
  // `branches` is the active list, so a remembered branch that has since been
  // deactivated falls through to the default. Resolved on render (not stored
  // on mount) so it is right even if the branch list arrives after opening.
  const [chosenBranchId, setChosenBranchId] = useState<string | null>(null);
  const branchId = chosenBranchId ?? defaultBranchId(branches);
  const setBranchId = (next: string) => {
    setChosenBranchId(next);
    saveBranch(next);
  };
  const [tab, setTab] = useState<'stock' | 'history'>('stock');
  const branchName = useMemo(
    () => new Map(branches.map((branch) => [branch.id, branch.name])),
    [branches],
  );

  return (
    <Modal title={`Cost breakdown — ${item.name}`} onClose={onClose} wide>
      <div className="mb-3 flex flex-wrap items-end gap-3">
        <div className="w-56">
          <Select
            label="Branch"
            value={branchId}
            onChange={(event) => setBranchId(event.target.value)}
            options={[
              { value: '', label: 'All branches' },
              ...branches.map((branch) => ({ value: branch.id, label: branch.name })),
            ]}
          />
        </div>
        <div className="flex-1">
          <TabBar
            tabs={[
              { key: 'stock', label: 'Current stock' },
              { key: 'history', label: 'Costing history' },
            ]}
            active={tab}
            onChange={(key) => setTab(key as 'stock' | 'history')}
          />
        </div>
      </div>
      {tab === 'stock' ? (
        <StockTab item={item} branchId={branchId} showWarehouse={!branchId} />
      ) : branchId ? (
        <HistoryTab
          key={branchId}
          item={item}
          branchId={branchId}
          branchLabel={branchName.get(branchId) ?? ''}
        />
      ) : (
        <p className="py-8 text-center text-sm text-gray-400 font-body">
          Pick a branch to see its costing history — each branch values its own stock.
        </p>
      )}
    </Modal>
  );
}

function StockTab({
  item,
  branchId,
  showWarehouse,
}: {
  item: InventoryItem;
  branchId: string;
  showWarehouse: boolean;
}) {
  // Keyed by what was asked for, so switching branch shows the spinner again
  // without resetting state inside the effect.
  const requestKey = `${item.id}:${branchId}`;
  const [result, setResult] = useState<{ key: string; data?: CostLayers; error?: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    inventoryApi
      .itemCostLayers(item.id, branchId || undefined)
      .then((data) => { if (!cancelled) setResult({ key: requestKey, data }); })
      .catch((err) => {
        if (!cancelled) {
          setResult({
            key: requestKey,
            error: err instanceof ApiError ? err.message : 'Failed to load cost layers.',
          });
        }
      });
    return () => { cancelled = true; };
  }, [item.id, branchId, requestKey]);

  const current = result?.key === requestKey ? result : null;
  if (current?.error) return <p className="text-xs text-red-600 font-body">{current.error}</p>;
  const data = current?.data;
  if (!data) return <div className="flex justify-center py-10"><Spinner /></div>;

  const layers = data.layers ?? [];
  const anyEstimate = layers.some((layer) => layer.cost_is_provisional);
  return (
    <>
      <p className="mb-3 text-xs text-gray-500 font-body">
        The stock on the shelf, layer by layer, oldest first — the order the next issue uses it.
        Each layer keeps the cost it came in at; the average is what these layers imply.
      </p>
      {!data.layers_match_stock && (
        <p className="mb-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 font-body">
          These layers cover {formatQuantity(data.total_quantity)} {item.storage_unit} but the
          ledger says {formatQuantity(data.on_hand_quantity)} {item.storage_unit} are on hand. The
          costing projection is out of date — a projection rebuild will restate it.
        </p>
      )}
      <div className="mb-3 flex gap-6 text-sm">
        <div><span className="text-gray-500 font-body">On hand</span><br /><span className="font-display text-primary">{formatQuantity(data.on_hand_quantity)} {item.storage_unit}</span></div>
        <div><span className="text-gray-500 font-body">Value</span><br /><span className="font-display text-primary">{formatCost(data.total_value)}</span></div>
        <div><span className="text-gray-500 font-body">Avg cost</span><br /><span className="font-display text-primary">{formatCost(data.average_cost)}</span></div>
      </div>
      {layers.length === 0 ? (
        <p className="py-8 text-center text-sm text-gray-400 font-body">
          Nothing on the shelf here.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-[11px] uppercase tracking-widest text-gray-500 font-body">
                <th className="py-2 text-left">Source</th>
                <th className="py-2 text-left">Posted</th>
                {showWarehouse && <th className="py-2 text-left">Warehouse</th>}
                <th className="py-2 text-right">Qty</th>
                <th className="py-2 text-center"> </th>
                <th className="py-2 text-right">Unit cost</th>
                <th className="py-2 text-right">= Value</th>
              </tr>
            </thead>
            <tbody>
              {layers.map((layer) => (
                <tr key={layer.id} className="border-b border-gray-100 align-top">
                  <td className="py-2">
                    <span className="font-medium text-gray-800">
                      {layer.purchase_order_id && layer.source_reference ? (
                        <PurchaseOrderLink id={layer.purchase_order_id} reference={layer.source_reference} />
                      ) : (
                        layer.source_reference ?? '—'
                      )}
                    </span>
                    {layer.next_out && <span className="ml-2"><Badge variant="info">next out</Badge></span>}
                    <br />
                    <span className="text-[10px] uppercase tracking-wide text-gray-400 font-body">
                      {layer.source_kind.replaceAll('_', ' ')}
                    </span>
                  </td>
                  <td className="py-2 text-gray-500">{(layer.posted_at ?? layer.received_at).slice(0, 10)}</td>
                  {showWarehouse && <td className="py-2 text-gray-600">{layer.warehouse_name ?? '—'}</td>}
                  <td className="py-2 text-right tabular-nums">{formatQuantity(layer.remaining_quantity)}</td>
                  <td className="py-2 text-center text-gray-400">×</td>
                  <td className="py-2 text-right tabular-nums">
                    {formatCost(layer.unit_cost)} {layer.cost_is_provisional && <EstimateBadge />}
                    {layer.cost_source_reference && (
                      <span className="block text-[10px] text-gray-400 font-body">
                        priced from{' '}
                        {layer.cost_source_purchase_order_id ? (
                          <PurchaseOrderLink
                            id={layer.cost_source_purchase_order_id}
                            reference={layer.cost_source_reference}
                          />
                        ) : (
                          layer.cost_source_reference
                        )}
                      </span>
                    )}
                  </td>
                  <td className="py-2 text-right tabular-nums">{formatCost(layer.line_value)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t-2 border-gray-300 font-medium">
                <td className="py-2" colSpan={showWarehouse ? 3 : 2}>Total on hand</td>
                <td className="py-2 text-right tabular-nums">{formatQuantity(data.total_quantity)}</td>
                <td />
                <td />
                <td className="py-2 text-right tabular-nums">{formatCost(data.total_value)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}
      {layers.length > 0 && (
        <p className="mt-3 text-xs text-gray-500 font-body">
          Average cost = total value ÷ on-hand qty = {formatCost(data.total_value)} ÷{' '}
          {formatQuantity(data.total_quantity)} {item.storage_unit} ={' '}
          <span className="font-medium text-gray-800">{formatCost(data.average_cost)}</span> per {item.storage_unit}
        </p>
      )}
      {anyEstimate && (
        <p className="mt-2 text-xs text-amber-700 font-body">
          <EstimateBadge /> Stock found by a count, or received with no purchase order, has no price
          of its own yet. It is valued at the last known cost here (or its recipe, or the latest PO)
          until the next priced receipt — which then re-costs it and everything already used from it.
        </p>
      )}
    </>
  );
}

function HistoryTab({
  item,
  branchId,
  branchLabel,
}: {
  item: InventoryItem;
  branchId: string;
  branchLabel: string;
}) {
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);
  const requestKey = `${item.id}:${branchId}:${page}:${perPage}`;
  const [result, setResult] = useState<{ key: string; data?: CostHistory; error?: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    inventoryApi
      .itemCostHistory(item.id, branchId, page, perPage)
      .then((data) => { if (!cancelled) setResult({ key: requestKey, data }); })
      .catch((err) => {
        if (!cancelled) {
          setResult({
            key: requestKey,
            error: err instanceof ApiError ? err.message : 'Failed to load the costing history.',
          });
        }
      });
    return () => { cancelled = true; };
  }, [item.id, branchId, page, perPage, requestKey]);

  const current = result?.key === requestKey ? result : null;
  if (current?.error) return <p className="text-xs text-red-600 font-body">{current.error}</p>;
  const data = current?.data;
  if (!data) return <div className="flex justify-center py-10"><Spinner /></div>;

  return (
    <>
      <p className="mb-3 text-xs text-gray-500 font-body">
        Every movement of {item.name} at {branchLabel}, newest first. The cost is what each is worth
        now; when a later price re-costed it, the figure it was booked at is shown beneath.
      </p>
      {data.items.length === 0 ? (
        <p className="py-8 text-center text-sm text-gray-400 font-body">No movements here yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-[11px] uppercase tracking-widest text-gray-500 font-body">
                <th className="py-2 text-left">Date</th>
                <th className="py-2 text-left">Reference</th>
                <th className="py-2 text-right">In / out</th>
                <th className="py-2 text-right">Unit cost</th>
                <th className="py-2 text-right">Value</th>
                <th className="py-2 text-right">Balance</th>
                <th className="py-2 text-right">Stock value</th>
                <th className="py-2 text-right">Avg cost</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((row) => {
                const qty = Number(row.quantity);
                return (
                  <tr key={row.line_id} className="border-b border-gray-100 align-top">
                    <td className="py-2 text-gray-500 whitespace-nowrap">{(row.posted_at ?? row.business_date ?? '').slice(0, 10)}</td>
                    <td className="py-2">
                      <span className="font-medium text-gray-800">
                        {row.purchase_order_id && row.purchase_order_reference ? (
                          <PurchaseOrderLink id={row.purchase_order_id} reference={row.purchase_order_reference} />
                        ) : (
                          row.reference
                        )}
                      </span>
                      <br />
                      <span className="text-[10px] uppercase tracking-wide text-gray-400 font-body">
                        {MOVEMENT_LABELS[row.type] ?? row.type.replaceAll('_', ' ')}
                        {row.purchase_order_id && row.purchase_order_reference && ` · ${row.reference}`}
                      </span>
                    </td>
                    <td className={`py-2 text-right tabular-nums ${qty < 0 ? 'text-red-700' : qty > 0 ? 'text-emerald-700' : 'text-gray-400'}`}>
                      {qty > 0 ? '+' : ''}{formatQuantity(row.quantity)}
                    </td>
                    <td className="py-2 text-right tabular-nums">
                      {row.superseded ? (
                        <span className="text-gray-400" title="A pre-v3 cost adjustment — superseded by the FIFO replay.">superseded</span>
                      ) : (
                        <>
                          {formatCost(row.unit_cost)} {row.is_provisional && <EstimateBadge />}
                          {row.cost_source_reference && (
                            <span className="block text-[10px] text-gray-400 font-body">
                              priced from{' '}
                              {row.cost_source_purchase_order_id ? (
                                <PurchaseOrderLink
                                  id={row.cost_source_purchase_order_id}
                                  reference={row.cost_source_reference}
                                />
                              ) : (
                                row.cost_source_reference
                              )}
                            </span>
                          )}
                        </>
                      )}
                    </td>
                    <td className="py-2 text-right tabular-nums">
                      {row.superseded ? '—' : formatCost(row.total_cost)}
                      {row.booked_total_cost != null && !row.superseded && (
                        <span className="block text-[10px] text-gray-400 font-body">booked {formatCost(row.booked_total_cost)}</span>
                      )}
                    </td>
                    <td className="py-2 text-right tabular-nums">{formatQuantity(row.running_quantity)}</td>
                    <td className="py-2 text-right tabular-nums">{formatCost(row.running_value)}</td>
                    <td className="py-2 text-right tabular-nums">{row.running_average_cost != null ? formatCost(row.running_average_cost) : '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="mt-3">
        <Pagination
          page={data.page}
          pages={data.pages}
          total={data.total}
          perPage={perPage}
          onPageChange={setPage}
          onPerPageChange={(value) => { setPerPage(value); setPage(1); }}
          label="movements"
        />
      </div>
    </>
  );
}
