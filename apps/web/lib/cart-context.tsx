'use client';

import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { cartApi, clearSessionId, ensureSessionId, getSessionId } from './api';
import { useAuth } from './auth-context';
import { Cart } from './types';

interface SelectedOption {
  modifier_id: string;
  option_id: string;
}

interface CartContextType {
  cart: Cart | null;
  itemCount: number;
  isLoading: boolean;
  /** False until the first cart fetch settles, so callers can tell "still
   *  loading" apart from "loaded and there is nothing here". */
  cartLoaded: boolean;
  /** True when the last cart fetch failed. Checkout shows a retry instead of
   *  spinning forever on a dropped mobile connection. */
  cartError: boolean;
  addItem: (productId: string, quantity?: number, selectedOptions?: SelectedOption[], personalisationNote?: string) => Promise<void>;
  updateItem: (itemId: string, quantity: number) => Promise<void>;
  /**
   * Set the message on a line.
   *
   * Deliberately does **not** raise `isLoading`. This is called as the customer
   * types, and `isLoading` disables the quantity steppers and the checkout
   * button — flickering all of them off and on every time somebody pauses
   * mid-word would make the basket feel broken.
   */
  updateNote: (itemId: string, note: string) => Promise<void>;
  removeItem: (itemId: string) => Promise<void>;
  clearCart: () => Promise<void>;
  mergeCart: (sessionId: string) => Promise<void>;
  refreshCart: () => Promise<void>;
}

const CartContext = createContext<CartContextType | null>(null);

export function CartProvider({ children }: { children: React.ReactNode }) {
  const [cart, setCart] = useState<Cart | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [cartLoaded, setCartLoaded] = useState(false);
  const [cartError, setCartError] = useState(false);
  const { user, isLoading: authLoading } = useAuth();

  const refreshCart = useCallback(async () => {
    ensureSessionId();
    try {
      const data = await cartApi.get();
      setCart(data);
      setCartError(false);
    } catch {
      setCart(null);
      setCartError(true);
    } finally {
      setCartLoaded(true);
    }
  }, []);

  useEffect(() => {
    ensureSessionId();
    refreshCart();
  }, [refreshCart]);

  // Mutations never restore a snapshot on failure. They used to — `const prev
  // = cart` before the request, `setCart(prev)` in the catch — but state is
  // only ever set from the response, so the "rollback" was at best a no-op,
  // and with two mutations in flight (updateNote fires per keystroke, without
  // `isLoading` to serialise it) the failed one could rewind the newer
  // server state the other had just written. The server is the only party that
  // knows what the cart is after a failed request, so on error we re-read it
  // and then rethrow for the caller to surface as before.

  const addItem = useCallback(async (productId: string, quantity = 1, selectedOptions: SelectedOption[] = [], personalisationNote?: string) => {
    setIsLoading(true);
    try {
      const updated = await cartApi.addItem(productId, quantity, selectedOptions, personalisationNote);
      setCart(updated);
    } catch (err) {
      await refreshCart();
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [refreshCart]);

  const updateNote = useCallback(async (itemId: string, note: string) => {
    try {
      const updated = await cartApi.updateItemNote(itemId, note);
      setCart(updated);
    } catch (err) {
      await refreshCart();
      throw err;
    }
  }, [refreshCart]);

  const updateItem = useCallback(async (itemId: string, quantity: number) => {
    setIsLoading(true);
    try {
      const updated = await cartApi.updateItem(itemId, quantity);
      setCart(updated);
    } catch (err) {
      await refreshCart();
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [refreshCart]);

  const removeItem = useCallback(async (itemId: string) => {
    setIsLoading(true);
    try {
      const updated = await cartApi.removeItem(itemId);
      setCart(updated);
    } catch (err) {
      await refreshCart();
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [refreshCart]);

  const clearCart = useCallback(async () => {
    setIsLoading(true);
    try {
      const updated = await cartApi.clear();
      setCart(updated);
    } finally {
      setIsLoading(false);
    }
  }, []);

  /**
   * Fold the device's guest cart into the signed-in customer's cart.
   *
   * A failure here rethrows rather than being swallowed — it used to vanish
   * (`catch {}` here and `.catch(() => {})` at every call site), which meant a
   * customer could sign in, see their basket "lose" the cakes they just chose,
   * and have nothing anywhere record why. The guest cart itself is left
   * intact on the server: the session id is untouched, so nothing is lost and
   * the merge can be retried. The request-level `api_error` event has already
   * recorded the failure by the time this throws; callers decide whether to
   * put it in front of the customer.
   */
  const mergeCart = useCallback(async (sessionId: string) => {
    const updated = await cartApi.merge(sessionId);
    setCart(updated);
  }, []);

  // The cart follows the auth session, in both directions.
  //
  // Merge-on-login used to happen only because the login and signup pages each
  // remembered to call `mergeCart` — sign in from anywhere else (a future OAuth
  // flow, a session restored mid-checkout) and the guest basket was simply
  // left behind. And on logout nothing happened at all: the previous customer's
  // `mm_session_id` survived in localStorage, which on a shared device is the
  // previous customer's basket showing up in the next one's cart. Owned here,
  // beside the state it protects, so no page has to remember anything.
  //
  // The ref skips the initial settle: `user` resolving from "unknown" to an
  // already-signed-in customer on page load is a restore, not a sign-in, and
  // must not fire a merge (or a second fetch) on every visit. `authLoading`
  // guards the window where `user` is null only because the session cookie has
  // not been checked yet.
  const prevUserIdRef = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (authLoading) return;
    const id = user?.id ?? null;
    const prev = prevUserIdRef.current;
    prevUserIdRef.current = id;
    if (prev === undefined || prev === id) return;

    if (prev === null && id !== null) {
      // Signed out → signed in: bring the guest basket along, then re-read.
      // The refresh runs even when the merge fails — the account's own cart is
      // still the right thing to show — and the guest cart survives server-side
      // for a retry.
      const sessionId = getSessionId();
      void (async () => {
        if (sessionId) {
          try {
            await mergeCart(sessionId);
          } catch {
            // Recorded by the request-level api_error event; the guest cart is
            // intact under its session id. No toast from here — this provider
            // sits above ToastProvider, and inventing a second notification
            // channel for one message is not worth it.
          }
        }
        await refreshCart();
      })();
    } else if (id === null) {
      // Signed in → signed out: retire the guest session id so the next person
      // on this device starts with an empty basket, not this one's leftovers.
      // `refreshCart` mints a fresh id via `ensureSessionId`.
      clearSessionId();
      void refreshCart();
    } else {
      // One account straight to another (no signed-out gap). Nothing to merge
      // — there was no guest basket in between — just stop showing the
      // previous account's cart.
      void refreshCart();
    }
  }, [user, authLoading, mergeCart, refreshCart]);

  const itemCount = cart?.item_count ?? 0;

  return (
    <CartContext.Provider value={{ cart, itemCount, isLoading, cartLoaded, cartError, addItem, updateItem, updateNote, removeItem, clearCart, mergeCart, refreshCart }}>
      {children}
    </CartContext.Provider>
  );
}

export function useCart(): CartContextType {
  const ctx = useContext(CartContext);
  if (!ctx) throw new Error('useCart must be used within CartProvider');
  return ctx;
}
