'use client';

import Image from 'next/image';
import Link from 'next/link';
import { Suspense, useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'next/navigation';
import { useCart } from '@/lib/cart-context';
import {
  ordersApi, paymentsApi, addressesApi, deliveryApi,
  getSessionId,
} from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { ensureCheckoutAuth } from '@/lib/checkout-auth';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { PhoneInput, isValidPhone } from '@/components/ui/PhoneInput';
import { Spinner } from '@/components/ui/Spinner';
import { useToast } from '@/components/ui/Toast';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { localizedField } from '@/lib/i18n/entity';
import { analytics } from '@/lib/analytics';
import { guestAddresses } from '@/lib/guest-addresses';
import { AddressModal, formatAddress, toDraft, type AddressDraft } from './components/AddressModal';
import { PromoCodeStep } from './components/PromoCodeStep';
import type { Address, Cart, CartItem, DeliveryRates, DeliveryQuote } from '@/lib/types';

// ─── Session persistence ──────────────────────────────────────────────────────

const SESSION_KEY = 'mm_checkout';

function saveToSession(data: object) {
  try { sessionStorage.setItem(SESSION_KEY, JSON.stringify(data)); } catch { /* noop */ }
}
function loadFromSession(): Record<string, unknown> | null {
  try { const s = sessionStorage.getItem(SESSION_KEY); return s ? JSON.parse(s) : null; } catch { return null; }
}
function clearCheckoutSession() {
  try { sessionStorage.removeItem(SESSION_KEY); } catch { /* noop */ }
}

// ─── Form state ───────────────────────────────────────────────────────────────

interface CheckoutForm {
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
  paymentMethod: 'stripe' | 'cod';
  promoCode: string;
  promoDiscount: number;
  promoMessage: string;
  notes: string;
}

const INITIAL_FORM: CheckoutForm = {
  email: '', firstName: '', lastName: '', phone: '',
  addressLine1: '', addressLine2: '', unitNumber: '', addressLabel: 'Home',
  locationLat: null, locationLng: null,
  selectedAddressId: '',
  deliveryMethod: 'delivery',
  paymentMethod: 'stripe',
  promoCode: '', promoDiscount: 0, promoMessage: '',
  notes: '',
};

/**
 * Cash only works when the customer comes to the counter, so it is offered for
 * collection and not for delivery. Card works for both.
 */
function paymentOptionsFor(method: 'delivery' | 'pickup'): ('stripe' | 'cod')[] {
  return method === 'pickup' ? ['cod', 'stripe'] : ['stripe'];
}

/** Where to send someone who wants to see the counter before choosing pickup. */
const STORE_LOCATION = { lat: 25.3304139, lng: 55.3736131 };
const STORE_MAPS_URL =
  `https://www.google.com/maps/search/?api=1&query=${STORE_LOCATION.lat},${STORE_LOCATION.lng}`;

// ─── Validation ───────────────────────────────────────────────────────────────

function isValidEmail(email: string): boolean {
  if (!email) return false;
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)) return false;
  const domain = email.split('@')[1]?.toLowerCase() ?? '';
  if (/\.(local|localhost|example|test|invalid|internal)$/.test(domain)) return false;
  if (domain === 'localhost') return false;
  return true;
}

/** Bring the first invalid field into view — the button sits below everything. */
function focusFirstError(field: string) {
  requestAnimationFrame(() => {
    const el =
      document.querySelector<HTMLElement>(`[data-field="${field}"]`) ??
      document.querySelector<HTMLElement>('[data-field-error="true"]');
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.querySelector<HTMLElement>('input, select, textarea')?.focus({ preventScroll: true });
  });
}

// ─── Section chrome ───────────────────────────────────────────────────────────

/**
 * Sections are set off by a hairline and a quiet caption rather than a display
 * heading and a rule each. Six full headings turned a form of about a dozen
 * fields into a page with no visible end.
 */
function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className="py-5 border-t border-gray-100 first:border-t-0 first:pt-0">
      <h2 className="font-body text-[11px] uppercase tracking-[0.2em] text-gray-400 mb-3">
        {label}
      </h2>
      {children}
    </section>
  );
}

/**
 * The one thing on this page that stops an order.
 *
 * A pin the courier will not quote has no price, and a checkout that quietly
 * shows a fee anyway sells a delivery nobody is going to make. So it says so —
 * and, because "we cannot deliver here" is useless on its own, it says it next
 * to the two things that actually resolve it: move the pin, or collect.
 *
 * Amber rather than red. Nothing has gone wrong and nothing was lost; this
 * address simply is not one of the ones that works.
 */
