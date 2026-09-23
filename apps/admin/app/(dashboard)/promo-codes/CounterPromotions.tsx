'use client';

import { useCallback, useEffect, useState } from 'react';
import type { Schemas } from '@mm/types';
import { promotionsApi, categoriesApi, ApiError } from '@/lib/api';
import { branchesApi, devicesApi } from '@/lib/pos-api';
import type { Promotion, Category } from '@/lib/types';
import type { Branch, Device } from '@/lib/pos-types';
import { Button, Input, Select, Badge, Spinner, LoadError } from '@/components/ui';
import { useApiList } from '@/hooks/useApiList';
import { useToast } from '@/components/ui/feedback';
import { cn } from '@/lib/utils';

/** The reward types this card can edit as a single order-level saving. */
const ORDER_REWARDS = new Set(['percentage_off_order', 'fixed_off_order']);

/** How a branch runs a promotion. */
type BranchMode = 'off' | 'auto' | 'coupon';

const MODE_OPTIONS: { value: BranchMode; label: string }[] = [
  { value: 'off', label: 'Off' },
  { value: 'auto', label: 'Auto' },
  { value: 'coupon', label: 'Coupon' },
];

/**
 * A terminal as the matrix reads it. `supports_coupons` is decided server-side
 * against the minimum build that shows coupon chips (`COUPON_MIN_BUILD`).
 */
type Terminal = Device & Partial<Pick<Schemas['DeviceResponse'], 'supports_coupons'>>;

function modeAt(promo: Promotion, branchId: string): BranchMode {
  if ((promo.auto_branch_ids ?? []).includes(branchId)) return 'auto';
  if ((promo.coupon_branch_ids ?? []).includes(branchId)) return 'coupon';
  return 'off';
}

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

function scopeLabel(sources: string[]): string {
  if (sources.length === 0) return 'All channels';
  if (sources.length === 1 && sources[0] === 'cashier') return 'Counter orders';
  return sources.join(', ');
}

/**
 * One auto-applied promotion, editable in place.
 *
 * The register applies these on its own, so the figures worth changing from the
 * console are how much comes off, the spend it needs, the on/off switch — and
 * which categories it is confined to. Everything else structural (which channel,
 * which reward) is set once in the migration that seeds the offer.
 */
