'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { analytics } from '@/lib/analytics';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { localizedField } from '@/lib/i18n/entity';
import { isModifierPriced } from '@/lib/pricing';
import { formatPrice } from '@/lib/utils';
import type { Product, ProductModifier } from '@/lib/types';

export interface SelectedOption {
  modifier_id: string;
  option_id: string;
}

interface Props {
  product: Product;
  onChange: (options: SelectedOption[], totalPrice: number, isValid: boolean) => void;
}

/**
 * A group the customer has no real decision to make in: exactly one pick from a
 * required list. Preselecting it means the page opens with a live price and an
 * enabled Add to Cart instead of a greyed-out "Select required options".
 */
function isForcedSingleChoice(pm: ProductModifier): boolean {
  return pm.minimum_options === 1 && pm.maximum_options === 1;
}

function ModifierGroup({
  pm,
  selected,
  onSelect,
  onIncrement,
  onDecrement,
  absolutePrices,
  t,
  locale,
}: {
  pm: ProductModifier;
  selected: SelectedOption[];
  onSelect: (modifierId: string, optionId: string, checked: boolean) => void;
  onIncrement: (modifierId: string, optionId: string) => void;
  onDecrement: (modifierId: string, optionId: string) => void;
  absolutePrices: boolean;
  t: (key: string, params?: Record<string, string | number>) => string;
  locale: string;
}) {
  const activeOptions = pm.modifier.options.filter(o => o.is_active);
  const selectedForThis = selected.filter(s => s.modifier_id === pm.modifier_id);
  const isSingle = pm.maximum_options === 1;

  const effectiveUnique = pm.unique_options || (pm.minimum_options === 1 && pm.maximum_options === 1);

  const modifierName = localizedField(pm.modifier, 'name', pm.modifier.name, locale);

  let pickLabel: string;
  if (pm.minimum_options > 0) {
    if (pm.minimum_options === pm.maximum_options) {
      pickLabel = t('product.pick_exactly', { n: pm.minimum_options });
    } else {
      pickLabel = t('product.pick_range', { min: pm.minimum_options, max: pm.maximum_options });
    }
  } else {
    pickLabel = t('product.up_to', { n: pm.maximum_options });
  }

  const total = selectedForThis.length;
  const short = pm.minimum_options - total;

  // When the product has no base price the option price IS the price, so a
  // "+" turns "From 40.00 AED" into something that reads like 80.
  const priceLabel = (price: number) =>
    `${absolutePrices ? '' : '+'}${formatPrice(price, locale)}`;

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between">
        <h4 className="text-xs font-body uppercase tracking-widest text-gray-600">{modifierName}</h4>
        <span
          className={`text-[11px] font-body ${short > 0 ? 'text-primary' : 'text-gray-400'}`}
        >
          {/*
            A box of three is easy to leave at two and hard to notice. The
            running count sits where the rule is stated rather than only
            surfacing as a disabled button with no explanation.
          */}
          {total > 0 ? `${total} / ${pm.maximum_options} — ${pickLabel}` : pickLabel}
        </span>
      </div>
      <div className="space-y-1.5">
        {activeOptions.map(opt => {
          const optionName = localizedField(opt, 'name', opt.name, locale);

          if (!effectiveUnique) {
            // Multi-qty stepper mode
            const qty = selectedForThis.filter(s => s.option_id === opt.id).length;
            return (
              <div
                key={opt.id}
                className={`flex items-center justify-between p-2.5 border transition-colors ${
                  qty > 0 ? 'border-primary bg-primary/5' : 'border-gray-200'
                }`}
              >
                <span className="text-sm font-body text-gray-700">{optionName}</span>
                <div className="flex items-center gap-3">
                  {opt.price > 0 && (
                    <span className="text-xs font-body text-primary">{priceLabel(opt.price)}</span>
                  )}
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => onDecrement(pm.modifier_id, opt.id)}
                      disabled={qty === 0}
                      className="w-6 h-6 flex items-center justify-center border border-gray-300 text-gray-600 disabled:opacity-30 hover:border-primary hover:text-primary transition-colors"
                    >
                      −
                    </button>
                    <span className="w-5 text-center text-sm font-body text-gray-700">{qty}</span>
                    <button
                      type="button"
                      onClick={() => onIncrement(pm.modifier_id, opt.id)}
                      disabled={total >= pm.maximum_options}
                      className="w-6 h-6 flex items-center justify-center border border-gray-300 text-gray-600 disabled:opacity-30 hover:border-primary hover:text-primary transition-colors"
                    >
                      +
                    </button>
                  </div>
                </div>
              </div>
            );
          }

          // Unique mode: existing radio / checkbox UI
          const isSelected = selectedForThis.some(s => s.option_id === opt.id);
          return (
            <label
              key={opt.id}
              className={`flex items-center justify-between p-2.5 border cursor-pointer transition-colors ${
                isSelected
                  ? 'border-primary bg-primary/5'
                  : 'border-gray-200 hover:border-gray-300'
              }`}
            >
              <div className="flex items-center gap-2.5">
                <input
                  type={isSingle ? 'radio' : 'checkbox'}
                  name={isSingle ? `mod-${pm.modifier_id}` : undefined}
                  checked={isSelected}
                  onChange={e => onSelect(pm.modifier_id, opt.id, e.target.checked)}
                  aria-label={`${optionName}${opt.price > 0 ? ` — ${priceLabel(opt.price)}` : ''}`}
                  className="accent-primary"
                />
                <span className="text-sm font-body text-gray-700">{optionName}</span>
              </div>
              {opt.price > 0 && (
                <span className="text-xs font-body text-primary">{priceLabel(opt.price)}</span>
              )}
            </label>
          );
        })}
      </div>
    </div>
  );
}

