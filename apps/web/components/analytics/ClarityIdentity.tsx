'use client';

import { useEffect } from 'react';

import { useAuth } from '@/lib/auth-context';
import { clarity } from '@/lib/clarity';

/**
 * Ties Clarity sessions to the signed-in customer.
 *
 * Renders nothing and exists only for the effect. Without it every recording is
 * anonymous and the three visits a customer made before they finally ordered are
 * three unrelated sessions — which is exactly the story worth watching.
 *
 * The opaque user id and nothing else. `identify` also takes a `friendly-name`,
 * which is where an email would go; see `lib/clarity.ts` for why it does not.
 *
 * Guests are deliberately not identified. `auth-context` already refuses to hold
 * a guest as a user — they exist only to carry a checkout — and a guest id is
 * minted per checkout, so identifying on one would tie a session to an identity
 * that means nothing and never recurs.
 */
export function ClarityIdentity() {
  const { user } = useAuth();

  useEffect(() => {
    if (user?.id) clarity.identify(user.id);
  }, [user?.id]);

  return null;
}
