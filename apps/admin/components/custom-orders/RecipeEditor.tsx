'use client';

/**
 * What a custom order will use from the kitchen's stock — consumed when it is
 * packed, not when it is taken.
 *
 * Items come from the configured "Customized Cake Raw Materials" category
 * (`GET /admin/custom-orders/items`), each with the custom-orders branch's
 * on-hand in its **ingredient** unit, which is the unit a quantity is typed in.
 * A quantity may exceed what is on hand: the kitchen is allowed to go
 * negative, and a count corrects it. So it is a warning, never a block.
 */

import { useMemo, useState } from 'react';
import { Input } from '@/components/ui';
import type { CustomCakeItem } from '@/lib/api';
import { cn, formatQuantity } from '@/lib/utils';

export interface RecipeDraft {
  item_id: string;
  /** In the item's ingredient unit, as typed. */
  quantity: string;
}

const QTY = /^\d{1,7}(\.\d{1,6})?$/;

export function recipeError(lines: RecipeDraft[], items: CustomCakeItem[]): string | null {
  for (const l of lines) {
    const name = items.find(i => i.id === l.item_id)?.name ?? 'An item';
    const qty = Number(l.quantity);
    if (!QTY.test(l.quantity.trim()) || qty <= 0 || qty > 1_000_000) {
      return `${name}: enter a quantity above zero and up to 1,000,000 (at most 6 decimals).`;
    }
  }
  return null;
}

export function toRecipeIn(lines: RecipeDraft[]) {
  return lines.map(l => ({ item_id: l.item_id, quantity: l.quantity.trim() }));
}

function itemLabel(i: { name: string; sku: string | null }) {
  return i.sku ? `${i.name} · ${i.sku}` : i.name;
}

export function RecipeEditor({
  items,
  value,
  onChange,
  disabled,
}: {
  items: CustomCakeItem[];
  value: RecipeDraft[];
  onChange: (next: RecipeDraft[]) => void;
  disabled?: boolean;
}) {
  const [search, setSearch] = useState('');
  const byId = useMemo(() => new Map(items.map(i => [i.id, i])), [items]);
  const chosen = new Set(value.map(v => v.item_id));
  const q = search.trim().toLowerCase();
  const matches = q
    ? items
        .filter(i => !chosen.has(i.id))
        .filter(i => i.name.toLowerCase().includes(q) || (i.sku ?? '').toLowerCase().includes(q))
        .slice(0, 8)
    : [];

  function add(item: CustomCakeItem) {
    onChange([...value, { item_id: item.id, quantity: '' }]);
    setSearch('');
  }

  return (
    <div className="space-y-3">
      {value.length === 0 && (
        <p className="text-xs font-body text-gray-400">
          No recipe yet. Add what this cake uses and it is taken from stock when the order is packed.
        </p>
      )}

      {value.length > 0 && (
        <div className="divide-y divide-gray-100 border border-gray-200">
          {value.map(line => {
            const item = byId.get(line.item_id);
            const qty = Number(line.quantity);
            const onHand = item ? Number(item.on_hand) : null;
            const short = onHand !== null && line.quantity !== '' && qty > onHand;
            return (
              <div key={line.item_id} className="flex flex-wrap items-center gap-3 px-3 py-2">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-body text-gray-800 truncate">
                    {item ? itemLabel(item) : 'Item no longer in the category'}
                  </p>
                  {item && (
                    <p className={cn('text-xs font-body', short ? 'text-amber-700' : 'text-gray-400')}>
                      {formatQuantity(item.on_hand)} {item.unit ?? ''} on hand
                      {short && ' — more than on hand; stock will go negative when packed'}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <input
                    aria-label={`Quantity of ${item?.name ?? 'item'}`}
                    inputMode="decimal"
                    value={line.quantity}
                    disabled={disabled}
                    placeholder="0"
                    onChange={e =>
                      onChange(
                        value.map(v =>
                          v.item_id === line.item_id
                            ? { ...v, quantity: e.target.value.replace(/[^\d.]/g, '') }
                            : v,
                        ),
                      )
                    }
                    className={cn(
                      'w-24 px-2 py-1.5 min-h-[var(--tap-min)] md:min-h-0 text-sm font-body text-right bg-white border rounded-sm outline-none focus:border-primary',
                      short ? 'border-amber-400' : 'border-gray-300',
                    )}
                  />
                  <span className="w-10 text-xs font-body text-gray-500">{item?.unit ?? ''}</span>
                  {!disabled && (
                    <button
                      type="button"
                      onClick={() => onChange(value.filter(v => v.item_id !== line.item_id))}
                      className="text-gray-400 hover:text-red-600"
                      aria-label={`Remove ${item?.name ?? 'item'}`}
                    >
                      <span className="material-icons text-[18px]">close</span>
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {!disabled && (
        <div className="relative">
          <Input
            id="recipe-item-search"
            placeholder={items.length ? 'Add an item — search by name or SKU' : 'No items in the custom-cake category yet'}
            value={search}
            disabled={items.length === 0}
            onChange={e => setSearch(e.target.value)}
          />
          {matches.length > 0 && (
            <div className="absolute z-20 top-full left-0 right-0 mt-0.5 max-h-64 overflow-y-auto border border-gray-200 bg-white shadow-md">
              {matches.map(i => (
                <button
                  key={i.id}
                  type="button"
                  onClick={() => add(i)}
                  className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-gray-50"
                >
                  <span className="text-sm font-body text-gray-700 truncate">{itemLabel(i)}</span>
                  <span className="shrink-0 text-xs font-body text-gray-400">
                    {formatQuantity(i.on_hand)} {i.unit ?? ''} on hand
                  </span>
                </button>
              ))}
            </div>
          )}
          {q && matches.length === 0 && (
            <p className="mt-1 text-xs font-body text-gray-400">No matching item that isn&rsquo;t already added.</p>
          )}
        </div>
      )}
    </div>
  );
}

/** The recipe as recorded — for an order already packed (consumed) or read-only. */
export function RecipeReadOnly({
  lines,
  note,
}: {
  lines: { item_id: string; name: string; sku: string | null; unit: string | null; quantity: string }[];
  note?: string;
}) {
  return (
    <div className="space-y-2">
      {note && <p className="text-xs font-body text-gray-500">{note}</p>}
      {lines.length === 0 ? (
        <p className="text-xs font-body text-gray-400">No recipe recorded.</p>
      ) : (
        <div className="divide-y divide-gray-100 border border-gray-200">
          {lines.map(l => (
            <div key={l.item_id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm font-body">
              <span className="text-gray-800">{itemLabel(l)}</span>
              <span className="tabular-nums text-gray-600">
                {formatQuantity(l.quantity)} {l.unit ?? ''}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
