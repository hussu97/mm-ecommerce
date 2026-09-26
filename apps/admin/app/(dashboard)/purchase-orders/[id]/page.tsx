'use client';

// Purchase order detail. Shows what was ordered, what was received and any
// short/excess variance per line, the money split, and the invoice — whose
// number and image can be added or corrected even after the order is posted
// (the lines themselves are frozen once stock has moved).

import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';

import {
  inventoryApi,
  type PurchaseOrderMiscCategory,
  type PurchaseOrderMiscPeriod,
} from '@/lib/pos-api';
import type { PurchaseOrder, PurchaseOrderMiscItem, PurchaseOrderStatus } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button, Input, Select, Spinner } from '@/components/ui';
import { Modal } from '@/components/pos/ResourcePage';
import {
  MiscPeriodPicker,
  inferPeriodValue,
  periodIsValid,
  type MiscPeriodValue,
} from '@/components/purchasing/MiscPeriodPicker';
import { periodLabel } from '@/lib/purchasing';
import { InvoicePreview } from '@/components/ui/InvoicePreview';
import { formatCost, formatCurrency, formatQuantity, interactiveRowClass } from '@/lib/utils';

const STATUS_VARIANT: Record<
  PurchaseOrderStatus,
  'success' | 'info' | 'warning' | 'danger' | 'neutral'
> = {
  draft: 'neutral',
  pending: 'warning',
  approved: 'info',
  declined: 'danger',
  partially_received: 'warning',
  closed: 'success',
  voided: 'danger',
};

