'use client';

import { useState } from 'react';
import { Button } from '@/components/ui';
import { useToast } from '@/components/ui/feedback';
import { formatCurrency } from '@/lib/utils';
import type { DeliverySettings } from '@/lib/types';


// ── Settings ──────────────────────────────────────────────────────────────────

/**
 * The three delivery numbers that belong to no zone.
 *
 * They used to live on a Regions screen alongside a table of emirates and
 * their fees. The emirates are gone — the pin decides the price — and these
 * three were the only part of that screen still worth keeping.
 */
export function SettingsCard({
  settings,
  busy,
  onSave,
}: {
  settings: DeliverySettings;
  busy: boolean;
  onSave: (data: { pickup_fee?: number }) => void;
}) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({
    pickup_fee: String(settings.pickup_fee),
    low_order_fee: String(settings.low_order_fee ?? 0),
    // Null renders as blank, which is what the field means: fee switched off.
    low_order_threshold:
      settings.low_order_threshold === null
        ? ''
        : String(settings.low_order_threshold),
  });

  // "Free delivery above" and "Outside every zone" used to live here and are
  // gone. The first is per zone now — a bike run inside Sharjah and a car to
  // Jebel Ali cannot share one threshold — and it is edited in the zone table
  // below. The second described a fee for a pin outside every shape, and the
  // map tiles the whole country, so such a pin is outside the UAE and is
  // refused rather than priced.
  const FIELDS = [
    { key: 'pickup_fee' as const, label: 'Pickup', hint: 'Usually nothing.' },
    {
      key: 'low_order_fee' as const,
      label: 'Small order fee',
      hint: 'Charged on delivery orders at or below the basket size beside it. Never on pickup.',
    },
    {
      key: 'low_order_threshold' as const,
      label: 'Small order below',
      hint: 'Inclusive — a basket of exactly this much still pays. Leave blank to switch the fee off.',
      // The only field here that may be empty, and empty means something: no
      // threshold is how the fee is turned off. Zero would charge every basket.
      nullable: true,
    },
  ];

  function save() {
    const parsed: Record<string, number | null> = {};
    for (const field of FIELDS) {
      const raw = String(form[field.key] ?? '').trim();
      // A blank nullable field is an instruction, not a missing value: it
      // switches the small-order fee off. Coercing it to 0 would instead charge
      // the fee on every basket, which is the opposite.
      if (!raw && 'nullable' in field && field.nullable) {
        parsed[field.key] = null;
        continue;
      }
      const value = Number(raw);
      if (!Number.isFinite(value) || value < 0) {
        toast.error('Every amount has to be a number, and none of them can be negative.');
        return;
      }
      parsed[field.key] = value;
    }
    onSave(parsed as Parameters<typeof onSave>[0]);
    setEditing(false);
  }

  return (
    <div className="bg-white border border-gray-200 p-4 mb-4">
      <div className="flex items-center mb-3">
        <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 flex-1">
          Applies to every zone
        </p>
        {!editing && (
          <button
            onClick={() => setEditing(true)}
            className="text-[11px] font-body text-primary hover:underline"
          >
            Edit
          </button>
        )}
      </div>

      <dl className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs font-body">
        {FIELDS.map(field => (
          <div key={field.key}>
            <dt className="text-gray-500">{field.label}</dt>
            {editing ? (
              <input
                value={form[field.key]}
                onChange={e => setForm({ ...form, [field.key]: e.target.value })}
                inputMode="decimal"
                className="mt-1 w-24 px-2 py-1 text-xs font-body bg-white border border-gray-300 rounded-sm outline-none focus:border-primary"
              />
            ) : (
              <dd className="text-gray-800 text-sm">
                {Number(settings[field.key]) > 0
                  ? formatCurrency(Number(settings[field.key]))
                  : 'Free'}
              </dd>
            )}
            <p className="text-[10px] text-gray-400 mt-1">{field.hint}</p>
          </div>
        ))}
      </dl>

      {editing && (
        <div className="flex justify-end gap-2 mt-3">
          <Button size="sm" variant="ghost" onClick={() => setEditing(false)} disabled={busy}>
            Cancel
          </Button>
          <Button size="sm" onClick={save} disabled={busy}>
            Save
          </Button>
        </div>
      )}
    </div>
  );
}
