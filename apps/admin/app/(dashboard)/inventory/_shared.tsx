'use client';

// Helpers, constants and the couple of components shared across the inventory
// sub-routes. Extracted from the old single-file `inventory/page.tsx` so each
// tab can live in its own route without copying the ledger machinery or the
// report-template guidance table into three places.

import { useEffect, useState } from 'react';
import Link from 'next/link';

import {
  branchesApi,
  inventoryApi,
  type ShiftInventoryReport,
  type Transfer,
} from '@/lib/pos-api';
import type { Branch, InventoryItem, InventoryTransaction } from '@/lib/pos-types';
import { Button, Input, Pagination, Select } from '@/components/ui';
import { DataTable } from '@/components/ui/DataTable';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { formatCurrency, formatQuantity } from '@/lib/utils';

// ─── Branch selector ──────────────────────────────────────────────────────────

export function BranchFilter({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const [branches, setBranches] = useState<Branch[]>([]);
  useEffect(() => { void branchesApi.list().then(setBranches); }, []);
  return <Select label="Branch" value={value} onChange={(e) => onChange(e.target.value)} placeholder="Choose branch" className="w-64" options={branches.map((b) => ({ value: b.id, label: b.name }))} />;
}

// ─── Report-template guidance ─────────────────────────────────────────────────

export type ReportTemplateKind = 'production' | 'finished_goods' | 'raw_materials' | 'packaging' | 'spot_check';

export const REPORT_TEMPLATE_GUIDANCE: Record<ReportTemplateKind, {
  defaultName: string;
  cadence: 'per_till' | 'per_business_day' | 'ad_hoc';
  required: boolean;
  kinds: InventoryItem['kind'][];
  requiredInput: 'physical_count';
  staffInstruction: string;
}> = {
  production: {
    defaultName: 'Production output',
    cadence: 'per_business_day',
    required: true,
    kinds: ['semi_finished', 'produced_good'],
    requiredInput: 'physical_count',
    staffInstruction: 'Enter finished units actually produced. The ledger consumes the captured item recipe and adds the finished stock.',
  },
  finished_goods: {
    defaultName: 'Finished goods closing count',
    // At till close (per_till) — the register prompts the cashier to count
    // finished goods when they close the till.
    cadence: 'per_till',
    required: true,
    kinds: ['semi_finished', 'produced_good'],
    requiredInput: 'physical_count',
    staffInstruction: 'Count each finished item at close. Opening, production, transfers and sales are calculated from the ledger; only the physical count and any variance reason are entered.',
  },
  raw_materials: {
    defaultName: 'Raw materials closing count',
    cadence: 'per_business_day',
    required: true,
    kinds: ['raw_material'],
    requiredInput: 'physical_count',
    staffInstruction: 'Count the actual raw material balance after production. Record receipts, internal use and waste as their own movements instead of typing a manual consumption total.',
  },
  packaging: {
    defaultName: 'Packaging & retail goods closing count',
    cadence: 'per_business_day',
    required: true,
    // Retail resale goods (drinks, the gift note card, boxed sets) reconcile
    // exactly like packaging — opening + received − sold = closing — and the
    // packaging column contract already carries both `received` and `sold`, so
    // they ride this report rather than needing a fourth kind. Their sold column
    // fills from CONSUMPTION_FROM_ORDERS once each retail product's recipe
    // consumes its inventory item.
    kinds: ['packaging', 'resale_good'],
    requiredInput: 'physical_count',
    staffInstruction: 'Count packaging (bags, boxes) and retail resale goods (drinks, cards, boxed sets) in their storage order. Expected use comes from the sales recipes, receipts and transfers already in the ledger.',
  },
  spot_check: {
    defaultName: 'Inventory spot check',
    cadence: 'ad_hoc',
    required: false,
    kinds: [],
    requiredInput: 'physical_count',
    staffInstruction: 'Use a small, ad-hoc physical check for an audit or investigation. It does not replace the daily closing templates.',
  },
};

// The order the register fills the reports in at close, low first. Production &
// finished goods must post before raw materials so the raw-material consumption
// appears — the order is load-bearing, not cosmetic.
export const REPORT_FILL_ORDER: Record<ReportTemplateKind, number> = {
  production: 1,
  finished_goods: 1,
  raw_materials: 2,
  packaging: 3,
  spot_check: 9,
};

// ─── Ledger labels ────────────────────────────────────────────────────────────

// Plain-English names for the raw movement types, so the ledger reads the way the
// shift report and the shop talk — "Sold", "Transfer out" — not `consumption_from_orders`.
export const MOVEMENT_LABELS: Record<string, string> = {
  purchasing: 'Received (purchase)',
  transfer_send: 'Transfer out',
  transfer_receive: 'Transfer in',
  return_from_transfers: 'Transfer returned',
  quantity_adjustment: 'Manual adjustment',
  return_to_supplier: 'Returned to supplier',
  production: 'Produced',
  consumption_from_production: 'Used in production',
  consumption_from_orders: 'Sold',
  return_from_orders: 'Customer return (restock)',
  waste_from_orders: 'Waste',
  waste_from_production: 'Production waste',
  cost_adjustment: 'Revaluation (cost)',
  inventory_count: 'Stock count',
  opening_balance: 'Opening balance',
  internal_use: 'Internal use',
  extra_production_use: 'Extra production use',
};

export const movementLabel = (type: string): string =>
  MOVEMENT_LABELS[type] ?? type.replaceAll('_', ' ');

// Where a movement came from, in words. The human reference (order#, transfer#)
// is resolved server-side into `source_reference` when present — for a shortfall
// top-up the backend hands us "Shortfall top-up · REF", so that reads through
// here as-is; otherwise this names the kind of source, and a reversal is called
// out.
export function sourceLabel(row: InventoryTransaction): string {
  if (row.reverses_transaction_id) return 'Correction / reversal';
  if (row.source_reference) return row.source_reference;
  if (!row.source_type) return 'Manual';
  const kinds: Record<string, string> = {
    order: 'Customer order',
    order_return: 'Customer return',
    transfer_order: 'Transfer / return',
    // The mini stock-adjustment a transfer order posts when the admin overrode
    // on-hand — its link points back at the parent order.
    transfer_shortfall_adjustment: 'Transfer shortfall top-up',
    production: 'Production',
    production_yield: 'Production yield',
    shift_inventory_report: 'Shift report',
    bulk_stock_audit: 'Stock audit',
    correction: 'Correction',
  };
  return kinds[row.source_type] ?? row.source_type.replaceAll('_', ' ');
}

// ─── Ledger ───────────────────────────────────────────────────────────────────
//
// Shared because it appears twice: as the standalone Ledger tab and, in
// `countOnly` mode, as the posted-count history under the Counts tab.

export function LedgerTab({ countOnly = false }: { countOnly?: boolean }) {
  const [branchId, setBranchId] = useState('');
  const [businessDate, setBusinessDate] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const search = useDebouncedValue(searchInput.trim(), 300);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(100);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [rows, setRows] = useState<InventoryTransaction[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    void branchesApi.list().then(setBranches).catch(() => setBranches([]));
  }, []);
  // Any filter or page-size change starts the paging over.
  useEffect(() => {
    setPage(1);
  }, [branchId, businessDate, search, perPage, countOnly]);
  useEffect(() => {
    // Server-side paging AND search now (F-ADM-2): the ledger is an unbounded
    // immutable log, so it used to load the first 100, search over just those
    // and print "N movements — every signed stock movement" — a lie past 100.
    // Ask for one more than the page to learn whether a next page exists without
    // a COUNT on every keystroke.
    setLoading(true);
    setError(null);
    void inventoryApi
      .transactions({
        branch_id: branchId || undefined,
        business_date: businessDate || undefined,
        type: countOnly ? 'inventory_count' : undefined,
        search: search || undefined,
        limit: perPage + 1,
        offset: (page - 1) * perPage,
      })
      .then((data) => {
        setHasMore(data.length > perPage);
        setRows(data.slice(0, perPage));
      })
      .catch(() => {
        setRows([]);
        setHasMore(false);
        setError('Could not load the ledger. Try again in a moment.');
      })
      .finally(() => setLoading(false));
  }, [branchId, businessDate, countOnly, search, page, perPage]);
  const branchName = (id: string) => branches.find((b) => b.id === id)?.name ?? '—';
  const start = rows.length === 0 ? 0 : (page - 1) * perPage + 1;
  const end = (page - 1) * perPage + rows.length;
  const rangeLabel = countOnly
    ? 'Physical counts post only the variance; levels are never edited directly.'
    : rows.length === 0
      ? 'No movements match.'
      : `Movements ${start}–${end}${hasMore ? ' (more on the next page)' : ''}, in immutable posting order with source and running balance.`;
  return <div className="max-w-[1500px] space-y-4"><div className="flex flex-wrap items-end gap-3"><BranchFilter value={branchId} onChange={setBranchId} /><Input label="Business date" type="date" value={businessDate} onChange={(e) => setBusinessDate(e.target.value)} className="w-44" /><Input label="Search reference or item" value={searchInput} onChange={(e) => setSearchInput(e.target.value)} placeholder="e.g. flour, CFO-000459" className="w-72" />{(branchId || businessDate || searchInput) && <Button variant="outline" size="sm" className="mb-1" onClick={() => { setBranchId(''); setBusinessDate(''); setSearchInput(''); }}>Clear</Button>}</div><p className="text-sm text-gray-500">{loading ? 'Loading…' : error ? error : rangeLabel}</p><DataTable rows={rows} rowKey={(row) => row.id} columns={[
    { header: 'Seq', render: (row) => row.posting_sequence ?? 'Draft' },
    { header: 'Branch', render: (row) => branchName(row.branch_id) },
    { header: 'Reference', priority: 'primary', render: (row) => row.reference },
    { header: 'Type', render: (row) => movementLabel(row.type) },
    // The source deep-links to the order / report / transfer that drove the
    // movement when the server resolved a path for it (F2/F3); otherwise it is
    // the plain resolved reference or kind label.
    { header: 'Source', render: (row) => row.source_link
      ? <Link href={row.source_link} className="text-primary hover:underline">{sourceLabel(row)}</Link>
      : sourceLabel(row) },
    { header: 'Movements', render: (row) => {
      // A revaluation moves value, not quantity: show the money, or the row reads
      // as "+0" with no visible number.
      if (row.type === 'cost_adjustment') {
        return <span className="text-xs text-gray-600">Value {Number(row.total_cost) >= 0 ? '+' : ''}{formatCurrency(row.total_cost)}</span>;
      }
      return <div className="space-y-1">{row.items.map((line) => {
        const isCount = row.type === 'inventory_count' || row.type === 'opening_balance';
        return <div key={line.id} className="text-xs"><span className={Number(line.signed_quantity) < 0 ? 'text-red-600' : 'text-green-700'}>{Number(line.signed_quantity) > 0 ? '+' : ''}{formatQuantity(line.signed_quantity ?? line.quantity)}</span> {line.item_name} <span className="text-gray-400">→ {formatQuantity(line.balance_after_quantity)}{isCount ? ' counted' : ''}</span></div>;
      })}</div>;
    } },
    { header: 'Posted', render: (row) => row.posted_at ? new Date(row.posted_at).toLocaleString() : '—' },
  ]} />{!loading && !error && (rows.length > 0 || page > 1) && <Pagination page={page} pages={hasMore ? page + 1 : page} total={end} perPage={perPage} onPageChange={setPage} onPerPageChange={setPerPage} label="movements" />}
    {!countOnly && <p className="text-xs text-gray-400 leading-relaxed">How to read this: the ledger is the source of truth and “On hand” is a running projection of it. A green number added stock, a red one removed it; “→” is the item’s balance after that movement. A stock count posts only the difference and sets the balance to what was counted. A revaluation changes cost, not quantity.</p>}</div>;
}

