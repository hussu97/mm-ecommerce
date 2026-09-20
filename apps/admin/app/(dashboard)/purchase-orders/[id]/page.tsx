'use client';

// Purchase order detail. Shows what was ordered, what was received and any
// short/excess variance per line, the money split, and the invoice — whose
// number and image can be added or corrected even after the order is posted
// (the lines themselves are frozen once stock has moved).

import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';

import { inventoryApi } from '@/lib/pos-api';
import type { PurchaseOrder, PurchaseOrderStatus } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button, Input, Spinner } from '@/components/ui';
import { formatCurrency, formatQuantity, interactiveRowClass } from '@/lib/utils';

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
};

export default function PurchaseOrderDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [po, setPo] = useState<PurchaseOrder | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

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
                  <td className="px-2 py-1 text-right tabular-nums text-gray-500">{formatCurrency(item.unit_cost)}</td>
                  <td className="px-2 py-1 text-right tabular-nums">{formatCurrency(item.total_cost)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="ml-auto w-full max-w-xs space-y-1 text-sm">
        <Total label="Net" value={po.subtotal_net} />
        <Total label="VAT" value={po.vat_total} />
        <Total label="Additional cost" value={po.additional_cost} />
        <Total label="Total" value={po.total_cost} strong />
      </div>
    </div>
  );
}

function InvoicePanel({ po, onSaved }: { po: PurchaseOrder; onSaved: () => void | Promise<void> }) {
  const [reference, setReference] = useState(po.supplier_reference ?? '');
  const [file, setFile] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');

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
              <a href={po.invoice_url} target="_blank" rel="noreferrer" className="text-primary hover:underline text-sm">
                View current
              </a>
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
