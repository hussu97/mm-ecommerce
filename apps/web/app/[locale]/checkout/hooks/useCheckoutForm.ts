'use client';

import { useCallback, useEffect, useState } from 'react';

import { addressesApi } from '@/lib/api';
import { DEFAULT_ADDRESS_LABEL, guestAddresses } from '@/lib/guest-addresses';
import type { Address, PaymentMethod, User } from '@/lib/types';

import { toDraft } from '../components/AddressModal';

// ─── Session persistence ──────────────────────────────────────────────────────

const SESSION_KEY = 'mm_checkout';
const CLIENT_REQUEST_ID_KEY = 'mm_checkout_crid';

function saveToSession(data: object) {
  try { sessionStorage.setItem(SESSION_KEY, JSON.stringify(data)); } catch { /* noop */ }
}
function loadFromSession(): Record<string, unknown> | null {
  try { const s = sessionStorage.getItem(SESSION_KEY); return s ? JSON.parse(s) : null; } catch { return null; }
}
export function clearCheckoutSession() {
  try { sessionStorage.removeItem(SESSION_KEY); } catch { /* noop */ }
}

// ─── Idempotency key (F-WEB-5) ──────────────────────────────────────────────────

/** A UUID, from the platform crypto where present and a plain fallback where not
 * (older Safari, an insecure origin). A weaker id here only weakens idempotency
 * in that rare browser, so the fallback is acceptable rather than a hard fail. */
function randomId(): string {
  try {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  } catch { /* fall through */ }
  return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}-${Math.random().toString(16).slice(2)}`;
}

/**
 * The idempotency key for the current checkout attempt.
 *
 * Persisted so it is *stable across retries of the same attempt* — the whole
 * point (F-WEB-5): a `POST /orders` that times out is retried, sometimes across
 * the trip out to the payment gateway, and the replay must carry the same key
 * for the API to return the order the first try already wrote rather than a
 * duplicate. It is rotated only once an order is actually placed, so the next,
 * genuinely different order gets its own key and is never deduped against the
 * last.
 */
function loadClientRequestId(): string {
  try {
    const existing = sessionStorage.getItem(CLIENT_REQUEST_ID_KEY);
    if (existing) return existing;
    const minted = randomId();
    sessionStorage.setItem(CLIENT_REQUEST_ID_KEY, minted);
    return minted;
  } catch {
    return randomId();
  }
}

// ─── Form state ───────────────────────────────────────────────────────────────

export interface CheckoutForm {
  email: string;
  firstName: string;
  lastName: string;
  phone: string;
  addressLine1: string;
  addressLine2: string;
  unitNumber: string;
  addressLabel: string;
  locationLat: number | null;
  locationLng: number | null;
  selectedAddressId: string; // '' = new address
  deliveryMethod: 'delivery' | 'pickup';
  /** Which branch to collect from. Empty until one is chosen; pickup only. */
  pickupBranchId: string;
  paymentMethod: PaymentMethod;
  promoCode: string;
  promoDiscount: number;
  promoMessage: string;
  /**
   * Whether the applied code still owes us a proved mobile number.
   *
   * Travels with the code rather than being worked out here, because the code
   * can arrive from either of two places — typed into the panel below, or
   * applied in the basket and carried over in `mm_checkout` — and only the one
   * that validated it heard the answer. Recomputing it here would mean a third
   * round trip that says what we already know.
   *
   * A stale `true` costs nothing: the gate also asks whether this number is
   * verified, and a verified number clears it. A stale `false` is caught by the
   * server at order creation, which refuses in its own words — see
   * `handleSubmit`.
   */
  promoNeedsVerify: boolean;
  notes: string;
}

export const INITIAL_FORM: CheckoutForm = {
  email: '', firstName: '', lastName: '', phone: '',
  addressLine1: '', addressLine2: '', unitNumber: '', addressLabel: DEFAULT_ADDRESS_LABEL,
  locationLat: null, locationLng: null,
  selectedAddressId: '',
  deliveryMethod: 'delivery',
  pickupBranchId: '',
  paymentMethod: 'card',
  promoCode: '', promoDiscount: 0, promoMessage: '', promoNeedsVerify: false,
  notes: '',
};

/**
 * The checkout form, everything that fills it in, and where it survives.
 *
 * One hook rather than thirteen `useState`s and four effects in the page,
 * because these three things are one concern: the values, the `sessionStorage`
 * copy that carries them across a trip to the payment gateway, and the address
 * book that pre-fills them. Every write goes through `onChange`, which is what
 * keeps the stored copy and the state from drifting — a `setForm` that skipped
 * it is how a customer returning from a cancelled payment lost the address they
 * had just typed.
 */
export function useCheckoutForm(user: User | null) {
  const [form, setForm] = useState<CheckoutForm>(INITIAL_FORM);
  const [savedAddresses, setSavedAddresses] = useState<Address[]>([]);
  // Minted lazily on the client only (SSR has no `sessionStorage`), so it stays
  // empty on the server render and the first client render, then fills in.
  const [clientRequestId, setClientRequestId] = useState<string>('');
  useEffect(() => {
    setClientRequestId(loadClientRequestId());
  }, []);

  /** Rotate the key once an order is placed, so the next order gets a fresh one
   * and is never deduped against the one just created (F-WEB-5). */
  const resetClientRequestId = useCallback(() => {
    const minted = randomId();
    try { sessionStorage.setItem(CLIENT_REQUEST_ID_KEY, minted); } catch { /* noop */ }
    setClientRequestId(minted);
  }, []);

  // Restore whatever the last visit left behind. `INITIAL_FORM` is spread in
  // the middle so a field added since the stored copy was written gets its
  // default rather than `undefined`.
  useEffect(() => {
    const stored = loadFromSession();
    if (stored) {
      setForm((prev) => ({ ...prev, ...INITIAL_FORM, ...(stored as Partial<CheckoutForm>) }));
    }
  }, []);

  // The address book: the API when signed in, localStorage when not. Either way
  // a returning customer lands on a filled-in address rather than a blank form.
  useEffect(() => {
    let cancelled = false;

    const preselect = (list: Address[]) => {
      if (cancelled) return;
      setSavedAddresses(list);
      const preferred = list.find((a) => a.is_default) ?? list[0];
      if (!preferred) return;
      setForm((prev) => {
        if (prev.selectedAddressId !== '' || prev.addressLine1) return prev;
        const d = toDraft(preferred);
        const next = {
          ...prev,
          selectedAddressId: d.id,
          addressLabel: d.label,
          firstName: d.firstName,
          lastName: d.lastName,
          phone: prev.phone || d.phone,
          addressLine1: d.addressLine1,
          addressLine2: d.addressLine2,
          unitNumber: d.unitNumber,
          locationLat: d.latitude,
          locationLng: d.longitude,
        };
        saveToSession(next);
        return next;
      });
    };

    if (user) {
      addressesApi.list().then(preselect).catch(() => { /* none yet */ });
    } else {
      preselect(guestAddresses.list());
    }

    return () => { cancelled = true; };
  }, [user]);

  const onChange = useCallback((patch: Partial<CheckoutForm>) => {
    setForm((prev) => {
      const next = { ...prev, ...patch };
      saveToSession(next);
      return next;
    });
  }, []);

  return {
    form,
    setForm,
    onChange,
    savedAddresses,
    setSavedAddresses,
    clientRequestId,
    resetClientRequestId,
  };
}