export function ModifierSelector({ product, onChange }: Props) {
  const { t, locale } = useTranslation();

  // Memoised because `recordPick` closes over it: the `?? []` produces a fresh
  // array on every render, which would rebuild the callback on every render too.
  const productModifiers = useMemo(
    () => product.product_modifiers ?? [],
    [product.product_modifiers],
  );
  const absolutePrices = isModifierPriced(product);

  // Open with the cheapest option already chosen for any group that only ever
  // allows one answer. The customer can still change it, but they are no longer
  // made to click twice — and never meets a disabled Add to Cart button.
  const [selected, setSelected] = useState<SelectedOption[]>(() =>
    productModifiers.flatMap((pm) => {
      if (!isForcedSingleChoice(pm)) return [];
      const active = pm.modifier.options
        .filter((o) => o.is_active)
        .sort((a, b) => Number(a.price) - Number(b.price) || a.display_order - b.display_order);
      const cheapest = active[0];
      return cheapest ? [{ modifier_id: pm.modifier_id, option_id: cheapest.id }] : [];
    }),
  );

  // Compute validity and total price.
  //
  // This has to agree with `modifier_rules.resolve` on the server, which is
  // what the basket is actually charged. It previously ignored `free_options`
  // and quoted a price the cart then contradicted.
  function compute(sel: SelectedOption[]) {
    let valid = true;
    let optionsPrice = 0;

    for (const pm of productModifiers) {
      const chosen = sel.filter(s => s.modifier_id === pm.modifier_id);
      if (chosen.length < pm.minimum_options || chosen.length > pm.maximum_options) {
        valid = false;
      }

      // The free allowance is spent in the order the options are laid out, so
      // which picks are free never depends on the order they were tapped in.
      const priced = chosen
        .map(s => pm.modifier.options.find(o => o.id === s.option_id))
        .filter((o): o is NonNullable<typeof o> => Boolean(o))
        .sort((a, b) => a.display_order - b.display_order);
      const free = Math.max(pm.free_options ?? 0, 0);
      priced.forEach((opt, index) => {
        if (index >= free) optionsPrice += Number(opt.price);
      });
    }

    const totalPrice = Number(product.base_price) + optionsPrice;
    return { valid, totalPrice };
  }

  useEffect(() => {
    const { valid, totalPrice } = compute(selected);
    onChange(selected, totalPrice, valid);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  /**
   * Record a pick, in the untranslated names.
   *
   * Deliberately not fired for the preselected cheapest option, and not for
   * clearing one: what is being counted is a decision a customer made, and a
   * default nobody touched is not one. English names throughout so an Arabic
   * session and an English session land on the same row — the dashboard is read
   * by one team, and two spellings of "Box of 6" is two products that do not
   * add up.
   */
  const recordPick = useCallback(
    (modifierId: string, optionId: string) => {
      const pm = productModifiers.find(p => p.modifier_id === modifierId);
      const option = pm?.modifier.options.find(o => o.id === optionId);
      if (!pm || !option) return;
      analytics.modifierSelected({
        product_name: product.name,
        group_name: pm.modifier.name,
        option_name: option.name,
        price_delta: Number(option.price),
      });
    },
    [productModifiers, product.name],
  );

  const handleSelect = (modifierId: string, optionId: string, checked: boolean) => {
    // Same reasoning as `handleIncrement`: only a tick that will actually stick
    // counts, and the decision is made out here so the updater stays pure.
    if (checked) {
      const group = productModifiers.find(p => p.modifier_id === modifierId);
      const atMax =
        group !== undefined &&
        group.maximum_options > 1 &&
        selected.filter(s => s.modifier_id === modifierId).length >= group.maximum_options;
      if (!atMax) recordPick(modifierId, optionId);
    }
    setSelected(prev => {
      const pm = productModifiers.find(p => p.modifier_id === modifierId);
      const isSingle = pm ? pm.maximum_options === 1 : false;

      if (isSingle) {
        // Replace any selection for this modifier
        const without = prev.filter(s => s.modifier_id !== modifierId);
        return checked ? [...without, { modifier_id: modifierId, option_id: optionId }] : without;
      } else {
        if (checked) {
          // Check max constraint
          const current = prev.filter(s => s.modifier_id === modifierId);
          if (pm && current.length >= pm.maximum_options) return prev;
          return [...prev, { modifier_id: modifierId, option_id: optionId }];
        } else {
          return prev.filter(s => !(s.modifier_id === modifierId && s.option_id === optionId));
        }
      }
    });
  };

  const handleIncrement = (modifierId: string, optionId: string) => {
    // A press that hits the group's ceiling changes nothing, so it is not a
    // pick. Checked out here rather than inside the updater: React may run an
    // updater more than once, and an event fired from inside one is an event
    // sent twice.
    const pm = productModifiers.find(p => p.modifier_id === modifierId);
    if (pm && selected.filter(s => s.modifier_id === modifierId).length >= pm.maximum_options) {
      return;
    }
    recordPick(modifierId, optionId);
    setSelected(prev => {
      const total = prev.filter(s => s.modifier_id === modifierId).length;
      if (pm && total >= pm.maximum_options) return prev;
      return [...prev, { modifier_id: modifierId, option_id: optionId }];
    });
  };

  const handleDecrement = (modifierId: string, optionId: string) => {
    setSelected(prev => {
      const idx = [...prev].reverse().findIndex(
        s => s.modifier_id === modifierId && s.option_id === optionId
      );
      if (idx === -1) return prev;
      const realIdx = prev.length - 1 - idx;
      return [...prev.slice(0, realIdx), ...prev.slice(realIdx + 1)];
    });
  };

  return (
    <div className="space-y-5">
      {productModifiers.map(pm => (
        <ModifierGroup
          key={pm.id}
          pm={pm}
          selected={selected}
          onSelect={handleSelect}
          onIncrement={handleIncrement}
          onDecrement={handleDecrement}
          absolutePrices={absolutePrices}
          t={t}
          locale={locale}
        />
      ))}
    </div>
  );
}
