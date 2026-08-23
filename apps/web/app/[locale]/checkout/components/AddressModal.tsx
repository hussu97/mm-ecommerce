'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Spinner } from '@/components/ui/Spinner';
import { PhoneInput, isValidPhone } from '@/components/ui/PhoneInput';
import { PhoneVerify } from '@/components/ui/PhoneVerify';
import { isFirebaseConfigured } from '@/lib/firebase';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { analytics, failureReason } from '@/lib/analytics';
import { addressesApi } from '@/lib/api';
import { DEFAULT_ADDRESS_LABEL, guestAddresses } from '@/lib/guest-addresses';
import { reverseGeocode } from '@/lib/geocode';
import type { Address } from '@/lib/types';
import { Icon } from '@/components/ui/Icon';

const LocationPicker = dynamic(
  () => import('@/components/ui/LocationPicker').then((m) => ({ default: m.LocationPicker })),
  { ssr: false, loading: () => <div className="h-72 bg-gray-100 rounded-sm animate-pulse" /> },
);

export interface AddressDraft {
  id: string;
  label: string;
  firstName: string;
  lastName: string;
  phone: string;
  addressLine1: string;
  addressLine2: string;
  unitNumber: string;
  latitude: number | null;
  longitude: number | null;
}

const EMPTY: AddressDraft = {
  id: '', label: DEFAULT_ADDRESS_LABEL, firstName: '', lastName: '', phone: '',
  addressLine1: '', addressLine2: '', unitNumber: '',
  latitude: null, longitude: null,
};

/**
 * How long the verification panel stays lit after the checkout points at it.
 *
 * The same figure the checkout uses for its own ring, so the two halves of one
 * gesture — scroll the page, then scroll the sheet — fade together.
 */
const HIGHLIGHT_MS = 1800;

/** How long the green tick is left on screen before the sheet closes itself. */
const VERIFIED_CLOSE_MS = 900;

/** Is this the address the checkout is already carrying, untouched? */
function sameDraft(a: AddressDraft, b: AddressDraft | null | undefined): boolean {
  if (!b) return false;
  return (Object.keys(a) as (keyof AddressDraft)[]).every((k) => a[k] === b[k]);
}

export function toDraft(a: Address): AddressDraft {
  return {
    id: a.id,
    label: a.label,
    firstName: a.first_name,
    lastName: a.last_name,
    phone: a.phone,
    addressLine1: a.address_line_1,
    addressLine2: a.address_line_2 ?? '',
    unitNumber: a.unit_number ?? '',
    latitude: a.latitude === null ? null : Number(a.latitude),
    longitude: a.longitude === null ? null : Number(a.longitude),
  };
}

/** One line a courier can read, used on the collapsed card and the list rows. */
export function formatAddress(a: AddressDraft): string {
  return [a.unitNumber, a.addressLine1]
    .map((p) => p?.trim())
    .filter(Boolean)
    .join(', ');
}

interface AddressModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (draft: AddressDraft) => void;
  isAuthenticated: boolean;
  /** Addresses already on file, so the modal can offer them before asking again. */
  savedAddresses: Address[];
  onSavedAddressesChange: (list: Address[]) => void;
  selectedAddressId: string;
  /** Opens straight on the form when the customer has nothing saved yet. */
  initialDraft?: AddressDraft | null;
  /**
   * Whether this order has something riding on a proved number — today, a
   * coupon whose gate only delivery orders are asked to clear.
   *
   * Off by default, and deliberately not "always on": most customers have no
   * coupon on the basket, and putting an SMS step in front of all of them to
   * serve the few is a cost paid by the wrong people. The panel appears when
   * there is a reason for it, which is also when the customer has a reason to
   * bother with it.
   */
  askToVerify?: boolean;
  /** The number most recently proved, owned by the checkout. */
  verifiedPhone?: string | null;
  /** Called with the number the *server* confirmed, not the one typed. */
  onVerified?: (phone: string) => void;
  /**
   * Why the sheet was opened.
   *
   * `'select'` is the ordinary case: choose an address, or write a new one.
   *
   * `'verifyPhone'` is the checkout saying "prove this number", and it is a
   * different errand with a different destination. It used to land on the list
   * anyway — and the list has no verification on it, so the only thing a
   * customer could do there was tap the address they had already chosen, which
   * re-selects it and closes the sheet. Three taps to arrive back where they
   * started, none of them wrong. On this intent the sheet opens straight on the
   * form for the address the checkout is already carrying, where the panel
   * lives, and points at it.
   */
  intent?: 'select' | 'verifyPhone';
}

