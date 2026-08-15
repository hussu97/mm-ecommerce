'use client';

import { useCallback, useEffect, useState } from 'react';

import { addressesApi } from '@/lib/api';
import { DEFAULT_ADDRESS_LABEL, guestAddresses } from '@/lib/guest-addresses';
import type { Address, PaymentMethod, User } from '@/lib/types';

import { toDraft } from '../components/AddressModal';

// ─── Session persistence ──────────────────────────────────────────────────────

const SESSION_KEY = 'mm_checkout';

function saveToSession(data: object) {
  try { sessionStorage.setItem(SESSION_KEY, JSON.stringify(data)); } catch { /* noop */ }
}
function loadFromSession(): Record<string, unknown> | null {
  try { const s = sessionStorage.getItem(SESSION_KEY); return s ? JSON.parse(s) : null; } catch { return null; }
}
export function clearCheckoutSession() {
  try { sessionStorage.removeItem(SESSION_KEY); } catch { /* noop */ }
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

  return { form, setForm, onChange, savedAddresses, setSavedAddresses };
}
