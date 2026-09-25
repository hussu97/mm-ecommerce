'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Schemas } from '@mm/types';
import { promotionsApi, categoriesApi, ApiError } from '@/lib/api';
import { branchesApi, devicesApi } from '@/lib/pos-api';
import type { Promotion, Category } from '@/lib/types';
import type { Branch, Device } from '@/lib/pos-types';
import { Button, Input, Select, Badge, Spinner, LoadError } from '@/components/ui';
import { DataTable, type DataColumn } from '@/components/ui/DataTable';
import { Modal } from '@/components/pos/ResourcePage';
import { useApiList } from '@/hooks/useApiList';
import { useToast } from '@/components/ui/feedback';
import { cn, formatCurrency } from '@/lib/utils';

type Usage = Schemas['PromotionUsageResponse'];
type Reward = 'percentage_off_order' | 'fixed_off_order';

/** The reward types a counter promotion can carry: one order-level saving. */
const ORDER_REWARDS = new Set<string>(['percentage_off_order', 'fixed_off_order']);

/** How a branch runs a promotion. */
type BranchMode = 'off' | 'auto' | 'coupon';

const MODE_OPTIONS: { value: BranchMode; label: string }[] = [
  { value: 'off', label: 'Off' },
  { value: 'auto', label: 'Auto' },
  { value: 'coupon', label: 'Coupon' },
];

/**
 * A terminal as the branch list reads it. `supports_coupons` is decided
 * server-side against the minimum build that shows coupon chips.
 */
type Terminal = Device & Partial<Pick<Schemas['DeviceResponse'], 'supports_coupons'>>;

/** Till terminals at `branchId` still in service whose build cannot select a coupon. */
function oldTerminalsAt(terminals: Terminal[], branchId: string): Terminal[] {
  return terminals.filter(
    d =>
      d.branch_id === branchId &&
      !d.deleted_at &&
      d.status !== 'disabled' &&
      (d.type === 'cashier' || d.type === 'sub_cashier') &&
      d.supports_coupons === false,
  );
}

function modeOf(autoIds: string[], couponIds: string[], branchId: string): BranchMode {
  if (autoIds.includes(branchId)) return 'auto';
  if (couponIds.includes(branchId)) return 'coupon';
  return 'off';
}

function discountLabel(p: Promotion): string {
  const value = Number(p.reward_value);
  return p.reward === 'percentage_off_order' ? `${value}% off` : `${formatCurrency(value)} off`;
}

// ─── The form (create + edit) ─────────────────────────────────────────────────

interface FormState {
  name: string;
  reward: Reward;
  value: string;
  minSpend: string;
  usageLimit: string;
  autoIds: string[];
  couponIds: string[];
  categoryIds: string[];
}

function formFrom(promo: Promotion | null): FormState {
  if (!promo) {
    return {
      name: '',
      reward: 'percentage_off_order',
      value: '',
      minSpend: '',
      usageLimit: '',
      autoIds: [],
      couponIds: [],
      categoryIds: [],
    };
  }
  return {
    name: promo.name,
    reward: promo.reward as Reward,
    value: String(Number(promo.reward_value)),
    minSpend: Number(promo.trigger_value) ? String(Number(promo.trigger_value)) : '',
    usageLimit: promo.usage_limit ? String(promo.usage_limit) : '',
    autoIds: promo.auto_branch_ids ?? [],
    couponIds: promo.coupon_branch_ids ?? [],
    categoryIds: promo.category_ids ?? [],
  };
}

/**
 * Everything about one counter promotion, saved together: what comes off, the
 * spend it needs, how many completed orders may use it, how each branch runs
 * it, and which categories it is confined to.
 */
