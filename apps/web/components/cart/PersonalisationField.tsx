'use client';

import { useEffect, useRef, useState } from 'react';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { analytics } from '@/lib/analytics';
import type { CartItem, PersonalisationType } from '@/lib/types';

/** How long after the last keystroke the note is saved. */
const SAVE_DEBOUNCE_MS = 600;

/** Copy keys per kind, so an unknown kind renders nothing rather than a bare box. */
const COPY: Record<PersonalisationType, { label: string; placeholder: string }> = {
  handwritten_note: {
    label: 'cart.note_label',
    placeholder: 'cart.note_placeholder',
  },
};

interface PersonalisationFieldProps {
  item: CartItem;
  /** Persists the note. Rejects with the reason it would not go on. */
  onSave: (note: string) => Promise<void>;
}

/**
 * The message that gets written on this line, typed where the line lives.
 *
 * Three things make this more than a textarea:
 *
 * **It is the source of truth while focused, and the server is afterwards.**
 * The saved cart comes back from every mutation on the page — a quantity
 * change, a coupon — and rendering straight from it would overwrite what
 * somebody is halfway through typing with what the server last heard. So local
 * state leads while the field is dirty, and only re-syncs from the item once a
 * save has landed.
 *
 * **The limit is the product's, not this component's.** A card is a physical
 * thing and different products are written on different ones. `maxLength` stops
 * the typing and the server checks it again, because a `maxLength` attribute is
 * a suggestion to anything that is not a browser.
 *
 * **Empty is a warning, not an error.** The field appears after the item is
 * already in the basket, so blank means "not finished" — it is the checkout
 * that refuses. Saying "required" in red the instant it renders would scold
 * somebody for not having typed yet.
 */
export function PersonalisationField({ item, onSave }: PersonalisationFieldProps) {
  const { t } = useTranslation();

  const type = item.personalisation_type;
  const maxLength = item.personalisation_max_length ?? 100;
  const saved = item.personalisation_note ?? '';

  const [value, setValue] = useState(saved);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<'idle' | 'saving' | 'saved'>('idle');

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dirty = useRef(false);
  // Whether this line has ever been reported to analytics, so holding down a
  // key does not count as a hundred personalisations.
  const counted = useRef(false);

  // Re-sync from the server only when we are not mid-edit. `saved` changes on
  // every cart refetch, including ones this field did not cause.
  useEffect(() => {
    if (!dirty.current) setValue(saved);
  }, [saved]);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  if (!type || !(type in COPY)) return null;
  // Bound after the guard so the closures below see the narrowed kind rather
  // than the nullable field — `schedule` is hoisted and would not inherit it.
  const kind: PersonalisationType = type;
  const copy = COPY[kind];

  function schedule(next: string) {
    dirty.current = true;
    setValue(next);
    setStatus('idle');
    setError(null);

    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      setStatus('saving');
      try {
        await onSave(next);
        dirty.current = false;
        setStatus('saved');
        if (next.trim() && !counted.current) {
          counted.current = true;
          analytics.personalisationEntered({
            product_name: item.product_name ?? '',
            type: kind,
            length: next.trim().length,
          });
        }
      } catch (err) {
        // The text stays on screen. Losing what somebody wrote because the
        // network dropped is worse than showing it next to a failure they can
        // retry by typing a character.
        setStatus('idle');
        setError(err instanceof Error ? err.message : t('cart.note_save_failed'));
      }
    }, SAVE_DEBOUNCE_MS);
  }

  const remaining = maxLength - value.length;
  const fieldId = `note-${item.id}`;

  return (
    <div className="mt-2">
      <label htmlFor={fieldId} className="block font-body text-xs text-gray-500 mb-1">
        {t(copy.label)}
      </label>
      <textarea
        id={fieldId}
        value={value}
        onChange={(e) => schedule(e.target.value)}
        maxLength={maxLength}
        rows={2}
        dir="auto"
        placeholder={t(copy.placeholder)}
        aria-describedby={`${fieldId}-count`}
        aria-invalid={error ? true : undefined}
        className={`w-full rounded-sm border px-3 py-2 font-body text-sm text-gray-900 resize-none
          focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary
          ${error ? 'border-red-300' : 'border-gray-200'}`}
      />
      <div className="flex items-center justify-between gap-2 mt-1">
        <span className="font-body text-xs">
          {error ? (
            <span className="text-red-500">{error}</span>
          ) : status === 'saving' ? (
            <span className="text-gray-400">{t('cart.note_saving')}</span>
          ) : status === 'saved' ? (
            <span className="text-green-600">{t('cart.note_saved')}</span>
          ) : !value.trim() ? (
            <span className="text-amber-600">{t('cart.note_needed')}</span>
          ) : null}
        </span>
        {/*
          `dir="ltr"` because "12/100" is a number pair and reverses into
          nonsense under RTL. `tabular-nums` stops the row jittering as the
          count crosses digit widths while somebody types.
        */}
        <span
          id={`${fieldId}-count`}
          dir="ltr"
          className={`font-body text-xs tabular-nums shrink-0 ${remaining <= 10 ? 'text-amber-600' : 'text-gray-400'}`}
        >
          {value.length}/{maxLength}
        </span>
      </div>
    </div>
  );
}
