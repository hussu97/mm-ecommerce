'use client';

import { useCallback, useEffect, useState } from 'react';
import { inventoryApi } from '@/lib/pos-api';
import type { InventoryItem, Supplier, SupplierContact, SupplierItem } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button, Input, Pagination, Spinner, Textarea } from '@/components/ui';
import { DataTable, RowAction } from '@/components/ui/DataTable';
import { Modal, StatusBadge } from '@/components/pos/ResourcePage';

// Only items that are bought (not produced from a recipe) can be supplied. The
// server enforces this too; filtering here keeps the picker honest.
const PURCHASABLE_KINDS = new Set(['raw_material', 'packaging', 'resale_good']);

interface ContactDraft {
  name: string;
  email: string;
  phone: string;
  is_primary: boolean;
}

interface MappingDraft {
  item_id: string;
  supplier_sku: string;
}

export default function SuppliersPage() {
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState<Supplier | null>(null);
  const [creating, setCreating] = useState(false);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, i] = await Promise.all([
        inventoryApi.suppliers({ include_inactive: true }),
        inventoryApi.items(),
      ]);
      setSuppliers(s);
      setItems(i);
      setError('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load suppliers.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const totalPages = Math.max(1, Math.ceil(suppliers.length / perPage));
  const currentPage = Math.min(page, totalPages);
  const pageRows = suppliers.slice((currentPage - 1) * perPage, currentPage * perPage);

  return (
    <div>
      <header className="mb-5 flex items-start justify-between gap-3">
        <div>
          <h1 className="font-display text-xl text-primary tracking-wide">Suppliers</h1>
          <p className="text-xs text-gray-500 font-body mt-1">
            Who you buy from, their contacts, and which items they supply.
          </p>
        </div>
        <Button onClick={() => setCreating(true)}>New Supplier</Button>
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
      ) : suppliers.length === 0 ? (
        <p className="py-16 text-center text-sm text-gray-400 font-body">No suppliers yet.</p>
      ) : (
        <>
          <DataTable<Supplier>
            rows={pageRows}
            rowKey={(s) => s.id}
            actions={(s) => <RowAction onClick={() => setEditing(s)}>Edit</RowAction>}
            columns={[
              { header: 'Name', priority: 'primary', sortable: true, sortAccessor: (s) => s.name, render: (s) => <span className="font-medium">{s.name}</span> },
              {
                header: 'Supplies',
                render: (s) =>
                  s.mapped_items.length === 0 ? (
                    <span className="text-gray-400">—</span>
                  ) : (
                    <span className="text-xs text-gray-600" title={s.mapped_items.map((m) => m.item_name ?? m.item_sku).join(', ')}>
                      {s.mapped_items.slice(0, 3).map((m) => m.item_name ?? m.item_sku).join(', ')}
                      {s.mapped_items.length > 3 ? ` +${s.mapped_items.length - 3}` : ''}
                    </span>
                  ),
              },
              { header: 'Contacts', render: (s) => (s.contacts.length ? `${s.contacts.length}` : '—') },
              { header: 'VAT', render: (s) => (s.is_vat_deductible ? <Badge variant="info">Deductible</Badge> : <span className="text-gray-400">—</span>) },
              { header: 'Terms', sortable: true, sortAccessor: (s) => s.payment_terms_days, render: (s) => `${s.payment_terms_days} days` },
              { header: 'Status', sortable: true, sortAccessor: (s) => (s.is_active && !s.deleted_at ? 'Active' : 'Inactive'), render: (s) => <StatusBadge active={s.is_active && !s.deleted_at} /> },
            ]}
          />
          <Pagination
            page={currentPage}
            pages={totalPages}
            total={suppliers.length}
            perPage={perPage}
            onPageChange={setPage}
            onPerPageChange={(p) => { setPerPage(p); setPage(1); }}
            label="suppliers"
          />
        </>
      )}

      {(creating || editing) && (
        <SupplierModal
          supplier={editing}
          items={items}
          onClose={() => { setCreating(false); setEditing(null); }}
          onSaved={() => { setCreating(false); setEditing(null); void load(); }}
        />
      )}
    </div>
  );
}

