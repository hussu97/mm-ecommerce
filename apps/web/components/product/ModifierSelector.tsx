'use client';

import { useEffect, useState } from 'react';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { localizedField } from '@/lib/i18n/entity';
import type { Product, ProductModifier } from '@/lib/types';

export interface SelectedOption {
  modifier_id: string;
  option_id: string;
}

interface Props {
  product: Product;
  onChange: (options: SelectedOption[], totalPrice: number, isValid: boolean) => void;
}

function ModifierGroup({
  pm,
  selected,
  onSelect,
  t,
  locale,
}: {
  pm: ProductModifier;
  selected: SelectedOption[];
  onSelect: (modifierId: string, optionId: string, checked: boolean) => void;
  t: (key: string, params?: Record<string, string | number>) => string;
  locale: string;
}) {
  const activeOptions = pm.modifier.options.filter(o => o.is_active);
  const selectedForThis = selected.filter(s => s.modifier_id === pm.modifier_id);
  const isSingle = pm.maximum_options === 1;

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

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between">
        <h4 className="text-xs font-body uppercase tracking-widest text-gray-600">{modifierName}</h4>
        <span className="text-[11px] text-gray-400 font-body">{pickLabel}</span>
      </div>
      <div className="space-y-1.5">
        {activeOptions.map(opt => {
          const isSelected = selectedForThis.some(s => s.option_id === opt.id);
          const optionName = localizedField(opt, 'name', opt.name, locale);
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
                  className="accent-primary"
                />
                <span className="text-sm font-body text-gray-700">{optionName}</span>
              </div>
              {opt.price > 0 && (
                <span className="text-xs font-body text-primary">+{Number(opt.price).toFixed(2)} AED</span>
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
  const [selected, setSelected] = useState<SelectedOption[]>([]);

  const productModifiers = product.product_modifiers ?? [];

  // Compute validity and total price
  function compute(sel: SelectedOption[]) {
    let valid = true;
    let optionsPrice = 0;

    for (const pm of productModifiers) {
      const count = sel.filter(s => s.modifier_id === pm.modifier_id).length;
      if (count < pm.minimum_options || count > pm.maximum_options) {
        valid = false;
      }
      for (const s of sel) {
        if (s.modifier_id !== pm.modifier_id) continue;
        const opt = pm.modifier.options.find(o => o.id === s.option_id);
        if (opt) optionsPrice += Number(opt.price);
      }
    }

    const totalPrice = Number(product.base_price) + optionsPrice;
    return { valid, totalPrice };
  }

  useEffect(() => {
    const { valid, totalPrice } = compute(selected);
    onChange(selected, totalPrice, valid);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  const handleSelect = (modifierId: string, optionId: string, checked: boolean) => {
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

  return (
    <div className="space-y-5">
      {productModifiers.map(pm => (
        <ModifierGroup
          key={pm.id}
          pm={pm}
          selected={selected}
          onSelect={handleSelect}
          t={t}
          locale={locale}
        />
      ))}
    </div>
  );
}