function PromotionRow({
  promo,
  categories,
  branches,
  terminals,
  onSaved,
}: {
  promo: Promotion;
  categories: Category[];
  branches: Branch[];
  terminals: Terminal[];
  onSaved: () => void;
}) {
  const toast = useToast();
  const isPercent = promo.reward === 'percentage_off_order';
  const [value, setValue] = useState(String(Number(promo.reward_value)));
  const [minSpend, setMinSpend] = useState(
    Number(promo.trigger_value) ? String(Number(promo.trigger_value)) : '',
  );
  const [categoryIds, setCategoryIds] = useState<string[]>(promo.category_ids ?? []);
  const [saving, setSaving] = useState(false);

  const nameOf = (id: string) => categories.find(c => c.id === id)?.name ?? 'Unknown category';
  const unselected = categories.filter(c => c.is_active && !categoryIds.includes(c.id));

  async function save() {
    setSaving(true);
    try {
      await promotionsApi.update(promo.id, {
        reward_value: Number(value),
        trigger_value: minSpend ? Number(minSpend) : 0,
      });
      toast.success(`${promo.name} updated`);
      onSaved();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not save the promotion.');
    } finally {
      setSaving(false);
    }
  }

  async function toggleActive() {
    setSaving(true);
    try {
      await promotionsApi.update(promo.id, { is_active: !promo.is_active });
      onSaved();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not change the status.');
    } finally {
      setSaving(false);
    }
  }

  // Categories save on each add/remove, so curating the set is one click and it
  // sticks — no separate Save to forget. Optimistic, reverting on failure.
  async function saveCategories(next: string[]) {
    const previous = categoryIds;
    setCategoryIds(next);
    setSaving(true);
    try {
      await promotionsApi.update(promo.id, { category_ids: next });
      onSaved();
    } catch (err) {
      setCategoryIds(previous);
      toast.error(err instanceof ApiError ? err.message : 'Could not update the categories.');
    } finally {
      setSaving(false);
    }
  }

  // Per-branch mode, saved on each click like the category chips. The two
  // lists are disjoint by construction: a branch moves out of one before it
  // lands in the other. Optimistic, reverting on failure.
  const [autoIds, setAutoIds] = useState<string[]>(promo.auto_branch_ids ?? []);
  const [couponIds, setCouponIds] = useState<string[]>(promo.coupon_branch_ids ?? []);
  const current = { ...promo, auto_branch_ids: autoIds, coupon_branch_ids: couponIds };

  async function setMode(branchId: string, mode: BranchMode) {
    const previous = { auto: autoIds, coupon: couponIds };
    const nextAuto = autoIds.filter(id => id !== branchId);
    const nextCoupon = couponIds.filter(id => id !== branchId);
    if (mode === 'auto') nextAuto.push(branchId);
    if (mode === 'coupon') nextCoupon.push(branchId);
    setAutoIds(nextAuto);
    setCouponIds(nextCoupon);
    setSaving(true);
    try {
      await promotionsApi.update(promo.id, {
        auto_branch_ids: nextAuto,
        coupon_branch_ids: nextCoupon,
      });
      onSaved();
    } catch (err) {
      setAutoIds(previous.auto);
      setCouponIds(previous.coupon);
      toast.error(err instanceof ApiError ? err.message : 'Could not change the branch mode.');
    } finally {
      setSaving(false);
    }
  }

  const runsAnywhere = autoIds.length + couponIds.length > 0;

  return (
    <div className="border border-gray-200 p-4">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-body font-medium text-gray-800 text-sm">{promo.name}</span>
            <Badge variant={promo.is_active ? 'success' : 'neutral'}>
              {promo.is_active ? 'Active' : 'Inactive'}
            </Badge>
          </div>
          <p className="text-[11px] text-gray-400 font-body mt-0.5">
            {scopeLabel(promo.sources)} ·{' '}
            {runsAnywhere
              ? `auto at ${autoIds.length}, coupon at ${couponIds.length} branch${couponIds.length === 1 ? '' : 'es'}`
              : 'not running at any branch'}
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          loading={saving}
          onClick={toggleActive}
        >
          {promo.is_active ? 'Turn off' : 'Turn on'}
        </Button>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end">
        <Input
          label={isPercent ? 'Discount (%)' : 'Discount (AED)'}
          type="number"
          min="0"
          step="0.01"
          value={value}
          onChange={e => setValue(e.target.value)}
        />
        <Input
          label="Min order (AED)"
          type="number"
          min="0"
          step="0.01"
          placeholder="None"
          helper="0 or blank applies it to every order."
          value={minSpend}
          onChange={e => setMinSpend(e.target.value)}
        />
        <div>
          <Button loading={saving} onClick={save}>Save</Button>
        </div>
      </div>

      {/* Per branch: off, applied by itself, or a one-tap coupon at the till. */}
      <div className="mt-4 pt-3 border-t border-gray-100">
        <span className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1">
          Branches
        </span>
        <p className="text-[11px] text-gray-400 font-body mb-2">
          Auto: the register takes it off every qualifying check. Coupon: the cashier taps it
          on at the till, and it replaces the auto promotion for that order.
        </p>
        {branches.length === 0 ? (
          <p className="text-[11px] text-gray-400 font-body">No branches run the register.</p>
        ) : (
          <div className="divide-y divide-gray-100">
            {branches.map(branch => {
              const mode = modeAt(current, branch.id);
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
                            disabled={saving}
                            onClick={() => !on && setMode(branch.id, opt.value)}
                            className={cn(
                              'border px-3 py-1 text-xs font-body transition-colors disabled:opacity-50',
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
                      {stale.length} terminal{stale.length === 1 ? '' : 's'} here{' '}
                      ({stale.map(d => `${d.name}${d.build_number ? ` build ${d.build_number}` : ''}`).join(', ')}){' '}
                      {stale.length === 1 ? 'runs' : 'run'} an app build without coupon chips and
                      cannot apply this coupon until updated.
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Which categories the discount is confined to. Empty = the whole order. */}
      <div className="mt-4 pt-3 border-t border-gray-100">
        <div className="flex items-center justify-between gap-3 mb-2">
          <span className="block text-xs font-medium uppercase tracking-wider text-gray-600">
            Applies to categories
          </span>
          {categoryIds.length > 0 && (
            <button
              type="button"
              className="text-[11px] text-gray-400 hover:text-gray-600 font-body disabled:opacity-50"
              disabled={saving}
              onClick={() => saveCategories([])}
            >
              Clear (whole order)
            </button>
          )}
        </div>

        {categoryIds.length === 0 ? (
          <p className="text-[11px] text-gray-400 font-body mb-2">
            No limit — the discount applies to the whole order. Add a category to
            confine it to those products only.
          </p>
        ) : (
          <div className="flex flex-wrap gap-2 mb-2">
            {categoryIds.map(id => (
              <span
                key={id}
                className="inline-flex items-center gap-1.5 pl-2.5 pr-1.5 py-1 text-xs font-body bg-gray-100 text-gray-700 rounded-sm"
              >
                {nameOf(id)}
                <button
                  type="button"
                  aria-label={`Remove ${nameOf(id)}`}
                  className="text-gray-400 hover:text-gray-700 disabled:opacity-50 leading-none text-sm"
                  disabled={saving}
                  onClick={() => saveCategories(categoryIds.filter(c => c !== id))}
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
            disabled={saving}
            className="sm:max-w-xs"
            options={unselected.map(c => ({ value: c.id, label: c.name }))}
            onChange={e => {
              if (e.target.value) saveCategories([...categoryIds, e.target.value]);
            }}
          />
        )}
      </div>
    </div>
  );
}

/** A new counter promotion: off at every branch until a mode is picked below. */
function NewCounterPromotion({ onCreated, onCancel }: { onCreated: () => void; onCancel: () => void }) {
  const toast = useToast();
  const [name, setName] = useState('');
  const [reward, setReward] = useState<'percentage_off_order' | 'fixed_off_order'>('percentage_off_order');
  const [value, setValue] = useState('');
  const [minSpend, setMinSpend] = useState('');
  const [saving, setSaving] = useState(false);

  async function create() {
    if (!name.trim() || !Number(value)) {
      toast.error('Give it a name and a discount.');
      return;
    }
    setSaving(true);
    try {
      await promotionsApi.create({
        name: name.trim(),
        reward,
        reward_value: Number(value),
        trigger: 'spend',
        trigger_value: minSpend ? Number(minSpend) : 0,
        // Counter only: the storefront and the marketplaces never inherit it.
        sources: ['cashier'],
        auto_branch_ids: [],
        coupon_branch_ids: [],
        is_active: true,
      });
      toast.success(`${name.trim()} created — pick where it runs below`);
      onCreated();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not create the promotion.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="border border-dashed border-gray-300 p-4 mb-3">
      <div className="grid grid-cols-1 sm:grid-cols-4 gap-3 items-end">
        <Input label="Name" value={name} onChange={e => setName(e.target.value)} placeholder="Cookies 20% off" />
        <Select
          label="Type"
          value={reward}
          onChange={e => setReward(e.target.value as typeof reward)}
          options={[
            { value: 'percentage_off_order', label: 'Percentage (%)' },
            { value: 'fixed_off_order', label: 'Fixed amount (AED)' },
          ]}
        />
        <Input
          label={reward === 'percentage_off_order' ? 'Discount (%)' : 'Discount (AED)'}
          type="number"
          min="0"
          step="0.01"
          value={value}
          onChange={e => setValue(e.target.value)}
        />
        <Input
          label="Min order (AED)"
          type="number"
          min="0"
          step="0.01"
          placeholder="None"
          value={minSpend}
          onChange={e => setMinSpend(e.target.value)}
        />
      </div>
      <div className="flex gap-2 mt-3">
        <Button loading={saving} onClick={create}>Create</Button>
        <Button variant="ghost" onClick={onCancel} disabled={saving}>Cancel</Button>
      </div>
    </div>
  );
}

/**
 * The counter promotions block on the Promotions page — chiefly the standing
 * "every counter order is 15% off cookies, brownies and cookie melts". Each one
 * runs per branch either automatically or as a coupon the cashier taps on.
 * Coupons a customer types live in the table below.
 */
export function CounterPromotions() {
  const fetchPromos = useCallback(() => promotionsApi.list(), []);
  const { items, loading, loadError, refetch } = useApiList<Promotion>({
    paginate: 'client',
    fetch: fetchPromos,
  });

  const [categories, setCategories] = useState<Category[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [terminals, setTerminals] = useState<Terminal[]>([]);
  const [creating, setCreating] = useState(false);
  useEffect(() => {
    categoriesApi.list().then(setCategories).catch(() => setCategories([]));
    branchesApi
      .list()
      .then(rows => setBranches(rows.filter(b => b.uses_pos && b.is_active && !b.deleted_at)))
      .catch(() => setBranches([]));
    // Best-effort: only feeds the old-build warning, and a role without device
    // access simply sees no warning.
    devicesApi.list().then(setTerminals).catch(() => setTerminals([]));
  }, []);

  // Every counter promotion — an order-level reward scoped to the counter —
  // whether or not it runs anywhere yet.
  const counter = items.filter(p => ORDER_REWARDS.has(p.reward) && p.sources.includes('cashier'));

  return (
    <section className="bg-white border border-gray-200 p-5 mb-8">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-base text-gray-800">Counter promotions</h2>
          <p className="text-xs text-gray-400 font-body mt-0.5">
            Discounts the register applies by itself, or offers as a one-tap coupon — per branch.
          </p>
        </div>
        {!creating && (
          <Button variant="secondary" size="sm" onClick={() => setCreating(true)}>
            <span className="material-icons text-[14px]">add</span>
            New counter promotion
          </Button>
        )}
      </div>

      <LoadError message={loadError} onRetry={refetch} />

      {creating && (
        <NewCounterPromotion
          onCancel={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            refetch();
          }}
        />
      )}

      {loading ? (
        <div className="flex justify-center py-10"><Spinner /></div>
      ) : counter.length === 0 ? (
        <p className="py-8 text-center text-sm text-gray-400 font-body">
          No counter promotions configured.
        </p>
      ) : (
        <div className="space-y-3">
          {counter.map(promo => (
            <PromotionRow
              key={promo.id}
              promo={promo}
              categories={categories}
              branches={branches}
              terminals={terminals}
              onSaved={refetch}
            />
          ))}
        </div>
      )}
    </section>
  );
}
