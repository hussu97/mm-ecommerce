'use client';

import { useMemo, useRef, useState } from 'react';
import {
  AsYouType,
  getCountries,
  getCountryCallingCode,
  parsePhoneNumberFromString,
  type CountryCode,
} from 'libphonenumber-js';
import { cn } from '@/lib/utils';

/**
 * Phone entry that survives how people actually supply a number: pasted out of
 * WhatsApp with a +971 on the front, typed with a leading 0, copied with spaces
 * and brackets. Parsing is libphonenumber's; only the picker is ours, so it
 * matches the rest of the form instead of importing a second design system.
 *
 * `value` is E.164 ("+971501234567") — the one shape worth storing.
 */

// The UAE first because that is who orders, then the corridors its residents
// most often carry a number from. Everything else follows alphabetically.
const PRIORITY: CountryCode[] = ['AE', 'SA', 'OM', 'BH', 'KW', 'QA', 'IN', 'PK', 'GB', 'US'];

/**
 * Several territories share a calling code, and libphonenumber resolves a
 * number to whichever of them the range belongs to — so a perfectly ordinary
 * UK mobile can come back as Guernsey. The dial code and the stored E.164 are
 * right either way, but the flag looks broken, so pick the country a caller
 * would expect for the shared codes people actually paste.
 */
const PRIMARY_FOR_CALLING_CODE: Record<string, CountryCode> = {
  '1': 'US',
  '7': 'RU',
  '44': 'GB',
  '39': 'IT',
  '61': 'AU',
  '47': 'NO',
  '212': 'MA',
  '262': 'RE',
  '590': 'GP',
  '599': 'CW',
};

function preferredCountry(parsedCountry: CountryCode, callingCode: string): CountryCode {
  const primary = PRIMARY_FOR_CALLING_CODE[callingCode];
  return primary ?? parsedCountry;
}

const REGION_NAMES =
  typeof Intl !== 'undefined' && 'DisplayNames' in Intl
    ? new Intl.DisplayNames(['en'], { type: 'region' })
    : null;

function countryName(code: CountryCode): string {
  return REGION_NAMES?.of(code) ?? code;
}

/** Flag emoji from an ISO country code — no image requests, no sprite sheet. */
function flag(code: CountryCode): string {
  return code
    .toUpperCase()
    .replace(/./g, (c) => String.fromCodePoint(127397 + c.charCodeAt(0)));
}

interface PhoneInputProps {
  value: string;
  onChange: (e164: string) => void;
  label?: string;
  error?: string;
  defaultCountry?: CountryCode;
  disabled?: boolean;
  id?: string;
}

export function PhoneInput({
  value,
  onChange,
  label,
  error,
  defaultCountry = 'AE',
  disabled,
  id,
}: PhoneInputProps) {
  const parsed = useMemo(
    () => (value ? parsePhoneNumberFromString(value) : undefined),
    [value],
  );

  // The selected country is derived from the value when the value knows it,
  // and only falls back to local state before anything has been typed.
  const [fallbackCountry, setFallbackCountry] = useState<CountryCode>(defaultCountry);
  const country = parsed?.country
    ? preferredCountry(parsed.country, parsed.countryCallingCode)
    : fallbackCountry;
  const inputRef = useRef<HTMLInputElement>(null);

  const countries = useMemo(() => {
    const rest = getCountries()
      .filter((c) => !PRIORITY.includes(c))
      .sort((a, b) => countryName(a).localeCompare(countryName(b)));
    return [...PRIORITY, ...rest];
  }, []);

  // What the user sees: the national part only. The dial code lives in the
  // picker, so it is never both shown twice and impossible to delete.
  const national = useMemo(() => {
    if (!value) return '';
    if (parsed) return new AsYouType(country).input(parsed.nationalNumber);
    const dial = `+${getCountryCallingCode(country)}`;
    const digits = value.startsWith(dial) ? value.slice(dial.length) : value.replace(/^\+/, '');
    return new AsYouType(country).input(digits);
  }, [value, parsed, country]);

  const emit = (nationalDigits: string, forCountry: CountryCode) => {
    const digits = nationalDigits.replace(/\D/g, '').replace(/^0+/, '');
    onChange(digits ? `+${getCountryCallingCode(forCountry)}${digits}` : '');
  };

  const handleChange = (raw: string) => {
    // A pasted or typed international number carries its own country. Honour
    // it, switch the picker, and keep only the national part in the field —
    // rather than leaving "+971" sitting in front of a UAE dial code.
    if (raw.trim().startsWith('+') || raw.trim().startsWith('00')) {
      const normalised = raw.trim().replace(/^00/, '+');
      const full = parsePhoneNumberFromString(normalised);
      if (full?.country) {
        setFallbackCountry(preferredCountry(full.country, full.countryCallingCode));
        onChange(full.number);
        return;
      }
    }
    emit(raw, country);
  };

  const handleCountry = (next: CountryCode) => {
    setFallbackCountry(next);
    emit(national, next);
    inputRef.current?.focus();
  };

  const inputId = id ?? 'phone';

  return (
    <div className="w-full">
      {label && (
        <label
          htmlFor={inputId}
          className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1.5"
        >
          {label}
        </label>
      )}

      <div
        className={cn(
          'flex items-stretch bg-white border rounded-sm transition-all duration-200',
          'focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/20',
          error ? 'border-red-400 focus-within:border-red-400 focus-within:ring-red-400/20' : 'border-gray-300',
          disabled && 'bg-gray-50 opacity-60',
        )}
      >
        <div className="relative flex items-center shrink-0 border-e border-gray-200">
          <span
            aria-hidden="true"
            className="pointer-events-none flex items-center gap-1 ps-3 pe-2 text-sm font-body text-gray-700"
          >
            <span className="text-base leading-none">{flag(country)}</span>
            <span>+{getCountryCallingCode(country)}</span>
            <span className="material-icons text-sm text-gray-400">arrow_drop_down</span>
          </span>
          <select
            aria-label="Country calling code"
            value={country}
            disabled={disabled}
            onChange={(e) => handleCountry(e.target.value as CountryCode)}
            className="absolute inset-0 w-full opacity-0 cursor-pointer disabled:cursor-not-allowed"
          >
            {countries.map((c) => (
              <option key={c} value={c}>
                {countryName(c)} +{getCountryCallingCode(c)}
              </option>
            ))}
          </select>
        </div>

        <input
          ref={inputRef}
          id={inputId}
          type="tel"
          inputMode="tel"
          autoComplete="tel-national"
          disabled={disabled}
          value={national}
          onChange={(e) => handleChange(e.target.value)}
          placeholder="50 123 4567"
          className="flex-1 min-w-0 px-3 py-2.5 text-sm font-body bg-transparent outline-none placeholder:text-gray-400 disabled:cursor-not-allowed"
        />
      </div>

      {error && <p className="mt-1.5 text-xs text-red-500">{error}</p>}
    </div>
  );
}

/** Shared with checkout validation so the rule lives in one place. */
export function isValidPhone(e164: string): boolean {
  return parsePhoneNumberFromString(e164)?.isValid() ?? false;
}
