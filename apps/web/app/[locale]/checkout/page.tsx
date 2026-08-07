'use client';

import Image from 'next/image';
import Link from 'next/link';
import { Suspense, useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'next/navigation';
import { useCart } from '@/lib/cart-context';
import {
  ordersApi, paymentsApi, addressesApi, branchesApi, deliveryApi,
  getSessionId,
} from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { accountEmailOf, ensureCheckoutAuth } from '@/lib/checkout-auth';
import { Button } from '@/components/ui/Button';
import { InfoTip } from '@/components/ui/InfoTip';
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
import type { Address, Cart, CartItem, DeliveryRates, DeliveryQuote, PickupBranch } from '@/lib/types';

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
  /** Which branch to collect from. Empty until one is chosen; pickup only. */
  pickupBranchId: string;
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
  pickupBranchId: '',
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

// ─── The small-basket fee ─────────────────────────────────────────────────────

/**
 * The basket at or below which a delivery attracts the small-order fee, and
 * what that fee is. Both in AED, both VAT-exclusive, exactly as delivery is.
 *
 * These mirror `delivery_settings.low_order_threshold` and `.low_order_fee`,
 * which is where the order is actually priced — `order_service.low_order_fee_for`
 * is the only figure a customer is ever charged, and this page never decides
 * anything, it only shows what that will come to. They are written here because
 * `/delivery/rates` publishes both, so this is a fallback for the render before
 * that call lands rather than a second definition of the numbers. They were
 * briefly hardcoded here with no endpoint behind them, which is the same
 * coupling the coupon tray was explicitly built to avoid: a commercial figure
 * in two places is a figure that will eventually disagree with what is charged.
 */
export const LOW_ORDER_THRESHOLD = 35;
export const LOW_ORDER_FEE = 15;

/**
 * What this basket will be charged as a small-order fee.
 *
 * Mirrors `low_order_fee_for` on the server, including the two things about it
 * that are easy to get wrong. It is judged on the basket **before** any
 * discount, so applying a coupon can never conjure the fee into existence — an
 * acquisition offer that hands back a 15-dirham surcharge is an offer fighting
 * itself. And it never applies to collection: handing a box across a counter
 * costs us nothing, so a fee there would have no cost behind it.
 *
 * The threshold is inclusive: a basket of exactly 35 still pays.
 */
export function lowOrderFeeFor(
  subtotal: number,
  method: 'delivery' | 'pickup',
  rates?: { low_order_fee: number; low_order_threshold: number | null } | null,
): number {
  if (method === 'pickup') return 0;
  // An empty basket is not a small order, it is a basket that cannot be
  // ordered. Charging it would put a fee on a page that is about to redirect.
  if (subtotal <= 0) return 0;

  const threshold = rates ? rates.low_order_threshold : LOW_ORDER_THRESHOLD;
  const fee = rates ? rates.low_order_fee : LOW_ORDER_FEE;
  // Null threshold means the fee is switched off — which is not the same as a
  // threshold of zero, and treating it as one would charge every basket.
  if (threshold === null) return 0;
  return subtotal <= threshold ? fee : 0;
}

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

/**
 * "Tomorrow, 13:00" — or just "Tomorrow" where an hour would be a lie.
 *
 * The words are translated; the date and the hour are formatted by the browser,
 * which knows the customer's locale and calendar far better than a translation
 * table ever will. Everything is read on the shop's clock rather than the
 * device's: a customer checking out from London is still being delivered to in
 * the UAE, and "tomorrow" has to mean the shop's tomorrow.
 */
const SHOP_TZ = 'Asia/Dubai';

function formatEstimate(
  estimate: { at: string; precision: 'time' | 'day' },
  locale: string,
  t: (k: string, p?: Record<string, string | number>) => string,
): string {
  const at = new Date(estimate.at);
  if (Number.isNaN(at.getTime())) return '';

  const dayKey = (d: Date) =>
    new Intl.DateTimeFormat('en-CA', { timeZone: SHOP_TZ }).format(d);
  const today = dayKey(new Date());
  const tomorrow = dayKey(new Date(Date.now() + 86_400_000));
  const target = dayKey(at);

  const day =
    target === today
      ? t('checkout.delivery_today')
      : target === tomorrow
        ? t('checkout.delivery_tomorrow')
        : new Intl.DateTimeFormat(locale, {
            weekday: 'short',
            day: 'numeric',
            month: 'short',
            timeZone: SHOP_TZ,
          }).format(at);

  if (estimate.precision === 'day') return t('checkout.delivery_by_day', { day });

  const time = new Intl.DateTimeFormat(locale, {
    hour: 'numeric',
    minute: '2-digit',
    timeZone: SHOP_TZ,
  }).format(at);
  return t('checkout.delivery_by_time', { day, time });
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

/**
 * Which counter to collect from.
 *
 * A pickup order is a promise to walk into a specific shop, so the shop is part
 * of the order rather than something resolved afterwards — before this, the API
 * picked the first branch that could take the job and the customer was shown
 * one hardcoded pin that had no connection to it.
 *
 * Rendered even when there is only one branch. A single row that names the
 * place, its city and a map link is the answer to "where am I going", and
 * hiding it because there was no choice to make is what left people asking.
 */
function PickupBranchPicker({
  branches, selectedId, onSelect, error, locale, t,
}: {
  branches: PickupBranch[];
  selectedId: string;
  onSelect: (id: string) => void;
  error?: string;
  locale: string;
  t: (k: string, p?: Record<string, string | number>) => string;
}) {
  if (branches.length === 0) {
    return (
      <p className="font-body text-sm text-gray-500">{t('checkout.pickup_branch_unavailable')}</p>
    );
  }

  const isAr = locale === 'ar';

  return (
    <div data-field="pickupBranch" data-field-error={error ? 'true' : undefined}>
      <p className="font-body text-xs text-gray-400 mb-2">{t('checkout.pickup_branch_hint')}</p>
      <div className="space-y-2">
        {branches.map((branch) => {
          const selected = branch.id === selectedId;
          const name = (isAr && branch.name_ar) || branch.name;
          const address = (isAr && branch.address_ar) || branch.address;
          const city = (isAr && branch.city_ar) || branch.city;
          return (
            <div
              key={branch.id}
              className={`border rounded-sm transition-colors ${
                selected ? 'border-primary bg-primary/5'
                  : error ? 'border-red-400' : 'border-gray-200 hover:border-primary/40'
              }`}
            >
              <button
                type="button"
                onClick={() => onSelect(branch.id)}
                aria-pressed={selected}
                className="w-full flex items-start gap-3 px-3.5 py-3 text-start"
              >
                <span className={`material-icons text-xl mt-0.5 ${selected ? 'text-primary' : 'text-gray-400'}`}>
                  {selected ? 'radio_button_checked' : 'radio_button_unchecked'}
                </span>
                <span className="flex-1 min-w-0">
                  <span className="block font-body text-sm text-gray-800">{name}</span>
                  {address && (
                    <span className="block font-body text-xs text-gray-500 mt-0.5">{address}</span>
                  )}
                  <span className="block font-body text-xs text-gray-400 mt-0.5">
                    {[city, `${branch.opening_from} – ${branch.opening_to}`].filter(Boolean).join(' · ')}
                  </span>
                </span>
              </button>
              {branch.maps_url && (
                <a
                  href={branch.maps_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mx-3.5 mb-3 inline-flex items-center gap-1.5 font-body text-xs text-primary hover:underline"
                >
                  <span className="material-icons text-sm">place</span>
                  {t('checkout.branch_directions')}
                </a>
              )}
            </div>
          );
        })}
      </div>
      {error && <p className="mt-1.5 text-xs text-red-500 font-body">{error}</p>}
    </div>
  );
}

// ─── Order summary ────────────────────────────────────────────────────────────

export function OrderSummary({
  cart, retryOrder, discount, promoCode, deliveryFee, lowOrderFee, baseFee, freeApplied,
  lowOrderThreshold, lowOrderFeeAmount,
  freeAvailable, hasPin, remainingForFree, deliveryMethod, unserviceable, locale, t,
}: {
  cart: Cart | null;
  retryOrder: import('@/lib/types').Order | null;
  discount: number;
  promoCode: string;
  /** Null while the fee is unknown — no pin yet, or nowhere we can deliver. */
  deliveryFee: number | null;
  /** The small-basket surcharge, zero on anything that does not attract one. */
  lowOrderFee: number;
  /**
   * The live threshold and amount, so the explanation quotes the numbers the
   * server actually charges on. Passed in rather than imported: a constant read
   * here would be a second definition, and the one that drifts.
   */
  lowOrderThreshold: number;
  lowOrderFeeAmount: number;
  /** What delivery would have cost — struck through once it is waived. */
  baseFee: number;
  freeApplied: boolean;
  /** Whether free delivery can reach this address at all. */
  freeAvailable: boolean;
  /** True once a pin exists, so "not in this area" is a fact and not a guess. */
  hasPin: boolean;
  remainingForFree: number;
  deliveryMethod: 'delivery' | 'pickup';
  /** Nothing can be delivered to this pin, so there is no total to commit to. */
  unserviceable: boolean;
  locale: string;
  t: (k: string, p?: Record<string, string | number>) => string;
}) {
  const subtotal = retryOrder ? Number(retryOrder.subtotal) : (cart?.subtotal ?? 0);
  const knownFee = deliveryFee ?? 0;
  const total = retryOrder
    ? Number(retryOrder.total)
    : Math.max(0, subtotal + knownFee + lowOrderFee - discount);
  // What it takes to get *past* the threshold, not up to it: the threshold is
  // inclusive, so a basket sitting exactly on it still pays. Spending the round
  // number this line would otherwise print and finding the fee still there is
  // the one way this sentence can lie, so it costs a fils to make it true.
  const remainingToClearFee = Math.max(0, lowOrderThreshold - subtotal + 0.01);

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
          {/* Three different things to say about one offer: it is not coming
              here, you are this far from it, or you have it. Saying the second
              to someone in the first case is the failure worth avoiding. */}
          {deliveryMethod === 'delivery' && !unserviceable && !freeAvailable && hasPin && (
            <p className="mt-1 font-body text-xs text-gray-400">
              {t('checkout.free_delivery_not_in_area')}
            </p>
          )}
          {deliveryMethod === 'delivery' && !unserviceable && freeAvailable && !freeApplied && remainingForFree > 0 && (
            <p className="mt-1 font-body text-xs text-secondary">
              {t(hasPin ? 'checkout.free_delivery_upsell' : 'checkout.free_delivery_upsell_areas', {
                amount: remainingForFree.toFixed(2),
              })}
            </p>
          )}
          {deliveryMethod === 'delivery' && !unserviceable && freeApplied && (
            <p className="mt-1 font-body text-xs text-green-600">
              {t('checkout.free_delivery_qualified')}
            </p>
          )}
        </div>

        {/* The small-basket fee — and only when there is one.
            Deliberately unlike the delivery line above, which stays put and
            reads "Free" once it is waived. Delivery is a thing the customer
            expects to be charged for, so showing it at zero is worth something:
            it is a saving they can see. This is a surcharge, and a surcharge
            printed at 0.00 AED is not reassurance, it is the shop reminding
            somebody who spent enough that it charges small orders for the
            privilege. Once it does not apply, it is not a line at all. */}
        {lowOrderFee > 0 && (
          <div>
            <div className="flex justify-between text-gray-500">
              <span className="flex items-center gap-1">
                {t('checkout.low_order_fee')}
                <InfoTip label={t('checkout.low_order_fee_what_is_this')}>
                  {t('checkout.low_order_fee_info', {
                    threshold: lowOrderThreshold,
                    fee: lowOrderFeeAmount,
                    remaining: remainingToClearFee.toFixed(2),
                  })}
                </InfoTip>
              </span>
              <span className="text-gray-700">{lowOrderFee.toFixed(2)} AED</span>
            </div>
            {/* The way out, next to the charge. Not offered on an order that is
                already written — there is nothing left to add to it. */}
            {!retryOrder && (
              <p className="mt-1 font-body text-xs text-secondary">
                {t('checkout.low_order_fee_remaining', { amount: remainingToClearFee.toFixed(2) })}
              </p>
            )}
          </div>
        )}

        <div className="flex justify-between pt-2 mt-1 border-t border-gray-100 font-medium text-base">
          <span className="text-gray-800">{t('common.total')}</span>
          {/* No deliverable address means no total. Printing the basket on its
              own here reads as the price of the order, and it is the one line
              on the page a customer takes at face value. */}
          {deliveryMethod === 'delivery' && unserviceable ? (
            <span className="text-gray-400">&mdash;</span>
          ) : (
            <span className="text-primary">{total.toFixed(2)} AED</span>
          )}
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
  const [pickupBranches, setPickupBranches] = useState<PickupBranch[]>([]);

  const isDelivery = form.deliveryMethod === 'delivery';
  // Where the receipt is already going, when there is an account to read it
  // off. Null for guests — see accountEmailOf.
  const accountEmail = accountEmailOf(user);
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

  // Loaded up front rather than when collection is chosen, so the list is
  // already there the moment somebody switches — a spinner between "I'll
  // collect" and "from where?" is a spinner in the middle of one decision.
  useEffect(() => {
    let cancelled = false;
    branchesApi
      .pickupPoints()
      .then((list) => {
        if (cancelled) return;
        setPickupBranches(list);
        // One branch is not a choice, so it is made. Two or more is, and is
        // left blank on purpose: preselecting one is how somebody drives to
        // the wrong city.
        if (list.length === 1) {
          setForm((prev) => (prev.pickupBranchId ? prev : { ...prev, pickupBranchId: list[0].id }));
        }
      })
      .catch(() => { /* the picker says so, and delivery is unaffected */ });
    return () => { cancelled = true; };
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
  const freeThreshold = quote?.free_threshold ?? deliveryRates?.free_threshold ?? 150;
  // The server decides this, not the basket. Free delivery only reaches the
  // zones we price ourselves, so "big enough order" is a necessary condition
  // and not a sufficient one — working it out here from the subtotal alone
  // would promise it to every address in the country.
  const freeApplied = quote?.free_delivery_applied ?? false;
  // Undefined until the first quote lands. Assumed available so the upsell is
  // not suppressed on a cold page; the copy for that state says "in selected
  // areas", which is exactly what we know at that point.
  const freeAvailable = quote?.free_delivery_available ?? true;
  // The question every shopper actually has. Only ever rendered from what the
  // server sent — the schedule that produces it is not something the browser
  // can or should reconstruct.
  const arrival =
    isDelivery && !retryOrder && quote?.delivery_estimate
      ? formatEstimate(quote.delivery_estimate, locale, t)
      : '';

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
  // Read off an order that already exists, worked out for one that does not.
  // A customer returning to pay owes what their order was priced at, not what
  // today's settings would charge for the same basket.
  const lowOrderFee = retryOrder
    ? Number(retryOrder.low_order_fee ?? 0)
    : lowOrderFeeFor(subtotal, form.deliveryMethod, deliveryRates);
  const total = retryOrder
    ? Number(retryOrder.total)
    : Math.max(0, subtotal + (deliveryFee ?? 0) + lowOrderFee - form.promoDiscount);

  // Re-price whenever the pin or the basket changes, so what is on screen is
  // what the order will be written with.
  //
  // Debounced, because `addressLine1` is in the dependency list and a customer
  // typing "Villa 12, Al Barsha" fired one quote per keystroke — each of which
  // can reach the courier's live pricing API. The delay is short enough that
  // the fee still lands while they are reading the line they just typed, and
  // the cleanup cancels the pending timer, so only the last edit is priced.
  useEffect(() => {
    if (retryOrder) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      deliveryApi
        .quote(effectiveSubtotal, form.locationLat, form.locationLng, form.addressLine1)
        .then((q) => { if (!cancelled) setQuote(q); })
        .catch(() => { /* leave the previous quote in place */ });
    }, 400);
    return () => { cancelled = true; clearTimeout(timer); };
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
      // blank never blocks. Nothing to check at all when it came from the
      // account rather than the keyboard.
      if (!accountEmail && form.email.trim() && !isValidEmail(form.email)) {
        found.email = t('checkout.valid_email_required');
      }

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
        // Only when there is a list to choose from. If the branches could not be
        // loaded the order still goes through and the API resolves one, because
        // losing a paid sale to a failed GET is the worse trade.
        if (pickupBranches.length > 0 && !form.pickupBranchId) {
          found.pickupBranch = t('checkout.pickup_branch_required');
        }
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
          email: accountEmail ?? (form.email.trim() ? form.email.trim().toLowerCase() : undefined),
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
          pickup_branch_id: isDelivery ? undefined : form.pickupBranchId || undefined,
          // Stamped on the order, and every email about it is written in it.
          locale,
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
        const orderEmail =
          createdOrder?.email ?? retryOrder?.email ?? accountEmail ?? form.email.trim().toLowerCase();
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
  }, [form, cart, retryOrder, user, accountEmail, locale, addToast, refreshCart, t, paymentMethod, isDelivery, unserviceable, pickupBranches]);

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
    <div className="max-w-xl mx-auto px-4 py-8 sm:py-10">
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
                  : !freeAvailable
                    // The fee here is a courier bill, not a number we set, so
                    // there is nothing to earn by spending more.
                    ? t('checkout.free_delivery_not_in_area')
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
          </div>
        </div>
      </Section>

      {/* 1b — Which counter. Only a question when collection is chosen, and it
          sits directly under the choice that raised it rather than further down
          the form, because it is part of the same decision. */}
      {!isDelivery && !retryOrder && (
        <Section label={t('checkout.pickup_branch')}>
          <PickupBranchPicker
            branches={pickupBranches}
            selectedId={form.pickupBranchId}
            onSelect={(id) => { onChange({ pickupBranchId: id }); clearError('pickupBranch'); }}
            error={errors.pickupBranch}
            locale={locale}
            t={t}
          />
        </Section>
      )}

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
            {/* The one line that answers "when", placed where the pin that
                decides it was just chosen. */}
            {arrival && !unserviceable && (
              <p className="mt-2.5 flex items-center gap-1.5 font-body text-xs text-gray-500">
                <span className="material-icons text-sm text-primary">schedule</span>
                <span>
                  {t('checkout.estimated_delivery')}:{' '}
                  <span className="text-gray-800">{arrival}</span>
                </span>
              </p>
            )}
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

      {/* 3 — Optional, and the last thing asked for. Signed in, it is not a
             question at all: the account already has an address, and asking for
             it again invites a second one that no order history will match. */}
      <Section label={accountEmail ? t('checkout.email') : t('checkout.email_optional')}>
        {accountEmail ? (
          <div className="flex items-start gap-2.5 border border-gray-200 bg-gray-50 rounded-sm px-3 py-2.5">
            <span className="material-icons text-base text-primary mt-0.5">mark_email_read</span>
            <div className="min-w-0">
              <p className="font-body text-sm text-gray-800 truncate">{accountEmail}</p>
              <p className="font-body text-xs text-gray-400 mt-0.5">
                {t('checkout.email_signed_in_hint')}
              </p>
            </div>
          </div>
        ) : (
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
        )}
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
          lowOrderFee={lowOrderFee}
          lowOrderThreshold={deliveryRates?.low_order_threshold ?? LOW_ORDER_THRESHOLD}
          lowOrderFeeAmount={deliveryRates?.low_order_fee ?? LOW_ORDER_FEE}
          baseFee={baseFee ?? 0}
          freeApplied={freeApplied}
          freeAvailable={freeAvailable}
          hasPin={hasPin}
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

      {/* Sticky, not fixed.
          `position: fixed` resolves `bottom: 0` against the *layout* viewport,
          which on a phone is not the viewport you can see: browsers with a
          collapsing address bar keep the layout viewport at its tallest, so the
          bar settled hundreds of pixels above the visible bottom and appeared to
          float in the middle of the page while scrolling. A sticky element is
          laid out in normal flow and pinned by the scroller itself, so there is
          no second viewport for it to disagree with. `-mx-4` cancels the page
          gutter so it still spans edge to edge. */}
      <div className="sm:hidden sticky bottom-0 z-30 -mx-4 mt-4 bg-white/95 backdrop-blur border-t border-gray-100 px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
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