export function AddressModal({
  isOpen, onClose, onSave, isAuthenticated,
  savedAddresses, onSavedAddressesChange, selectedAddressId, initialDraft,
  askToVerify = false, verifiedPhone = null, onVerified, intent = 'select',
}: AddressModalProps) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<'list' | 'form'>('list');
  const [draft, setDraft] = useState<AddressDraft>(EMPTY);
  const [errors, setErrors] = useState<Record<string, string>>({});
  // Raised by `PhoneVerify` while a code is outstanding, so the number it was
  // sent to cannot move underneath it.
  const [codePending, setCodePending] = useState(false);
  const [saving, setSaving] = useState(false);
  const [geocoding, setGeocoding] = useState(false);
  /** The verification panel, lit for a moment because the checkout pointed here. */
  const [verifyLit, setVerifyLit] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  /** The self-close after a successful verification — cleared if the sheet goes first. */
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Reopening should not resume a half-finished edit from last time.
  useEffect(() => {
    if (!isOpen) return;
    setErrors({});
    // Sent here to prove a number: skip the list, which cannot do it. Only
    // when there is an address to prove it against — with none chosen yet,
    // picking one is genuinely the next step and the list is right.
    if (intent === 'verifyPhone' && initialDraft) {
      setDraft(initialDraft);
      setMode('form');
    } else if (savedAddresses.length > 0) {
      setMode('list');
    } else {
      setDraft(initialDraft ?? EMPTY);
      setMode('form');
    }
    // A sheet closed by hand before the self-close lands must not reopen the
    // question by firing `onClose` at whatever is on screen a second later.
    return () => { if (closeTimer.current) clearTimeout(closeTimer.current); };
  }, [isOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  /**
   * Scroll the sheet to the verification panel and light it up.
   *
   * The address form is a map, five inputs and then the number, so on a phone
   * the thing the customer was sent here for is below the fold — and a sheet
   * that opens on a map when you asked to verify a number reads as the wrong
   * sheet. Keyed on the mode as well as the intent so it fires again if they
   * detour to the list and come back.
   */
  useEffect(() => {
    if (!isOpen || intent !== 'verifyPhone' || mode !== 'form') return;

    /**
     * Attempted more than once, and instantly rather than smoothly.
     *
     * The map above the number is a dynamic import: it lays out after this
     * effect runs and again when its tiles arrive, each time moving the field
     * further down and leaving a smooth scroll stranded 20px from the top with
     * the customer looking at Dubai. Instant scrolls cannot be interrupted
     * mid-animation, and repeating while the sheet settles is what makes the
     * last layout the one that counts.
     *
     * Each attempt after the first is skipped once the field is actually in
     * view, so this cannot drag the sheet back from somewhere the customer
     * scrolled to themselves.
     */
    const reach = () => {
      const body = bodyRef.current;
      const el = body?.querySelector<HTMLElement>('[data-field="verifyPhone"]');
      if (!body || !el) return;
      const field = el.getBoundingClientRect();
      const frame = body.getBoundingClientRect();
      // An unmeasurable layout — nothing laid out yet — is not the same as
      // "already visible", and reading it as such is how this quietly does
      // nothing. Without a height to compare against, scroll.
      const visible =
        frame.height > 0 && field.top >= frame.top && field.bottom <= frame.bottom;
      if (visible) return;
      el.scrollIntoView({ block: 'center' });
    };

    const raf = requestAnimationFrame(reach);
    const settling = [150, 400, 800].map((ms) => setTimeout(reach, ms));
    setVerifyLit(true);
    const timer = setTimeout(() => setVerifyLit(false), HIGHLIGHT_MS);
    return () => {
      cancelAnimationFrame(raf);
      settling.forEach(clearTimeout);
      clearTimeout(timer);
      setVerifyLit(false);
    };
  }, [isOpen, intent, mode]);

  // Scroll lock + Escape, matching the shared Modal's behaviour.
  useEffect(() => {
    if (!isOpen) return;
    document.body.style.overflow = 'hidden';
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => {
      document.body.style.overflow = '';
      window.removeEventListener('keydown', onKey);
    };
  }, [isOpen, onClose]);

  /**
   * Moving the pin rewrites the address, every time.
   *
   * It used to fill the field only when it was empty, so the second pin drop
   * did nothing visible: the customer corrected their location and the address
   * underneath still described the first guess. The pin is the statement of
   * where they are, so it wins — and the field stays editable underneath.
   */
  const handlePin = useCallback(async (lat: number, lng: number, selectedAddress?: string) => {
    setDraft((prev) => ({ ...prev, latitude: lat, longitude: lng }));
    setErrors((prev) => { const next = { ...prev }; delete next.latitude; return next; });
    if (selectedAddress) {
      setDraft((prev) => ({ ...prev, addressLine1: selectedAddress }));
      setErrors((prev) => {
        const next = { ...prev };
        delete next.addressLine1;
        return next;
      });
      return;
    }
    setGeocoding(true);
    const found = await reverseGeocode(lat, lng);
    setGeocoding(false);
    if (!found) return;
    setDraft((prev) => ({
      ...prev,
      addressLine1: found.address,
    }));
    setErrors((prev) => {
      const next = { ...prev };
      delete next.addressLine1;
      return next;
    });
  }, []);

  const field = (key: keyof AddressDraft) => (value: string) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setErrors((prev) => { const next = { ...prev }; delete next[key]; return next; });
  };

  function validate(d: AddressDraft): Record<string, string> {
    const e: Record<string, string> = {};
    if (!d.firstName.trim()) e.firstName = t('checkout.first_name_required');
    if (!d.lastName.trim()) e.lastName = t('checkout.last_name_required');
    if (!d.phone.trim() || !isValidPhone(d.phone)) e.phone = t('checkout.valid_phone_required');
    if (!d.addressLine1.trim()) e.addressLine1 = t('checkout.address_required');
    // The API requires the pin and zone pricing is derived from it. The
    // checkout's own gate already refuses to submit without one; this stops
    // the sheet from saving an address that could never be delivered to.
    if (d.latitude === null || d.longitude === null) {
      e.latitude = t('checkout.address_pin_required');
    }
    return e;
  }

  /**
   * The number came back proved.
   *
   * When this sheet was opened for that and nothing else, and the customer has
   * not touched the address while they were here, there is nothing left to
   * save — the checkout is already carrying this exact address, and the only
   * thing that changed lives on the checkout's own state. So it hands the
   * result up and closes, after a beat with the green tick on screen. Pressing
   * "Save and continue" would spend an API round trip to write back the
   * address that was already there.
   *
   * Any edit at all keeps the sheet open: the address on the checkout is then
   * stale, and closing on it would throw the edit away.
   */
  const handleVerified = (confirmed: string) => {
    onVerified?.(confirmed);
    if (intent !== 'verifyPhone' || !sameDraft(draft, initialDraft)) return;
    closeTimer.current = setTimeout(onClose, VERIFIED_CLOSE_MS);
  };

  const handleSubmit = async () => {
    const found = validate(draft);
    if (Object.keys(found).length > 0) {
      setErrors(found);
      requestAnimationFrame(() => {
        bodyRef.current
          ?.querySelector<HTMLElement>('[data-field-error="true"]')
          ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
      return;
    }

    setSaving(true);
    const payload = {
      label: draft.label || DEFAULT_ADDRESS_LABEL,
      first_name: draft.firstName.trim(),
      last_name: draft.lastName.trim(),
      phone: draft.phone.trim(),
      address_line_1: draft.addressLine1.trim(),
      address_line_2: draft.addressLine2.trim() || undefined,
      unit_number: draft.unitNumber.trim() || undefined,
      // Non-null by `validate()` above, which returns early without a pin.
      latitude: draft.latitude as number,
      longitude: draft.longitude as number,
    };

    try {
      if (isAuthenticated) {
        const saved = draft.id
          ? await addressesApi.update(draft.id, payload)
          : await addressesApi.create(payload);
        const list = await addressesApi.list();
        onSavedAddressesChange(list);
        onSave(toDraft(saved));
      } else {
        const saved = draft.id
          ? guestAddresses.update(draft.id, payload)
          : guestAddresses.create(payload);
        onSavedAddressesChange(guestAddresses.list());
        onSave(toDraft(saved ?? guestAddresses.create(payload)));
      }
      analytics.addressSaved({
        surface: 'checkout',
        // Whether the driver gets a map pin or a line of prose. The share
        // without one is the number that decides whether the picker is worth
        // redesigning.
        has_pin: draft.latitude !== null && draft.longitude !== null,
        is_new: !draft.id,
      });
      onClose();
    } catch (err) {
      // Saving to the address book is a convenience; never let it block the
      // order. Use the details as typed and move on — but say so, because this
      // is the path where a customer's address silently stops being remembered
      // and every future checkout starts from a blank form.
      analytics.addressSaveFailed({ surface: 'checkout', reason: failureReason(err) });
      onSave({ ...draft, id: draft.id || 'unsaved' });
      onClose();
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (isAuthenticated) {
      try {
        await addressesApi.delete(id);
        onSavedAddressesChange(await addressesApi.list());
        analytics.addressDeleted({ surface: 'checkout' });
      } catch { /* leave the list as-is; the row simply stays */ }
    } else {
      guestAddresses.remove(id);
      onSavedAddressesChange(guestAddresses.list());
      analytics.addressDeleted({ surface: 'checkout' });
    }
  };

  if (!isOpen) return null;

  /**
   * The code panel, pinned above the save button rather than under the field.
   *
   * It used to sit where the number is typed, which is the honest place for it
   * and the wrong one. The number is the *last* field on this form — map,
   * address, unit, label, two names, then the phone — so on a phone with the
   * keyboard up, the panel is below the fold of a scrolling sheet while the
   * footer button stays pinned in view. What the customer sees under the number
   * they have just finished typing is "Save address", so that is what they
   * press, and they arrive back on the checkout with the same warning they
   * came to clear.
   *
   * In the footer it cannot be scrolled past. It also lands directly above the
   * button it is competing with, which is the moment the choice is actually
   * being made.
   *
   * Only ever one of these is mounted: `PhoneVerify` renders Firebase's
   * reCAPTCHA and a Turnstile widget into the DOM, so a second copy beside the
   * field would be two bot checks racing for one send.
   */
  const verifyPanel = askToVerify
    && isValidPhone(draft.phone)
    && verifiedPhone !== draft.phone
    // `PhoneVerify` renders nothing on a build without the Firebase vars —
    // a preview deploy, chiefly. Asked here as well so that build gets no
    // card at all, rather than a heading and a promise of a code over an
    // empty space where the button would have been.
    && isFirebaseConfigured() ? (
      <div
        className={`mb-3 rounded-sm border border-amber-300 bg-amber-50 px-3.5 py-3 ${
          verifyLit
            ? 'ring-2 ring-primary/70 ring-offset-2 ring-offset-white transition-shadow duration-300'
            : 'transition-shadow duration-300'
        }`}
      >
        <p className="flex items-center gap-1.5 font-body text-sm font-medium text-amber-900">
          <Icon name="verified_user" className="text-base shrink-0" />
          {t('verify.title')}
        </p>
        {/* The number itself, because the field it was typed into may well be
            scrolled out of sight behind the keyboard by the time this is read.
            Kept unformatted — it is E.164, which is unambiguous, and a customer
            checking their own number wants the digits rather than the styling. */}
        <p className="font-body text-xs text-amber-800/90 mt-0.5">
          <span dir="ltr">{draft.phone}</span> · {t('verify.subtitle')}
        </p>
        <PhoneVerify
          surface="checkout"
          phone={draft.phone}
          onVerified={handleVerified}
          onCodePending={setCodePending}
          className="mt-2"
        />
      </div>
    ) : null;

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center sm:p-4">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />

      <div
        role="dialog"
        aria-modal="true"
        aria-label={t('checkout.delivery_address')}
        className="relative z-10 w-full sm:max-w-lg bg-white sm:rounded-sm shadow-xl h-[92vh] sm:h-auto sm:max-h-[90vh] flex flex-col"
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            {mode === 'form' && savedAddresses.length > 0 && (
              <button
                onClick={() => setMode('list')}
                className="p-1 -ms-1 text-gray-400 hover:text-primary transition-colors"
                aria-label={t('common.back')}
              >
                <Icon name="arrow_back" className="text-xl" />
              </button>
            )}
            <h3 className="font-display text-lg text-primary tracking-wide truncate">
              {mode === 'list'
                ? t('checkout.delivery_address')
                : draft.id ? t('address.edit_address') : t('address.new_address')}
            </h3>
          </div>
          <button
            onClick={onClose}
            className="p-1 text-gray-400 hover:text-gray-600 transition-colors"
            aria-label={t('common.close')}
          >
            <Icon name="close" className="text-xl" />
          </button>
        </div>

        <div ref={bodyRef} className="flex-1 overflow-y-auto px-5 py-5">
          {mode === 'list' ? (
            <div className="space-y-3">
              {savedAddresses.map((a) => {
                const d = toDraft(a);
                const active = a.id === selectedAddressId;
                return (
                  <div
                    key={a.id}
                    className={`border rounded-sm p-3 transition-colors ${
                      active ? 'border-primary bg-primary/5' : 'border-gray-200'
                    }`}
                    // A saved address is a home address and a phone number, and
                    // Clarity only masks inputs and dropdowns by default. The
                    // card is masked whole — which of them was picked is still
                    // visible from the highlight and from
                    // `saved_address_selected`.
                    data-clarity-mask="true"
                  >
                    <button
                      onClick={() => {
                        analytics.savedAddressSelected({ surface: 'checkout' });
                        onSave(d);
                        onClose();
                      }}
                      className="w-full text-start"
                    >
                      <p className="font-body text-sm font-medium text-gray-800">
                        {a.label}
                        {a.is_default && (
                          <span className="ms-2 text-[10px] uppercase tracking-wider text-secondary">
                            {t('address.default')}
                          </span>
                        )}
                      </p>
                      <p className="font-body text-xs text-gray-500 mt-0.5">
                        {formatAddress(d)}
                      </p>
                      <p className="font-body text-xs text-gray-400 mt-0.5">
                        {d.firstName} {d.lastName} · {d.phone}
                      </p>
                    </button>
                    <div className="flex gap-4 mt-2 pt-2 border-t border-gray-100">
                      <button
                        onClick={() => { setDraft(d); setMode('form'); }}
                        className="text-xs font-body text-primary hover:underline"
                      >
                        {t('common.edit')}
                      </button>
                      <button
                        onClick={() => handleDelete(a.id)}
                        className="text-xs font-body text-gray-400 hover:text-red-500 transition-colors"
                      >
                        {t('common.delete')}
                      </button>
                    </div>
                  </div>
                );
              })}

              <button
                onClick={() => { setDraft(EMPTY); setMode('form'); }}
                className="w-full flex items-center justify-center gap-2 py-3 border border-dashed border-gray-300 rounded-sm text-sm font-body text-primary hover:border-primary transition-colors"
              >
                <Icon name="add" className="text-base" />
                {t('address.new_address')}
              </button>
            </div>
          ) : (
            <div className="space-y-5">
              {/* The map leads: it is the fastest way to answer most of what
                  follows, and on a phone it is far easier than typing. */}
              <div>
                <p className="text-xs font-medium uppercase tracking-wider text-gray-600 mb-2">
                  {t('address.pin_location')}
                </p>
                <LocationPicker
                  lat={draft.latitude}
                  lng={draft.longitude}
                  onChange={handlePin}
                  placeholder={t('address.search_location')}
                  height="280px"
                />
                {geocoding && (
                  <p className="mt-2 flex items-center gap-2 text-xs text-gray-400 font-body">
                    <Spinner size="sm" /> {t('address.finding_address')}
                  </p>
                )}
                {errors.latitude && (
                  <p
                    data-field-error="true"
                    className="mt-1.5 text-xs text-red-500"
                  >
                    {errors.latitude}
                  </p>
                )}
              </div>

              <div className="h-px bg-secondary/30" />

              <div className="space-y-4">
                <div data-field-error={errors.addressLine1 ? 'true' : undefined}>
                  <Input
                    label={t('common.address')}
                    placeholder={t('checkout.address_placeholder')}
                    value={draft.addressLine1}
                    onChange={(e) => field('addressLine1')(e.target.value)}
                    error={errors.addressLine1}
                  />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <Input
                    label={t('address.unit_number')}
                    placeholder={t('address.unit_placeholder')}
                    value={draft.unitNumber}
                    onChange={(e) => field('unitNumber')(e.target.value)}
                  />
                  <Input
                    label={t('address.label')}
                    placeholder={t('address.label_placeholder')}
                    value={draft.label}
                    onChange={(e) => field('label')(e.target.value)}
                  />
                </div>

              </div>

              <div className="h-px bg-secondary/30" />

              {/* Who receives it and how the rider reaches them — part of the
                  address, not a separate form somewhere above. */}
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div data-field-error={errors.firstName ? 'true' : undefined}>
                    <Input
                      label={t('common.first_name')}
                      placeholder={t('checkout.first_name_placeholder')}
                      value={draft.firstName}
                      onChange={(e) => field('firstName')(e.target.value)}
                      error={errors.firstName}
                    />
                  </div>
                  <div data-field-error={errors.lastName ? 'true' : undefined}>
                    <Input
                      label={t('common.last_name')}
                      placeholder={t('checkout.last_name_placeholder')}
                      value={draft.lastName}
                      onChange={(e) => field('lastName')(e.target.value)}
                      error={errors.lastName}
                    />
                  </div>
                </div>
                <div
                  // Where the sheet scrolls to when the checkout sends somebody
                  // here to prove a number. A scroll anchor only — the ring goes
                  // round the panel in the footer, which is the half that acts.
                  data-field="verifyPhone"
                  data-field-error={errors.phone ? 'true' : undefined}
                >
                  <PhoneInput
                    label={t('common.phone')}
                    value={draft.phone}
                    onChange={field('phone')}
                    error={errors.phone}
                    // Fixed while a code is outstanding against it. The code
                    // Firebase issued is bound to one number; letting the field
                    // drift under it means confirming a code for a number the
                    // customer is no longer looking at. "Change number" in the
                    // panel below is the way back.
                    disabled={codePending}
                  />
                  {/* Only the outcome lives beside the field. The panel that
                      sends the code is pinned to the footer instead — see
                      `verifyPanel`. */}
                  {askToVerify && verifiedPhone === draft.phone && isValidPhone(draft.phone) && (
                    <p className="mt-2 flex items-center gap-1.5 text-sm text-green-700">
                      <Icon name="check_circle" className="text-[18px]" />
                      {t('verify.verified')}
                    </p>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>

        {mode === 'form' && (
          <div className="px-5 py-4 border-t border-gray-100 shrink-0">
            {verifyPanel}
            <Button variant="primary" size="lg" fullWidth onClick={handleSubmit} loading={saving}>
              {t('address.save_and_continue')}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
