'use client';

import { useCallback, useEffect, useState } from 'react';
import { branchesApi, posOrdersApi } from '@/lib/pos-api';
import type { Branch, PosOrder, PosOrderStatus } from '@/lib/pos-types';
import { ORDER_TYPE_LABELS } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Input, Select, Spinner } from '@/components/ui';
import { Modal, money } from '@/components/pos/ResourcePage';

const STATUS_VARIANT: Record<PosOrderStatus, 'success' | 'info' | 'warning' | 'danger' | 'neutral'> = {
  closed: 'success',
  active: 'info',
  pending: 'warning',
  draft: 'neutral',
  declined: 'danger',
  void: 'danger',
  returned: 'warning',
  joined: 'neutral',
};

export default function PosOrdersPage() {
  const [branches, setBranches] = useState<Branch[]>([]);
  const [branchId, setBranchId] = useState('');
  const [status, setStatus] = useState('');
  const [businessDate, setBusinessDate] = useState('');
  const [orders, setOrders] = useState<PosOrder[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<PosOrder | null>(null);

  useEffect(() => {
    void branchesApi.list().then(setBranches).catch(() => setBranches([]));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await posOrdersApi.list({
        branch_id: branchId || undefined,
        pos_status: status || undefined,
        business_date: businessDate || undefined,
        limit: 200,
      });
      setOrders(rows);
      setError('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load orders.');
    } finally {
      setLoading(false);
    }
  }, [branchId, status, businessDate]);

  useEffect(() => {
    void load();
  }, [load]);

  const branchName = (id: string | null) =>
    branches.find((b) => b.id === id)?.name ?? '—';

  return (
    <div className="p-6 max-w-[1400px]">
      <header className="mb-5">
        <h1 className="font-display text-xl text-primary tracking-wide">POS Orders</h1>
        <p className="text-xs text-gray-500 font-body mt-1">
          Checks rung through the terminals. Open checks are still on the floor.
        </p>
      </header>

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <Select
          label="Branch"
          value={branchId}
          onChange={(e) => setBranchId(e.target.value)}
          options={branches.map((b) => ({ value: b.id, label: b.name }))}
          placeholder="All branches"
          className="w-52"
        />
        <Select
          label="Status"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          options={[
            { value: 'active', label: 'Open' },
            { value: 'closed', label: 'Closed' },
            { value: 'void', label: 'Void' },
            { value: 'draft', label: 'Parked' },
          ]}
          placeholder="Any status"
          className="w-44"
        />
        <Input
          label="Trading day"
          type="date"
          value={businessDate}
          onChange={(e) => setBusinessDate(e.target.value)}
          className="w-44"
        />
      </div>

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
          No POS orders for this filter.
        </p>
      ) : (
        <div className="overflow-x-auto rounded border border-gray-200 bg-white">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 bg-gray-50 text-[11px] uppercase tracking-widest text-gray-500 font-body">
                <th className="px-3 py-2 text-left">Check</th>
                <th className="px-3 py-2 text-left">Order</th>
                <th className="px-3 py-2 text-left">Branch</th>
                <th className="px-3 py-2 text-left">Type</th>
                <th className="px-3 py-2 text-left">Customer</th>
                <th className="px-3 py-2 text-right">Items</th>
                <th className="px-3 py-2 text-right">Total</th>
                <th className="px-3 py-2 text-right">Due</th>
                <th className="px-3 py-2 text-left">Status</th>
                <th className="px-3 py-2 text-left">Day</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr
                  key={o.id}
                  onClick={() => setSelected(o)}
                  className="cursor-pointer border-b border-gray-100 last:border-0 hover:bg-gray-50"
                >
                  <td className="px-3 py-2 font-medium">#{o.check_number ?? '—'}</td>
                  <td className="px-3 py-2">
                    <code className="text-xs text-gray-500">{o.order_number}</code>
                  </td>
                  <td className="px-3 py-2">{branchName(o.branch_id)}</td>
                  <td className="px-3 py-2">
                    {o.order_type ? ORDER_TYPE_LABELS[o.order_type] : '—'}
                  </td>
                  <td className="px-3 py-2">{o.customer_name ?? '—'}</td>
                  <td className="px-3 py-2 text-right">
                    {o.items.reduce((n, i) => n + i.quantity, 0)}
                  </td>
                  <td className="px-3 py-2 text-right font-medium">{money(o.total)}</td>
                  <td className="px-3 py-2 text-right">
                    {Number(o.balance_due) > 0 ? (
                      <span className="text-red-600">{money(o.balance_due)}</span>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {o.pos_status && (
                      <Badge variant={STATUS_VARIANT[o.pos_status]}>{o.pos_status}</Badge>
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs text-gray-500">{o.business_date}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selected && <OrderDetail order={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

function OrderDetail({ order, onClose }: { order: PosOrder; onClose: () => void }) {
  return (
    <Modal title={`Check #${order.check_number ?? ''} — ${order.order_number}`} onClose={onClose} wide>
      <div className="mb-4 grid grid-cols-2 gap-3 text-sm font-body sm:grid-cols-4">
        <Field label="Type" value={order.order_type ? ORDER_TYPE_LABELS[order.order_type] : '—'} />
        <Field label="Status" value={order.pos_status ?? '—'} />
        <Field label="Trading day" value={order.business_date ?? '—'} />
        <Field label="Guests" value={String(order.guests)} />
        <Field label="Customer" value={order.customer_name ?? '—'} />
        <Field label="Phone" value={order.customer_phone ?? '—'} />
        <Field
          label="Opened"
          value={order.opened_at ? new Date(order.opened_at).toLocaleString() : '—'}
        />
        <Field
          label="Closed"
          value={order.closed_at ? new Date(order.closed_at).toLocaleString() : '—'}
        />
      </div>

      <table className="mb-4 w-full text-sm">
        <thead>
          <tr className="border-b border-gray-200 text-[11px] uppercase tracking-widest text-gray-500 font-body">
            <th className="py-2 text-left">Item</th>
            <th className="py-2 text-right">Qty</th>
            <th className="py-2 text-right">Unit</th>
            <th className="py-2 text-right">Discount</th>
            <th className="py-2 text-right">Total</th>
          </tr>
        </thead>
        <tbody>
          {order.items.map((item) => (
            <tr key={item.id} className="border-b border-gray-100 last:border-0">
              <td className="py-2">
                {item.product_name}
                {item.returned_quantity > 0 && (
                  <span className="ml-2 text-xs text-amber-600">
                    {item.returned_quantity} returned
                  </span>
                )}
              </td>
              <td className="py-2 text-right">{item.quantity}</td>
              <td className="py-2 text-right">{money(item.unit_price)}</td>
              <td className="py-2 text-right text-gray-500">
                {Number(item.discount_amount) > 0 ? `-${money(item.discount_amount)}` : '—'}
              </td>
              <td className="py-2 text-right">{money(item.total_price)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="ml-auto max-w-xs space-y-1 text-sm font-body">
        <Total label="Subtotal" value={order.subtotal} />
        {Number(order.discount_amount) > 0 && (
          <Total label="Discount" value={-Number(order.discount_amount)} />
        )}
        {Number(order.charges_amount) > 0 && (
          <Total label="Charges" value={order.charges_amount} />
        )}
        <Total label="Excl. VAT" value={order.total_excl_vat} />
        <Total label="VAT" value={order.vat_amount} />
        {Number(order.rounding_amount) !== 0 && (
          <Total label="Rounding" value={order.rounding_amount} />
        )}
        <div className="border-t border-gray-200 pt-1">
          <Total label="Total" value={order.total} bold />
        </div>
        <Total label="Paid" value={order.amount_paid} />
        {Number(order.balance_due) > 0 && <Total label="Due" value={order.balance_due} bold />}
      </div>
    </Modal>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-widest text-gray-400">{label}</p>
      <p className="capitalize">{value}</p>
    </div>
  );
}

function Total({ label, value, bold }: { label: string; value: number | string; bold?: boolean }) {
  return (
    <div className={`flex justify-between ${bold ? 'font-semibold text-primary' : ''}`}>
      <span>{label}</span>
      <span>{money(value)}</span>
    </div>
  );
}
