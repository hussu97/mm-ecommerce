'use client';

// Raise a NEW transfer order (F-transfers-fanout). An admin picks one source
// branch and allocates quantities to several destination branches at once in an
// editable grid — item rows × one quantity column per OTHER active branch. The
// source's on-hand is shown per item; when a row's total across branches exceeds
// it, the row needs an explicit override (which posts a shortfall top-up
// adjustment at create and needs the adjustments permission). Submitting fans
// the order out into one child transfer per destination — no stock moves yet.
//
// The grid mirrors items/page.tsx (per-branch columns from activeBranches) and
// reports/[id]/page.tsx (per-cell Record<itemId, Record<colKey,string>> + setCell,
// category grouping, inputMode="decimal").

import { useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import {
  branchesApi,
  inventoryApi,
  type TransferOrderCreate,
} from '@/lib/pos-api';
import type { Branch, InventoryCategory, InventoryItem, InventoryLevel } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button, Input, Select, Spinner, Textarea } from '@/components/ui';
import { useAuth } from '@/lib/auth-context';
import { useToast } from '@/components/ui/feedback';
import { formatQuantity } from '@/lib/utils';

const parseNum = (value: string | undefined): number => {
  if (value === undefined) return 0;
  const n = Number(value.trim());
  return Number.isFinite(n) ? n : 0;
};

