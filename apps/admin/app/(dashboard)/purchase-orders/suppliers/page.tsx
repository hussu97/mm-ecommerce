'use client';

import { useCallback, useEffect, useState } from 'react';
import { inventoryApi } from '@/lib/pos-api';
import type {
  InventoryItem,
  Supplier,
  SupplierContactInput,
  SupplierDocumentKind,
  SupplierItem,
  TradeLicenseAuthority,
  TradeLicenseAuthorityOption,
} from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button, Input, Pagination, Select, Spinner, TabBar, Textarea } from '@/components/ui';
import { DataTable, RowAction } from '@/components/ui/DataTable';
import { Modal, StatusBadge } from '@/components/pos/ResourcePage';
import { useConfirm, useToast } from '@/components/ui/feedback';
import { useAuth } from '@/lib/auth-context';

// Only items that are bought (not produced from a recipe) can be supplied. The
// server enforces this too; filtering here keeps the picker honest.
const PURCHASABLE_KINDS = new Set(['raw_material', 'packaging', 'resale_good']);

interface ContactDraft {
  name: string;
  email: string;
  phone: string;
  is_primary: boolean;
}

// What the form holds for one registration document: a file picked to upload on
// save, or a request to remove the one already stored.
interface DocumentDraft {
  file: File | null;
  remove: boolean;
}

const DOCUMENTS: { kind: SupplierDocumentKind; label: string; has: (s: Supplier) => boolean }[] = [
  { kind: 'trn_certificate', label: 'VAT (TRN) certificate', has: (s) => s.has_trn_certificate ?? false },
  { kind: 'trade_license', label: 'Trade licence', has: (s) => s.has_trade_license ?? false },
];

interface MappingDraft {
  item_id: string;
  supplier_sku: string;
}

