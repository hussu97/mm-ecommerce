'use client';

import { useEffect, useState } from 'react';
import { promoApi, bulkApi, ApiError } from '@/lib/api';
import type { PromoCode } from '@/lib/types';
import { Button, Input, Pagination, Select, TabBar, LoadError} from '@/components/ui';
import { formatCurrency, formatDate } from '@/lib/utils';

const DISCOUNT_TYPE_OPTIONS = [
  { value: 'percentage', label: 'Percentage (%)' },
  { value: 'fixed', label: 'Fixed Amount (AED)' },
];

interface FormState {
  code: string;
  code_ar: string;
  discount_type: 'percentage' | 'fixed';
  discount_value: string;
  min_order_amount: string;
  max_discount_amount: string;
  first_orders_limit: string;
  requires_phone_verification: boolean;
  max_uses: string;
  max_uses_per_user: string;
  valid_from: string;
  valid_until: string;
}

const EMPTY_FORM: FormState = {
  code: '',
  code_ar: '',
  discount_type: 'percentage',
  discount_value: '',
  min_order_amount: '',
  max_discount_amount: '',
  first_orders_limit: '',
  requires_phone_verification: false,
  max_uses: '',
  max_uses_per_user: '',
  valid_from: '',
  valid_until: '',
};

export default function PromoCodesPage() {
  const [codes, setCodes] = useState<PromoCode[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [activeTab, setActiveTab] = useState<'active' | 'inactive'>('active');
  const [showForm, setShowForm] = useState(false);
  const [editingCode, setEditingCode] = useState<PromoCode | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');
  const [actionCode, setActionCode] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulking, setBulking] = useState(false);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    setLoadError('');
    try {
      const res = await promoApi.list(true);
      setCodes(res);
    } catch (err) {
      setLoadError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  const filteredCodes = codes.filter(c =>
    activeTab === 'active' ? c.is_active : !c.is_active
  );
  const codePages = Math.max(1, Math.ceil(filteredCodes.length / perPage));
  const paginatedCodes = filteredCodes.slice((page - 1) * perPage, page * perPage);

  function openCreate() {
    setEditingCode(null);
    setForm(EMPTY_FORM);
    setFormError('');
    setShowForm(true);
  }

  function openEdit(promo: PromoCode) {
    setEditingCode(promo);
    setForm({
      code: promo.code,
      code_ar: promo.code_ar ?? '',
      discount_type: promo.discount_type,
      discount_value: String(promo.discount_value),
      min_order_amount: promo.min_order_amount != null ? String(promo.min_order_amount) : '',
      max_discount_amount: promo.max_discount_amount != null ? String(promo.max_discount_amount) : '',
      first_orders_limit: promo.first_orders_limit != null ? String(promo.first_orders_limit) : '',
      requires_phone_verification: Boolean(promo.requires_phone_verification),
      max_uses: promo.max_uses != null ? String(promo.max_uses) : '',
      max_uses_per_user: promo.max_uses_per_user != null ? String(promo.max_uses_per_user) : '',
      valid_from: promo.valid_from ? promo.valid_from.slice(0, 10) : '',
      valid_until: promo.valid_until ? promo.valid_until.slice(0, 10) : '',
    });
    setFormError('');
    setShowForm(true);
  }

  function closeForm() {
    setShowForm(false);
    setEditingCode(null);
    setForm(EMPTY_FORM);
    setFormError('');
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.code.trim() || !form.discount_value) {
      setFormError('Code and discount value are required.');
      return;
    }
    setSaving(true);
    setFormError('');
    const payload = {
      code: form.code.trim().toUpperCase(),
      code_ar: form.code_ar.trim() || null,
      discount_type: form.discount_type,
      discount_value: Number(form.discount_value),
      min_order_amount: form.min_order_amount ? Number(form.min_order_amount) : null,
      // A cap on a fixed amount is meaningless — never send one, even if the
      // field was filled in before the type was switched.
      max_discount_amount: form.discount_type === 'percentage' && form.max_discount_amount
        ? Number(form.max_discount_amount)
        : null,
      first_orders_limit: form.first_orders_limit ? Number(form.first_orders_limit) : null,
      requires_phone_verification: form.requires_phone_verification,
      max_uses: form.max_uses ? Number(form.max_uses) : null,
      max_uses_per_user: form.max_uses_per_user ? Number(form.max_uses_per_user) : null,
      valid_from: form.valid_from || null,
      valid_until: form.valid_until || null,
    };
    try {
      if (editingCode) {
        const updated = await promoApi.update(editingCode.code, payload);
        setCodes(prev => prev.map(c => c.code === editingCode.code ? updated : c));
      } else {
        const created = await promoApi.create(payload);
        setCodes(prev => [created, ...prev]);
      }
      closeForm();
    } catch (err) {
      setFormError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  function toggleSelect(id: string) {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    const allOnPage = paginatedCodes.every(c => selectedIds.has(c.id));
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (allOnPage) {
        paginatedCodes.forEach(c => next.delete(c.id));
      } else {
        paginatedCodes.forEach(c => next.add(c.id));
      }
      return next;
    });
  }

  async function handleBulkStatus(is_active: boolean) {
    setBulking(true);
    try {
      await bulkApi.updateStatus('promo-codes', Array.from(selectedIds), is_active);
      await load();
      setSelectedIds(new Set());
      setPage(1);
    } catch (err) {
      alert(err instanceof ApiError ? err.message : 'Bulk action failed.');
    } finally {
      setBulking(false);
    }
  }

  async function handleDeactivate(code: string) {
    if (!confirm(`Deactivate promo code "${code}"? It will move to the Inactive tab.`)) return;
    setActionCode(code);
    try {
      await promoApi.delete(code);
      setCodes(prev => prev.map(c => c.code === code ? { ...c, is_active: false } : c));
    } catch (err) {
      alert((err as Error).message);
    } finally {
      setActionCode(null);
    }
  }

  async function handleRestore(code: string) {
    setActionCode(code);
    try {
      const updated = await promoApi.update(code, { is_active: true });
      setCodes(prev => prev.map(c => c.code === code ? updated : c));
    } catch (err) {
      alert((err as Error).message);
    } finally {
      setActionCode(null);
    }
  }

  const activeCount = codes.filter(c => c.is_active).length;
  const inactiveCount = codes.filter(c => !c.is_active).length;

  return (
    <div>
      <LoadError message={loadError} onRetry={load} />
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-display text-2xl text-gray-800">Promo Codes</h1>
          <p className="text-xs text-gray-400 font-body mt-0.5">{codes.length} total</p>
        </div>
        {!showForm && (
          <Button onClick={openCreate}>
            <span className="material-icons text-[14px]">add</span>
            New Promo Code
          </Button>
        )}
      </div>

      {/* Inline Form */}
      {showForm && (
        <form onSubmit={handleSubmit} className="bg-white border border-gray-200 p-5 mb-6">
          <h2 className="font-display text-base text-gray-800 mb-4">
            {editingCode ? `Edit ${editingCode.code}` : 'New Promo Code'}
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Input
              label="Code"
              placeholder="e.g. SUMMER20"
              value={form.code}
              onChange={e => setForm(f => ({ ...f, code: e.target.value.toUpperCase() }))}
              disabled={!!editingCode}
              required
            />
            <Input
              label="Code (Arabic)"
              placeholder="Optional — e.g. صيف٢٠"
              helper="Optional. The same coupon typed in Arabic, sharing every limit and count with the code above."
              dir="rtl"
              value={form.code_ar}
              onChange={e => setForm(f => ({ ...f, code_ar: e.target.value }))}
            />
            <Select
              label="Discount Type"
              value={form.discount_type}
              onChange={e => setForm(f => ({ ...f, discount_type: e.target.value as 'percentage' | 'fixed' }))}
              options={DISCOUNT_TYPE_OPTIONS}
            />
            <Input
              label={form.discount_type === 'percentage' ? 'Value (%)' : 'Value (AED)'}
              type="number"
              min="0"
              step="0.01"
              value={form.discount_value}
              onChange={e => setForm(f => ({ ...f, discount_value: e.target.value }))}
              required
            />
            <Input
              label="Min Order Amount (AED)"
              type="number"
              min="0"
              step="0.01"
              placeholder="Optional"
              value={form.min_order_amount}
              onChange={e => setForm(f => ({ ...f, min_order_amount: e.target.value }))}
            />
            <Input
              label="Max Discount (AED)"
              type="number"
              min="0"
              step="0.01"
              placeholder={form.discount_type === 'percentage' ? 'No cap' : 'Percentage only'}
              helper={form.discount_type === 'percentage'
                ? 'Optional. Caps what the percentage can take off — 15% of a large catering order.'
                : 'Only applies to percentage discounts.'}
              disabled={form.discount_type !== 'percentage'}
              value={form.discount_type === 'percentage' ? form.max_discount_amount : ''}
              onChange={e => setForm(f => ({ ...f, max_discount_amount: e.target.value }))}
            />
            <Input
              label="Max Uses"
              type="number"
              min="1"
              placeholder="Unlimited"
              value={form.max_uses}
              onChange={e => setForm(f => ({ ...f, max_uses: e.target.value }))}
            />
            <Input
              label="Max Uses Per Customer"
              type="number"
              min="1"
              placeholder="Unlimited"
              helper="Optional. Stops one customer burning the whole campaign."
              value={form.max_uses_per_user}
              onChange={e => setForm(f => ({ ...f, max_uses_per_user: e.target.value }))}
            />
            <Input
              label="First Orders Only"
              type="number"
              min="1"
              placeholder="No restriction"
              helper="Optional. Redeemable only on a customer's first N orders."
              value={form.first_orders_limit}
              onChange={e => setForm(f => ({ ...f, first_orders_limit: e.target.value }))}
            />
            {/* The setting that makes "first orders only" mean anything.
                Without it the rule is enforced against an account and an email,
                both of which cost nothing to mint — a guest checkout creates a
                fresh account per session. A mobile number is the only identity
                in the checkout that costs something to fake, so an acquisition
                code without this is a code anyone can redeem indefinitely. */}
            <label className="flex items-start gap-2 cursor-pointer sm:col-span-2">
              <input
                type="checkbox"
                className="mt-0.5 accent-primary"
                checked={form.requires_phone_verification}
                onChange={e =>
                  setForm(f => ({ ...f, requires_phone_verification: e.target.checked }))
                }
              />
              <span>
                <span className="block font-body text-sm text-gray-800">
                  Require a verified mobile number
                </span>
                <span className="block font-body text-xs text-gray-500">
                  Strongly recommended alongside &ldquo;First Orders Only&rdquo; — without it
                  the limit is enforced against identities anyone can create for free.
                </span>
              </span>
            </label>
            <div />
            <Input
              label="Valid From"
              type="date"
              value={form.valid_from}
              onChange={e => setForm(f => ({ ...f, valid_from: e.target.value }))}
            />
            <Input
              label="Valid Until"
              type="date"
              value={form.valid_until}
              onChange={e => setForm(f => ({ ...f, valid_until: e.target.value }))}
            />
          </div>
          {formError && <p className="text-xs text-red-500 mt-3">{formError}</p>}
          <div className="flex gap-2 mt-4">
            <Button type="submit" loading={saving}>
              {editingCode ? 'Save Changes' : 'Create'}
            </Button>
            <Button type="button" variant="ghost" onClick={closeForm}>Cancel</Button>
          </div>
        </form>
      )}

      {/* Tabs */}
      <TabBar
        tabs={[
          { key: 'active', label: 'Active', count: activeCount },
          { key: 'inactive', label: 'Inactive', count: inactiveCount },
        ]}
        active={activeTab}
        onChange={key => { setActiveTab(key as 'active' | 'inactive'); setSelectedIds(new Set()); setPage(1); }}
      />

      {/* Bulk action bar */}
      {selectedIds.size > 0 && (
        <div className="flex items-center gap-3 bg-primary/10 border border-primary/30 px-4 py-2.5 mb-4">
          <span className="text-xs font-body text-primary font-medium">{selectedIds.size} selected</span>
          <button onClick={() => setSelectedIds(new Set(filteredCodes.map(c => c.id)))} className="text-xs font-body text-gray-500 hover:text-primary underline">All</button>
          <button onClick={() => setSelectedIds(new Set())} className="text-xs font-body text-gray-500 hover:text-primary underline">None</button>
          <div className="flex-1" />
          <Button size="sm" loading={bulking} onClick={() => handleBulkStatus(true)}>Activate</Button>
          <Button size="sm" variant="ghost" loading={bulking} onClick={() => handleBulkStatus(false)}>Deactivate</Button>
        </div>
      )}

      {/* Table */}
      <div className="bg-white border border-gray-200 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50">
              <th className="px-4 py-3 w-8">
                <input
                  type="checkbox"
                  checked={paginatedCodes.length > 0 && paginatedCodes.every(c => selectedIds.has(c.id))}
                  onChange={toggleSelectAll}
                  className="accent-primary"
                />
              </th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500">Code</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 hidden sm:table-cell">Discount</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 hidden md:table-cell">Min Order</th>
              <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500 hidden md:table-cell">Uses</th>
              <th className="px-4 py-3 text-center text-[11px] font-body uppercase tracking-widest text-gray-500 hidden lg:table-cell">First Orders</th>
              <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 hidden lg:table-cell">Valid</th>
              <th className="px-4 py-3 text-right text-[11px] font-body uppercase tracking-widest text-gray-500">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading ? (
              Array.from({ length: 4 }).map((_, i) => (
                <tr key={i}>
                  {Array.from({ length: 8 }).map((_, j) => (
                    <td key={j} className="px-4 py-3">
                      <div className="h-4 bg-gray-100 animate-pulse rounded-sm" />
                    </td>
                  ))}
                </tr>
              ))
            ) : filteredCodes.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center text-sm text-gray-400 font-body">
                  No promo codes yet.
                </td>
              </tr>
            ) : (
              paginatedCodes.map(promo => (
                <tr key={promo.id} className={`hover:bg-gray-50 transition-colors ${selectedIds.has(promo.id) ? 'bg-primary/5' : ''}`}>
                  <td className="px-4 py-3 w-8">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(promo.id)}
                      onChange={() => toggleSelect(promo.id)}
                      className="accent-primary"
                    />
                  </td>
                  <td className="px-4 py-3">
                    <span className="font-body font-medium text-gray-800 text-xs tracking-wider">{promo.code}</span>
                    {promo.code_ar && (
                      <span dir="rtl" className="block text-[11px] font-body text-gray-400 mt-0.5">{promo.code_ar}</span>
                    )}
                  </td>
                  <td className="px-4 py-3 hidden sm:table-cell">
                    <span className="text-xs font-body text-gray-700">
                      {promo.discount_type === 'percentage'
                        ? `${promo.discount_value}%`
                        : formatCurrency(promo.discount_value)
                      }
                    </span>
                    <span className="text-[11px] font-body text-gray-400 ml-1 capitalize">({promo.discount_type})</span>
                    {promo.max_discount_amount != null && (
                      <span className="block text-[11px] font-body text-gray-400 mt-0.5">
                        max {formatCurrency(promo.max_discount_amount)}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 hidden md:table-cell">
                    <span className="text-xs font-body text-gray-500">
                      {promo.min_order_amount != null ? formatCurrency(promo.min_order_amount) : '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-center hidden md:table-cell">
                    <span className="text-xs font-body text-gray-500">
                      {promo.current_uses}{promo.max_uses != null ? ` / ${promo.max_uses}` : ''}
                    </span>
                    {promo.max_uses_per_user != null && (
                      <span className="block text-[11px] font-body text-gray-400 mt-0.5">
                        {promo.max_uses_per_user} per customer
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-center hidden lg:table-cell">
                    <span className="text-xs font-body text-gray-500">
                      {promo.first_orders_limit != null ? `First ${promo.first_orders_limit}` : '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3 hidden lg:table-cell">
                    <span className="text-[11px] font-body text-gray-400">
                      {promo.valid_from ? formatDate(promo.valid_from) : '—'}
                      {' → '}
                      {promo.valid_until ? formatDate(promo.valid_until) : '∞'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex items-center justify-end gap-2">
                      {activeTab === 'active' ? (
                        <>
                          <Button variant="ghost" size="sm" onClick={() => openEdit(promo)}>Edit</Button>
                          <Button
                            variant="danger"
                            size="sm"
                            loading={actionCode === promo.code}
                            onClick={() => handleDeactivate(promo.code)}
                          >
                            Deactivate
                          </Button>
                        </>
                      ) : (
                        <Button
                          variant="ghost"
                          size="sm"
                          loading={actionCode === promo.code}
                          onClick={() => handleRestore(promo.code)}
                        >
                          Restore
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <Pagination
        page={page}
        pages={codePages}
        total={filteredCodes.length}
        perPage={perPage}
        onPageChange={setPage}
        onPerPageChange={p => { setPerPage(p); setPage(1); }}
        label="promo codes"
      />
    </div>
  );
}
