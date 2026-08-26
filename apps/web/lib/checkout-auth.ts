import { authApi, ensureSessionId } from './api';
import type { User } from './types';

/**
 * Guest users are intentionally hidden from AuthContext, but their httpOnly
 * cookies are still valid for checkout. Reuse those cookies before minting a
 * fresh guest, otherwise checkout can move from a populated guest cart to a new
 * empty guest cart.
 */
export async function ensureCheckoutAuth(user: User | null): Promise<void> {
  ensureSessionId();

  if (user) return;

  try {
    await authApi.me();
  } catch {
    await authApi.guest();
  }
}

/**
 * The address a customer's receipt can actually be sent to, or null.
 *
 * Being signed in is not the same as having an account here: checkout mints a
 * `…@guest.local` user for every anonymous order, and those addresses reach
 * nothing. Showing one back as "your confirmation goes here" would be a lie in
 * the one place a customer is checking where their receipt lands — so the
 * synthetic domain is rejected as well as the flag, since the flag is only as
 * good as whatever set it.
 */
export function accountEmailOf(user: User | null): string | null {
  if (!user || user.is_guest) return null;
  const email = user.email?.trim();
  if (!email || /@guest\.local$/i.test(email)) return null;
  return email;
}
