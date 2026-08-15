'use client';

import { useCallback, useEffect, useState } from 'react';
import { branchesApi, inventoryApi } from '@/lib/pos-api';
import type {
  Branch,
  InventoryItem,
  PurchaseOrder,
  PurchaseOrderStatus,
  Supplier,
} from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button, Input, Select, Spinner } from '@/components/ui';
import { Modal } from '@/components/pos/ResourcePage';
import { formatCurrency } from '@/lib/utils';

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
  unit_cost: string;
}

export default function PurchaseOrdersPage() {
  const [orders, setOrders] = useState<PurchaseOrder[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [receiving, setReceiving] = useState<PurchaseOrder | null>(null);

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

  return (
    <div className="p-6 max-w-[1400px]">
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
        <div className="overflow-x-auto rounded border border-gray-200 bg-white">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 bg-gray-50 text-[11px] uppercase tracking-widest text-gray-500 font-body">
                <th className="px-3 py-2 text-left">Reference</th>
                <th className="px-3 py-2 text-left">Supplier</th>
                <th className="px-3 py-2 text-right">Lines</th>
                <th className="px-3 py-2 text-right">Total</th>
                <th className="px-3 py-2 text-left">Delivery</th>
                <th className="px-3 py-2 text-left">Status</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {orders.map((po) => (
                <tr key={po.id} className="border-b border-gray-100 last:border-0 hover:bg-gray-50">
                  <td className="px-3 py-2">
                    <code className="text-xs text-gray-600">{po.reference}</code>
                  </td>
                  <td className="px-3 py-2 font-medium">{po.supplier_name ?? '—'}</td>
                  <td className="px-3 py-2 text-right">{po.items.length}</td>
                  <td className="px-3 py-2 text-right">{formatCurrency(po.total_cost)}</td>
                  <td className="px-3 py-2 text-xs text-gray-500">{po.delivery_date ?? '—'}</td>
                  <td className="px-3 py-2">
                    <Badge variant={STATUS_VARIANT[po.status]}>
                      {po.status.replace(/_/g, ' ')}
                    </Badge>
                  </td>
                  <td className="px-3 py-2 text-right whitespace-nowrap">
                    <div className="flex justify-end gap-2">
                      {po.status === 'draft' && (
                        <button
                          onClick={() => act(po.id, 'submit')}
                          className="text-xs text-primary hover:underline font-body"
                        >
                          Submit
                        </button>
                      )}
                      {po.status === 'pending' && (
                        <>
                          <button
                            onClick={() => act(po.id, 'approve')}
                            className="text-xs text-primary hover:underline font-body"
                          >
                            Approve
                          </button>
                          <button
                            onClick={() => act(po.id, 'decline')}
                            className="text-xs text-red-500 hover:underline font-body"
                          >
                            Decline
                          </button>
                        </>
                      )}
                      {(po.status === 'approved' || po.status === 'partially_received') && (
                        <button
                          onClick={() => setReceiving(po)}
                          className="text-xs text-primary hover:underline font-body"
                        >
                          Receive
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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
  const [lines, setLines] = useState<DraftLine[]>([{ item_id: '', quantity: '1', unit_cost: '0' }]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const total = lines.reduce(
    (sum, l) => sum + Number(l.quantity || 0) * Number(l.unit_cost || 0),
    0,
  );

  function updateLine(index: number, patch: Partial<DraftLine>) {
    setLines((prev) => prev.map((l, i) => (i === index ? { ...l, ...patch } : l)));
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
      await inventoryApi.createPurchaseOrder({
        supplier_id: supplierId,
        branch_id: branchId,
        delivery_date: deliveryDate || null,
        items: valid.map((l) => ({
          item_id: l.item_id,
          quantity: Number(l.quantity),
          unit_cost: Number(l.unit_cost),
          unit: 'storage',
        })),
      });
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
      </div>

      <table className="mt-4 w-full text-sm">
        <thead>
          <tr className="border-b border-gray-200 text-[11px] uppercase tracking-widest text-gray-500 font-body">
            <th className="py-2 text-left">Item</th>
            <th className="py-2 text-right w-28">Qty</th>
            <th className="py-2 text-right w-32">Unit cost</th>
            <th className="py-2 text-right w-28">Total</th>
            <th className="w-8" />
          </tr>
        </thead>
        <tbody>
          {lines.map((line, index) => (
            <tr key={index} className="border-b border-gray-100">
              <td className="py-2 pr-2">
                <Select
                  value={line.item_id}
                  onChange={(e) => updateLine(index, { item_id: e.target.value })}
                  options={items.map((i) => ({
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
                  step="0.000001"
                  value={line.unit_cost}
                  onChange={(e) => updateLine(index, { unit_cost: e.target.value })}
                />
              </td>
              <td className="py-2 text-right">
                {formatCurrency(Number(line.quantity || 0) * Number(line.unit_cost || 0))}
              </td>
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
          ))}
        </tbody>
      </table>

      <div className="mt-3 flex items-center justify-between">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setLines((prev) => [...prev, { item_id: '', quantity: '1', unit_cost: '0' }])}
        >
          Add line
        </Button>
        <p className="font-display text-lg text-primary">{formatCurrency(total)}</p>
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
    Object.fromEntries(order.items.map((i) => [i.id, String(i.outstanding_quantity)])),
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
              <td className="py-2 text-right">{Number(item.quantity)}</td>
              <td className="py-2 text-right text-gray-500">{Number(item.received_quantity)}</td>
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
