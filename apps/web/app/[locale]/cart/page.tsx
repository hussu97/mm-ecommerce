'use client';

import Image from 'next/image';
import Link from 'next/link';
import { useState, useCallback, useEffect, useRef } from 'react';
import { useCart } from '@/lib/cart-context';
import { promoApi, ensureSessionId } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { accountEmailOf, ensureCheckoutAuth } from '@/lib/checkout-auth';
import { analytics, failureReason } from '@/lib/analytics';
import { Button } from '@/components/ui/Button';
import { Breadcrumb } from '@/components/ui/Breadcrumb';
import { Input } from '@/components/ui/Input';
import { QuantitySelector } from '@/components/ui/QuantitySelector';
import { Spinner } from '@/components/ui/Spinner';
import { useToast } from '@/components/ui/Toast';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { localizedField } from '@/lib/i18n/entity';
import { FeaturedProductsCarousel } from '@/components/product/FeaturedProductsCarousel';
import { NewCustomerCouponTray, type ApplyOutcome } from '@/components/promo/NewCustomerCouponTray';
import { CartAddonTray } from '@/components/cart/CartAddonTray';
import { FreeDeliveryNudge } from '@/components/cart/FreeDeliveryNudge';
import { PersonalisationField } from '@/components/cart/PersonalisationField';
import { pendingVerification, needsPersonalisation, type Product } from '@/lib/types';
import { Icon } from '@/components/ui/Icon';

const PLACEHOLDER_IMAGE = '/images/logos/main_logo.png';

