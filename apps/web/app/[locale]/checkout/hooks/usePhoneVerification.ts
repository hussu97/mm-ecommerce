'use client';

import { useEffect, useState } from 'react';

import { authApi } from '@/lib/api';
import { isValidPhone } from '@/components/ui/PhoneInput';

/**
 * The number most recently proved, and the question that finds out.
 *
 * Held above the address form rather than inside it because two things need it
 * — the form, to stop offering an SMS for a number already proved, and the pay
 * button, to know whether the discount is safe — and a second copy is a second
 * chance for them to disagree.
 *
 * It asks whether this number is *already* proved before offering to prove it.
 * A verification belongs to the handset, not to the order it was first typed
 * on, so a returning customer must not sit through a second SMS. Debounced,
 * because it watches a field being typed into.
 */
export function usePhoneVerification(phoneInput: string) {
  const [verifiedPhone, setVerifiedPhone] = useState<string | null>(null);

  useEffect(() => {
    const phone = phoneInput.trim();
    if (!phone || !isValidPhone(phone) || verifiedPhone === phone) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      authApi
        .phoneVerified(phone)
        .then((r) => { if (!cancelled && r.verified) setVerifiedPhone(phone); })
        // A failed check must never block an order. The server asks the same
        // question again at order creation, and that answer is the binding one.
        .catch(() => { /* offer the button */ });
    }, 400);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [phoneInput, verifiedPhone]);

  return { verifiedPhone, setVerifiedPhone };
}