// ─── Report submissions helpers ───────────────────────────────────────────────

export function reportStatusVariant(status: string): 'success' | 'warning' | 'danger' | 'neutral' {
  if (status === 'posted' || status === 'approved') return 'success';
  if (status === 'pending_approval') return 'warning';
  if (status === 'rejected') return 'danger';
  return 'neutral';
}

export const SUBMISSION_STATUSES = ['outstanding', 'draft', 'pending_approval', 'approved', 'posted', 'deferred', 'skipped', 'rejected'] as const;

export const reportVariance = (row: ShiftInventoryReport) =>
  row.lines.reduce((sum, line) => sum + Number(line.variance_cost ?? 0), 0);

// ─── Transfer / return helpers ────────────────────────────────────────────────

// A colour for a transfer status — shared by the parent order (pending,
// partially_sent, sent, partially_received, closed, cancelled) and its child
// legs (pending, sent, closed, cancelled). Terminal-good is green, in-flight is
// amber, cancelled is red.
export const transferStatusVariant = (status: string): 'success' | 'warning' | 'danger' | 'neutral' => {
  if (status === 'closed' || status === 'received' || status === 'sent' || status === 'completed' || status === 'approved') return 'success';
  if (status === 'pending' || status === 'partially_sent' || status === 'partially_received' || status === 'submitted' || status === 'in_transit') return 'warning';
  if (status === 'cancelled' || status === 'declined' || status === 'rejected') return 'danger';
  return 'neutral';
};

// The status in words — the raw enum with underscores read as spaces.
export const transferStatusLabel = (status: string): string => status.replaceAll('_', ' ');

// Whether a child leg's line was received short or over what was sent.
export const transferLineVaries = (line: Transfer['items'][number]) =>
  Number(line.received_quantity) !== Number(line.sent_quantity);
