'use client';

import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { cartApi, ensureSessionId } from './api';
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

  const addItem = useCallback(async (productId: string, quantity = 1, selectedOptions: SelectedOption[] = [], personalisationNote?: string) => {
    setIsLoading(true);
    const prev = cart;
    try {
      const updated = await cartApi.addItem(productId, quantity, selectedOptions, personalisationNote);
      setCart(updated);
    } catch (err) {
      setCart(prev);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [cart]);

  const updateNote = useCallback(async (itemId: string, note: string) => {
    const prev = cart;
    try {
      const updated = await cartApi.updateItemNote(itemId, note);
      setCart(updated);
    } catch (err) {
      setCart(prev);
      throw err;
    }
  }, [cart]);

  const updateItem = useCallback(async (itemId: string, quantity: number) => {
    setIsLoading(true);
    const prev = cart;
    try {
      const updated = await cartApi.updateItem(itemId, quantity);
      setCart(updated);
    } catch (err) {
      setCart(prev);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [cart]);

  const removeItem = useCallback(async (itemId: string) => {
    setIsLoading(true);
    const prev = cart;
    try {
      const updated = await cartApi.removeItem(itemId);
      setCart(updated);
    } catch (err) {
      setCart(prev);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [cart]);

  const clearCart = useCallback(async () => {
    setIsLoading(true);
    try {
      const updated = await cartApi.clear();
      setCart(updated);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const mergeCart = useCallback(async (sessionId: string) => {
    try {
      const updated = await cartApi.merge(sessionId);
      setCart(updated);
    } catch {
      // Merge failed silently
    }
  }, []);

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
