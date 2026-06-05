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