function SupplierModal({
  supplier,
  items,
  onClose,
  onSaved,
}: {
  supplier: Supplier | null;
  items: InventoryItem[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(supplier?.name ?? '');
  const [reference, setReference] = useState(supplier?.reference ?? '');
  const [taxNumber, setTaxNumber] = useState(supplier?.tax_number ?? '');
  const [address, setAddress] = useState(supplier?.address ?? '');
  const [paymentTerms, setPaymentTerms] = useState(String(supplier?.payment_terms_days ?? 0));
  const [vatDeductible, setVatDeductible] = useState(supplier?.is_vat_deductible ?? true);
  const [active, setActive] = useState(supplier?.is_active ?? true);
  const [contacts, setContacts] = useState<ContactDraft[]>(
    supplier?.contacts.map((c) => ({
      name: c.name,
      email: c.email ?? '',
      phone: c.phone ?? '',
      is_primary: c.is_primary,
    })) ?? [],
  );
  const [mappings, setMappings] = useState<MappingDraft[]>([]);
  const [mappingsLoaded, setMappingsLoaded] = useState(!supplier);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  // Load the existing item mappings when editing.
  useEffect(() => {
    if (!supplier) return;
    inventoryApi
      .supplierItems(supplier.id)
      .then((rows: SupplierItem[]) =>
        setMappings(
          rows.map((r) => ({
            item_id: r.item_id,
            supplier_sku: r.supplier_sku ?? '',
          })),
        ),
      )
      .catch(() => setMappings([]))
      .finally(() => setMappingsLoaded(true));
  }, [supplier]);

  const purchasableItems = items.filter(
    (i) => i.is_active && !i.deleted_at && PURCHASABLE_KINDS.has(i.kind),
  );

  function updateContact(index: number, patch: Partial<ContactDraft>) {
    setContacts((prev) => prev.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  }
  function updateMapping(index: number, patch: Partial<MappingDraft>) {
    setMappings((prev) => prev.map((m, i) => (i === index ? { ...m, ...patch } : m)));
  }

  async function save() {
    if (!name.trim()) {
      setError('Name is required.');
      return;
    }
    // A contact must be reachable — the server refuses a name-only contact.
    const cleanContacts: SupplierContact[] = [];
    for (const c of contacts) {
      if (!c.name.trim()) continue;
      if (!c.email.trim() && !c.phone.trim()) {
        setError(`Contact "${c.name}" needs an email or a phone.`);
        return;
      }
      cleanContacts.push({
        name: c.name.trim(),
        email: c.email.trim() || null,
        phone: c.phone.trim() || null,
        is_primary: c.is_primary,
      });
    }
    const cleanMappings = mappings.filter((m) => m.item_id);

    setSaving(true);
    setError('');
    try {
      const payload = {
        name: name.trim(),
        reference: reference.trim() || null,
        tax_number: taxNumber.trim() || null,
        address: address.trim() || null,
        payment_terms_days: Number(paymentTerms) || 0,
        is_vat_deductible: vatDeductible,
        is_active: active,
        contacts: cleanContacts,
      };
      const saved = supplier
        ? await inventoryApi.updateSupplier(supplier.id, payload)
        : await inventoryApi.createSupplier(payload);
      await inventoryApi.setSupplierItems(
        saved.id,
        cleanMappings.map((m) => ({
          item_id: m.item_id,
          supplier_sku: m.supplier_sku.trim() || null,
        })),
      );
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Save failed.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal title={supplier ? `Edit ${supplier.name}` : 'New supplier'} onClose={onClose} wide>
      <div className="grid gap-3 sm:grid-cols-2">
        <Input label="Name" value={name} onChange={(e) => setName(e.target.value)} required />
        <Input label="Reference" value={reference} onChange={(e) => setReference(e.target.value)} />
        <Input label="Tax number (TRN)" value={taxNumber} onChange={(e) => setTaxNumber(e.target.value)} />
        <Input label="Payment terms (days)" type="number" value={paymentTerms} onChange={(e) => setPaymentTerms(e.target.value)} />
      </div>
      <Textarea label="Address" className="mt-3" value={address} onChange={(e) => setAddress(e.target.value)} />

      <div className="mt-3 flex flex-wrap gap-5">
        <label className="flex items-center gap-2 text-sm font-body">
          <input type="checkbox" checked={vatDeductible} onChange={(e) => setVatDeductible(e.target.checked)} />
          VAT deductible
        </label>
        <label className="flex items-center gap-2 text-sm font-body">
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
          Active
        </label>
      </div>

      {/* Contacts */}
      <section className="mt-5">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-[11px] uppercase tracking-widest text-gray-500 font-body">Contacts</h3>
          <Button variant="ghost" size="sm" onClick={() => setContacts((p) => [...p, { name: '', email: '', phone: '', is_primary: p.length === 0 }])}>
            Add contact
          </Button>
        </div>
        {contacts.length === 0 && <p className="text-xs text-gray-400 font-body">No contacts. A contact needs an email or a phone.</p>}
        {contacts.map((c, index) => (
          <div key={index} className="mb-2 grid items-end gap-2 sm:grid-cols-[1.2fr_1.4fr_1fr_auto_auto]">
            <Input label={index === 0 ? 'Name' : undefined} value={c.name} onChange={(e) => updateContact(index, { name: e.target.value })} />
            <Input label={index === 0 ? 'Email' : undefined} value={c.email} onChange={(e) => updateContact(index, { email: e.target.value })} />
            <Input label={index === 0 ? 'Phone' : undefined} value={c.phone} onChange={(e) => updateContact(index, { phone: e.target.value })} />
            <label className="flex items-center gap-1 pb-2 text-xs font-body">
              <input type="checkbox" checked={c.is_primary} onChange={(e) => updateContact(index, { is_primary: e.target.checked })} />
              Primary
            </label>
            <button className="pb-2 text-gray-400 hover:text-red-500" onClick={() => setContacts((p) => p.filter((_, i) => i !== index))}>
              <span className="material-icons text-[16px]">close</span>
            </button>
          </div>
        ))}
      </section>

      {/* Item mapping */}
      <section className="mt-5">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-[11px] uppercase tracking-widest text-gray-500 font-body">Supplied items</h3>
          <Button variant="ghost" size="sm" onClick={() => setMappings((p) => [...p, { item_id: '', supplier_sku: '' }])}>
            Add item
          </Button>
        </div>
        {!mappingsLoaded ? (
          <Spinner />
        ) : (
          <>
            {mappings.length === 0 && <p className="text-xs text-gray-400 font-body">No items mapped. Only purchased items (no recipe) can be supplied.</p>}
            {mappings.map((m, index) => (
              <div key={index} className="mb-2 grid items-end gap-2 sm:grid-cols-[2fr_1fr_auto]">
                <label className="text-xs font-body">
                  {index === 0 && <span className="mb-1 block text-gray-500">Item</span>}
                  <select
                    className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
                    value={m.item_id}
                    onChange={(e) => updateMapping(index, { item_id: e.target.value })}
                  >
                    <option value="">Choose item…</option>
                    {purchasableItems.map((i) => (
                      <option key={i.id} value={i.id}>
                        {i.sku} — {i.name} ({i.storage_unit})
                      </option>
                    ))}
                  </select>
                </label>
                <Input label={index === 0 ? 'Supplier SKU' : undefined} value={m.supplier_sku} onChange={(e) => updateMapping(index, { supplier_sku: e.target.value })} />
                <button className="pb-2 text-gray-400 hover:text-red-500" onClick={() => setMappings((p) => p.filter((_, i) => i !== index))}>
                  <span className="material-icons text-[16px]">close</span>
                </button>
              </div>
            ))}
          </>
        )}
      </section>

      {error && <p className="mt-3 text-xs text-red-600 font-body">{error}</p>}

      <div className="mt-5 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose} disabled={saving}>Cancel</Button>
        <Button onClick={save} loading={saving}>{supplier ? 'Save' : 'Create'}</Button>
      </div>
    </Modal>
  );
}