export default function NewTransferOrderPage() {
  const router = useRouter();
  const toast = useToast();
  const { user } = useAuth();

  const [branches, setBranches] = useState<Branch[]>([]);
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [categories, setCategories] = useState<InventoryCategory[]>([]);
  const [levels, setLevels] = useState<InventoryLevel[]>([]);
  const [loading, setLoading] = useState(true);

  const [sourceBranchId, setSourceBranchId] = useState('');
  const [kind, setKind] = useState<'transfer' | 'return'>('transfer');
  const [requiredDate, setRequiredDate] = useState('');
  const [notes, setNotes] = useState('');
  const [search, setSearch] = useState('');

  // itemId → branchId → typed quantity; and itemId → override toggle.
  const [cells, setCells] = useState<Record<string, Record<string, string>>>({});
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const [submitting, setSubmitting] = useState(false);
  const [banner, setBanner] = useState<{ text: string; error: boolean } | null>(null);

  // A stable token so a retried submit does not raise the fan-out twice.
  const requestId = useRef(typeof crypto !== 'undefined' ? crypto.randomUUID() : `${Date.now()}`);

  // Whether this admin may override on-hand (posts a stock write-off). The create
  // endpoint enforces it too; surfacing it here avoids a 403 surprise.
  const canOverride = !!user && (user.is_superadmin || user.permissions.includes('inventory.adjustments.manage'));

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      branchesApi.list().catch(() => [] as Branch[]),
      inventoryApi.items().catch(() => [] as InventoryItem[]),
      inventoryApi.categories().catch(() => [] as InventoryCategory[]),
      inventoryApi.levels({ limit: 5000 }).catch(() => [] as InventoryLevel[]),
    ]).then(([b, i, c, l]) => {
      if (cancelled) return;
      setBranches(b);
      setItems(i);
      setCategories(c);
      setLevels(l);
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  const activeBranches = useMemo(
    () => branches
      .filter((b) => b.is_active && !b.deleted_at)
      .sort((a, b) => a.display_order - b.display_order || a.name.localeCompare(b.name)),
    [branches],
  );
  // The destinations a transfer can fan out to — every active branch but the source.
  const destinationBranches = useMemo(
    () => activeBranches.filter((b) => b.id !== sourceBranchId),
    [activeBranches, sourceBranchId],
  );

  // The source's on-hand per item — summed across its warehouses (a branch can
  // hold several), so the shortfall check sees the whole branch.
  const onHandByItem = useMemo(() => {
    const map = new Map<string, number>();
    if (!sourceBranchId) return map;
    for (const level of levels) {
      if (level.branch_id !== sourceBranchId) continue;
      map.set(level.item_id, (map.get(level.item_id) ?? 0) + Number(level.quantity));
    }
    return map;
  }, [levels, sourceBranchId]);

  const categoryMeta = useMemo(
    () => new Map(categories.map((c) => [c.id, { name: c.name, order: c.display_order }])),
    [categories],
  );

  // Active items, filtered by the search box, grouped by category (category order
  // → category name → item name), Uncategorised last — the order the shop counts in.
  const groups = useMemo(() => {
    const q = search.trim().toLocaleLowerCase();
    const selectable = items
      .filter((item) => item.is_active && !item.deleted_at)
      .filter((item) => !q || `${item.name} ${item.sku}`.toLocaleLowerCase().includes(q))
      .sort((a, b) => a.name.localeCompare(b.name));
    const map = new Map<string, { name: string; order: number; items: InventoryItem[] }>();
    for (const item of selectable) {
      const meta = item.category_id ? categoryMeta.get(item.category_id) : undefined;
      const name = meta?.name ?? 'Uncategorised';
      const order = meta ? meta.order : Number.MAX_SAFE_INTEGER;
      const bucket = map.get(name) ?? { name, order, items: [] };
      bucket.order = Math.min(bucket.order, order);
      bucket.items.push(item);
      map.set(name, bucket);
    }
    return [...map.values()].sort((a, b) => a.order - b.order || a.name.localeCompare(b.name));
  }, [items, search, categoryMeta]);

  const setCell = (itemId: string, branchId: string, value: string) =>
    setCells((prev) => ({ ...prev, [itemId]: { ...prev[itemId], [branchId]: value } }));

  const rowTotal = (itemId: string): number => {
    const row = cells[itemId];
    if (!row) return 0;
    return destinationBranches.reduce((sum, b) => sum + parseNum(row[b.id]), 0);
  };
  const shortfall = (itemId: string): number => {
    // A return draws from the destination, not the source, so the source's
    // on-hand does not gate it — no shortfall concept applies.
    if (kind === 'return') return 0;
    const over = rowTotal(itemId) - (onHandByItem.get(itemId) ?? 0);
    return over > 0 ? over : 0;
  };

  // Every item with a nonzero allocation, ready to submit.
  const activeItemIds = useMemo(() => {
    const ids: string[] = [];
    for (const itemId of Object.keys(cells)) {
      if (rowTotal(itemId) > 0) ids.push(itemId);
    }
    return ids;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cells, destinationBranches]);

  const grandTotal = useMemo(
    () => activeItemIds.reduce((sum, id) => sum + rowTotal(id), 0),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [activeItemIds, cells, destinationBranches],
  );

  // Rows over the source's on-hand that have NOT been overridden — the create is
  // refused until each is toggled (or the quantity dropped).
  const unresolvedShortfalls = useMemo(
    () => activeItemIds.filter((id) => shortfall(id) > 0 && !overrides[id]),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [activeItemIds, cells, overrides, onHandByItem, kind, destinationBranches],
  );
  const overridingItems = useMemo(
    () => activeItemIds.filter((id) => shortfall(id) > 0 && overrides[id]),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [activeItemIds, cells, overrides, onHandByItem, kind, destinationBranches],
  );

  const itemName = (id: string) => items.find((i) => i.id === id)?.name ?? id;

  const canSubmit = sourceBranchId
    && destinationBranches.length > 0
    && activeItemIds.length > 0
    && unresolvedShortfalls.length === 0
    && (overridingItems.length === 0 || canOverride);

  const submit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setBanner(null);
    const body: TransferOrderCreate = {
      source_branch_id: sourceBranchId,
      kind,
      required_date: requiredDate || null,
      notes: notes.trim() || null,
      client_request_id: requestId.current,
      items: activeItemIds.map((itemId) => {
        const row = cells[itemId] ?? {};
        return {
          item_id: itemId,
          unit: 'storage' as const,
          override: shortfall(itemId) > 0 ? !!overrides[itemId] : false,
          allocations: destinationBranches
            .filter((b) => parseNum(row[b.id]) > 0)
            .map((b) => ({ branch_id: b.id, quantity: parseNum(row[b.id]) })),
        };
      }),
    };
    try {
      const created = await inventoryApi.createTransferOrder(body);
      toast.success(`Transfer order ${created.reference} raised.`);
      router.push(`/inventory/transfers/${created.id}`);
    } catch (err) {
      setBanner({ text: err instanceof ApiError ? err.message : 'Could not raise the transfer order.', error: true });
      setSubmitting(false);
    }
  };

  const colCount = 2 + destinationBranches.length + 2; // item, on-hand, branches…, total, override

  return (
    <div className="max-w-[var(--content-max)] space-y-5">
      <div>
        <Link href="/inventory/submissions/transfers" className="text-xs text-gray-400 hover:text-primary">← Transfers &amp; returns</Link>
        <h1 className="font-display text-xl text-primary tracking-wide">New transfer order</h1>
      </div>

      <p className="text-sm text-gray-500">
        Pick a source branch, then allocate quantities to the other branches. Nothing moves yet — creating the order fans it out into one pending transfer per destination for the source&apos;s POS to send.
      </p>

      <div className="grid gap-3 border border-gray-200 p-4 sm:grid-cols-2 lg:grid-cols-4">
        <Select
          label="Source branch"
          value={sourceBranchId}
          onChange={(e) => { setSourceBranchId(e.target.value); setCells({}); setOverrides({}); setBanner(null); }}
          placeholder="Choose branch"
          options={activeBranches.map((b) => ({ value: b.id, label: b.name }))}
        />
        <Select
          label="Kind"
          value={kind}
          onChange={(e) => setKind(e.target.value as 'transfer' | 'return')}
          options={[{ value: 'transfer', label: 'Transfer' }, { value: 'return', label: 'Return' }]}
        />
        <Input label="Required date" type="date" value={requiredDate} onChange={(e) => setRequiredDate(e.target.value)} />
        <div className="sm:col-span-2 lg:col-span-1">
          <Textarea label="Notes" value={notes} onChange={(e) => setNotes(e.target.value)} rows={1} placeholder="Optional" />
        </div>
      </div>

      {loading ? (
        <Spinner />
      ) : !sourceBranchId ? (
        <p className="border border-dashed border-gray-300 p-4 text-sm text-gray-500">Choose a source branch to start allocating.</p>
      ) : destinationBranches.length === 0 ? (
        <p className="border border-dashed border-gray-300 p-4 text-sm text-gray-500">There are no other active branches to transfer to.</p>
      ) : (
        <>
          <div className="flex flex-wrap items-end justify-between gap-3">
            <Input label="Search item" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Name or SKU" className="w-72" />
            <div className="flex items-center gap-2 text-sm text-gray-600">
              <Badge>{activeItemIds.length} item{activeItemIds.length === 1 ? '' : 's'}</Badge>
              <span>Grand total <strong className="tabular-nums">{formatQuantity(grandTotal)}</strong></span>
            </div>
          </div>

          {!canOverride && (
            <p className="bg-amber-50 border border-amber-200 p-2 text-xs text-amber-800">
              You do not have the <code>inventory.adjustments.manage</code> permission, so you cannot allocate more than a branch holds on hand (that would write stock off). Keep each row within its on-hand.
            </p>
          )}

          <div className="overflow-x-auto border border-gray-200">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase tracking-wider text-gray-500">
                <tr>
                  <th className="px-2 py-1 sticky left-0 bg-gray-50">Item</th>
                  <th className="px-2 py-1 text-right">On hand</th>
                  {destinationBranches.map((b) => <th key={b.id} className="px-2 py-1 text-right whitespace-nowrap">{b.name}</th>)}
                  <th className="px-2 py-1 text-right">Row total</th>
                  <th className="px-2 py-1">Override</th>
                </tr>
              </thead>
              <tbody>
                {groups.map((group) => (
                  <GroupRows key={group.name} name={group.name} span={colCount}>
                    {group.items.map((item) => {
                      const onHand = onHandByItem.get(item.id);
                      const total = rowTotal(item.id);
                      const short = shortfall(item.id);
                      const overThreshold = short > 0;
                      const row = cells[item.id] ?? {};
                      return (
                        <tr key={item.id} className="border-t border-gray-100">
                          <td className="px-2 py-1 font-medium sticky left-0 bg-white">
                            {item.name}
                            <span className="ml-1 text-xs text-gray-400">{item.storage_unit}</span>
                          </td>
                          <td className={`px-2 py-1 text-right tabular-nums ${overThreshold ? 'text-red-600 font-medium' : 'text-gray-500'}`}>
                            {kind === 'return' ? '—' : onHand === undefined ? <span className="text-gray-300">0</span> : formatQuantity(onHand)}
                          </td>
                          {destinationBranches.map((b) => (
                            <td key={b.id} className="px-2 py-1 text-right">
                              <input
                                inputMode="decimal"
                                value={row[b.id] ?? ''}
                                onChange={(e) => setCell(item.id, b.id, e.target.value)}
                                className="w-16 border border-gray-300 px-1 py-0.5 text-right"
                                placeholder="0"
                              />
                            </td>
                          ))}
                          <td className={`px-2 py-1 text-right tabular-nums ${total > 0 ? 'font-medium text-gray-800' : 'text-gray-300'}`}>{formatQuantity(total)}</td>
                          <td className="px-2 py-1">
                            {overThreshold ? (
                              <label className="flex items-center gap-1.5 text-xs">
                                <input
                                  type="checkbox"
                                  checked={!!overrides[item.id]}
                                  disabled={!canOverride}
                                  onChange={(e) => setOverrides((prev) => ({ ...prev, [item.id]: e.target.checked }))}
                                />
                                <span className="text-red-600">short {formatQuantity(short)}</span>
                              </label>
                            ) : (
                              <span className="text-gray-300">—</span>
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

          {overridingItems.length > 0 && canOverride && (
            <p className="bg-amber-50 border border-amber-200 p-2 text-xs text-amber-800">
              {overridingItems.length} row{overridingItems.length === 1 ? '' : 's'} allocate more than the source holds. A shortfall top-up adjustment will be posted for the difference when you create the order.
            </p>
          )}
          {unresolvedShortfalls.length > 0 && (
            <p className="bg-red-50 border border-red-200 p-2 text-xs text-red-800">
              Over on-hand and not overridden: {unresolvedShortfalls.map(itemName).join(', ')}. Toggle Override on each (or lower the quantity) to continue{canOverride ? '' : ' — and Override needs the adjustments permission you do not have'}.
            </p>
          )}
          {banner && (
            <p className={`border p-3 text-sm ${banner.error ? 'border-red-200 bg-red-50 text-red-800' : 'border-green-200 bg-green-50 text-green-800'}`}>{banner.text}</p>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={() => void submit()} loading={submitting} disabled={!canSubmit}>
              {submitting ? 'Raising…' : 'Raise transfer order'}
            </Button>
            <Link href="/inventory/submissions/transfers" className="text-sm text-gray-500 hover:text-primary">Cancel</Link>
          </div>
        </>
      )}
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
