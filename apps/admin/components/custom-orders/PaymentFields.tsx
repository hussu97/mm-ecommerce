'use client';

/**
 * How the customer pays — and, for a card, who carries the card fee.
 *
 * The fee itself is priced by the API (a separate "Card payment fee" line grossed
 * up to the gateway's rates, or nothing when it is included); this only asks the
 * question. A custom order is never cash on delivery: cash is taken separately.
 */

import { cn } from '@/lib/utils';

export type PaymentType = '' | 'bank_transfer' | 'card' | 'cash';
export type CardFeeMode = '' | 'separate_line' | 'included';

const TYPES: { value: PaymentType; label: string; hint: string }[] = [
  { value: '', label: 'Not set', hint: 'Decide later' },
  { value: 'bank_transfer', label: 'Bank transfer', hint: 'Bank details print on the invoice' },
  { value: 'card', label: 'Card', hint: 'Payment link or card machine' },
  { value: 'cash', label: 'Cash', hint: 'Collected separately, never COD' },
];

const FEE_MODES: { value: Exclude<CardFeeMode, ''>; label: string; hint: string }[] = [
  {
    value: 'separate_line',
    label: 'Add card fee as a separate line',
    hint: 'The customer pays the fee on top',
  },
  { value: 'included', label: 'Included in the prices', hint: 'The shop absorbs the fee' },
];

/** The request fields, or an error when a card has no fee choice. */
export function toPaymentFields(type: PaymentType, feeMode: CardFeeMode) {
  return {
    payment_type: type || null,
    card_fee_mode: type === 'card' ? feeMode || null : null,
  } as {
    payment_type: 'bank_transfer' | 'card' | 'cash' | null;
    card_fee_mode: 'separate_line' | 'included' | null;
  };
}

export function paymentError(type: PaymentType, feeMode: CardFeeMode): string | null {
  return type === 'card' && !feeMode
    ? 'Choose whether the card fee is a separate line or included in the prices.'
    : null;
}

function Choice({
  checked,
  onSelect,
  label,
  hint,
  name,
  disabled,
}: {
  checked: boolean;
  onSelect: () => void;
  label: string;
  hint: string;
  name: string;
  disabled?: boolean;
}) {
  return (
    <label
      className={cn(
        'flex cursor-pointer items-start gap-2 border px-3 py-2 transition-colors',
        checked ? 'border-primary bg-primary/5' : 'border-gray-200 hover:border-gray-300',
        disabled && 'cursor-not-allowed opacity-60',
      )}
    >
      <input
        type="radio"
        name={name}
        checked={checked}
        disabled={disabled}
        onChange={onSelect}
        className="mt-0.5 accent-primary"
      />
      <span>
        <span className="block text-sm font-body text-gray-800">{label}</span>
        <span className="block text-xs font-body text-gray-400">{hint}</span>
      </span>
    </label>
  );
}

export function PaymentFields({
  type,
  feeMode,
  onChange,
  disabled,
  name = 'payment',
}: {
  type: PaymentType;
  feeMode: CardFeeMode;
  onChange: (type: PaymentType, feeMode: CardFeeMode) => void;
  disabled?: boolean;
  name?: string;
}) {
  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-4">
        {TYPES.map(t => (
          <Choice
            key={t.value || 'none'}
            name={`${name}-type`}
            checked={type === t.value}
            onSelect={() => onChange(t.value, t.value === 'card' ? feeMode : '')}
            label={t.label}
            hint={t.hint}
            disabled={disabled}
          />
        ))}
      </div>
      {type === 'card' && (
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-gray-600 mb-2">
            Card fee <span className="text-red-500">*</span>
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            {FEE_MODES.map(m => (
              <Choice
                key={m.value}
                name={`${name}-fee`}
                checked={feeMode === m.value}
                onSelect={() => onChange('card', m.value)}
                label={m.label}
                hint={m.hint}
                disabled={disabled}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