function PromotionForm({
  promo,
  usage,
  categories,
  branches,
  terminals,
  onClose,
  onSaved,
}: {
  promo: Promotion | null;
  usage: Usage | undefined;
  categories: Category[];
  branches: Branch[];
  terminals: Terminal[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [form, setForm] = useState<FormState>(() => formFrom(promo));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm(f => ({ ...f, [key]: value }));

  const nameOf = (id: string) => categories.find(c => c.id === id)?.name ?? 'Unknown category';
  const unselected = categories.filter(c => c.is_active && !form.categoryIds.includes(c.id));

  // The two lists are disjoint by construction: a branch leaves one before it
  // joins the other.
  function setMode(branchId: string, mode: BranchMode) {
    setForm(f => ({
      ...f,
      autoIds: [...f.autoIds.filter(id => id !== branchId), ...(mode === 'auto' ? [branchId] : [])],
      couponIds: [
        ...f.couponIds.filter(id => id !== branchId),
        ...(mode === 'coupon' ? [branchId] : []),
      ],
    }));
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    if (!form.name.trim()) return setError('Give it a name.');
    if (!Number(form.value)) return setError('Set how much comes off.');
    const limit = form.usageLimit.trim();
    if (limit && (!Number.isInteger(Number(limit)) || Number(limit) < 1)) {
      return setError('The usage limit is a whole number of orders, 1 or more — or blank for unlimited.');
    }
    const payload = {
      name: form.name.trim(),
      reward: form.reward,
      reward_value: Number(form.value),
      trigger: 'spend',
      trigger_value: form.minSpend ? Number(form.minSpend) : 0,
      // Explicit null so an edit can make a limited promotion unlimited again.
      usage_limit: limit ? Number(limit) : null,
      auto_branch_ids: form.autoIds,
      coupon_branch_ids: form.couponIds,
      category_ids: form.categoryIds,
    };
    setSaving(true);
    try {
      if (promo) {
        await promotionsApi.update(promo.id, payload);
        toast.success(`${payload.name} saved`);
      } else {
        // Counter only: the storefront and the marketplaces never inherit it.
        await promotionsApi.create({ ...payload, sources: ['cashier'], is_active: true });
        toast.success(`${payload.name} created`);
      }
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the promotion.');
    } finally {
      setSaving(false);
    }
  }

  const isPercent = form.reward === 'percentage_off_order';

  return (
    <Modal title={promo ? `Edit ${promo.name}` : 'New counter promotion'} onClose={onClose} wide>
      <form onSubmit={save} className="space-y-5">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <Input
            label="Name"
            value={form.name}
            onChange={e => set('name', e.target.value)}
            placeholder="Cookies 20% off"
          />
          <Select
            label="Type"
            value={form.reward}
            onChange={e => set('reward', e.target.value as Reward)}
            options={[
              { value: 'percentage_off_order', label: 'Percentage (%)' },
              { value: 'fixed_off_order', label: 'Fixed amount (AED)' },
            ]}
          />
          <Input
            label={isPercent ? 'Discount (%)' : 'Discount (AED)'}
            type="number"
            min="0"
            step="0.01"
            value={form.value}
            onChange={e => set('value', e.target.value)}
          />
          <Input
            label="Min order (AED)"
            type="number"
            min="0"
            step="0.01"
            placeholder="None"
            helper="0 or blank applies it to every order."
            value={form.minSpend}
            onChange={e => set('minSpend', e.target.value)}
          />
          <Input
            label="Usage limit (orders)"
            type="number"
            min="1"
            step="1"
            placeholder="Unlimited"
            helper={
              usage && promo
                ? `Used on ${usage.used} completed order${usage.used === 1 ? '' : 's'} so far, across all branches. Blank = unlimited.`
                : 'Completed counter orders across all branches. Drafts and voids don’t count. Blank = unlimited.'
            }
            value={form.usageLimit}
            onChange={e => set('usageLimit', e.target.value)}
          />
        </div>

        {/* Per branch: off, applied by itself, or a one-tap coupon at the till. */}
        <div className="pt-3 border-t border-gray-100">
          <span className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1">
            Branches
          </span>
          <p className="text-[11px] text-gray-400 font-body mb-2">
            Auto: the register takes it off every qualifying check. Coupon: the cashier taps it on at
            the till, and it replaces the auto promotion for that order.
          </p>
          {branches.length === 0 ? (
            <p className="text-[11px] text-gray-400 font-body">No branches run the register.</p>
          ) : (
            <div className="divide-y divide-gray-100">
              {branches.map(branch => {
                const mode = modeOf(form.autoIds, form.couponIds, branch.id);
                const stale = mode === 'coupon' ? oldTerminalsAt(terminals, branch.id) : [];
                return (
                  <div key={branch.id} className="py-2">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-sm font-body text-gray-700">{branch.name}</span>
                      <div role="radiogroup" aria-label={`${branch.name} mode`} className="inline-flex">
                        {MODE_OPTIONS.map((opt, i) => {
                          const on = mode === opt.value;
                          return (
                            <button
                              key={opt.value}
                              type="button"
                              role="radio"
                              aria-checked={on}
                              onClick={() => setMode(branch.id, opt.value)}
                              className={cn(
                                'border px-3 py-1 text-xs font-body transition-colors',
                                'min-h-[var(--tap-min)] md:min-h-0',
                                i > 0 && '-ml-px',
                                on
                                  ? 'relative border-primary bg-primary/5 text-primary'
                                  : 'border-gray-200 text-gray-600 hover:border-gray-300',
                              )}
                            >
                              {opt.label}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                    {stale.length > 0 && (
                      <p className="mt-1 text-[11px] font-body text-amber-700">
                        {stale.length} terminal{stale.length === 1 ? '' : 's'} here (
                        {stale.map(d => `${d.name}${d.build_number ? ` build ${d.build_number}` : ''}`).join(', ')}
                        ) {stale.length === 1 ? 'runs' : 'run'} an app build without coupon chips and cannot
                        apply this coupon until updated.
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Which categories the discount is confined to. Empty = the whole order. */}
        <div className="pt-3 border-t border-gray-100">
          <div className="flex items-center justify-between gap-3 mb-2">
            <span className="block text-xs font-medium uppercase tracking-wider text-gray-600">
              Applies to categories
            </span>
            {form.categoryIds.length > 0 && (
              <button
                type="button"
                className="text-[11px] text-gray-400 hover:text-gray-600 font-body"
                onClick={() => set('categoryIds', [])}
              >
                Clear (whole order)
              </button>
            )}
          </div>
          {form.categoryIds.length === 0 ? (
            <p className="text-[11px] text-gray-400 font-body mb-2">
              No limit — the discount applies to the whole order. Add a category to confine it to
              those products only.
            </p>
          ) : (
            <div className="flex flex-wrap gap-2 mb-2">
              {form.categoryIds.map(id => (
                <span
                  key={id}
                  className="inline-flex items-center gap-1.5 pl-2.5 pr-1.5 py-1 text-xs font-body bg-gray-100 text-gray-700 rounded-sm"
                >
                  {nameOf(id)}
                  <button
                    type="button"
                    aria-label={`Remove ${nameOf(id)}`}
                    className="text-gray-400 hover:text-gray-700 leading-none text-sm"
                    onClick={() => set('categoryIds', form.categoryIds.filter(c => c !== id))}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          {unselected.length > 0 && (
            <Select
              aria-label="Add a category"
              placeholder="Add a category…"
              value=""
              className="sm:max-w-xs"
              options={unselected.map(c => ({ value: c.id, label: c.name }))}
              onChange={e => {
                if (e.target.value) set('categoryIds', [...form.categoryIds, e.target.value]);
              }}
            />
          )}
        </div>

        {error && <p className="text-sm text-red-600 font-body">{error}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="ghost" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button type="submit" loading={saving}>
            {promo ? 'Save' : 'Create'}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

// ─── The table ────────────────────────────────────────────────────────────────

/**
 * The counter promotions block on the Promotions page — chiefly the standing
 * "every counter order is 15% off cookies, brownies and cookie melts". A table,
 * one row per promotion, edited in a popup. Each one runs per branch either
 * automatically or as a coupon the cashier taps on. Coupons a customer types
 * live in the table below.
 */
export function CounterPromotions() {
  const toast = useToast();
  const fetchPromos = useCallback(() => promotionsApi.list(), []);
  const { items, loading, loadError, refetch } = useApiList<Promotion>({
    paginate: 'client',
    fetch: fetchPromos,
  });

  const [usage, setUsage] = useState<Record<string, Usage>>({});
  const [categories, setCategories] = useState<Category[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [terminals, setTerminals] = useState<Terminal[]>([]);
  const [editing, setEditing] = useState<Promotion | 'new' | null>(null);
  const [toggling, setToggling] = useState<string | null>(null);

  const loadUsage = useCallback(() => {
    promotionsApi
      .usage()
      .then(rows => setUsage(Object.fromEntries(rows.map(u => [u.promotion_id, u]))))
      .catch(() => setUsage({}));
  }, []);

  useEffect(() => {
    loadUsage();
    categoriesApi.list().then(setCategories).catch(() => setCategories([]));
    branchesApi
      .list()
      .then(rows => setBranches(rows.filter(b => b.uses_pos && b.is_active && !b.deleted_at)))
      .catch(() => setBranches([]));
    // Best-effort: only feeds the old-build warning, and a role without device
    // access simply sees no warning.
    devicesApi.list().then(setTerminals).catch(() => setTerminals([]));
  }, [loadUsage]);

  // Every counter promotion — an order-level reward scoped to the counter —
  // whether or not it runs anywhere yet.
  const counter = useMemo(
    () => items.filter(p => ORDER_REWARDS.has(p.reward) && p.sources.includes('cashier')),
    [items],
  );

  const branchName = (id: string) => branches.find(b => b.id === id)?.name;
  const categoryName = (id: string) => categories.find(c => c.id === id)?.name ?? 'Unknown';

  async function toggleActive(promo: Promotion) {
    setToggling(promo.id);
    try {
      await promotionsApi.update(promo.id, { is_active: !promo.is_active });
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not change the status.');
    } finally {
      setToggling(null);
    }
  }

  const columns: DataColumn<Promotion>[] = [
    {
      header: 'Promotion',
      priority: 'primary',
      render: p => {
        const u = usage[p.id];
        return (
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-gray-800">{p.name}</span>
            {u?.exhausted ? (
              <Badge variant="warning">Limit reached</Badge>
            ) : (
              <Badge variant={p.is_active ? 'success' : 'neutral'}>
                {p.is_active ? 'Active' : 'Inactive'}
              </Badge>
            )}
          </div>
        );
      },
    },
    {
      header: 'Discount',
      priority: 'secondary',
      render: p => (
        <span>
          {discountLabel(p)}
          {Number(p.trigger_value) > 0 && (
            <span className="text-gray-400"> · min {formatCurrency(Number(p.trigger_value))}</span>
          )}
        </span>
      ),
    },
    {
      header: 'Branches',
      priority: 'secondary',
      render: p => {
        const modes = [
          ...(p.auto_branch_ids ?? []).map(id => `${branchName(id) ?? 'Unknown'}: auto`),
          ...(p.coupon_branch_ids ?? []).map(id => `${branchName(id) ?? 'Unknown'}: coupon`),
        ];
        return modes.length ? (
          <span className="text-gray-600">{modes.join(' · ')}</span>
        ) : (
          <span className="text-gray-400">Not running</span>
        );
      },
    },
    {
      header: 'Categories',
      priority: 'desktop',
      render: p =>
        (p.category_ids ?? []).length ? (
          <span className="text-gray-600">{(p.category_ids ?? []).map(categoryName).join(', ')}</span>
        ) : (
          <span className="text-gray-400">Whole order</span>
        ),
    },
    {
      header: 'Used',
      priority: 'meta',
      render: p => {
        const used = usage[p.id]?.used ?? 0;
        return (
          <span className={cn('tabular-nums', usage[p.id]?.exhausted && 'text-amber-700')}>
            {p.usage_limit ? `${used} / ${p.usage_limit}` : `${used} · unlimited`}
          </span>
        );
      },
    },
  ];

  return (
    <section className="bg-white border border-gray-200 p-5 mb-8">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-base text-gray-800">Counter promotions</h2>
          <p className="text-xs text-gray-400 font-body mt-0.5">
            Discounts the register applies by itself, or offers as a one-tap coupon — per branch.
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={() => setEditing('new')}>
          <span className="material-icons text-[14px]">add</span>
          New counter promotion
        </Button>
      </div>

      <LoadError message={loadError} onRetry={refetch} />

      {loading ? (
        <div className="flex justify-center py-10"><Spinner /></div>
      ) : (
        <DataTable<Promotion>
          rows={counter}
          rowKey={p => p.id}
          columns={columns}
          empty={
            <p className="py-8 text-center text-sm text-gray-400 font-body">
              No counter promotions configured.
            </p>
          }
          actions={p => (
            <>
              <Button variant="ghost" size="sm" onClick={() => setEditing(p)}>
                Edit
              </Button>
              <Button
                variant="ghost"
                size="sm"
                loading={toggling === p.id}
                onClick={() => toggleActive(p)}
              >
                {p.is_active ? 'Turn off' : 'Turn on'}
              </Button>
            </>
          )}
        />
      )}

      {editing && (
        <PromotionForm
          promo={editing === 'new' ? null : editing}
          usage={editing === 'new' ? undefined : usage[editing.id]}
          categories={categories}
          branches={branches}
          terminals={terminals}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            refetch();
            loadUsage();
          }}
        />
      )}
    </section>
  );
}
