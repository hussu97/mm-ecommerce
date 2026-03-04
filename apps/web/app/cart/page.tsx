'use client';

import Image from 'next/image';
import Link from 'next/link';
import { useState, useCallback } from 'react';
import { useCart } from '@/lib/cart-context';
import { promoApi, authApi, getToken, setToken, ensureSessionId } from '@/lib/api';
import { analytics } from '@/lib/analytics';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { QuantitySelector } from '@/components/ui/QuantitySelector';
import { Spinner } from '@/components/ui/Spinner';
import { useToast } from '@/components/ui/Toast';

const PLACEHOLDER_IMAGE = '/images/logos/main_logo.png';

export default function CartPage() {
  const { cart, isLoading, updateItem, removeItem } = useCart();
  const { addToast } = useToast();

  const [promoCode, setPromoCode] = useState('');
  const [promoLoading, setPromoLoading] = useState(false);
  const [appliedPromo, setAppliedPromo] = useState<{ code: string; discount: number; message: string } | null>(null);
  const [promoError, setPromoError] = useState<string | null>(null);

  const [checkoutLoading, setCheckoutLoading] = useState(false);

  const items = cart?.items ?? [];
  const subtotal = cart?.subtotal ?? 0;
  const discount = appliedPromo?.discount ?? 0;
  const total = Math.max(0, subtotal - discount);

  const handleQuantityChange = useCallback(async (itemId: string, quantity: number) => {
    try {
      await updateItem(itemId, quantity);
    } catch {
      addToast('Failed to update quantity', 'error');
    }
  }, [updateItem, addToast]);

  const handleRemove = useCallback(async (itemId: string, productName: string) => {
    try {
      await removeItem(itemId);
      analytics.removeFromCart({ product_name: productName });
    } catch {
      addToast('Failed to remove item', 'error');
    }
  }, [removeItem, addToast]);

  const handleApplyPromo = useCallback(async () => {
    const code = promoCode.trim().toUpperCase();
    if (!code) return;

    setPromoLoading(true);
    setPromoError(null);
    setAppliedPromo(null);

    try {
      const result = await promoApi.validate(code, subtotal);
      if (result.valid) {
        const discountAmount = Number(result.discount_amount);
        setAppliedPromo({ code, discount: discountAmount, message: result.message ?? '' });
        analytics.promoApplied({ code, discount: discountAmount });
        addToast(`Promo code "${code}" applied!`, 'success');
      } else {
        setPromoError(result.message ?? 'Invalid promo code');
      }
    } catch {
      setPromoError('Failed to validate promo code. Please try again.');
    } finally {
      setPromoLoading(false);
    }
  }, [promoCode, subtotal, addToast]);

  const handleRemovePromo = useCallback(() => {
    setAppliedPromo(null);
    setPromoCode('');
    setPromoError(null);
  }, []);

  const handleProceedToCheckout = useCallback(async () => {
    if (items.length === 0) return;

    analytics.beginCheckout({ item_count: items.length, subtotal });
    setCheckoutLoading(true);
    try {
      // Create guest session if not authenticated
      if (!getToken()) {
        const sessionId = ensureSessionId();
        const res = await authApi.guest();
        setToken(res.access_token);
        // Session ID is already set, merge will happen on login
        void sessionId; // already stored in localStorage
      }
      window.location.href = '/checkout';
    } catch {
      addToast('Something went wrong. Please try again.', 'error');
      setCheckoutLoading(false);
    }
  }, [items.length, addToast]);

  // Empty cart
  if (!isLoading && items.length === 0) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-16 flex flex-col items-center text-center gap-6">
        <span className="material-icons text-6xl text-secondary">shopping_bag</span>
        <h1 className="font-display text-3xl text-primary uppercase tracking-widest">Your cart is empty</h1>
        <p className="font-body text-sm text-gray-500 max-w-sm">
          Looks like you haven&apos;t added anything yet. Explore our handcrafted treats and find something you&apos;ll love.
        </p>
        <Link href="/">
          <Button variant="primary" size="lg">
            Continue Shopping
          </Button>
        </Link>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-10">

      {/* Heading */}
      <header className="mb-8">
        <h1 className="font-display text-3xl sm:text-4xl text-primary uppercase tracking-widest">
          My Cart
          {cart && cart.item_count > 0 && (
            <span className="ml-3 font-body text-base font-normal text-gray-400 normal-case tracking-normal">
              ({cart.item_count} {cart.item_count === 1 ? 'item' : 'items'})
            </span>
          )}
        </h1>
        <div className="h-px bg-secondary/40 mt-4" />
      </header>

      {isLoading && !cart ? (
        <div className="flex justify-center py-20">
          <Spinner size="lg" />
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-10">

          {/* Cart items */}
          <section className="lg:col-span-2 space-y-0" aria-label="Cart items">
            {items.map((item, idx) => {
              const variantName = item.variant?.name ?? '';
              const unitPrice = item.variant?.price ?? 0;
              const lineTotal = item.line_total ?? unitPrice * item.quantity;
              const stock = item.variant?.stock_quantity ?? 99;
              const image = item.product_image ?? PLACEHOLDER_IMAGE;

              return (
                <article
                  key={item.id}
                  className={`flex gap-4 py-6 ${idx > 0 ? 'border-t border-gray-100' : ''}`}
                >
                  {/* Thumbnail */}
                  <div className="relative w-24 h-24 sm:w-28 sm:h-28 shrink-0 rounded-sm overflow-hidden bg-gray-50">
                    <Image
                      src={image}
                      alt={item.product_name ?? 'Product'}
                      fill
                      sizes="112px"
                      className="object-cover"
                    />
                  </div>

                  {/* Details */}
                  <div className="flex-1 min-w-0 flex flex-col gap-1">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <p className="font-body font-medium text-sm text-gray-900 leading-snug">
                          {item.product_name}
                        </p>
                        {variantName && (
                          <p className="font-body text-xs text-gray-400 mt-0.5">{variantName}</p>
                        )}
                      </div>
                      {/* Remove */}
                      <button
                        onClick={() => handleRemove(item.id, item.product_name ?? '')}
                        disabled={isLoading}
                        className="text-gray-300 hover:text-red-400 transition-colors shrink-0 disabled:opacity-50"
                        aria-label="Remove item"
                      >
                        <span className="material-icons text-xl">close</span>
                      </button>
                    </div>

                    <p className="font-body text-xs text-gray-400">
                      {unitPrice.toFixed(2)} AED each
                    </p>

                    <div className="flex items-center justify-between mt-auto pt-2">
                      <QuantitySelector
                        value={item.quantity}
                        onChange={(q) => handleQuantityChange(item.id, q)}
                        min={1}
                        max={stock}
                        disabled={isLoading}
                      />
                      <p className="font-body font-semibold text-sm text-gray-900">
                        {lineTotal.toFixed(2)} AED
                      </p>
                    </div>
                  </div>
                </article>
              );
            })}
          </section>

          {/* Order summary */}
          <aside className="lg:col-span-1">
            <div className="bg-gray-50 border border-gray-100 rounded-sm p-6 space-y-5 sticky top-24">
              <h2 className="font-display text-lg text-primary uppercase tracking-widest">Order Summary</h2>

              {/* Subtotal */}
              <div className="space-y-2 text-sm font-body">
                <div className="flex justify-between">
                  <span className="text-gray-500">Subtotal</span>
                  <span className="text-gray-900">{subtotal.toFixed(2)} AED</span>
                </div>

                {appliedPromo && (
                  <div className="flex justify-between text-green-700">
                    <span>Discount ({appliedPromo.code})</span>
                    <span>-{appliedPromo.discount.toFixed(2)} AED</span>
                  </div>
                )}

                <div className="flex justify-between text-gray-500">
                  <span>Delivery</span>
                  <span className="text-gray-400 italic text-xs self-center">Calculated at checkout</span>
                </div>
              </div>

              <div className="h-px bg-gray-200" />

              {/* Total */}
              <div className="flex justify-between font-body font-semibold text-base">
                <span className="text-gray-700">Total</span>
                <span className="text-primary">{total.toFixed(2)} AED</span>
              </div>

              {/* Promo code */}
              <div className="space-y-2">
                {appliedPromo ? (
                  <div className="flex items-center justify-between bg-green-50 border border-green-200 rounded-sm px-3 py-2">
                    <div>
                      <p className="font-body text-xs font-medium text-green-800">{appliedPromo.code}</p>
                      {appliedPromo.message && (
                        <p className="font-body text-xs text-green-600">{appliedPromo.message}</p>
                      )}
                    </div>
                    <button
                      onClick={handleRemovePromo}
                      className="text-green-400 hover:text-green-700 transition-colors"
                      aria-label="Remove promo code"
                    >
                      <span className="material-icons text-base">close</span>
                    </button>
                  </div>
                ) : (
                  <div className="flex gap-2 items-start">
                    <div className="flex-1 min-w-0">
                      <Input
                        placeholder="Promo code"
                        value={promoCode}
                        onChange={(e) => {
                          setPromoCode(e.target.value.toUpperCase());
                          setPromoError(null);
                        }}
                        onKeyDown={(e) => e.key === 'Enter' && handleApplyPromo()}
                        className="text-xs"
                        error={promoError ?? undefined}
                      />
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={handleApplyPromo}
                      loading={promoLoading}
                      disabled={!promoCode.trim()}
                      className="shrink-0 mt-0"
                    >
                      Apply
                    </Button>
                  </div>
                )}
              </div>

              {/* CTA */}
              <Button
                variant="primary"
                size="lg"
                fullWidth
                onClick={handleProceedToCheckout}
                loading={checkoutLoading}
                disabled={items.length === 0}
              >
                Proceed to Checkout
              </Button>

              <Link
                href="/"
                className="block text-center font-body text-xs text-gray-400 hover:text-primary transition-colors"
              >
                ← Continue Shopping
              </Link>
            </div>
          </aside>

        </div>
      )}
    </div>
  );
}