export default function PurchaseOrderDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [po, setPo] = useState<PurchaseOrder | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editingMisc, setEditingMisc] = useState<PurchaseOrderMiscItem | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setPo(await inventoryApi.purchaseOrder(id));
      setError('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load the purchase order.');
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { void load(); }, [load]);

  if (loading) return <div className="p-6"><Spinner /></div>;
  if (error || !po) return (
    <div className="p-6 space-y-3">
      <p className="bg-red-50 p-3 text-sm text-red-800">{error || 'Purchase order not found.'}</p>
      <Link href="/purchase-orders" className="text-sm text-primary underline">Back to purchase orders</Link>
    </div>
  );

  return (
    <div className="max-w-[var(--content-max)] space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link href="/purchase-orders" className="text-xs text-gray-400 hover:text-primary">← Purchase orders</Link>
          <h1 className="font-display text-xl text-primary tracking-wide">{po.reference}</h1>
        </div>
        <Badge variant={STATUS_VARIANT[po.status]}>{po.status.replace(/_/g, ' ')}</Badge>
      </div>

      <div className="grid gap-3 border border-gray-200 p-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <Detail label="Supplier" value={po.supplier_name ?? '—'} />
        <Detail label="Business date" value={po.business_date} />
        <Detail label="Delivery date" value={po.delivery_date ?? '—'} />
        <Detail label="Origin" value={po.origin} />
      </div>

      <InvoicePanel po={po} onSaved={load} />

      <div className="overflow-x-auto border border-gray-200">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs uppercase tracking-wider text-gray-500">
            <tr>
              <th className="px-2 py-1">Item</th>
              <th className="px-2 py-1 text-right">Ordered</th>
              <th className="px-2 py-1 text-right">Received</th>
              <th className="px-2 py-1 text-right">Variance</th>
              <th className="px-2 py-1">Variance reason</th>
              <th className="px-2 py-1 text-right">Unit cost</th>
              <th className="px-2 py-1 text-right">Line total</th>
            </tr>
          </thead>
          <tbody>
            {po.items.map((item) => {
              const v = Number(item.received_quantity) - Number(item.quantity);
              return (
                <tr key={item.id} className={`border-t border-gray-100 ${interactiveRowClass}`}>
                  <td className="px-2 py-1 font-medium">
                    {item.item_name ?? item.item_id}
                    {item.item_sku && <span className="ml-1 text-xs text-gray-400">{item.item_sku}</span>}
                  </td>
                  <td className="px-2 py-1 text-right tabular-nums">{formatQuantity(item.quantity)}</td>
                  <td className="px-2 py-1 text-right tabular-nums">{formatQuantity(item.received_quantity)}</td>
                  <td className={`px-2 py-1 text-right tabular-nums ${v === 0 ? 'text-gray-300' : v < 0 ? 'text-red-600 font-medium' : 'text-amber-600 font-medium'}`}>
                    {v === 0 ? '—' : `${v > 0 ? '+' : ''}${formatQuantity(v)}`}
                  </td>
                  <td className="px-2 py-1 text-xs text-gray-600">{item.variance_reason ?? ''}</td>
                  <td className="px-2 py-1 text-right tabular-nums text-gray-500">{formatCost(item.unit_cost)}</td>
                  <td className="px-2 py-1 text-right tabular-nums">{formatCurrency(item.total_cost)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {po.misc_items.length > 0 && (
        <div>
          <p className="mb-1 text-xs uppercase tracking-wider text-gray-400">
            Miscellaneous items (not tracked as inventory)
          </p>
          <div className="overflow-x-auto border border-gray-200">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase tracking-wider text-gray-500">
                <tr>
                  <th className="px-2 py-1">Name</th>
                  <th className="px-2 py-1">Category</th>
                  <th className="px-2 py-1">Period</th>
                  <th className="px-2 py-1">Unit</th>
                  <th className="px-2 py-1 text-right">Qty</th>
                  <th className="px-2 py-1 text-right">Unit cost</th>
                  <th className="px-2 py-1 text-right">Line total</th>
                  <th className="w-8" />
                </tr>
              </thead>
              <tbody>
                {po.misc_items.map((m) => (
                  <tr key={m.id} className={`border-t border-gray-100 ${interactiveRowClass}`}>
                    <td className="px-2 py-1 font-medium">{m.name}</td>
                    <td className="px-2 py-1">
                      {m.category_name ?? '—'}
                      {m.category_admin_only && (
                        <Badge variant="warning" className="ml-2">Admin only</Badge>
                      )}
                    </td>
                    <td className="px-2 py-1 text-gray-600 whitespace-nowrap">
                      {periodLabel(m.period_from, m.period_to)}
                    </td>
                    <td className="px-2 py-1 text-gray-500">{m.storage_unit}</td>
                    <td className="px-2 py-1 text-right tabular-nums">{formatQuantity(m.quantity)}</td>
                    <td className="px-2 py-1 text-right tabular-nums text-gray-500">{formatCost(m.unit_cost)}</td>
                    <td className="px-2 py-1 text-right tabular-nums">{formatCurrency(m.entered_total)}</td>
                    <td className="px-2 py-1 text-right">
                      {po.status !== 'voided' && (
                        <button
                          onClick={() => setEditingMisc(m)}
                          className="text-gray-400 hover:text-primary"
                          aria-label={`Change category or period of ${m.name}`}
                          title="Change category or period"
                        >
                          <span className="material-icons text-[16px]">edit</span>
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="ml-auto w-full max-w-xs space-y-1 text-sm">
        <Total label="Net" value={po.subtotal_net} />
        <Total label="VAT" value={po.vat_total} />
        <Total label="Additional cost" value={po.additional_cost} />
        <Total label="Total" value={po.total_cost} strong />
      </div>

      {editingMisc && (
        <EditMiscLine
          po={po}
          line={editingMisc}
          onClose={() => setEditingMisc(null)}
          onSaved={(next) => {
            setPo(next);
            setEditingMisc(null);
          }}
        />
      )}
    </div>
  );
}

/** Re-file one misc line under another category or period. No money moves, so
 *  this stays open on a received order; the P&L reads it on its next load. */
function EditMiscLine({
  po,
  line,
  onClose,
  onSaved,
}: {
  po: PurchaseOrder;
  line: PurchaseOrderMiscItem;
  onClose: () => void;
  onSaved: (po: PurchaseOrder) => void;
}) {
  const [categories, setCategories] = useState<PurchaseOrderMiscCategory[]>([]);
  const [periods, setPeriods] = useState<PurchaseOrderMiscPeriod[]>([]);
  const [categoryId, setCategoryId] = useState(line.category_id);
  const [period, setPeriod] = useState<MiscPeriodValue>({
    preset: 'custom',
    from: line.period_from,
    to: line.period_to,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    Promise.all([inventoryApi.miscCategories(), inventoryApi.miscPeriods()])
      .then(([cats, presets]) => {
        if (cancelled) return;
        // Live categories, plus the line's own even if it has since been retired.
        setCategories(
          cats.filter((c) => (c.is_active && !c.deleted_at) || c.id === line.category_id),
        );
        setPeriods(presets);
        setPeriod(inferPeriodValue(presets, line.period_from, line.period_to));
      })
      .catch(() => { if (!cancelled) setError('Could not load categories.'); });
    return () => { cancelled = true; };
  }, [line.category_id, line.period_from, line.period_to]);

  async function save() {
    if (!categoryId || !periodIsValid(period)) {
      setError('Pick a category and a period that ends on or after it starts.');
      return;
    }
    setSaving(true);
    setError('');
    try {
      onSaved(
        await inventoryApi.editPurchaseOrderMiscLine(po.id, line.id, {
          category_id: categoryId,
          period_from: period.from,
          period_to: period.to,
        }),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Save failed.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal title={`${line.name} — category & period`} onClose={onClose} wide>
      <div className="space-y-4">
        <div className="max-w-xs">
          <Select
            label="Category"
            value={categoryId}
            onChange={(e) => setCategoryId(e.target.value)}
            options={categories.map((c) => ({
              value: c.id,
              label: c.admin_only ? `${c.name} (admin only)` : c.name,
            }))}
            placeholder="Choose…"
          />
        </div>
        <MiscPeriodPicker periods={periods} value={period} onChange={setPeriod} />
        {error && <p className="text-xs text-red-600 font-body">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={save} loading={saving}>
            Save
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function InvoicePanel({ po, onSaved }: { po: PurchaseOrder; onSaved: () => void | Promise<void> }) {
  const [reference, setReference] = useState(po.supplier_reference ?? '');
  const [file, setFile] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');
  const [preview, setPreview] = useState(false);

  async function save() {
    setSaving(true);
    setMsg('');
    try {
      if (reference.trim() !== (po.supplier_reference ?? '')) {
        await inventoryApi.updatePurchaseOrder(po.id, { supplier_reference: reference.trim() || null });
      }
      if (file) {
        await inventoryApi.uploadPurchaseOrderInvoice(po.id, file);
        setFile(null);
      }
      setMsg('Saved.');
      await onSaved();
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : 'Save failed.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="border border-gray-200 p-4">
      <p className="mb-3 text-xs uppercase tracking-wider text-gray-400">Invoice</p>
      <div className="grid gap-3 sm:grid-cols-3">
        <Input
          label="Invoice / supplier reference"
          value={reference}
          onChange={(e) => setReference(e.target.value)}
          placeholder="Their PO / invoice no."
        />
        <div className="text-xs font-body sm:col-span-2">
          <span className="mb-1 block text-gray-500">Invoice image</span>
          <div className="flex flex-wrap items-center gap-3">
            {po.invoice_url && (
              <button
                type="button"
                onClick={() => setPreview(true)}
                className="text-primary hover:underline text-sm"
              >
                View current
              </button>
            )}
            <label className="cursor-pointer rounded border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50">
              {file ? 'Change file' : po.invoice_url ? 'Replace' : 'Choose file'}
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp,application/pdf"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="hidden"
              />
            </label>
            {file ? <span className="text-sm text-gray-600">{file.name}</span> : <span className="text-gray-400">JPEG, PNG, WebP or PDF</span>}
          </div>
        </div>
      </div>
      <div className="mt-3 flex items-center gap-3">
        <Button size="sm" onClick={save} loading={saving}>Save invoice details</Button>
        {msg && <span className="text-xs text-gray-500">{msg}</span>}
      </div>
      {preview && po.invoice_url && (
        <InvoicePreview url={po.invoice_url} title={po.reference} onClose={() => setPreview(false)} />
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

function Total({ label, value, strong }: { label: string; value: number; strong?: boolean }) {
  return (
    <div className={`flex justify-between ${strong ? 'border-t border-gray-200 pt-1 font-display text-primary' : 'text-gray-600'}`}>
      <span>{label}</span>
      <span className="tabular-nums">{formatCurrency(value)}</span>
    </div>
  );
}
