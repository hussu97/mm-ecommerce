'use client';

import { useCallback, useEffect, useState } from 'react';
import { branchesApi, inventoryApi } from '@/lib/pos-api';
import type {
  Branch,
  InventoryItem,
  PurchaseOrder,
  PurchaseOrderStatus,
  Supplier,
  SupplierItem,
} from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button, Input, Pagination, Select, Spinner } from '@/components/ui';
import { DataTable, RowAction } from '@/components/ui/DataTable';
import { Modal } from '@/components/pos/ResourcePage';
import { formatCurrency, formatQuantity } from '@/lib/utils';

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

interface DraftLine {
  item_id: string;
  quantity: string;
  entered_total: string;
}

const VAT_RATE = 0.05;

export default function PurchaseOrdersPage() {
  const [orders, setOrders] = useState<PurchaseOrder[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [receiving, setReceiving] = useState<PurchaseOrder | null>(null);
  // Client-side paging: the list is fetched whole (server cap 1,000) and sliced
  // here, so a busy purchasing week does not render every row at once (F-ADM).
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [o, s, b, i] = await Promise.all([
        inventoryApi.purchaseOrders(),
        inventoryApi.suppliers(),
        branchesApi.list(),
        inventoryApi.items(),
      ]);
      setOrders(o);
      setSuppliers(s);
      setBranches(b);
      setItems(i);
      setError('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load purchase orders.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(id: string, action: 'submit' | 'approve' | 'decline') {
    try {
      if (action === 'submit') await inventoryApi.submitPurchaseOrder(id);
      if (action === 'approve') await inventoryApi.approvePurchaseOrder(id);
      if (action === 'decline') await inventoryApi.declinePurchaseOrder(id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Action failed.');
    }
  }

  const totalPages = Math.max(1, Math.ceil(orders.length / perPage));
  // Clamp rather than reset, so an approve/decline that shrinks the list never
  // strands the viewer on an empty page past the new end.
  const currentPage = Math.min(page, totalPages);
  const pageRows = orders.slice((currentPage - 1) * perPage, currentPage * perPage);

  return (
    <div>
      <header className="mb-5 flex items-start justify-between gap-3">
        <div>
          <h1 className="font-display text-xl text-primary tracking-wide">Purchase Orders</h1>
          <p className="text-xs text-gray-500 font-body mt-1">
            Draft → submit → approve → receive. Approving requires a second person.
          </p>
        </div>
        <Button onClick={() => setCreating(true)}>New Order</Button>
      </header>

      {error && (
        <div className="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : orders.length === 0 ? (
        <p className="py-16 text-center text-sm text-gray-400 font-body">
          No purchase orders yet.
        </p>
      ) : (
        <>
        <DataTable<PurchaseOrder>
          rows={pageRows}
          rowKey={(po) => po.id}
          actions={(po) => (
            <>
              {po.status === 'draft' && (
                <RowAction onClick={() => act(po.id, 'submit')}>Submit</RowAction>
              )}
              {po.status === 'pending' && (
                <>
                  <RowAction onClick={() => act(po.id, 'approve')}>Approve</RowAction>
                  <RowAction danger onClick={() => act(po.id, 'decline')}>
                    Decline
                  </RowAction>
                </>
              )}
              {(po.status === 'approved' || po.status === 'partially_received') && (
                <RowAction onClick={() => setReceiving(po)}>Receive</RowAction>
              )}
            </>
          )}
          columns={[
            { header: 'Supplier', priority: 'primary', sortable: true, sortAccessor: (po) => po.supplier_name ?? null, render: (po) => po.supplier_name ?? '—' },
            {
              header: 'Reference',
              priority: 'secondary',
              sortable: true,
              sortAccessor: (po) => po.reference,
              render: (po) => <code className="text-xs text-gray-600">{po.reference}</code>,
            },
            {
              header: 'Status',
              sortable: true,
              sortAccessor: (po) => po.status,
              render: (po) => (
                <Badge variant={STATUS_VARIANT[po.status]}>{po.status.replace(/_/g, ' ')}</Badge>
              ),
            },
            { header: 'Lines', className: 'text-right', sortable: true, sortAccessor: (po) => po.items.length, render: (po) => po.items.length },
            {
              header: 'Total',
              className: 'text-right',
              sortable: true,
              sortAccessor: (po) => Number(po.total_cost),
              render: (po) => formatCurrency(po.total_cost),
            },
            {
              header: 'Delivery',
              sortable: true,
              sortAccessor: (po) => po.delivery_date ?? null,
              render: (po) => <span className="text-gray-500">{po.delivery_date ?? '—'}</span>,
            },
          ]}
        />
        <Pagination
          page={currentPage}
          pages={totalPages}
          total={orders.length}
          perPage={perPage}
          onPageChange={setPage}
          onPerPageChange={(p) => { setPerPage(p); setPage(1); }}
          label="purchase orders"
        />
        </>
      )}

      {creating && (
        <CreateOrder
          suppliers={suppliers}
          branches={branches}
          items={items}
          onClose={() => setCreating(false)}
          onSaved={() => {
            setCreating(false);
            void load();
          }}
        />
      )}

      {receiving && (
        <ReceiveOrder
          order={receiving}
          onClose={() => setReceiving(null)}
          onSaved={() => {
            setReceiving(null);
            void load();
          }}
        />
      )}
    </div>
  );
}

function CreateOrder({
  suppliers,
  branches,
  items,
  onClose,
  onSaved,
}: {
  suppliers: Supplier[];
  branches: Branch[];
  items: InventoryItem[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [supplierId, setSupplierId] = useState('');
  const [branchId, setBranchId] = useState(branches[0]?.id ?? '');
  const [deliveryDate, setDeliveryDate] = useState('');
  const [supplierReference, setSupplierReference] = useState('');
  const [invoiceFile, setInvoiceFile] = useState<File | null>(null);
  const [lines, setLines] = useState<DraftLine[]>([{ item_id: '', quantity: '1', entered_total: '0' }]);
  // The items this supplier can supply — prefilled once a supplier is chosen.
  const [supplierItems, setSupplierItems] = useState<SupplierItem[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const supplier = suppliers.find((s) => s.id === supplierId) ?? null;
  const vatDeductible = supplier?.is_vat_deductible ?? false;

  useEffect(() => {
    if (!supplierId) {
      setSupplierItems(null);
      return;
    }
    let cancelled = false;
    inventoryApi
      .supplierItems(supplierId)
      .then((rows) => { if (!cancelled) setSupplierItems(rows); })
      .catch(() => { if (!cancelled) setSupplierItems([]); });
    return () => { cancelled = true; };
  }, [supplierId]);

  // Offer the supplier's mapped items when we have them; otherwise every
  // purchasable item, so a PO is never blocked on missing mappings.
  const mappedIds = new Set((supplierItems ?? []).map((r) => r.item_id));
  const pickable = (supplierItems && supplierItems.length > 0)
    ? items.filter((i) => mappedIds.has(i.id))
    : items.filter((i) => i.is_active && !i.deleted_at);

  const grossTotal = lines.reduce((sum, l) => sum + Number(l.entered_total || 0), 0);
  const vatTotal = vatDeductible ? grossTotal - grossTotal / (1 + VAT_RATE) : 0;

  function updateLine(index: number, patch: Partial<DraftLine>) {
    setLines((prev) => prev.map((l, i) => (i === index ? { ...l, ...patch } : l)));
  }

  function prefillCost(index: number, itemId: string) {
    const mapped = (supplierItems ?? []).find((r) => r.item_id === itemId);
    const qty = Number(lines[index].quantity || 0);
    const patch: Partial<DraftLine> = { item_id: itemId };
    if (mapped && qty > 0) patch.entered_total = String(mapped.default_unit_cost * qty);
    updateLine(index, patch);
  }

  async function save() {
    const valid = lines.filter((l) => l.item_id && Number(l.quantity) > 0);
    if (!supplierId || !branchId || valid.length === 0) {
      setError('Supplier, branch and at least one line are required.');
      return;
    }
    setSaving(true);
    setError('');
    try {
      const po = await inventoryApi.createPurchaseOrder({
        supplier_id: supplierId,
        branch_id: branchId,
        delivery_date: deliveryDate || null,
        supplier_reference: supplierReference.trim() || null,
        items: valid.map((l) => ({
          item_id: l.item_id,
          quantity: Number(l.quantity),
          entered_total: Number(l.entered_total),
          unit: 'storage',
        })),
      });
      if (invoiceFile) {
        await inventoryApi.uploadPurchaseOrderInvoice(po.id, invoiceFile);
      }
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Save failed.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal title="New purchase order" onClose={onClose} wide>
      <div className="grid gap-3 sm:grid-cols-3">
        <Select
          label="Supplier"
          value={supplierId}
          onChange={(e) => setSupplierId(e.target.value)}
          options={suppliers.map((s) => ({ value: s.id, label: s.name }))}
          placeholder="Choose…"
        />
        <Select
          label="Branch"
          value={branchId}
          onChange={(e) => setBranchId(e.target.value)}
          options={branches.map((b) => ({ value: b.id, label: b.name }))}
          placeholder="Choose…"
        />
        <Input
          label="Delivery date"
          type="date"
          value={deliveryDate}
          onChange={(e) => setDeliveryDate(e.target.value)}
        />
        <Input
          label="Supplier reference"
          value={supplierReference}
          onChange={(e) => setSupplierReference(e.target.value)}
          placeholder="Their PO / invoice no."
        />
        <label className="text-xs font-body sm:col-span-2">
          <span className="mb-1 block text-gray-500">Invoice image (optional)</span>
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp,application/pdf"
            onChange={(e) => setInvoiceFile(e.target.files?.[0] ?? null)}
            className="block w-full text-sm"
          />
        </label>
      </div>

      {supplier && (
        <p className="mt-2 text-xs text-gray-500 font-body">
          {vatDeductible
            ? 'VAT-deductible supplier — VAT is split out of the total you enter for the reclaim report.'
            : 'Not VAT-deductible — the whole amount is the cost.'}
        </p>
      )}

      <table className="mt-4 w-full text-sm">
        <thead>
          <tr className="border-b border-gray-200 text-[11px] uppercase tracking-widest text-gray-500 font-body">
            <th className="py-2 text-left">Item</th>
            <th className="py-2 text-right w-28">Qty</th>
            <th className="py-2 text-right w-36">Total cost</th>
            <th className="py-2 text-right w-28">Unit cost</th>
            <th className="w-8" />
          </tr>
        </thead>
        <tbody>
          {lines.map((line, index) => {
            const qty = Number(line.quantity || 0);
            const unit = qty > 0 ? Number(line.entered_total || 0) / qty : 0;
            return (
              <tr key={index} className="border-b border-gray-100">
                <td className="py-2 pr-2">
                  <Select
                    value={line.item_id}
                    onChange={(e) => prefillCost(index, e.target.value)}
                    options={pickable.map((i) => ({
                      value: i.id,
                      label: `${i.sku} — ${i.name} (${i.storage_unit})`,
                    }))}
                    placeholder="Choose item…"
                  />
                </td>
                <td className="py-2 pr-2">
                  <Input
                    type="number"
                    step="0.0001"
                    value={line.quantity}
                    onChange={(e) => updateLine(index, { quantity: e.target.value })}
                  />
                </td>
                <td className="py-2 pr-2">
                  <Input
                    type="number"
                    step="0.01"
                    value={line.entered_total}
                    onChange={(e) => updateLine(index, { entered_total: e.target.value })}
                  />
                </td>
                <td className="py-2 text-right text-gray-500">{formatCurrency(unit)}</td>
                <td className="py-2 text-right">
                  {lines.length > 1 && (
                    <button
                      onClick={() => setLines((prev) => prev.filter((_, i) => i !== index))}
                      className="text-gray-400 hover:text-red-500"
                    >
                      <span className="material-icons text-[16px]">close</span>
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div className="mt-3 flex items-center justify-between">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setLines((prev) => [...prev, { item_id: '', quantity: '1', entered_total: '0' }])}
        >
          Add line
        </Button>
        <div className="text-right">
          {vatDeductible && (
            <p className="text-xs text-gray-500 font-body">
              incl. VAT {formatCurrency(vatTotal)}
            </p>
          )}
          <p className="font-display text-lg text-primary">{formatCurrency(grossTotal)}</p>
        </div>
      </div>

      {error && <p className="mt-3 text-xs text-red-600 font-body">{error}</p>}

      <div className="mt-5 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button onClick={save} loading={saving}>
          Create draft
        </Button>
      </div>
    </Modal>
  );
}

function ReceiveOrder({
  order,
  onClose,
  onSaved,
}: {
  order: PurchaseOrder;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [quantities, setQuantities] = useState<Record<string, string>>(() =>
    Object.fromEntries(order.items.map((i) => [i.id, formatQuantity(i.outstanding_quantity)])),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  async function receive() {
    const lines = order.items
      .map((item) => ({
        purchase_order_item_id: item.id,
        quantity: Number(quantities[item.id] || 0),
      }))
      .filter((l) => l.quantity > 0);

    if (lines.length === 0) {
      setError('Enter at least one received quantity.');
      return;
    }
    setSaving(true);
    setError('');
    try {
      await inventoryApi.receivePurchaseOrder(order.id, lines);
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Receiving failed.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal title={`Receive ${order.reference}`} onClose={onClose} wide>
      <p className="mb-3 text-xs text-gray-500 font-body">
        Receiving moves stock in at the line cost. A short delivery leaves the order partially
        received so the remainder can be taken later.
      </p>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-200 text-[11px] uppercase tracking-widest text-gray-500 font-body">
            <th className="py-2 text-left">Item</th>
            <th className="py-2 text-right">Ordered</th>
            <th className="py-2 text-right">Already received</th>
            <th className="py-2 text-right w-32">Receiving now</th>
          </tr>
        </thead>
        <tbody>
          {order.items.map((item) => (
            <tr key={item.id} className="border-b border-gray-100">
              <td className="py-2">
                <span className="font-medium">{item.item_name}</span>{' '}
                <code className="text-xs text-gray-400">{item.item_sku}</code>
              </td>
              <td className="py-2 text-right">{formatQuantity(item.quantity)}</td>
              <td className="py-2 text-right text-gray-500">{formatQuantity(item.received_quantity)}</td>
              <td className="py-2 pl-2">
                <Input
                  type="number"
                  step="0.0001"
                  value={quantities[item.id] ?? ''}
                  onChange={(e) =>
                    setQuantities((prev) => ({ ...prev, [item.id]: e.target.value }))
                  }
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {error && <p className="mt-3 text-xs text-red-600 font-body">{error}</p>}

      <div className="mt-5 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button onClick={receive} loading={saving}>
          Receive
        </Button>
      </div>
    </Modal>
  );
}