export default function SuppliersPage() {
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [authorities, setAuthorities] = useState<TradeLicenseAuthorityOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState<Supplier | null>(null);
  const [creating, setCreating] = useState(false);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);
  const [activeTab, setActiveTab] = useState<'active' | 'inactive'>('active');
  const confirm = useConfirm();
  const toast = useToast();
  const { user } = useAuth();
  // The list is open to anyone who can see the screen, but every supplier write
  // (create, edit, deactivate) needs `inventory.manage` on the API — so a viewer
  // without it is not offered buttons that would only 403.
  const canManage = !!user && (user.is_superadmin || user.permissions.includes('inventory.manage'));

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, i, a] = await Promise.all([
        inventoryApi.suppliers({ include_inactive: true }),
        inventoryApi.items(),
        inventoryApi.tradeLicenseAuthorities(),
      ]);
      setSuppliers(s);
      setItems(i);
      setAuthorities(a);
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

  // The API never returns a deleted supplier; the two tabs split the rest by
  // the is_active flag (deactivation only flips that).
  const activeCount = suppliers.filter((s) => s.is_active).length;
  const inactiveCount = suppliers.length - activeCount;
  const tabRows = suppliers.filter((s) => (activeTab === 'active' ? s.is_active : !s.is_active));

  const totalPages = Math.max(1, Math.ceil(tabRows.length / perPage));
  const currentPage = Math.min(page, totalPages);
  const pageRows = tabRows.slice((currentPage - 1) * perPage, currentPage * perPage);

  async function deactivate(s: Supplier) {
    if (
      !(await confirm({
        title: 'Deactivate supplier',
        message: `Move ${s.name} to the inactive list? Purchase-order history is kept unchanged. You can only deactivate a supplier with no active item mappings.`,
        confirmLabel: 'Deactivate',
      }))
    )
      return;
    try {
      await inventoryApi.deactivateSupplier(s.id);
      toast.success(`${s.name} deactivated.`);
      void load();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not deactivate the supplier.');
    }
  }

  async function reactivate(s: Supplier) {
    try {
      await inventoryApi.reactivateSupplier(s.id);
      toast.success(`${s.name} reactivated.`);
      void load();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not reactivate the supplier.');
    }
  }

  return (
    <div>
      {/* The section title lives in the layout, above the tabs. */}
      <header className="mb-5 flex items-start justify-between gap-3">
        <p className="text-xs text-gray-500 font-body">
          Who you buy from, their contacts, and which items they supply.
        </p>
        {canManage && <Button onClick={() => setCreating(true)}>New Supplier</Button>}
      </header>

      {error && (
        <div className="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          {error}
        </div>
      )}

      <TabBar
        tabs={[
          { key: 'active', label: 'Active', count: activeCount },
          { key: 'inactive', label: 'Inactive', count: inactiveCount },
        ]}
        active={activeTab}
        onChange={(key) => { setActiveTab(key as 'active' | 'inactive'); setPage(1); }}
      />

      {loading ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : tabRows.length === 0 ? (
        <p className="py-16 text-center text-sm text-gray-400 font-body">
          {activeTab === 'active' ? 'No active suppliers.' : 'No inactive suppliers.'}
        </p>
      ) : (
        <>
          <DataTable<Supplier>
            rows={pageRows}
            rowKey={(s) => s.id}
            actions={canManage ? (s) => (
              <>
                <RowAction onClick={() => setEditing(s)}>Edit</RowAction>
                {s.is_active ? (
                  <RowAction onClick={() => deactivate(s)}>Deactivate</RowAction>
                ) : (
                  <RowAction onClick={() => reactivate(s)}>Reactivate</RowAction>
                )}
              </>
            ) : undefined}
            columns={[
              { header: 'Name', priority: 'primary', sortable: true, sortAccessor: (s) => s.name, render: (s) => <span className="font-medium">{s.name}</span> },
              {
                header: 'Supplies',
                className: 'max-w-xs align-top',
                render: (s) =>
                  s.mapped_items.length === 0 ? (
                    <span className="text-gray-400">—</span>
                  ) : (
                    <span className="block whitespace-normal break-words text-xs text-gray-600">
                      {s.mapped_items.map((m) => m.item_name ?? m.item_sku).join(', ')}
                    </span>
                  ),
              },
              { header: 'Contacts', render: (s) => (s.contacts.length ? `${s.contacts.length}` : '—') },
              { header: 'VAT', render: (s) => (s.is_vat_deductible ? <Badge variant="info">Deductible</Badge> : <span className="text-gray-400">—</span>) },
              { header: 'Flexible items', render: (s) => (s.allow_any_item ? <Badge variant="info">Any item</Badge> : <span className="text-gray-400">—</span>) },
              { header: 'Misc. items', render: (s) => (s.allows_misc_items ? <Badge variant="info">Allowed</Badge> : <span className="text-gray-400">—</span>) },
              {
                header: 'Documents',
                render: (s) => {
                  const held = DOCUMENTS.filter((d) => d.has(s));
                  return held.length === 0 ? (
                    <span className="text-gray-400">—</span>
                  ) : (
                    <span className="flex flex-wrap gap-1">
                      {held.map((d) => (
                        <Badge key={d.kind} variant="info">{d.kind === 'trn_certificate' ? 'TRN' : 'Licence'}</Badge>
                      ))}
                    </span>
                  );
                },
              },
              { header: 'Status', sortable: true, sortAccessor: (s) => (s.is_active ? 'Active' : 'Inactive'), render: (s) => <StatusBadge active={s.is_active} /> },
            ]}
          />
          <Pagination
            page={currentPage}
            pages={totalPages}
            total={tabRows.length}
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
          authorities={authorities}
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
  authorities,
  onClose,
  onSaved,
}: {
  supplier: Supplier | null;
  items: InventoryItem[];
  authorities: TradeLicenseAuthorityOption[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(supplier?.name ?? '');
  const [reference, setReference] = useState(supplier?.reference ?? '');
  const [taxNumber, setTaxNumber] = useState(supplier?.tax_number ?? '');
  const [licenseNumber, setLicenseNumber] = useState(supplier?.trade_license_number ?? '');
  const [licenseAuthority, setLicenseAuthority] = useState<TradeLicenseAuthority | ''>(
    supplier?.trade_license_authority ?? '',
  );
  const [documents, setDocuments] = useState<Record<SupplierDocumentKind, DocumentDraft>>({
    trn_certificate: { file: null, remove: false },
    trade_license: { file: null, remove: false },
  });
  const [address, setAddress] = useState(supplier?.address ?? '');
  const [paymentTerms, setPaymentTerms] = useState(String(supplier?.payment_terms_days ?? 0));
  const [vatDeductible, setVatDeductible] = useState(supplier?.is_vat_deductible ?? true);
  const [allowAnyItem, setAllowAnyItem] = useState(supplier?.allow_any_item ?? false);
  const [allowsMiscItems, setAllowsMiscItems] = useState(supplier?.allows_misc_items ?? false);
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
  function updateDocument(kind: SupplierDocumentKind, patch: Partial<DocumentDraft>) {
    setDocuments((prev) => ({ ...prev, [kind]: { ...prev[kind], ...patch } }));
  }

  async function viewDocument(kind: SupplierDocumentKind) {
    if (!supplier) return;
    // Open the tab synchronously so the popup blocker allows it, then point it
    // at the signed URL once the API returns one.
    const tab = window.open('', '_blank');
    try {
      const res = await inventoryApi.supplierDocumentUrl(supplier.id, kind);
      if (tab) tab.location.href = res.url;
      else window.location.href = res.url;
    } catch (err) {
      tab?.close();
      setError(err instanceof ApiError ? err.message : 'Could not open the document.');
    }
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
    const cleanContacts: SupplierContactInput[] = [];
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
        trade_license_number: licenseNumber.trim() || null,
        trade_license_authority: licenseAuthority || null,
        address: address.trim() || null,
        payment_terms_days: Number(paymentTerms) || 0,
        is_vat_deductible: vatDeductible,
        allow_any_item: allowAnyItem,
        allows_misc_items: allowsMiscItems,
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
      // Documents go up once the supplier exists (a new one has no id before).
      for (const { kind } of DOCUMENTS) {
        const draft = documents[kind];
        if (draft.file) {
          await inventoryApi.uploadSupplierDocument(saved.id, kind, draft.file);
        } else if (draft.remove) {
          await inventoryApi.removeSupplierDocument(saved.id, kind);
        }
      }
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
        <Input label="Trade licence number" value={licenseNumber} onChange={(e) => setLicenseNumber(e.target.value)} />
        <Select
          label="Trade licence authority"
          value={licenseAuthority}
          onChange={(e) => setLicenseAuthority(e.target.value as TradeLicenseAuthority | '')}
          placeholder="Choose authority…"
          options={authorities.map((a) => ({ value: a.code, label: a.label }))}
        />
      </div>

      {/* Registration documents — private, signed on view */}
      <section className="mt-4 grid gap-3 sm:grid-cols-2">
        {DOCUMENTS.map(({ kind, label, has }) => {
          const draft = documents[kind];
          const stored = !!supplier && has(supplier) && !draft.remove;
          return (
            <div key={kind} className="text-xs font-body">
              <span className="mb-1 block font-medium uppercase tracking-wider text-gray-600">{label}</span>
              <div className="flex flex-wrap items-center gap-3">
                {stored && !draft.file && (
                  <>
                    <button type="button" className="text-sm text-primary hover:underline" onClick={() => viewDocument(kind)}>
                      View current
                    </button>
                    <button type="button" className="text-sm text-gray-500 hover:text-red-600" onClick={() => updateDocument(kind, { remove: true })}>
                      Remove
                    </button>
                  </>
                )}
                <label className="cursor-pointer rounded border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50">
                  {draft.file ? 'Change file' : stored ? 'Replace' : 'Choose file'}
                  <input
                    type="file"
                    accept="image/jpeg,image/png,image/webp,application/pdf"
                    className="hidden"
                    onChange={(e) => updateDocument(kind, { file: e.target.files?.[0] ?? null, remove: false })}
                  />
                </label>
                {draft.file ? (
                  <span className="text-sm text-gray-600">{draft.file.name}</span>
                ) : draft.remove ? (
                  <span className="text-gray-500">
                    Will be removed on save ·{' '}
                    <button type="button" className="text-primary hover:underline" onClick={() => updateDocument(kind, { remove: false })}>
                      Undo
                    </button>
                  </span>
                ) : (
                  !stored && <span className="text-gray-400">JPEG, PNG, WebP or PDF · max 10 MB</span>
                )}
              </div>
            </div>
          );
        })}
      </section>
      <Textarea label="Address" className="mt-3" value={address} onChange={(e) => setAddress(e.target.value)} />

      <div className="mt-3 flex flex-wrap gap-5">
        <label className="flex items-center gap-2 text-sm font-body">
          <input type="checkbox" checked={vatDeductible} onChange={(e) => setVatDeductible(e.target.checked)} />
          VAT deductible
        </label>
        <label className="flex items-center gap-2 text-sm font-body" title="Let a purchase order add any active item, not just the mapped ones below.">
          <input type="checkbox" checked={allowAnyItem} onChange={(e) => setAllowAnyItem(e.target.checked)} />
          Flexible item mapping
        </label>
        <label className="flex items-center gap-2 text-sm font-body" title="Allow free-text miscellaneous (non-inventory) lines on this supplier's purchase orders — e.g. one-off buys tracked only for expenses and VAT. The supplier appears in the PO picker even with no mapped items.">
          <input type="checkbox" checked={allowsMiscItems} onChange={(e) => setAllowsMiscItems(e.target.checked)} />
          Allows misc. items
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