export default function CartPage() {
  const { t, locale } = useTranslation();
  const { cart, isLoading, addItem, updateItem, updateNote, removeItem, mergeCart } = useCart();
  const { addToast } = useToast();
  const { user } = useAuth();

  const [promoCode, setPromoCode] = useState('');
  const [promoLoading, setPromoLoading] = useState(false);
  // `needsVerify` rides on the applied code rather than sitting in a state of
  // its own, because the two are one fact: this coupon, and what it still owes
  // before a delivery order can be placed with it. Held apart they can be set
  // in different orders and disagree — and the one that gets handed to the
  // checkout is the one that decides whether the pay button works.
  const [appliedPromo, setAppliedPromo] = useState<
    { code: string; discount: number; message: string; needsVerify: boolean } | null
  >(null);
  const [promoError, setPromoError] = useState<string | null>(null);

  const [checkoutLoading, setCheckoutLoading] = useState(false);

  const items = cart?.items ?? [];
  const subtotal = cart?.subtotal ?? 0;
  const discount = appliedPromo?.discount ?? 0;
  const total = Math.max(0, subtotal - discount);

  // Which add-ons are already here, so the tray stops offering them.
  const productIdsInCart = new Set(items.map((i) => i.product_id));

  /**
   * Lines that are paid for and still say nothing.
   *
   * The checkout refuses these — see `order_service._compute_item_totals` — so
   * catching it here saves a round trip to a page the customer would be bounced
   * off. The server keeps the rule regardless; this is courtesy, not the guard.
   */
  //
  // Filtered off `cart?.items` rather than off `items` above, which is the same
  // array. React Compiler cannot prove an imported predicate leaves its
  // argument alone, so filtering `items` marks it as possibly-mutated and every
  // hook downstream that depends on `items.length` loses its memoization —
  // which fails the lint as an error, not a warning.
  const unfinished = (cart?.items ?? []).filter(needsPersonalisation);

  /**
   * The basket, once per visit to this page.
   *
   * The main funnel used to step through `/*​/cart` as a page view, which counts
   * the address rather than the basket — a refresh is a second view, and an
   * empty basket looks identical to a full one. An event carries what is in it,
   * so cart-to-checkout drop-off can finally be read against basket size.
   *
   * Guarded by a ref rather than a dependency list because `cart` is refetched
   * on every mutation: without it, taking one brownie out of the basket counts
   * as arriving at the basket again.
   */
  const cartViewed = useRef(false);
  useEffect(() => {
    if (isLoading || !cart || cartViewed.current) return;
    cartViewed.current = true;
    if (items.length === 0) {
      analytics.cartEmpty();
      return;
    }
    analytics.viewCart({
      item_count: items.length,
      subtotal,
      has_promo: appliedPromo !== null,
    });
  }, [isLoading, cart, items.length, subtotal, appliedPromo]);

  const handleQuantityChange = useCallback(async (itemId: string, quantity: number, productName: string, from: number) => {
    try {
      await updateItem(itemId, quantity);
      analytics.updateCartQuantity({ product_name: productName, from, to: quantity, surface: 'cart' });
    } catch (err) {
      analytics.cartActionFailed({ action: 'update', reason: failureReason(err), surface: 'cart' });
      addToast(t('cart.failed_update'), 'error');
    }
  }, [updateItem, addToast, t]);

  const handleRemove = useCallback(async (itemId: string, productName: string) => {
    try {
      await removeItem(itemId);
      analytics.removeFromCart({ product_name: productName, surface: 'cart' });
    } catch (err) {
      analytics.cartActionFailed({ action: 'remove', reason: failureReason(err), surface: 'cart' });
      addToast(t('cart.failed_remove'), 'error');
    }
  }, [removeItem, addToast, t]);

  /**
   * Take an add-on from the tray.
   *
   * Returns the failure rather than toasting it, so the tray can show it beside
   * its own button — a toast for a control this small is easy to miss, and the
   * customer is looking at the strip they just tapped.
   */
  const handleAddAddon = useCallback(async (product: Product): Promise<string | null> => {
    try {
      await addItem(product.id, 1);
      return null;
    } catch (err) {
      return err instanceof Error ? err.message : t('cart.failed_add');
    }
  }, [addItem, t]);

  /**
   * Save a line's message.
   *
   * Rethrows so the field can keep what was typed on screen next to the reason
   * it did not save. Swallowing it here would clear the error state and leave
   * somebody believing a message was stored that never was.
   */
  const handleNoteSave = useCallback(async (itemId: string, note: string) => {
    await updateNote(itemId, note);
  }, [updateNote]);

  /**
   * Put a code on the basket, whichever control asked for it.
   *
   * One path for the typed field and the coupon tray, so the two cannot end up
   * validating against different subtotals or recording the discount
   * differently. Returns the reason it did not go on rather than setting an
   * error itself — the tray shows its own failure next to its own button, and
   * the input shows it under the input.
   */
  const applyCode = useCallback(async (raw: string, fromTray = false): Promise<ApplyOutcome> => {
    const code = raw.trim().toUpperCase();
    if (!code) return { kind: 'applied' };

    setPromoError(null);
    setAppliedPromo(null);

    try {
      // The account's email, where there is one. The basket has no phone, no
      // typed email and no chosen delivery method, so this is as much identity
      // as this page can offer — and the coupon is re-validated at the checkout
      // against the full set. A signed-in returning customer therefore finds
      // out here rather than two screens later.
      //
      // No `delivery_method` on purpose: unsent means "not chosen", and the
      // server answers that cautiously by reporting the phone gate as still
      // outstanding. Reporting is all it does here — see `promo_code_service`.
      const result = await promoApi.validate(code, subtotal, {
        email: accountEmailOf(user),
      });
      if (!result.valid) {
        analytics.promoFailed({
          code,
          // The server's own words, kept short enough to group on. Whether a
          // coupon was refused for being expired, spent or not-for-you is the
          // difference between a campaign that ended and one that is broken.
          reason: (result.message ?? 'invalid').slice(0, 60),
          surface: 'cart',
          subtotal,
          from_tray: fromTray,
        });
        return { kind: 'error', note: result.message ?? t('cart.invalid_promo') };
      }

      const discountAmount = Number(result.discount_amount);
      const pending = pendingVerification(result);
      setPromoCode(code);
      setAppliedPromo({
        code,
        discount: discountAmount,
        message: result.message ?? '',
        needsVerify: pending,
      });
      analytics.promoApplied({
        code,
        discount: discountAmount,
        surface: 'cart',
        subtotal,
        from_tray: fromTray,
      });
      addToast(t('cart.promo_applied', { code }), 'success');
      // The code is on and the discount is real; what is left is an errand, and
      // one that only a delivery order will actually be asked to run. Counted
      // as an application rather than a failure, because that is what it is.
      return { kind: pending ? 'pending' : 'applied' };
    } catch (err) {
      analytics.promoFailed({
        code,
        reason: failureReason(err),
        surface: 'cart',
        subtotal,
        from_tray: fromTray,
      });
      return { kind: 'error', note: t('cart.promo_error') };
    }
  }, [subtotal, user, addToast, t]);

  const handleApplyPromo = useCallback(async () => {
    if (!promoCode.trim()) return;
    setPromoLoading(true);
    const outcome = await applyCode(promoCode);
    setPromoError(outcome.kind === 'error' ? outcome.note : null);
    setPromoLoading(false);
  }, [promoCode, applyCode]);

  const handleRemovePromo = useCallback(() => {
    setAppliedPromo(null);
    setPromoCode('');
    setPromoError(null);
  }, []);

  const handleProceedToCheckout = useCallback(async () => {
    if (items.length === 0) return;

    analytics.beginCheckout({
      item_count: items.length,
      subtotal,
      has_promo: appliedPromo !== null,
    });
    setCheckoutLoading(true);
    try {
      // Create guest session if not authenticated
      if (!user) {
        const sessionId = ensureSessionId();
        await ensureCheckoutAuth(user);
        await mergeCart(sessionId);
      }
      // Persist applied promo so checkout page picks it up from sessionStorage
      if (appliedPromo) {
        try {
          const existing = JSON.parse(sessionStorage.getItem('mm_checkout') ?? '{}');
          sessionStorage.setItem('mm_checkout', JSON.stringify({
            ...existing,
            promoCode: appliedPromo.code,
            promoDiscount: appliedPromo.discount,
            promoMessage: appliedPromo.message,
            // Carried with the code, because the basket is where it was
            // answered and the checkout would otherwise have to ask again to
            // learn something it has already been told. It is what turns the
            // pay button into a prompt rather than a refusal.
            promoNeedsVerify: appliedPromo.needsVerify,
          }));
        } catch { /* noop */ }
      }
      // Named with its locale. `/checkout` is not a route — the proxy answers
      // it with a redirect, which puts a second document load between the
      // basket and the form and throws away every request still in flight,
      // `begin_checkout` above among them.
      window.location.href = `/${locale}/checkout`;
    } catch {
      addToast(t('cart.something_wrong'), 'error');
      setCheckoutLoading(false);
    }
  // `locale` belongs here: it is read to build the URL this navigates to, and
  // a stale one sends an Arabic basket to the English checkout.
  }, [items.length, subtotal, appliedPromo, user, mergeCart, addToast, t, locale]);

  // Empty cart
  if (!isLoading && items.length === 0) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-16 space-y-16">
        <div className="flex flex-col items-center text-center gap-6">
          <Icon name="shopping_bag" className="text-6xl text-secondary" />
          <h1 className="font-display text-3xl text-primary uppercase tracking-widest">{t('cart.empty_title')}</h1>
          <p className="font-body text-sm text-gray-500 max-w-sm">
            {t('cart.empty_body')}
          </p>
          <Link href={`/${locale}`}>
            <Button variant="primary" size="lg">
              {t('cart.continue_shopping')}
            </Button>
          </Link>
        </div>
        <FeaturedProductsCarousel />
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-10">

      <Breadcrumb items={[{ label: t('breadcrumb.home'), href: `/${locale}` }, { label: t('breadcrumb.cart') }]} />

      {/* Heading */}
      <header className="mb-8">
        <h1 className="font-display text-3xl sm:text-4xl text-primary uppercase tracking-widest">
          {t('cart.title')}
          {cart && cart.item_count > 0 && (
            <span className="ml-3 font-body text-base font-normal text-gray-400 normal-case tracking-normal">
              ({cart.item_count} {cart.item_count === 1 ? t('cart.item') : t('cart.items')})
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
              const unitPrice = item.unit_price ?? 0;
              const lineTotal = item.line_total ?? unitPrice * item.quantity;
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
                      onError={(e) => {
                        (e.currentTarget as HTMLImageElement).src = PLACEHOLDER_IMAGE;
                      }}
                    />
                  </div>

                  {/* Details */}
                  <div className="flex-1 min-w-0 flex flex-col gap-1">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <p className="font-body font-medium text-sm text-gray-900 leading-snug">
                          {localizedField({ translations: item.product_translations }, 'name', item.product_name ?? '', locale)}
                        </p>
                        {item.selected_options && item.selected_options.length > 0 && (
                          <p className="font-body text-xs text-gray-400 mt-0.5">
                            {item.selected_options.map(o => localizedField({ translations: o.option_translations }, 'name', o.option_name, locale)).join(', ')}
                          </p>
                        )}
                      </div>
                      {/* Remove */}
                      <button
                        onClick={() => handleRemove(item.id, item.product_name ?? '')}
                        disabled={isLoading}
                        className="text-gray-300 hover:text-red-400 transition-colors shrink-0 disabled:opacity-50"
                        aria-label="Remove item"
                      >
                        <Icon name="close" className="text-xl" />
                      </button>
                    </div>

                    <p className="font-body text-xs text-gray-400">
                      {unitPrice.toFixed(2)} AED each
                    </p>

                    {/*
                      Renders itself away when this product asks for nothing, so
                      no condition is needed here — and the one that decides is
                      the product's own config rather than a list kept in sync
                      by hand.
                    */}
                    <PersonalisationField
                      item={item}
                      onSave={(note) => handleNoteSave(item.id, note)}
                    />

                    <div className="flex items-center justify-between mt-auto pt-2">
                      <QuantitySelector
                        value={item.quantity}
                        onChange={(q) => handleQuantityChange(item.id, q, item.product_name ?? '', item.quantity)}
                        min={1}
                        max={99}
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

            <CartAddonTray inCart={productIdsInCart} onAdd={handleAddAddon} />
          </section>

          {/* Order summary */}
          <aside className="lg:col-span-1">
            <div className="bg-gray-50 border border-gray-100 rounded-sm p-6 space-y-5 sticky top-24">
              <h2 className="font-display text-lg text-primary uppercase tracking-widest">{t('cart.order_summary')}</h2>

              {/* Subtotal */}
              <div className="space-y-2 text-sm font-body">
                <div className="flex justify-between">
                  <span className="text-gray-500">{t('cart.subtotal')}</span>
                  <span className="text-gray-900">{subtotal.toFixed(2)} AED</span>
                </div>

                {appliedPromo && (
                  <div className="flex justify-between text-green-700">
                    <span>{t('cart.discount')} ({appliedPromo.code})</span>
                    <span>-{appliedPromo.discount.toFixed(2)} AED</span>
                  </div>
                )}

                <div className="flex justify-between text-gray-500">
                  <span>{t('cart.delivery')}</span>
                  <span className="text-gray-400 italic text-xs self-center">{t('cart.calculated_at_checkout')}</span>
                </div>
              </div>

              {/* Measured against the discounted total, because that is what
                  the checkout compares to the threshold. Promising free
                  delivery on a figure the next screen disagrees with is worse
                  than not promising it. */}
              <FreeDeliveryNudge total={total} />

              <div className="h-px bg-gray-200" />

              {/* Total */}
              <div className="flex justify-between font-body font-semibold text-base">
                <span className="text-gray-700">{t('cart.total')}</span>
                <span className="text-primary">{total.toFixed(2)} AED</span>
              </div>

              {/* The offer, before the box that assumes you already know a code.
                  A coupon a customer has to have been told about is claimed by
                  the people who were buying anyway. */}
              <NewCustomerCouponTray
                appliedCode={appliedPromo?.code ?? null}
                onApply={(code) => applyCode(code, true)}
              />

              {/* Promo code */}
              <div className="space-y-2">
                {appliedPromo ? (
                  <>
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
                        <Icon name="close" className="text-base" />
                      </button>
                    </div>
                    {/* Outside the green box on purpose. The box says the code
                        went on, which is true; this says what is still owed
                        before a delivery order can be placed with it, which is
                        a different statement and should not read as part of the
                        good news. */}
                    {appliedPromo.needsVerify && (
                      <p className="font-body text-xs text-gray-500 flex items-start gap-1.5">
                        <Icon name="info" className="text-sm shrink-0 text-secondary" />
                        <span>{t('cart.promo_verify_at_checkout')}</span>
                      </p>
                    )}
                  </>
                ) : (
                  <div className="flex gap-2 items-start">
                    <div className="flex-1 min-w-0">
                      <Input
                        placeholder={t('cart.promo_placeholder')}
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
                      {t('cart.apply')}
                    </Button>
                  </div>
                )}
              </div>

              {/* CTA */}
              {/*
                A disabled button with no explanation is a dead end. The reason
                sits above it and names the product, because with several lines
                in the basket "add your message" does not say which one.
              */}
              {unfinished.length > 0 && (
                <p className="font-body text-xs text-amber-600" role="alert">
                  {t('cart.note_required_before_checkout', {
                    product: unfinished
                      .map((i) => localizedField({ translations: i.product_translations }, 'name', i.product_name ?? '', locale))
                      .join(', '),
                  })}
                </p>
              )}
              <Button
                variant="primary"
                size="lg"
                fullWidth
                onClick={handleProceedToCheckout}
                loading={checkoutLoading}
                disabled={items.length === 0 || unfinished.length > 0}
              >
                {t('cart.proceed_to_checkout')}
              </Button>

              <Link
                href={`/${locale}`}
                className="block text-center font-body text-xs text-gray-400 hover:text-primary transition-colors"
              >
                {t('cart.continue_shopping_link')}
              </Link>
            </div>
          </aside>

        </div>
      )}
    </div>
  );
}