function UnserviceableNotice({
  onChangeAddress, onSwitchToPickup, t,
}: {
  onChangeAddress: () => void;
  onSwitchToPickup: () => void;
  t: (k: string, p?: Record<string, string | number>) => string;
}) {
  return (
    <div
      role="alert"
      data-field="unserviceable"
      className="mt-3 border border-amber-300 bg-amber-50/70 rounded-sm px-3.5 py-3"
    >
      <div className="flex gap-2.5">
        <span className="material-icons text-xl text-amber-600 shrink-0">wrong_location</span>
        <div className="min-w-0">
          <p className="font-body text-sm text-amber-900">{t('checkout.unserviceable_title')}</p>
          <p className="font-body text-xs text-amber-800/80 mt-1 leading-relaxed">
            {t('checkout.unserviceable_body')}
          </p>
          <div className="flex flex-wrap gap-2 mt-3">
            <button
              type="button"
              onClick={onChangeAddress}
              className="font-body text-xs px-3 py-1.5 border border-amber-400 text-amber-900 rounded-sm hover:bg-amber-100 transition-colors"
            >
              {t('checkout.unserviceable_change')}
            </button>
            <button
              type="button"
              onClick={onSwitchToPickup}
              className="font-body text-xs px-3 py-1.5 text-amber-800 underline underline-offset-2 hover:text-amber-900 transition-colors"
            >
              {t('checkout.unserviceable_pickup')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** One tappable choice: icon, label, and what it costs. */
function ChoiceRow({
  selected, onSelect, icon, title, subtitle, trailing,
}: {
  selected: boolean;
  onSelect: () => void;
  icon: string;
  title: string;
  subtitle?: string;
  trailing: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={`w-full flex items-center gap-3 px-3.5 py-3 border rounded-sm text-start transition-colors ${
        selected ? 'border-primary bg-primary/5' : 'border-gray-200 hover:border-primary/40'
      }`}
    >
      <span className={`material-icons text-xl ${selected ? 'text-primary' : 'text-gray-400'}`}>{icon}</span>
      <span className="flex-1 min-w-0">
        <span className="block font-body text-sm text-gray-800">{title}</span>
        {subtitle && <span className="block font-body text-xs text-gray-400 mt-0.5">{subtitle}</span>}
      </span>
      <span className="font-body text-sm shrink-0">{trailing}</span>
    </button>
  );
}

// ─── Order summary ────────────────────────────────────────────────────────────

function OrderSummary({
  cart, retryOrder, discount, promoCode, deliveryFee, baseFee, freeApplied,
  remainingForFree, deliveryMethod, unserviceable, locale, t,
}: {
  cart: Cart | null;
  retryOrder: import('@/lib/types').Order | null;
  discount: number;
  promoCode: string;
  /** Null while the fee is unknown — no pin yet, or nowhere we can deliver. */
  deliveryFee: number | null;
  /** What delivery would have cost — struck through once it is waived. */
  baseFee: number;
  freeApplied: boolean;
  remainingForFree: number;
  deliveryMethod: 'delivery' | 'pickup';
  /** Nothing can be delivered to this pin, so there is no total to commit to. */
  unserviceable: boolean;
  locale: string;
  t: (k: string, p?: Record<string, string | number>) => string;
}) {
  const subtotal = retryOrder ? Number(retryOrder.subtotal) : (cart?.subtotal ?? 0);
  const knownFee = deliveryFee ?? 0;
  const total = retryOrder ? Number(retryOrder.total) : Math.max(0, subtotal + knownFee - discount);

  const rows = retryOrder
    ? retryOrder.items.map((i) => ({
        id: i.id,
        name: localizedField({ translations: i.product_translations }, 'name', i.product_name, locale),
        options: '',
        qty: i.quantity,
        image: null as string | null,
        amount: Number(i.total_price),
      }))
    : (cart?.items ?? []).map((i: CartItem) => ({
        id: i.id,
        name: localizedField({ translations: i.product_translations }, 'name', i.product_name ?? '', locale),
        options: (i.selected_options ?? [])
          .map((o) => localizedField({ translations: o.option_translations }, 'name', o.option_name, locale))
          .join(', '),
        qty: i.quantity,
        image: i.product_image,
        amount: i.line_total ?? (i.unit_price ?? 0) * i.quantity,
      }));

  return (
    <div className="space-y-3">
      <ul className="space-y-2.5">
        {rows.map((r) => (
          <li key={r.id} className="flex gap-3 items-center">
            <div className="relative w-10 h-10 rounded-sm overflow-hidden bg-gray-100 shrink-0">
              {r.image ? (
                <Image src={r.image} alt={r.name} fill sizes="40px" className="object-cover" />
              ) : (
                <div className="w-full h-full bg-secondary/20" />
              )}
              <span className="absolute -top-1 -end-1 w-4 h-4 rounded-full bg-primary text-white text-[10px] flex items-center justify-center font-body">
                {r.qty}
              </span>
            </div>
            <div className="flex-1 min-w-0">
              <p className="font-body text-sm text-gray-800 truncate">{r.name}</p>
              {r.options && <p className="font-body text-xs text-gray-400 truncate">{r.options}</p>}
            </div>
            <p className="font-body text-sm text-gray-700 shrink-0">{r.amount.toFixed(2)} AED</p>
          </li>
        ))}
      </ul>

      <div className="pt-3 border-t border-gray-100 space-y-1.5 font-body text-sm">
        <div className="flex justify-between text-gray-500">
          <span>{t('common.subtotal')}</span>
          <span className="text-gray-700">{subtotal.toFixed(2)} AED</span>
        </div>
        {discount > 0 && (
          <div className="flex justify-between text-green-700">
            <span>{t('common.discount')}{promoCode ? ` (${promoCode})` : ''}</span>
            <span>-{discount.toFixed(2)} AED</span>
          </div>
        )}
        <div>
          <div className="flex justify-between text-gray-500">
            <span>{deliveryMethod === 'pickup' ? t('checkout.store_pickup') : t('common.delivery')}</span>
            {/* Striking the real fee through, rather than just printing "Free",
                shows the customer the number they no longer owe. */}
            {deliveryMethod === 'delivery' && unserviceable ? (
              <span className="text-amber-700">{t('checkout.unserviceable_short')}</span>
            ) : deliveryMethod === 'delivery' && deliveryFee === null ? (
              // Not free, and not a number we are willing to guess at yet.
              <span className="text-gray-400">{t('checkout.fee_from_address')}</span>
            ) : deliveryMethod === 'delivery' && freeApplied && baseFee > 0 ? (
              <span className="flex items-center gap-2">
                <span className="text-gray-400 line-through">{baseFee.toFixed(2)} AED</span>
                <span className="text-green-600 font-medium">{t('common.free')}</span>
              </span>
            ) : (
              <span className={knownFee === 0 ? 'text-green-600' : 'text-gray-700'}>
                {knownFee === 0 ? t('common.free') : `${knownFee.toFixed(2)} AED`}
              </span>
            )}
          </div>
          {deliveryMethod === 'delivery' && !unserviceable && !freeApplied && remainingForFree > 0 && (
            <p className="mt-1 font-body text-xs text-secondary">
              {t('checkout.free_delivery_upsell', { amount: remainingForFree.toFixed(2) })}
            </p>
          )}
          {deliveryMethod === 'delivery' && !unserviceable && freeApplied && (
            <p className="mt-1 font-body text-xs text-green-600">
              {t('checkout.free_delivery_qualified')}
            </p>
          )}
        </div>
        <div className="flex justify-between pt-2 mt-1 border-t border-gray-100 font-medium text-base">
          <span className="text-gray-800">{t('common.total')}</span>
          <span className="text-primary">{total.toFixed(2)} AED</span>
        </div>
        <p className="text-[11px] text-gray-400 text-end">
          VAT included (5%) · {((subtotal - discount) * 5 / 105).toFixed(2)} AED
        </p>
      </div>
    </div>
  );
}

// ─── Checkout ─────────────────────────────────────────────────────────────────

function CheckoutContent() {
  const searchParams = useSearchParams();
  const { cart, refreshCart, cartLoaded, cartError } = useCart();
  const { addToast } = useToast();
  const { t, locale } = useTranslation();
  const { user } = useAuth();

  const [form, setForm] = useState<CheckoutForm>(INITIAL_FORM);
  const [savedAddresses, setSavedAddresses] = useState<Address[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [retryOrder, setRetryOrder] = useState<import('@/lib/types').Order | null>(null);
  const [deliveryRates, setDeliveryRates] = useState<DeliveryRates | null>(null);
  const [restoringOrder, setRestoringOrder] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [addressOpen, setAddressOpen] = useState(false);
  const [showExtras, setShowExtras] = useState(false);
  const [quote, setQuote] = useState<DeliveryQuote | null>(null);

  const isDelivery = form.deliveryMethod === 'delivery';
  const paymentOptions = paymentOptionsFor(form.deliveryMethod);
  // Keep the selection legal: switching to delivery must not leave cash chosen.
  const paymentMethod = paymentOptions.includes(form.paymentMethod)
    ? form.paymentMethod
    : paymentOptions[0];

  // Restore from sessionStorage + handle a cancelled payment coming back.
  useEffect(() => {
    const stored = loadFromSession();
    if (stored) {
      setForm((prev) => ({ ...prev, ...INITIAL_FORM, ...(stored as Partial<CheckoutForm>) }));
    }

    const returnOrder = searchParams.get('order_number');
    if (searchParams.get('step') === 'payment' && returnOrder) {
      setRestoringOrder(true);
      ordersApi.get(returnOrder)
        .then((order) => {
          setRetryOrder(order);
          addToast(t('checkout.payment_cancelled'), 'warning');
        })
        .catch(() => addToast(t('checkout.payment_cancelled'), 'warning'))
        // The cart was emptied when the order was created, so until this settles
        // the page must not decide the basket is empty and discard the order the
        // customer came back to pay for.
        .finally(() => setRestoringOrder(false));
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    deliveryApi.getRates().then(setDeliveryRates).catch(() => { /* the quote carries the numbers */ });
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  const onChange = useCallback((patch: Partial<CheckoutForm>) => {
    setForm((prev) => {
      const next = { ...prev, ...patch };
      saveToSession(next);
      return next;
    });
  }, []);

  const clearError = useCallback((key: string) => {
    setErrors((prev) => { const next = { ...prev }; delete next[key]; return next; });
  }, []);

  const subtotal = cart?.subtotal ?? 0;
  const effectiveSubtotal = Math.max(0, subtotal - form.promoDiscount);
  const freeThreshold = quote?.free_threshold ?? deliveryRates?.free_threshold ?? 200;
  const freeApplied = effectiveSubtotal >= freeThreshold;

  // Priced off the pin against the active zone map. Close to the kitchen that is
  // a published flat fee; beyond it, the courier's own price for this exact
  // trip. Which means the number is not knowable before the pin is, so until
  // then there is no fee on screen rather than a placeholder we would have to
  // take back — a total that rises after the customer has read it is the worse
  // surprise. It also means the pin can have no price at all, which is not a
  // free delivery and not a zero: it is an address we cannot serve.
  const hasPin = form.locationLat !== null && form.locationLng !== null;
  const unserviceable = isDelivery && !retryOrder && quote?.serviceable === false;
  const baseFee = quote?.base_fee ?? null;
  const homeDeliveryFee = unserviceable ? null : (quote?.delivery_fee ?? null);
  const knowsFee = homeDeliveryFee !== null;
  const remainingForFree = Math.max(0, freeThreshold - effectiveSubtotal);

  const deliveryFee = retryOrder
    ? Number(retryOrder.delivery_fee)
    : form.deliveryMethod === 'pickup'
      ? (deliveryRates?.pickup_fee ?? 0)
      : homeDeliveryFee;
  const total = retryOrder
    ? Number(retryOrder.total)
    : Math.max(0, subtotal + (deliveryFee ?? 0) - form.promoDiscount);

  // Re-price whenever the pin or the basket changes, so what is on screen is
  // what the order will be written with.
  useEffect(() => {
    if (retryOrder) return;
    let cancelled = false;
    deliveryApi
      .quote(effectiveSubtotal, form.locationLat, form.locationLng, form.addressLine1)
      .then((q) => { if (!cancelled) setQuote(q); })
      .catch(() => { /* leave the previous quote in place */ });
    return () => { cancelled = true; };
  }, [effectiveSubtotal, form.locationLat, form.locationLng, form.addressLine1, retryOrder]);


  const currentDraft: AddressDraft = {
    id: form.selectedAddressId,
    label: form.addressLabel || 'Home',
    firstName: form.firstName,
    lastName: form.lastName,
    phone: form.phone,
    addressLine1: form.addressLine1,
    addressLine2: form.addressLine2,
    unitNumber: form.unitNumber,
    latitude: form.locationLat,
    longitude: form.locationLng,
  };
  const hasAddress = Boolean(form.addressLine1.trim());

  const applyDraft = (d: AddressDraft) => {
    onChange({
      selectedAddressId: d.id,
      addressLabel: d.label,
      firstName: d.firstName,
      lastName: d.lastName,
      phone: d.phone,
      addressLine1: d.addressLine1,
      addressLine2: d.addressLine2,
      unitNumber: d.unitNumber,
      locationLat: d.latitude,
      locationLng: d.longitude,
    });
    ['address', 'firstName', 'lastName', 'phone'].forEach(clearError);
  };

  const handleSubmit = useCallback(async () => {
    if (!retryOrder) {
      const found: Record<string, string> = {};
      // Email is only checked when something was typed: a typo is caught, a
      // blank never blocks.
      if (form.email.trim() && !isValidEmail(form.email)) found.email = t('checkout.valid_email_required');

      if (isDelivery) {
        if (!form.addressLine1.trim()) found.address = t('checkout.address_required');
        else if (!form.firstName.trim() || !form.phone.trim() || !isValidPhone(form.phone)) {
          found.address = t('checkout.address_contact_incomplete');
        } else if (unserviceable) {
          // The API refuses this too. Stopping here saves the customer a round
          // trip and a payment page they were never going to get through.
          found.unserviceable = t('checkout.unserviceable_title');
        }
      } else {
        if (!form.firstName.trim()) found.firstName = t('checkout.first_name_required');
        if (!form.phone.trim() || !isValidPhone(form.phone)) found.phone = t('checkout.valid_phone_required');
      }

      if (Object.keys(found).length > 0) {
        setErrors(found);
        analytics.checkoutError({ step: 1, field: Object.keys(found)[0] });
        focusFirstError(Object.keys(found)[0]);
        return;
      }
      setErrors({});
    }

    setSubmitting(true);
    let createdOrder: import('@/lib/types').Order | null = null;
    try {
      if (!user) await ensureCheckoutAuth(user);

      let orderNumber: string;
      if (retryOrder) {
        orderNumber = retryOrder.order_number;
      } else {
        if (!cart || cart.items.length === 0) {
          addToast(t('checkout.cart_empty'), 'error');
          setSubmitting(false);
          return;
        }

        const order = await ordersApi.create({
          // Blank means "no email" — the API falls back to the session's own
          // address rather than refusing the order.
          email: form.email.trim() ? form.email.trim().toLowerCase() : undefined,
          delivery_method: form.deliveryMethod,
          shipping_address: isDelivery
            ? {
                label: form.addressLabel || 'Home',
                first_name: form.firstName,
                last_name: form.lastName,
                phone: form.phone,
                address_line_1: form.addressLine1,
                address_line_2: form.addressLine2 || undefined,
                unit_number: form.unitNumber || undefined,
                latitude: form.locationLat ?? undefined,
                longitude: form.locationLng ?? undefined,
              }
            : undefined,
          promo_code: form.promoDiscount > 0 ? form.promoCode : undefined,
          payment_method: paymentMethod,
          notes: form.notes || undefined,
          session_id: getSessionId() ?? undefined,
        });
        createdOrder = order;
        orderNumber = order.order_number;

        clearCheckoutSession();
        await refreshCart();
      }

      const provider = retryOrder?.payment_method ?? paymentMethod;
      const session = await paymentsApi.createSession(orderNumber, provider);

      analytics.checkoutStepComplete({ step: 1, delivery_method: form.deliveryMethod });

      // Cash and zero-total orders are confirmed server-side — there is no
      // gateway to visit, so go straight to the confirmation.
      if (session.confirmed) {
        const orderEmail = createdOrder?.email ?? retryOrder?.email ?? form.email.trim().toLowerCase();
        window.location.href =
          `/${locale}/checkout/confirmation?order_number=${orderNumber}&email=${encodeURIComponent(orderEmail)}`;
        return;
      }
      window.location.href = session.checkout_url!;
    } catch (err) {
      // Keep a created order so payment can be retried without a cart.
      if (createdOrder) setRetryOrder(createdOrder);
      const message = err instanceof Error ? err.message : 'Something went wrong. Please try again.';
      analytics.paymentFailed({
        order_number: createdOrder?.order_number ?? retryOrder?.order_number ?? '',
        error_message: message,
      });
      addToast(message, 'error');
      setSubmitting(false);
    }
  }, [form, cart, retryOrder, user, locale, addToast, refreshCart, t, paymentMethod, isDelivery, unserviceable]);

  // ── Non-form states ────────────────────────────────────────────────────────

  if (cartError && !submitting && !retryOrder) {
    return (
      <div className="max-w-md mx-auto px-4 py-16 flex flex-col items-center text-center gap-4">
        <span className="material-icons text-5xl text-secondary">wifi_off</span>
        <h1 className="font-display text-2xl text-primary uppercase tracking-widest">
          {t('checkout.cart_load_failed')}
        </h1>
        <Button variant="primary" onClick={() => refreshCart()}>{t('common.try_again')}</Button>
        <Link href={`/${locale}/cart`} className="font-body text-sm text-gray-500 underline">
          {t('breadcrumb.cart')}
        </Link>
      </div>
    );
  }

  if (!cart && !cartLoaded && !submitting) {
    return (
      <div className="max-w-md mx-auto px-4 py-20 flex flex-col items-center gap-4">
        <Spinner size="lg" />
        <p className="font-body text-sm text-gray-400">{t('checkout.loading_cart')}</p>
      </div>
    );
  }

  if (cart && cart.items.length === 0 && !submitting && !retryOrder && !restoringOrder) {
    return (
      <div className="max-w-md mx-auto px-4 py-16 flex flex-col items-center text-center gap-4">
        <span className="material-icons text-5xl text-secondary">shopping_bag</span>
        <h1 className="font-display text-2xl text-primary uppercase tracking-widest">{t('checkout.cart_empty')}</h1>
        <Link href={`/${locale}`}><Button variant="primary">{t('cart.continue_shopping')}</Button></Link>
      </div>
    );
  }

  // ── The page ───────────────────────────────────────────────────────────────

  const placeOrderLabel = t('checkout.place_order', { total: total.toFixed(2) });
  // Left clickable rather than disabled: a dead button explains nothing, and
  // pressing it scrolls the explanation into view.
  const blocked = Boolean(unserviceable);

  return (
    <div className="max-w-xl mx-auto px-4 py-8 sm:py-10 pb-28 sm:pb-10">
      <header className="flex items-baseline justify-between mb-6">
        <h1 className="font-display text-2xl sm:text-3xl text-primary uppercase tracking-[0.15em]">
          {t('breadcrumb.checkout')}
        </h1>
        <Link href={`/${locale}/cart`} className="font-body text-xs text-gray-400 hover:text-primary transition-colors">
          {t('breadcrumb.cart')}
        </Link>
      </header>

      {/* 1 — How they want it. Decides everything below. */}
      <Section label={t('checkout.delivery_method')}>
        <div className="space-y-2">
          <ChoiceRow
            selected={isDelivery}
            onSelect={() => {
              analytics.selectDeliveryMethod({ method: 'delivery', fee: homeDeliveryFee ?? 0 });
              onChange({ deliveryMethod: 'delivery' });
            }}
            icon="local_shipping"
            title={t('checkout.delivery_option')}
            subtitle={unserviceable
              ? t('checkout.unserviceable_short')
              : freeApplied
                ? t('checkout.free_delivery_qualified')
                : !hasPin
                  ? t('checkout.fee_from_address')
                  : t('checkout.free_delivery_upsell', { amount: remainingForFree.toFixed(2) })}
            trailing={
              unserviceable ? (
                <span className="material-icons text-lg text-amber-600">wrong_location</span>
              ) : !knowsFee ? (
                <span className="text-gray-400">—</span>
              ) : freeApplied && (baseFee ?? 0) > 0 ? (
                // The saving is the point, so show what was avoided.
                <span className="flex items-center gap-2">
                  <span className="text-gray-400 line-through">{(baseFee ?? 0).toFixed(2)} AED</span>
                  <span className="text-green-600 font-medium">{t('common.free')}</span>
                </span>
              ) : homeDeliveryFee === 0 ? (
                <span className="text-green-600">{t('common.free')}</span>
              ) : (
                <span className="text-gray-700">{homeDeliveryFee.toFixed(2)} AED</span>
              )
            }
          />
          <div>
            <ChoiceRow
              selected={!isDelivery}
              onSelect={() => {
                analytics.selectDeliveryMethod({ method: 'pickup', fee: 0 });
                onChange({ deliveryMethod: 'pickup' });
              }}
              icon="storefront"
              title={t('checkout.store_pickup')}
              subtitle={t('checkout.pickup_description')}
              trailing={<span className="text-green-600">{t('common.free')}</span>}
            />
            {/* Nobody agrees to collect from somewhere they cannot picture. */}
            {!isDelivery && (
              <a
                href={STORE_MAPS_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 inline-flex items-center gap-1.5 font-body text-xs text-primary hover:underline"
              >
                <span className="material-icons text-sm">place</span>
                {t('checkout.view_pickup_location')}
              </a>
            )}
          </div>
        </div>
      </Section>

      {/* 2 — Where it goes and who receives it. */}
      {isDelivery ? (
        <Section label={t('checkout.delivery_address')}>
          <div data-field="address" data-field-error={errors.address ? 'true' : undefined}>
            <button
              type="button"
              onClick={() => setAddressOpen(true)}
              className={`w-full text-start border rounded-sm px-3.5 py-3 flex items-center gap-3 transition-colors hover:border-primary/50 ${
                errors.address ? 'border-red-400' : 'border-gray-200'
              }`}
            >
              <span className="material-icons text-xl text-primary">
                {hasAddress ? 'location_on' : 'add_location_alt'}
              </span>
              <span className="flex-1 min-w-0">
                {hasAddress ? (
                  <>
                    <span className="block font-body text-sm text-gray-800 truncate">
                      {formatAddress(currentDraft)}
                    </span>
                    <span className="block font-body text-xs text-gray-400 truncate mt-0.5">
                      {form.firstName} {form.lastName} · {form.phone}
                    </span>
                  </>
                ) : (
                  <>
                    <span className="block font-body text-sm text-gray-800">{t('checkout.add_delivery_address')}</span>
                    <span className="block font-body text-xs text-gray-400 mt-0.5">{t('checkout.add_address_hint')}</span>
                  </>
                )}
              </span>
              <span className="material-icons text-lg text-gray-300">chevron_right</span>
            </button>
            {errors.address && <p className="mt-1.5 text-xs text-red-500 font-body">{errors.address}</p>}
            {/* Directly under the thing that caused it, and above everything it
                makes pointless to fill in. */}
            {unserviceable && (
              <UnserviceableNotice
                onChangeAddress={() => setAddressOpen(true)}
                onSwitchToPickup={() => {
                  analytics.selectDeliveryMethod({ method: 'pickup', fee: 0 });
                  onChange({ deliveryMethod: 'pickup' });
                  clearError('unserviceable');
                }}
                t={t}
              />
            )}
          </div>
        </Section>
      ) : (
        <Section label={t('checkout.contact_information')}>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div data-field="firstName" data-field-error={errors.firstName ? 'true' : undefined}>
                <Input
                  aria-label={t('common.first_name')}
                  placeholder={t('checkout.first_name_placeholder')}
                  value={form.firstName}
                  onChange={(e) => { onChange({ firstName: e.target.value }); clearError('firstName'); }}
                  error={errors.firstName}
                />
              </div>
              <Input
                aria-label={t('common.last_name')}
                placeholder={t('checkout.last_name_placeholder')}
                value={form.lastName}
                onChange={(e) => onChange({ lastName: e.target.value })}
              />
            </div>
            <div data-field="phone" data-field-error={errors.phone ? 'true' : undefined}>
              <PhoneInput
                value={form.phone}
                onChange={(v) => { onChange({ phone: v }); clearError('phone'); }}
                error={errors.phone}
              />
            </div>
          </div>
        </Section>
      )}

      {/* 3 — Optional, and the last thing asked for. */}
      <Section label={t('checkout.email_optional')}>
        <div data-field="email" data-field-error={errors.email ? 'true' : undefined}>
          <Input
            type="email"
            aria-label={t('checkout.email_optional')}
            placeholder={t('common.email_placeholder')}
            value={form.email}
            onChange={(e) => { onChange({ email: e.target.value }); clearError('email'); }}
            error={errors.email}
            helper={errors.email ? undefined : t('checkout.email_optional_hint')}
          />
        </div>
      </Section>

      {/* 4 — A choice when collecting, a statement when delivering: there is no
             cash handling on the delivery side. */}
      <Section label={t('checkout.payment_method')}>
        <div className="space-y-2">
          {paymentOptions.map((id) => {
            const isCod = id === 'cod';
            const only = paymentOptions.length === 1;
            const row = (
              <>
                <span className={`material-icons text-xl ${paymentMethod === id || only ? 'text-primary' : 'text-gray-400'}`}>
                  {isCod ? 'payments' : 'credit_card'}
                </span>
                <span className="flex-1 min-w-0">
                  <span className="block font-body text-sm text-gray-800">
                    {isCod ? t('checkout.cash_on_delivery') : t('checkout.credit_debit_card')}
                  </span>
                  <span className="block font-body text-xs text-gray-400 mt-0.5">
                    {isCod ? t('checkout.cod_pickup_sublabel') : t('checkout.payment_sublabel')}
                  </span>
                </span>
              </>
            );
            return only ? (
              <div key={id} className="flex items-center gap-3 px-3.5 py-3 border border-gray-200 rounded-sm bg-gray-50/60">
                {row}
              </div>
            ) : (
              <button
                key={id}
                type="button"
                aria-pressed={paymentMethod === id}
                onClick={() => onChange({ paymentMethod: id })}
                className={`w-full flex items-center gap-3 px-3.5 py-3 border rounded-sm text-start transition-colors ${
                  paymentMethod === id ? 'border-primary bg-primary/5' : 'border-gray-200 hover:border-primary/40'
                }`}
              >
                {row}
              </button>
            );
          })}
        </div>
      </Section>

      {/* 5 — What they are buying. */}
      <Section label={t('checkout.order_summary')}>
        <OrderSummary
          cart={cart}
          retryOrder={retryOrder}
          discount={retryOrder ? Number(retryOrder.discount_amount) : form.promoDiscount}
          promoCode={retryOrder?.promo_code_used ?? form.promoCode}
          deliveryFee={deliveryFee}
          baseFee={baseFee ?? 0}
          freeApplied={freeApplied}
          remainingForFree={remainingForFree}
          deliveryMethod={form.deliveryMethod}
          unserviceable={Boolean(unserviceable)}
          locale={locale}
          t={t}
        />

        {/* A promo code and a note matter to a few and are read by everyone, so
            they stay folded away until asked for. */}
        {!retryOrder && (
          <div className="mt-4">
            {showExtras ? (
              <div className="space-y-4 pt-1">
                <PromoCodeStep
                  promoCode={form.promoCode}
                  promoDiscount={form.promoDiscount}
                  promoMessage={form.promoMessage}
                  subtotal={subtotal}
                  onChange={(patch) => onChange(patch)}
                />
                <textarea
                  value={form.notes}
                  onChange={(e) => onChange({ notes: e.target.value })}
                  placeholder={t('checkout.notes_placeholder')}
                  rows={2}
                  maxLength={500}
                  aria-label={t('checkout.order_notes_label')}
                  className="w-full px-3.5 py-2.5 text-sm font-body bg-white border border-gray-300 rounded-sm outline-none resize-none focus:border-primary focus:ring-2 focus:ring-primary/20 transition-all"
                />
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setShowExtras(true)}
                className="font-body text-xs text-primary hover:underline"
              >
                {t('checkout.add_promo_or_note')}
              </button>
            )}
          </div>
        )}
      </Section>

      {/* 6 — One button, and on a phone it never leaves the screen. */}
      <div className="hidden sm:block pt-2">
        <Button
          variant="primary"
          size="lg"
          fullWidth
          onClick={handleSubmit}
          loading={submitting}
          className={blocked ? 'opacity-60' : undefined}
        >
          {blocked ? t('checkout.unserviceable_short') : placeOrderLabel}
        </Button>
        <p className="mt-3 flex items-center justify-center gap-1.5 text-gray-400">
          <span className="material-icons text-sm">lock</span>
          <span className="font-body text-xs">{t('checkout.security_note')}</span>
        </p>
      </div>

      <div className="sm:hidden fixed bottom-0 inset-x-0 z-30 bg-white/95 backdrop-blur border-t border-gray-100 px-4 py-3">
        <Button
          variant="primary"
          size="lg"
          fullWidth
          onClick={handleSubmit}
          loading={submitting}
          className={blocked ? 'opacity-60' : undefined}
        >
          {blocked ? t('checkout.unserviceable_short') : placeOrderLabel}
        </Button>
      </div>

      <AddressModal
        isOpen={addressOpen}
        onClose={() => setAddressOpen(false)}
        onSave={applyDraft}
        isAuthenticated={Boolean(user)}
        savedAddresses={savedAddresses}
        onSavedAddressesChange={setSavedAddresses}
        selectedAddressId={form.selectedAddressId}
        initialDraft={hasAddress ? currentDraft : null}
      />
    </div>
  );
}

export default function CheckoutPage() {
  return (
    <Suspense fallback={
      <div className="flex justify-center items-center py-20">
        <Spinner size="lg" />
      </div>
    }>
      <CheckoutContent />
    </Suspense>
  );
}
