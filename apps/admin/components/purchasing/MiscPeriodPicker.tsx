'use client';

import { Input, Select } from '@/components/ui';
import type { PurchaseOrderMiscPeriod } from '@/lib/pos-api';
import {
  MONTH_OPTIONS,
  monthEnd,
  monthStart,
  periodLabel,
  weekEnd,
  weekStart,
} from '@/lib/purchasing';

/** The picker's state: which preset (or `custom`), and the ISO range it holds. */
export interface MiscPeriodValue {
  preset: string;
  from: string;
  to: string;
}

export const CUSTOM = 'custom';

/** A new line's period: the default preset and the range it pre-fills. */
export function defaultPeriodValue(periods: PurchaseOrderMiscPeriod[]): MiscPeriodValue {
  const preset = periods.find((p) => p.is_default) ?? periods[0];
  if (!preset) return { preset: CUSTOM, from: '', to: '' };
  return { preset: preset.id, from: preset.default_from, to: preset.default_to };
}

function isWholeMonths(from: string, to: string): boolean {
  const [ty, tm] = to.split('-').map(Number);
  return from.endsWith('-01') && to === monthEnd(ty, tm);
}

/** The picker state for a line that already has dates: the preset that
 *  pre-fills exactly them, else a month preset when they are whole months (so
 *  the picker asks in months), else custom. */
export function inferPeriodValue(
  periods: PurchaseOrderMiscPeriod[],
  from: string,
  to: string,
): MiscPeriodValue {
  const exact = periods.find((p) => p.default_from === from && p.default_to === to);
  if (exact) return { preset: exact.id, from, to };
  const monthly = periods.find((p) => p.unit === 'month');
  if (monthly && isWholeMonths(from, to)) return { preset: monthly.id, from, to };
  return { preset: CUSTOM, from, to };
}

/** Whether a picker state is a usable range. */
export function periodIsValid(value: MiscPeriodValue): boolean {
  return !!value.from && !!value.to && value.from <= value.to;
}

function yearOptions(around: string[]): { value: string; label: string }[] {
  const now = new Date().getFullYear();
  const years = new Set<number>();
  for (let y = now - 2; y <= now + 5; y++) years.add(y);
  for (const iso of around) if (iso) years.add(Number(iso.slice(0, 4)));
  return [...years].sort().map((y) => ({ value: String(y), label: String(y) }));
}

function MonthYear({
  label,
  iso,
  years,
  onChange,
}: {
  label: string;
  iso: string;
  years: { value: string; label: string }[];
  onChange: (year: number, month: number) => void;
}) {
  const [y, m] = iso ? iso.split('-').map(Number) : [new Date().getFullYear(), new Date().getMonth() + 1];
  return (
    <div>
      <span className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1">{label}</span>
      <div className="flex gap-1">
        <div className="w-24">
          <Select
            aria-label={`${label} month`}
            value={String(m)}
            onChange={(e) => onChange(y, Number(e.target.value))}
            options={MONTH_OPTIONS}
          />
        </div>
        <div className="w-28">
          <Select
            aria-label={`${label} year`}
            value={String(y)}
            onChange={(e) => onChange(Number(e.target.value), m)}
            options={years}
          />
        </div>
      </div>
    </div>
  );
}

/**
 * The period a misc PO line covers, asked with as few inputs as its preset
 * allows: a month preset asks month + year at each end, a week preset snaps
 * to Monday–Sunday, a day preset or "Custom dates" takes dates. Picking a
 * preset resets the range to what it pre-fills (the API quotes it); the value
 * is always a pair of ISO dates.
 */
export function MiscPeriodPicker({
  periods,
  value,
  onChange,
  error,
}: {
  periods: PurchaseOrderMiscPeriod[];
  value: MiscPeriodValue;
  onChange: (value: MiscPeriodValue) => void;
  error?: string;
}) {
  const preset = periods.find((p) => p.id === value.preset);
  const unit = preset?.unit ?? 'day';
  const years = yearOptions([value.from, value.to]);

  function choosePreset(id: string) {
    const next = periods.find((p) => p.id === id);
    onChange(
      next
        ? { preset: next.id, from: next.default_from, to: next.default_to }
        : { ...value, preset: CUSTOM },
    );
  }

  return (
    <div className="flex flex-wrap items-end gap-2">
      <div className="w-40">
        <Select
          label="Period"
          value={value.preset}
          onChange={(e) => choosePreset(e.target.value)}
          options={[
            ...periods.map((p) => ({ value: p.id, label: p.name })),
            { value: CUSTOM, label: 'Custom dates' },
          ]}
        />
      </div>
      {preset && unit === 'month' ? (
        <>
          <MonthYear
            label="From"
            iso={value.from}
            years={years}
            onChange={(y, m) => onChange({ ...value, from: monthStart(y, m) })}
          />
          <MonthYear
            label="To"
            iso={value.to}
            years={years}
            onChange={(y, m) => onChange({ ...value, to: monthEnd(y, m) })}
          />
        </>
      ) : (
        <>
          <div className="w-40">
            <Input
              label={preset && unit === 'week' ? 'From (week of)' : 'From'}
              type="date"
              value={value.from}
              onChange={(e) =>
                onChange({
                  ...value,
                  from: preset && unit === 'week' && e.target.value ? weekStart(e.target.value) : e.target.value,
                })
              }
            />
          </div>
          <div className="w-40">
            <Input
              label={preset && unit === 'week' ? 'To (week of)' : 'To'}
              type="date"
              value={value.to}
              onChange={(e) =>
                onChange({
                  ...value,
                  to: preset && unit === 'week' && e.target.value ? weekEnd(e.target.value) : e.target.value,
                })
              }
            />
          </div>
        </>
      )}
      <p className={`pb-2 text-xs font-body ${error ? 'text-red-600' : 'text-gray-500'}`}>
        {error ?? (periodIsValid(value) ? periodLabel(value.from, value.to) : 'Pick the days this covers')}
      </p>
    </div>
  );
}
