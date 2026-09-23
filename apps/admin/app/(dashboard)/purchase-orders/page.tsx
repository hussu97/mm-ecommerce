'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { branchesApi, inventoryApi } from '@/lib/pos-api';
import type {
  Branch,
  InventoryItem,
  PurchaseOrder,
  PurchaseOrderItemOption,
  PurchaseOrderStatus,
  Supplier,
  SupplierItem,
} from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button, Input, Pagination, Select, Spinner } from '@/components/ui';
import { DataTable, RowAction } from '@/components/ui/DataTable';
import { InvoicePreview } from '@/components/ui/InvoicePreview';
import { Modal } from '@/components/pos/ResourcePage';
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

interface DraftLine {
  item_id: string;
  quantity: string;
  entered_total: string;
}

interface MiscDraft {
  name: string;
  quantity: string;
  storage_unit: string;
  entered_total: string;
}

const VAT_RATE = 0.05;

const STATUS_OPTIONS: { value: PurchaseOrderStatus; label: string }[] = [
  { value: 'draft', label: 'Draft' },
  { value: 'pending', label: 'Pending' },
  { value: 'approved', label: 'Approved' },
  { value: 'declined', label: 'Declined' },
  { value: 'partially_received', label: 'Partially received' },
  { value: 'closed', label: 'Closed' },
  { value: 'voided', label: 'Voided' },
];

