'use client';

/**
 * The lines of a custom order: what is being made, how many, and the agreed
 * price for each — VAT inclusive, as the customer was quoted.
 *
 * No totals are drawn here. The API prices the order (VAT split, the card-fee
 * line) and the screen shows its figures once saved; a running total computed
 * in the browser would be a second, unofficial answer (rule 10).
 */

import { Button, Input } from '@/components/ui';

export interface LineDraft {
  /** Local identity for React; never sent. */
  key: string;
  title: string;
  quantity: string;
  /** VAT-inclusive, per unit, as typed — sent as a decimal string. */
  unit_price: string;
  notes: string;
}

let nextKey = 0;
export function newLine(partial?: Partial<Omit<LineDraft, 'key'>>): LineDraft {
  nextKey += 1;
  return { key: `line-${nextKey}`, title: '', quantity: '1', unit_price: '', notes: '', ...partial };
}

const PRICE = /^\d{1,6}(\.\d{1,2})?$/;
const QTY = /^\d{1,3}$/;

/** The first thing wrong with the lines, or `null` when they can be sent. */
export function linesError(lines: LineDraft[]): string | null {
  if (lines.length === 0) return 'Add at least one line.';
  for (const [i, l] of lines.entries()) {
    const n = i + 1;
    if (!l.title.trim()) return `Line ${n} needs a title.`;
    if (!QTY.test(l.quantity.trim()) || Number(l.quantity) < 1) {
      return `Line ${n}: quantity must be a whole number from 1.`;
    }
    if (!PRICE.test(l.unit_price.trim())) {
      return `Line ${n}: enter the unit price in AED (up to 2 decimals).`;
    }
  }
  return null;
}

/** The request shape. Call only after `linesError` returned null. */
export function toLinesIn(lines: LineDraft[]) {
  return lines.map(l => ({
    title: l.title.trim(),
    quantity: Number(l.quantity.trim()),
    // A string, so the amount reaches the API's Decimal exactly as typed.
    unit_price: l.unit_price.trim(),
    notes: l.notes.trim() || null,
  }));
}

export function LinesEditor({
  value,
  onChange,
  disabled,
}: {
  value: LineDraft[];
  onChange: (next: LineDraft[]) => void;
  disabled?: boolean;
}) {
  const set = (key: string, patch: Partial<LineDraft>) =>
    onChange(value.map(l => (l.key === key ? { ...l, ...patch } : l)));

  return (
    <div className="space-y-3">
      {value.map((line, i) => (
        <div key={line.key} className="border border-gray-200 bg-gray-50/50 p-3">
          <div className="flex items-center justify-between mb-2">
            <p className="text-[11px] font-body uppercase tracking-widest text-gray-400">
              Line {i + 1}
            </p>
            {value.length > 1 && !disabled && (
              <button
                type="button"
                onClick={() => onChange(value.filter(l => l.key !== line.key))}
                className="inline-flex items-center gap-1 text-xs font-body text-gray-500 hover:text-red-600"
                aria-label={`Remove line ${i + 1}`}
              >
                <span className="material-icons text-[16px]">delete_outline</span>
                Remove
              </button>
            )}
          </div>
          <div className="grid gap-3 sm:grid-cols-[1fr_6rem_9rem]">
            <Input
              id={`${line.key}-title`}
              label="Title"
              placeholder="e.g. 3-tier red velvet, gold drip"
              value={line.title}
              maxLength={200}
              disabled={disabled}
              onChange={e => set(line.key, { title: e.target.value })}
            />
            <Input
              id={`${line.key}-qty`}
              label="Qty"
              inputMode="numeric"
              value={line.quantity}
              disabled={disabled}
              onChange={e => set(line.key, { quantity: e.target.value.replace(/[^\d]/g, '') })}
            />
            <Input
              id={`${line.key}-price`}
              label="Unit price (AED)"
              inputMode="decimal"
              placeholder="0.00"
              helper="VAT inclusive"
              value={line.unit_price}
              disabled={disabled}
              onChange={e => set(line.key, { unit_price: e.target.value.replace(/[^\d.]/g, '') })}
            />
          </div>
          <div className="mt-3">
            <Input
              id={`${line.key}-notes`}
              label="Notes for the kitchen (optional)"
              placeholder="Message on the cake, flavours, allergies…"
              value={line.notes}
              maxLength={1000}
              disabled={disabled}
              onChange={e => set(line.key, { notes: e.target.value })}
            />
          </div>
        </div>
      ))}
      {!disabled && (
        <Button type="button" variant="ghost" size="sm" onClick={() => onChange([...value, newLine()])}>
          <span className="material-icons text-[14px]">add</span>
          Add line
        </Button>
      )}
    </div>
  );
}
