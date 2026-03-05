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
  addItem: (productId: string, quantity?: number, selectedOptions?: SelectedOption[]) => Promise<void>;
  updateItem: (itemId: string, quantity: number) => Promise<void>;
  removeItem: (itemId: string) => Promise<void>;
  clearCart: () => Promise<void>;
  mergeCart: (sessionId: string) => Promise<void>;
  refreshCart: () => Promise<void>;
}

const CartContext = createContext<CartContextType | null>(null);

export function CartProvider({ children }: { children: React.ReactNode }) {
  const [cart, setCart] = useState<Cart | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const refreshCart = useCallback(async () => {
    ensureSessionId();
    try {
      const data = await cartApi.get();
      setCart(data);
    } catch {
      setCart(null);
    }
  }, []);

  useEffect(() => {
    ensureSessionId();
    refreshCart();
  }, [refreshCart]);

  const addItem = useCallback(async (productId: string, quantity = 1, selectedOptions: SelectedOption[] = []) => {
    setIsLoading(true);
    const prev = cart;
    try {
      const updated = await cartApi.addItem(productId, quantity, selectedOptions);
      setCart(updated);
    } catch (err) {
      setCart(prev);
      throw err;
    } finally {
      setIsLoading(false);
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
    <CartContext.Provider value={{ cart, itemCount, isLoading, addItem, updateItem, removeItem, clearCart, mergeCart, refreshCart }}>
      {children}
    </CartContext.Provider>
  );
}

export function useCart(): CartContextType {
  const ctx = useContext(CartContext);
  if (!ctx) throw new Error('useCart must be used within CartProvider');
  return ctx;
}