export default function PurchaseOrdersPage() {
  const [orders, setOrders] = useState<PurchaseOrder[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [itemOptions, setItemOptions] = useState<PurchaseOrderItemOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [creating, setCreating] = useState(false);
  const [receiving, setReceiving] = useState<PurchaseOrder | null>(null);
  const [editingInvoice, setEditingInvoice] = useState<PurchaseOrder | null>(null);
  const [preview, setPreview] = useState<{ url: string; title: string } | null>(null);
  const [exporting, setExporting] = useState(false);

  // Filters. Applied server-side so paging and the export always agree with what
  // is on screen; an empty value means "no filter" (buildQs drops it).
  const [statusFilter, setStatusFilter] = useState('');
  const [itemFilter, setItemFilter] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');

  // Client-side paging over the filtered set the server returns.
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);

  const filterParams = useMemo(
    () => ({
      status: statusFilter || undefined,
      item_id: itemFilter || undefined,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
    }),
    [statusFilter, itemFilter, dateFrom, dateTo],
  );

  // Reference data that does not change with a filter — loaded once.
  useEffect(() => {
    let cancelled = false;
    Promise.all([
      inventoryApi.suppliers(),
      branchesApi.list(),
      inventoryApi.items(),
      inventoryApi.purchaseOrderItemOptions(),
    ])
      .then(([s, b, i, opts]) => {
        if (cancelled) return;
        setSuppliers(s);
        setBranches(b);
        setItems(i);
        setItemOptions(opts);
      })
      .catch(() => { /* the orders load surfaces any auth/network error */ });
    return () => { cancelled = true; };
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const o = await inventoryApi.purchaseOrders(filterParams);
      setOrders(o);
      setPage(1);
      setError('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load purchase orders.');
    } finally {
      setLoading(false);
    }
  }, [filterParams]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(id: string, action: 'submit' | 'approve' | 'decline' | 'void') {
    try {
      if (action === 'submit') await inventoryApi.submitPurchaseOrder(id);
      if (action === 'approve') await inventoryApi.approvePurchaseOrder(id);
      if (action === 'decline') await inventoryApi.declinePurchaseOrder(id);
      if (action === 'void') {
        // Voiding reverses received stock and its costing — confirm and capture
        // a reason (the API requires one; it lands on the reversal trail).
        const reason = window.prompt(
          'Void this purchase order? Any received stock and its cost will be reversed.\n\nReason:',
        );
        if (!reason || !reason.trim()) return;
        await inventoryApi.voidPurchaseOrder(id, reason.trim());
      }
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Action failed.');
    }
  }

  // The list rows carry no signed invoice URL (signing every row is a per-row
  // round-trip); fetch the single order, which signs it, then preview.
  async function viewInvoice(po: PurchaseOrder) {
    try {
      const full = await inventoryApi.purchaseOrder(po.id);
      if (full.invoice_url) setPreview({ url: full.invoice_url, title: po.reference });
      else setError('No invoice image is attached.');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load the invoice.');
    }
  }

  async function exportXlsx() {
    setExporting(true);
    setError('');
    try {
      const blob = await inventoryApi.exportPurchaseOrders(filterParams);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'purchase-orders.xlsx';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Export failed.');
    } finally {
      setExporting(false);
    }
  }

  const totalPages = Math.max(1, Math.ceil(orders.length / perPage));
  // Clamp rather than reset, so an approve/decline that shrinks the list never
  // strands the viewer on an empty page past the new end.
  const currentPage = Math.min(page, totalPages);
  const pageRows = orders.slice((currentPage - 1) * perPage, currentPage * perPage);

  return (
    <div>
      {/* The section title lives in the layout, above the tabs. */}
      <header className="mb-5 flex items-start justify-between gap-3">
        <p className="text-xs text-gray-500 font-body">
          Submit → approve → receive. An unapproved order stays editable; approving requires a second person.
        </p>
        <div className="flex items-center gap-2">
          <Button variant="ghost" onClick={exportXlsx} loading={exporting}>Export</Button>
          <Button onClick={() => setCreating(true)}>New Order</Button>
        </div>
      </header>

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="w-40">
          <Input label="From" type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        </div>
        <div className="w-40">
          <Input label="To" type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        </div>
        <div className="w-48">
          <Select
            label="Status"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            options={STATUS_OPTIONS}
            placeholder="All statuses"
          />
        </div>
        <div className="w-64">
          <Select
            label="Item"
            value={itemFilter}
            onChange={(e) => setItemFilter(e.target.value)}
            options={itemOptions.map((o) => ({
              value: o.id,
              label: o.sku ? `${o.sku} — ${o.name}` : o.name,
            }))}
            placeholder="All items"
          />
        </div>
        {(statusFilter || itemFilter || dateFrom || dateTo) && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => { setStatusFilter(''); setItemFilter(''); setDateFrom(''); setDateTo(''); }}
          >
            Clear
          </Button>
        )}
      </div>

      {notice && (
        <div className="mb-4 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {notice}
        </div>
      )}

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
          No purchase orders match these filters.
        </p>
      ) : (
        <>
        <DataTable<PurchaseOrder>
          rows={pageRows}
          rowKey={(po) => po.id}
          actions={(po) => (
            <>
              <RowAction href={`/purchase-orders/${po.id}`}>Details</RowAction>
              {/* Legacy drafts can still be submitted; new POs are born pending. */}
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
              {(po.status === 'approved' ||
                po.status === 'partially_received' ||
                po.status === 'closed') && (
                <RowAction danger onClick={() => act(po.id, 'void')}>
                  Void
                </RowAction>
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
            {
              header: 'Invoice',
              render: (po) => (
                <div className="flex flex-col gap-1.5 text-xs">
                  <span className="truncate max-w-[180px] text-gray-700" title={po.supplier_reference ?? ''}>
                    {po.supplier_reference || 'No reference'}
                  </span>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <RowAction icon="edit" onClick={() => setEditingInvoice(po)}>Edit</RowAction>
                    {po.has_invoice ? (
                      <RowAction icon="visibility" onClick={() => viewInvoice(po)}>View</RowAction>
                    ) : (
                      <span className="text-gray-400">No image</span>
                    )}
                  </div>
                </div>
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
          onSaved={(message) => {
            setCreating(false);
            setNotice(message ?? '');
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

      {editingInvoice && (
        <EditInvoice
          order={editingInvoice}
          onClose={() => setEditingInvoice(null)}
          onSaved={() => {
            setEditingInvoice(null);
            void load();
          }}
        />
      )}

      {preview && (
        <InvoicePreview
          url={preview.url}
          title={preview.title}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  );
}

// Edit the invoice reference and/or (re)attach the invoice image from the list,
// without opening the detail page. Mirrors the detail page's InvoicePanel.
function EditInvoice({
  order,
  onClose,
  onSaved,
}: {
  order: PurchaseOrder;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [reference, setReference] = useState(order.supplier_reference ?? '');
  const [file, setFile] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  async function save() {
    setSaving(true);
    setError('');
    try {
      if (reference.trim() !== (order.supplier_reference ?? '')) {
        await inventoryApi.updatePurchaseOrder(order.id, {
          supplier_reference: reference.trim() || null,
        });
      }
      if (file) await inventoryApi.uploadPurchaseOrderInvoice(order.id, file);
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Save failed.');
      setSaving(false);
    }
  }

  return (
    <Modal title={`Invoice — ${order.reference}`} onClose={onClose}>
      <div className="space-y-4">
        <Input
          label="Invoice / supplier reference"
          value={reference}
          onChange={(e) => setReference(e.target.value)}
          placeholder="Their PO / invoice no."
        />
        <div className="text-xs font-body">
          <span className="mb-1 block text-gray-500">Invoice image</span>
          <div className="flex flex-wrap items-center gap-3">
            <label className="cursor-pointer rounded border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50">
              {file ? 'Change file' : order.has_invoice ? 'Replace' : 'Choose file'}
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp,application/pdf"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="hidden"
              />
            </label>
            {file ? (
              <span className="text-sm text-gray-600">{file.name}</span>
            ) : order.has_invoice ? (
              <span className="text-gray-400">An image is attached — choose a file to replace it.</span>
            ) : (
              <span className="text-gray-400">No image yet — JPEG, PNG, WebP or PDF.</span>
            )}
          </div>
        </div>
        {error && <p className="text-xs text-red-600 font-body">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button onClick={save} loading={saving}>Save</Button>
        </div>
      </div>
    </Modal>
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
  onSaved: (message?: string) => void;
}) {
  const [supplierId, setSupplierId] = useState('');
  // Sharjah is the kitchen that raises almost every PO, so default to it (falling
  // back to the first branch if it isn't in the list).
  const [branchId, setBranchId] = useState(
    () => branches.find((b) => b.name.toLowerCase().includes('sharjah'))?.id ?? branches[0]?.id ?? '',
  );
  const [deliveryDate, setDeliveryDate] = useState('');
  const [supplierReference, setSupplierReference] = useState('');
  const [invoiceFile, setInvoiceFile] = useState<File | null>(null);
  const [lines, setLines] = useState<DraftLine[]>([{ item_id: '', quantity: '1', entered_total: '0' }]);
  const [miscLines, setMiscLines] = useState<MiscDraft[]>([]);
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

  // With flexible item mapping on, any active item may be ordered; otherwise the
  // supplier's mapped items when it has them, falling back to every active item so
  // a PO is never blocked on missing mappings.
  const mappedIds = new Set((supplierItems ?? []).map((r) => r.item_id));
  const activeItems = items.filter((i) => i.is_active && !i.deleted_at);
  const pickable = supplier?.allow_any_item
    ? activeItems
    : (supplierItems && supplierItems.length > 0)
      ? items.filter((i) => mappedIds.has(i.id))
      : activeItems;

  const allowsMisc = supplier?.allows_misc_items ?? false;
  const grossTotal =
    lines.reduce((sum, l) => sum + Number(l.entered_total || 0), 0) +
    miscLines.reduce((sum, l) => sum + Number(l.entered_total || 0), 0);
  const vatTotal = vatDeductible ? grossTotal - grossTotal / (1 + VAT_RATE) : 0;

  function updateLine(index: number, patch: Partial<DraftLine>) {
    setLines((prev) => prev.map((l, i) => (i === index ? { ...l, ...patch } : l)));
  }
  function updateMisc(index: number, patch: Partial<MiscDraft>) {
    setMiscLines((prev) => prev.map((l, i) => (i === index ? { ...l, ...patch } : l)));
  }

  // The unit for a chosen item, shown in its own column so the line reads cleanly.
  function unitFor(itemId: string): string {
    return items.find((i) => i.id === itemId)?.storage_unit ?? '';
  }

  async function save() {
    // A line counts only with an item, a positive quantity AND a positive,
    // numeric total — otherwise it would post a zero-cost FIFO layer on receive.
    const valid = lines.filter(
      (l) => l.item_id && Number(l.quantity) > 0 && Number(l.entered_total) > 0,
    );
    const validMisc = miscLines.filter(
      (l) => l.name.trim() && l.storage_unit.trim() && Number(l.quantity) > 0 && Number(l.entered_total) > 0,
    );
    if (!supplierId || !branchId || (valid.length === 0 && validMisc.length === 0)) {
      setError('Add at least one line (item or misc.) with a quantity and a total cost above zero.');
      return;
    }
    setSaving(true);
    setError('');
    let po;
    try {
      po = await inventoryApi.createPurchaseOrder({
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
        misc_items: validMisc.map((l) => ({
          name: l.name.trim(),
          quantity: Number(l.quantity),
          storage_unit: l.storage_unit.trim(),
          entered_total: Number(l.entered_total),
        })),
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Save failed.');
      setSaving(false);
      return;
    }
    // The PO now exists. An invoice-upload failure must not strand the modal for
    // a retry that would mint a second PO — finish, and tell them to re-attach.
    try {
      if (invoiceFile) {
        await inventoryApi.uploadPurchaseOrderInvoice(po.id, invoiceFile);
      }
      onSaved();
    } catch {
      onSaved(`Purchase order ${po.reference} created, but the invoice upload failed — edit it to re-attach.`);
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
        <div className="text-xs font-body sm:col-span-2">
          <span className="mb-1 block text-gray-500">Invoice image (optional)</span>
          <div className="flex items-center gap-3">
            <label className="cursor-pointer rounded border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50">
              {invoiceFile ? 'Change file' : 'Choose file'}
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp,application/pdf"
                onChange={(e) => setInvoiceFile(e.target.files?.[0] ?? null)}
                className="hidden"
              />
            </label>
            {invoiceFile ? (
              <span className="flex items-center gap-2 text-sm text-gray-600">
                <span className="material-icons text-[16px] text-primary">description</span>
                {invoiceFile.name}
                <button
                  type="button"
                  onClick={() => setInvoiceFile(null)}
                  className="text-gray-400 hover:text-red-500"
                  aria-label="Remove invoice"
                >
                  <span className="material-icons text-[16px]">close</span>
                </button>
              </span>
            ) : (
              <span className="text-gray-400">JPEG, PNG, WebP or PDF</span>
            )}
          </div>
        </div>
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
            <th className="py-2 text-left w-20">Unit</th>
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
              <tr key={index} className={`border-b border-gray-100 ${interactiveRowClass}`}>
                <td className="py-2 pr-2">
                  <Select
                    value={line.item_id}
                    onChange={(e) => updateLine(index, { item_id: e.target.value })}
                    options={pickable.map((i) => ({
                      value: i.id,
                      label: `${i.sku} — ${i.name}`,
                    }))}
                    placeholder="Choose item…"
                  />
                </td>
                <td className="py-2 pr-2 text-gray-500">{unitFor(line.item_id) || '—'}</td>
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
                <td className="py-2 text-right text-gray-500">{formatCost(unit)}</td>
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

      <div className="mt-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setLines((prev) => [...prev, { item_id: '', quantity: '1', entered_total: '0' }])}
        >
          Add item line
        </Button>
      </div>

      {allowsMisc && (
        <section className="mt-5 border-t border-gray-100 pt-4">
          <div className="mb-1 flex items-center justify-between">
            <h3 className="text-[11px] uppercase tracking-widest text-gray-500 font-body">Miscellaneous items</h3>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setMiscLines((prev) => [...prev, { name: '', quantity: '1', storage_unit: '', entered_total: '0' }])}
            >
              Add misc item
            </Button>
          </div>
          <p className="mb-2 text-xs text-gray-400 font-body">
            Non-inventory buys on this invoice — tracked for expenses and VAT only, never added to stock. The name can’t match an existing inventory item.
          </p>
          {miscLines.length > 0 && (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200 text-[11px] uppercase tracking-widest text-gray-500 font-body">
                  <th className="py-2 text-left">Name</th>
                  <th className="py-2 text-left w-24">Unit</th>
                  <th className="py-2 text-right w-24">Qty</th>
                  <th className="py-2 text-right w-36">Total cost</th>
                  <th className="w-8" />
                </tr>
              </thead>
              <tbody>
                {miscLines.map((line, index) => (
                  <tr key={index} className={`border-b border-gray-100 ${interactiveRowClass}`}>
                    <td className="py-2 pr-2">
                      <Input value={line.name} onChange={(e) => updateMisc(index, { name: e.target.value })} placeholder="e.g. Gift wrap" />
                    </td>
                    <td className="py-2 pr-2">
                      <Input value={line.storage_unit} onChange={(e) => updateMisc(index, { storage_unit: e.target.value })} placeholder="e.g. roll" />
                    </td>
                    <td className="py-2 pr-2">
                      <Input type="number" step="0.0001" value={line.quantity} onChange={(e) => updateMisc(index, { quantity: e.target.value })} />
                    </td>
                    <td className="py-2 pr-2">
                      <Input type="number" step="0.01" value={line.entered_total} onChange={(e) => updateMisc(index, { entered_total: e.target.value })} />
                    </td>
                    <td className="py-2 text-right">
                      <button onClick={() => setMiscLines((prev) => prev.filter((_, i) => i !== index))} className="text-gray-400 hover:text-red-500">
                        <span className="material-icons text-[16px]">close</span>
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      <div className="mt-4 flex items-center justify-end border-t border-gray-100 pt-3">
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
          Create order
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
  // Seed each line to what's expected (the full ordered quantity); the staff edit
  // it to what actually arrived, and a discrepant line needs a reason.
  const [quantities, setQuantities] = useState<Record<string, string>>(() =>
    Object.fromEntries(order.items.map((i) => [i.id, formatQuantity(i.quantity)])),
  );
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const variance = (item: PurchaseOrder['items'][number]): number =>
    Number(quantities[item.id] || 0) - Number(item.quantity);
  const differs = (item: PurchaseOrder['items'][number]): boolean =>
    variance(item) !== 0;

  async function receive() {
    // One-shot: every line is sent with what arrived (0 = full no-show), and any
    // line that differs from the ordered quantity must carry a reason.
    const missing = order.items.filter(
      (i) => differs(i) && !(reasons[i.id] ?? '').trim(),
    );
    if (missing.length > 0) {
      setError(
        `A reason is required where received differs from ordered: ${missing
          .map((i) => i.item_name ?? i.item_sku ?? i.item_id)
          .join(', ')}.`,
      );
      return;
    }
    if (order.items.every((i) => Number(quantities[i.id] || 0) <= 0)) {
      setError('Enter at least one received quantity.');
      return;
    }
    setSaving(true);
    setError('');
    try {
      await inventoryApi.receivePurchaseOrder(
        order.id,
        order.items.map((item) => ({
          purchase_order_item_id: item.id,
          quantity: Number(quantities[item.id] || 0),
          variance_reason: differs(item) ? (reasons[item.id] ?? '').trim() : null,
        })),
      );
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
        Receiving is one action and closes the order: enter what actually arrived,
        and whatever is short is recorded against the line. A line that differs from
        what was ordered needs a reason, and the office is emailed the short/excess.
      </p>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-200 text-[11px] uppercase tracking-widest text-gray-500 font-body">
            <th className="py-2 text-left">Item</th>
            <th className="py-2 text-right">To receive</th>
            <th className="py-2 text-right w-32">Received</th>
            <th className="py-2 text-right w-24">Variance</th>
            <th className="py-2 text-left w-56">Reason</th>
          </tr>
        </thead>
        <tbody>
          {order.items.map((item) => {
            const v = variance(item);
            return (
              <tr key={item.id} className={`border-b border-gray-100 ${interactiveRowClass}`}>
                <td className="py-2">
                  <span className="font-medium">{item.item_name}</span>{' '}
                  <code className="text-xs text-gray-400">{item.item_sku}</code>
                </td>
                <td className="py-2 text-right text-gray-500">{formatQuantity(item.quantity)}</td>
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
                <td className={`py-2 text-right tabular-nums ${v === 0 ? 'text-gray-300' : v < 0 ? 'text-red-600 font-medium' : 'text-amber-600 font-medium'}`}>
                  {v === 0 ? '—' : `${v > 0 ? '+' : ''}${formatQuantity(v)}`}
                </td>
                <td className="py-2 pl-2">
                  {differs(item) ? (
                    <Input
                      value={reasons[item.id] ?? ''}
                      onChange={(e) =>
                        setReasons((prev) => ({ ...prev, [item.id]: e.target.value }))
                      }
                      placeholder="Why short / over?"
                    />
                  ) : (
                    <span className="text-gray-300 text-xs">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {error && <p className="mt-3 text-xs text-red-600 font-body">{error}</p>}

      <div className="mt-5 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button onClick={receive} loading={saving}>
          Receive &amp; close
        </Button>
      </div>
    </Modal>
  );
}
