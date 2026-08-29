'use client';

import { useCallback, useEffect, useState } from 'react';
import { promotionsApi, categoriesApi, ApiError } from '@/lib/api';
import type { Promotion, Category } from '@/lib/types';
import { Button, Input, Select, Badge, Spinner, LoadError } from '@/components/ui';
import { useApiList } from '@/hooks/useApiList';
import { useToast } from '@/components/ui/feedback';

/** The reward types this card can edit as a single order-level saving. */
const ORDER_REWARDS = new Set(['percentage_off_order', 'fixed_off_order']);

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
  onSaved,
}: {
  promo: Promotion;
  categories: Category[];
  onSaved: () => void;
}) {
  const toast = useToast();
  const isPercent = promo.reward === 'percentage_off_order';
  const [value, setValue] = useState(String(promo.reward_value));
  const [minSpend, setMinSpend] = useState(promo.trigger_value ? String(promo.trigger_value) : '');
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
            {scopeLabel(promo.sources)} · applied automatically at the register
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

/**
 * The auto-applied promotions block on the Promotions page — chiefly the
 * standing "every counter order is 15% off cookies, brownies and cookie melts".
 * Coupons a customer types live in the table below; these are the discounts the
 * shop applies by itself.
 */
export function CounterPromotions() {
  const fetchPromos = useCallback(() => promotionsApi.list(), []);
  const { items, loading, loadError, refetch } = useApiList<Promotion>({
    paginate: 'client',
    fetch: fetchPromos,
  });

  const [categories, setCategories] = useState<Category[]>([]);
  useEffect(() => {
    categoriesApi.list().then(setCategories).catch(() => setCategories([]));
  }, []);

  const autoApplied = items.filter(p => p.auto_apply && ORDER_REWARDS.has(p.reward));

  return (
    <section className="bg-white border border-gray-200 p-5 mb-8">
      <div className="mb-4">
        <h2 className="font-display text-base text-gray-800">Auto-applied promotions</h2>
        <p className="text-xs text-gray-400 font-body mt-0.5">
          Discounts the register puts on by itself — no coupon, no cashier action.
        </p>
      </div>

      <LoadError message={loadError} onRetry={refetch} />

      {loading ? (
        <div className="flex justify-center py-10"><Spinner /></div>
      ) : autoApplied.length === 0 ? (
        <p className="py-8 text-center text-sm text-gray-400 font-body">
          No auto-applied promotions configured.
        </p>
      ) : (
        <div className="space-y-3">
          {autoApplied.map(promo => (
            <PromotionRow key={promo.id} promo={promo} categories={categories} onSaved={refetch} />
          ))}
        </div>
      )}
    </section>
  );
}
