'use client';

/**
 * The fee editor's two-part field, and the coercions between what a courier
 * rate is (a percent and a flat amount) and what an input holds (a string
 * that may be blank).
 *
 * Shared by the marketplace, courier and group rows, which all edit the same
 * shape.
 */

import type React from 'react';

/** A fee's two halves, or the amber blank that says nobody has supplied one. */
export function Rate({ pair }: { pair: RatePair | null }) {
  if (pair === null) return <span className="text-amber-600">—</span>;
  return (
    <span className="text-gray-800">
      {pair.percent}%
      {pair.flat > 0 && <span> + {pair.flat.toFixed(2)}</span>}
    </span>
  );
}

export function RateInput({
  percent,
  flat,
  onPercent,
  onFlat,
}: {
  percent: string;
  flat: string;
  onPercent: (value: string) => void;
  onFlat: (value: string) => void;
}) {
  return (
    <span className="inline-flex items-center gap-1">
      <input
        type="number"
        min={0}
        max={100}
        step="0.01"
        value={percent}
        onChange={e => onPercent(e.target.value)}
        placeholder="—"
        aria-label="Percentage of the order"
        className="w-16 border border-gray-300 px-2 py-1 text-xs font-body"
      />
      <span className="text-[11px] font-body text-gray-400">% +</span>
      <input
        type="number"
        min={0}
        step="0.01"
        value={flat}
        onChange={e => onFlat(e.target.value)}
        placeholder="—"
        aria-label="Flat amount per order"
        className="w-16 border border-gray-300 px-2 py-1 text-xs font-body"
      />
      <span className="text-[11px] font-body text-gray-400">flat</span>
    </span>
  );
}

/**
 * The API sends a `Decimal` as a string; the form edits a string; the console
 * renders a number. These three convert between them in one place so the
 * null-versus-zero distinction cannot be lost at one of the crossings.
 */
export function asField(value: number | string | null): string {
  return value === null || value === undefined ? '' : String(Number(value));
}

/** One fee, once both of its halves are known to be present or absent. */
export type RatePair = { percent: number; flat: number };

/**
 * A fee's two halves as one thing, or null when neither was supplied.
 *
 * The rule the whole feature rests on: a fee is unknown only when **both**
 * halves are null. One half set and the other null is a contract that quotes
 * only a percentage — the missing half is genuinely nothing, and treating it as
 * unknown would blank the margin on every channel with a simple contract.
 */
export function asPair(
  percent: number | string | null,
  flat: number | string | null,
): RatePair | null {
  const hasPercent = percent !== null && percent !== undefined;
  const hasFlat = flat !== null && flat !== undefined;
  if (!hasPercent && !hasFlat) return null;
  return { percent: hasPercent ? Number(percent) : 0, flat: hasFlat ? Number(flat) : 0 };
}

export function asWrite(value: string): number | null {
  return value.trim() === '' ? null : Number(value);
}

export function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="px-3 py-2 text-left text-[11px] font-body uppercase tracking-widest text-gray-400">
      {children}
    </th>
  );
}
