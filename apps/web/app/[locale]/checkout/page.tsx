'use client';

import Image from 'next/image';
import Link from 'next/link';
import { Suspense, useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'next/navigation';
import { useCart } from '@/lib/cart-context';
import {
  ordersApi, paymentsApi, addressesApi, deliveryApi,
  getSessionId, ensureSessionId, authApi,
} from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { Breadcrumb } from '@/components/ui/Breadcrumb';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Spinner } from '@/components/ui/Spinner';
import { useToast } from '@/components/ui/Toast';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { localizedField } from '@/lib/i18n/entity';
import { AddressForm } from './components/AddressForm';
import { DeliveryCalculator } from './components/DeliveryCalculator';
import { PromoCodeStep } from './components/PromoCodeStep';
import type { Address, Cart, CartItem, RegionCode, DeliveryRates, PublicRegion } from '@/lib/types';

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
  // Step 1
  email: string;
  firstName: string;
  lastName: string;
  phone: string;
  addressLine1: string;
  addressLine2: string;
  region: string;
  locationLat: number | null;
  locationLng: number | null;
  selectedAddressId: string; // '' = new address
  // Step 2
  deliveryMethod: 'delivery' | 'pickup';
  // Step 3
  promoCode: string;
  promoDiscount: number;
  promoMessage: string;
  paymentMethod: 'stripe';
  notes: string;
}

const INITIAL_FORM: CheckoutForm = {
  email: '', firstName: '', lastName: '', phone: '',
  addressLine1: '', addressLine2: '', region: 'dubai',
  locationLat: null, locationLng: null,
  selectedAddressId: '',
  deliveryMethod: 'delivery',
  promoCode: '', promoDiscount: 0, promoMessage: '',
  paymentMethod: 'stripe',
  notes: '',
};

// ─── Delivery fee helper ──────────────────────────────────────────────────────

function calcFeeFromRates(
  rates: DeliveryRates | null,
  method: 'delivery' | 'pickup',
  region: string,
  subtotal: number,
): number {
  if (method === 'pickup') return rates?.pickup_fee ?? 0;
  if (!rates) return 0;
  if (subtotal >= rates.free_threshold) return 0;
  const r = rates.regions.find((reg) => reg.slug === region);
  if (r) return r.delivery_fee;
  return rates.regions.reduce((max, reg) => Math.max(max, reg.delivery_fee), 50);
}

// ─── Step indicator ───────────────────────────────────────────────────────────

function StepIndicator({ step }: { step: number }) {
  const { t } = useTranslation();
  const STEPS = [t('checkout.step_information'), t('checkout.step_delivery'), t('checkout.step_payment')];
  return (
    <nav className="flex items-center gap-2 mb-8 font-body text-xs uppercase tracking-widest">
      {STEPS.map((label, i) => {
        const n = i + 1;
        const active = n === step;
        const done = n < step;
        return (
          <span key={label} className="flex items-center gap-2">
            {i > 0 && <span className="text-gray-300">›</span>}
            <span
              className={
                active
                  ? 'text-primary font-medium'
                  : done
                  ? 'text-gray-400'
                  : 'text-gray-300'
              }
            >
              {done && <span className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-primary/20 text-primary mr-1">✓</span>}
              {label}
            </span>
          </span>
        );
      })}
    </nav>
  );
}

// ─── Order summary sidebar ────────────────────────────────────────────────────

function OrderSummarySidebar({
  cart, form, step, retryOrder, deliveryRates,
}: {
  cart: Cart | null;
  form: CheckoutForm;
  step: number;
  retryOrder: import('@/lib/types').Order | null;
  deliveryRates: DeliveryRates | null;
}) {
  const { t, locale } = useTranslation();

  const subtotal = retryOrder ? Number(retryOrder.subtotal) : (cart?.subtotal ?? 0);
  const discount = retryOrder ? Number(retryOrder.discount_amount) : form.promoDiscount;
  const effectiveSubtotal = Math.max(0, subtotal - discount);
  const deliveryFee = retryOrder
    ? Number(retryOrder.delivery_fee)
    : step >= 2
      ? calcFeeFromRates(deliveryRates, form.deliveryMethod, form.region, effectiveSubtotal)
      : null;
  const total = retryOrder ? Number(retryOrder.total) : subtotal + (deliveryFee ?? 0) - discount;

  return (
    <div className="bg-gray-50 border border-gray-100 rounded-sm p-5 space-y-4 sticky top-24">
      <h2 className="font-display text-base text-primary uppercase tracking-widest">{t('checkout.order_summary')}</h2>

      {/* Items */}
      {retryOrder && retryOrder.items.length > 0 ? (
        <ul className="space-y-3">
          {retryOrder.items.map((item) => (
            <li key={item.id} className="flex gap-3 items-start">
              <div className="relative w-12 h-12 rounded-sm overflow-hidden bg-gray-100 shrink-0">
                <div className="w-full h-full bg-secondary/20" />
                <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-primary text-white text-[10px] flex items-center justify-center font-body">
                  {item.quantity}
                </span>
              </div>
              <div className="flex-1 min-w-0">
                <p className="font-body text-xs text-gray-800 leading-snug truncate">
                  {localizedField({ translations: item.product_translations }, 'name', item.product_name, locale)}
                </p>
              </div>
              <p className="font-body text-xs text-gray-700 shrink-0">
                {Number(item.total_price).toFixed(2)} AED
              </p>
            </li>
          ))}
        </ul>
      ) : cart && cart.items.length > 0 ? (
        <ul className="space-y-3">
          {cart.items.map((item: CartItem) => (
            <li key={item.id} className="flex gap-3 items-start">
              <div className="relative w-12 h-12 rounded-sm overflow-hidden bg-gray-100 shrink-0">
                {item.product_image ? (
                  <Image src={item.product_image} alt={item.product_name ?? ''} fill sizes="48px" className="object-cover" />
                ) : (
                  <div className="w-full h-full bg-secondary/20" />
                )}
                <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-primary text-white text-[10px] flex items-center justify-center font-body">
                  {item.quantity}
                </span>
              </div>
              <div className="flex-1 min-w-0">
                <p className="font-body text-xs text-gray-800 leading-snug truncate">{localizedField({ translations: item.product_translations }, 'name', item.product_name ?? '', locale)}</p>
                {item.selected_options && item.selected_options.length > 0 && (
                  <p className="font-body text-[11px] text-gray-400">
                    {item.selected_options.map(o => localizedField({ translations: o.option_translations }, 'name', o.option_name, locale)).join(', ')}
                  </p>
                )}
              </div>
              <p className="font-body text-xs text-gray-700 shrink-0">
                {((item.line_total ?? (item.unit_price ?? 0) * item.quantity)).toFixed(2)} AED
              </p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="font-body text-xs text-gray-400">{t('checkout.no_items')}</p>
      )}

      <div className="h-px bg-gray-200" />

      {/* Totals */}
      <div className="space-y-1.5 font-body text-xs">
        <div className="flex justify-between">
          <span className="text-gray-500">{t('common.subtotal')}</span>
          <span>{subtotal.toFixed(2)} AED</span>
        </div>
        {discount > 0 && (
          <div className="flex justify-between text-green-700">
            <span>{t('common.discount')}{(retryOrder?.promo_code_used ?? form.promoCode) ? ` (${retryOrder?.promo_code_used ?? form.promoCode})` : ''}</span>
            <span>-{discount.toFixed(2)} AED</span>
          </div>
        )}
        <div className="flex justify-between">
          <span className="text-gray-500">{t('common.delivery')}</span>
          {deliveryFee === null ? (
            <span className="text-gray-400 italic">{t('checkout.next_step')}</span>
          ) : deliveryFee === 0 ? (
            <span className="text-green-600">{t('common.free')}</span>
          ) : (
            <span>{deliveryFee.toFixed(2)} AED</span>
          )}
        </div>
      </div>

      <div className="h-px bg-gray-200" />

      <div className="flex justify-between font-body font-semibold text-sm">
        <span>{t('common.total')}</span>
        <span className="text-primary">{Math.max(0, total).toFixed(2)} AED</span>
      </div>
      <div className="flex justify-between text-xs text-gray-400 mt-1">
        <span>VAT included (5%)</span>
        <span>{((subtotal - discount) * 5 / 105).toFixed(2)} AED</span>
      </div>
    </div>
  );
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

// ─── Step 1: Information ──────────────────────────────────────────────────────

function StepInformation({
  form, onChange, onNext, savedAddresses, loadingAddresses, regions,
}: {
  form: CheckoutForm;
  onChange: (patch: Partial<CheckoutForm>) => void;
  onNext: () => void;
  savedAddresses: Address[];
  loadingAddresses: boolean;
  regions: PublicRegion[];
}) {
  const [errors, setErrors] = useState<Record<string, string>>({});
  const { t } = useTranslation();

  function validateStep1(f: CheckoutForm): Record<string, string> {
    const errs: Record<string, string> = {};
    if (!isValidEmail(f.email)) errs.email = t('checkout.valid_email_required');
    if (!f.firstName.trim()) errs.firstName = t('checkout.first_name_required');
    if (!f.lastName.trim()) errs.lastName = t('checkout.last_name_required');
    if (!f.phone.trim() || !/^\+?[0-9\s()\-+]{7,15}$/.test(f.phone.trim())) errs.phone = t('checkout.valid_phone_required');
    return errs;
  }

  function validateStep1Address(f: CheckoutForm): Record<string, string> {
    const errs: Record<string, string> = {};
    if (!f.addressLine1.trim()) errs.addressLine1 = t('checkout.address_required');
    if (!f.region) errs.region = t('checkout.region_required');
    return errs;
  }

  const handleNext = () => {
    const contactErrors = validateStep1(form);
    const needsAddress = !form.selectedAddressId;
    const addressFieldErrors = needsAddress ? validateStep1Address(form) : {};
    const locationError: Record<string, string> = (form.locationLat === null || form.locationLng === null)
      ? { locationLat: t('checkout.pin_location_required') }
      : {};
    const all = { ...contactErrors, ...addressFieldErrors, ...locationError };
    if (Object.keys(all).length > 0) { setErrors(all); return; }
    setErrors({});
    onNext();
  };

  const field = (key: keyof CheckoutForm) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    onChange({ [key]: e.target.value });
    setErrors((prev) => { const next = { ...prev }; delete next[key]; return next; });
  };

  return (
    <div className="space-y-6">
      {/* Contact information */}
      <div>
        <h2 className="font-display text-xl text-primary uppercase tracking-widest mb-1">{t('checkout.contact_information')}</h2>
        <div className="h-px bg-secondary/30 mb-5" />
        <div className="space-y-4">
          <div>
            <Input
              label={t('common.email')}
              type="email"
              placeholder={t('common.email_placeholder')}
              value={form.email}
              onChange={field('email')}
              error={errors.email}
            />
            <p className="mt-1.5 text-xs text-foreground/50">{t('checkout.email_hint')}</p>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Input
              label={t('common.first_name')}
              placeholder={t('checkout.first_name_placeholder')}
              value={form.firstName}
              onChange={field('firstName')}
              error={errors.firstName}
            />
            <Input
              label={t('common.last_name')}
              placeholder={t('checkout.last_name_placeholder')}
              value={form.lastName}
              onChange={field('lastName')}
              error={errors.lastName}
            />
          </div>
          <Input
            label={t('common.phone')}
            type="tel"
            placeholder={t('common.phone_placeholder')}
            value={form.phone}
            onChange={field('phone')}
            error={errors.phone}
          />
        </div>
      </div>

      {/* Delivery address — extracted component */}
      <AddressForm
        values={{
          selectedAddressId: form.selectedAddressId,
          firstName: form.firstName,
          lastName: form.lastName,
          phone: form.phone,
          addressLine1: form.addressLine1,
          addressLine2: form.addressLine2,
          region: form.region,
          locationLat: form.locationLat,
          locationLng: form.locationLng,
        }}
        onChange={onChange}
        errors={errors}
        onClearError={(key) => setErrors((prev) => { const next = { ...prev }; delete next[key]; return next; })}
        savedAddresses={savedAddresses}
        loadingAddresses={loadingAddresses}
        regions={regions}
      />

      <Button variant="primary" size="lg" fullWidth onClick={handleNext}>
        {t('checkout.continue_to_delivery')} →
      </Button>
    </div>
  );
}

// ─── Step 2: Delivery ─────────────────────────────────────────────────────────

function StepDelivery({
  form, onChange, onBack, onNext, subtotal, deliveryRates,
}: {
  form: CheckoutForm;
  onChange: (patch: Partial<CheckoutForm>) => void;
  onBack: () => void;
  onNext: () => void;
  subtotal: number;
  deliveryRates: DeliveryRates | null;
}) {
  const { t } = useTranslation();
  const effectiveSubtotal = Math.max(0, subtotal - form.promoDiscount);
  const deliveryFee = calcFeeFromRates(deliveryRates, form.deliveryMethod, form.region, effectiveSubtotal);
  const freeThreshold = deliveryRates?.free_threshold ?? 200;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="font-display text-xl text-primary uppercase tracking-widest mb-1">{t('checkout.delivery_method')}</h2>
        <div className="h-px bg-secondary/30 mb-5" />

        {/* Delivery method selector — extracted component */}
        <DeliveryCalculator
          deliveryMethod={form.deliveryMethod}
          region={form.region}
          effectiveSubtotal={effectiveSubtotal}
          deliveryFee={deliveryFee}
          freeThreshold={freeThreshold}
          onChange={(method) => onChange({ deliveryMethod: method })}
        />

        <div className="mt-4 p-3 bg-secondary/10 rounded-sm flex gap-2">
          <span className="material-icons text-base text-secondary mt-0.5">info</span>
          <p className="font-body text-xs text-gray-600">
            {t('checkout.delivery_time_note')}
          </p>
        </div>
      </div>

      <div className="flex gap-3">
        <Button variant="ghost" size="lg" onClick={onBack} className="flex-1">
          ← {t('common.back')}
        </Button>
        <Button variant="primary" size="lg" onClick={onNext} className="flex-1">
          {t('checkout.continue_to_payment')} →
        </Button>
      </div>
    </div>
  );
}

// ─── Step 3: Payment ──────────────────────────────────────────────────────────

function StepPayment({
  form, onChange, onBack, cart, retryOrder, onSubmit, isSubmitting, deliveryRates,
}: {
  form: CheckoutForm;
  onChange: (patch: Partial<CheckoutForm>) => void;
  onBack: () => void;
  cart: Cart | null;
  retryOrder: import('@/lib/types').Order | null;
  onSubmit: () => void;
  isSubmitting: boolean;
  deliveryRates: DeliveryRates | null;
}) {
  const { t } = useTranslation();

  const subtotal = retryOrder ? Number(retryOrder.subtotal) : (cart?.subtotal ?? 0);
  const effectiveSubtotal = retryOrder
    ? Math.max(0, subtotal - Number(retryOrder.discount_amount))
    : Math.max(0, subtotal - form.promoDiscount);
  const deliveryFee = retryOrder
    ? Number(retryOrder.delivery_fee)
    : calcFeeFromRates(deliveryRates, form.deliveryMethod, form.region, effectiveSubtotal);
  const total = retryOrder
    ? Number(retryOrder.total)
    : Math.max(0, subtotal + deliveryFee - form.promoDiscount);

  const PAYMENT_METHODS = [
    {
      id: 'stripe' as const,
      label: t('checkout.credit_debit_card'),
      sublabel: t('checkout.payment_sublabel'),
      icon: 'credit_card',
      enabled: true,
    },
  ] as const;

  return (
    <div className="space-y-6">
      {/* Order review */}
      <div>
        <h2 className="font-display text-xl text-primary uppercase tracking-widest mb-1">{t('checkout.review_and_pay')}</h2>
        <div className="h-px bg-secondary/30 mb-5" />

        {/* Totals review */}
        <div className="bg-gray-50 rounded-sm p-4 space-y-2 font-body text-sm mb-5">
          <div className="flex justify-between">
            <span className="text-gray-500">{t('common.subtotal')}</span>
            <span>{subtotal.toFixed(2)} AED</span>
          </div>
          {form.promoDiscount > 0 && (
            <div className="flex justify-between text-green-700">
              <span>{t('common.discount')} ({form.promoCode})</span>
              <span>-{form.promoDiscount.toFixed(2)} AED</span>
            </div>
          )}
          <div className="flex justify-between">
            <span className="text-gray-500">
              {form.deliveryMethod === 'pickup' ? t('checkout.store_pickup') : t('common.delivery')}
            </span>
            <span className={deliveryFee === 0 ? 'text-green-600' : ''}>
              {deliveryFee === 0 ? t('common.free') : `${deliveryFee.toFixed(2)} AED`}
            </span>
          </div>
          <div className="h-px bg-gray-200" />
          <div className="flex justify-between font-semibold text-base">
            <span>{t('common.total')}</span>
            <span className="text-primary">{total.toFixed(2)} AED</span>
          </div>
          <div className="flex justify-between text-xs text-gray-400 mt-1">
            <span>VAT included (5%)</span>
            <span>{((subtotal - form.promoDiscount) * 5 / 105).toFixed(2)} AED</span>
          </div>
        </div>

        {/* Promo code — extracted component */}
        {!retryOrder && (
          <PromoCodeStep
            promoCode={form.promoCode}
            promoDiscount={form.promoDiscount}
            promoMessage={form.promoMessage}
            subtotal={subtotal}
            onChange={(patch) => onChange(patch)}
          />
        )}

        {/* Notes */}
        <div className="mb-1">
          <label className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1.5">
            {t('checkout.order_notes_label')}
          </label>
          <textarea
            value={form.notes}
            onChange={(e) => onChange({ notes: e.target.value })}
            placeholder={t('checkout.notes_placeholder')}
            rows={2}
            maxLength={500}
            className="w-full px-3.5 py-2.5 text-sm font-body bg-white border border-gray-300 rounded-sm outline-none resize-none focus:border-primary focus:ring-2 focus:ring-primary/20 transition-all"
          />
        </div>
      </div>

      {/* Payment method */}
      <div>
        <h2 className="font-display text-xl text-primary uppercase tracking-widest mb-1">{t('checkout.payment_method')}</h2>
        <div className="h-px bg-secondary/30 mb-5" />

        <div className="space-y-3">
          {PAYMENT_METHODS.map(({ id, label, sublabel, icon, enabled }) => (
            <label
              key={id}
              className={`flex gap-4 p-4 border rounded-sm transition-colors ${
                !enabled
                  ? 'border-gray-100 bg-gray-50 cursor-not-allowed opacity-60'
                  : form.paymentMethod === id
                  ? 'border-primary bg-primary/5 cursor-pointer'
                  : 'border-gray-200 hover:border-primary/40 cursor-pointer'
              }`}
            >
              <input
                type="radio"
                name="paymentMethod"
                value={id}
                checked={form.paymentMethod === id}
                disabled={!enabled}
                onChange={() => onChange({ paymentMethod: id })}
                className="mt-0.5 accent-primary"
              />
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <span className="material-icons text-xl text-primary">{icon}</span>
                  <p className="font-body font-medium text-sm text-gray-800">{label}</p>
                </div>
                <p className="font-body text-xs text-gray-500 mt-0.5 ml-7">{sublabel}</p>
              </div>
            </label>
          ))}
        </div>

        <div className="mt-4 flex gap-2 items-center text-gray-400">
          <span className="material-icons text-base">lock</span>
          <p className="font-body text-xs">{t('checkout.security_note')}</p>
        </div>
      </div>

      <div className="flex gap-3">
        <Button variant="ghost" size="lg" onClick={onBack} className="flex-1" disabled={isSubmitting}>
          ← {t('common.back')}
        </Button>
        <Button variant="primary" size="lg" onClick={onSubmit} loading={isSubmitting} className="flex-1">
          {t('checkout.pay_now', { total: total.toFixed(2) })}
        </Button>
      </div>
    </div>
  );
}

// ─── Main checkout ────────────────────────────────────────────────────────────

function CheckoutContent() {
  const searchParams = useSearchParams();
  const { cart, refreshCart } = useCart();
  const { addToast } = useToast();
  const { t, locale } = useTranslation();
  const { user } = useAuth();

  const [step, setStep] = useState(1);
  const [form, setForm] = useState<CheckoutForm>(INITIAL_FORM);
  const [savedAddresses, setSavedAddresses] = useState<Address[]>([]);
  const [loadingAddresses, setLoadingAddresses] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [retryOrder, setRetryOrder] = useState<import('@/lib/types').Order | null>(null);
  const [deliveryRates, setDeliveryRates] = useState<DeliveryRates | null>(null);

  // Restore from sessionStorage + handle cancelled/failed payment return
  useEffect(() => {
    const stored = loadFromSession();
    if (stored) {
      setForm((prev) => ({ ...prev, ...(stored as Partial<CheckoutForm>) }));
    }

    const returnStep = searchParams.get('step');
    const returnOrder = searchParams.get('order_number');
    if (returnStep === 'payment' && returnOrder) {
      setStep(3);
      ordersApi.get(returnOrder)
        .then((order) => {
          setRetryOrder(order);
          addToast(t('checkout.payment_cancelled'), 'warning');
        })
        .catch(() => {
          addToast(t('checkout.payment_cancelled'), 'warning');
        });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fetch delivery rates from backend
  useEffect(() => {
    deliveryApi.getRates().then(setDeliveryRates).catch(() => { /* use fallback calcFeeFromRates(null) */ });
  }, []);

  // Load saved addresses for authenticated users
  useEffect(() => {
    if (!user) return;
    setLoadingAddresses(true);
    addressesApi.list()
      .then((list) => {
        setSavedAddresses(list);
        const defaultAddr = list.find(a => a.is_default) ?? list[0];
        if (defaultAddr) {
          setForm((prev) => {
            if (prev.selectedAddressId !== '') return prev;
            const next = {
              ...prev,
              selectedAddressId: defaultAddr.id,
              firstName: defaultAddr.first_name,
              lastName: defaultAddr.last_name,
              phone: prev.phone || defaultAddr.phone,
              addressLine1: defaultAddr.address_line_1,
              addressLine2: defaultAddr.address_line_2 ?? '',
              region: defaultAddr.region,
              locationLat: defaultAddr.latitude ?? null,
              locationLng: defaultAddr.longitude ?? null,
            };
            saveToSession(next);
            return next;
          });
        }
      })
      .catch(() => { /* not authenticated or no addresses */ })
      .finally(() => setLoadingAddresses(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Pre-fill contact info from API if authenticated (skip guest-generated emails)
  useEffect(() => {
    if (!form.email && user) {
      import('@/lib/api').then(({ api }) => {
        api.get<{ email: string; phone?: string }>('/auth/me')
          .then((user) => {
            const isGuestEmail = /@guest\.local$/.test(user.email);
            setForm((prev) => ({
              ...prev,
              email: isGuestEmail ? prev.email : user.email,
              phone: prev.phone || (user.phone ?? ''),
            }));
          })
          .catch(() => { /* guest user */ });
      });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onChange = useCallback((patch: Partial<CheckoutForm>) => {
    setForm((prev) => {
      const next = { ...prev, ...patch };
      saveToSession(next);
      return next;
    });
  }, []);

  const goToStep = useCallback((n: number) => {
    setStep(n);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }, []);

  const handleSubmit = useCallback(async () => {
    setSubmitting(true);
    let createdOrder: import('@/lib/types').Order | null = null;
    try {
      // Ensure we have an auth session (create guest session if not logged in)
      if (!user) {
        ensureSessionId();
        await authApi.guest();
      }

      let orderNumber: string;

      if (retryOrder) {
        orderNumber = retryOrder.order_number;
      } else {
        if (!cart || cart.items.length === 0) {
          addToast(t('checkout.cart_empty'), 'error');
          setSubmitting(false);
          return;
        }

        const needsAddress = form.deliveryMethod === 'delivery';
        const shippingAddress = needsAddress
          ? {
              first_name: form.firstName,
              last_name: form.lastName,
              phone: form.phone,
              address_line_1: form.addressLine1,
              address_line_2: form.addressLine2 || undefined,
              region: form.region as RegionCode,
              latitude: form.locationLat ?? undefined,
              longitude: form.locationLng ?? undefined,
            }
          : undefined;

        const order = await ordersApi.create({
          email: form.email.trim().toLowerCase(),
          delivery_method: form.deliveryMethod,
          shipping_address: shippingAddress,
          promo_code: form.promoDiscount > 0 ? form.promoCode : undefined,
          payment_method: form.paymentMethod,
          notes: form.notes || undefined,
          session_id: getSessionId() ?? undefined,
        });
        createdOrder = order;
        orderNumber = order.order_number;

        clearCheckoutSession();
        await refreshCart();
      }

      const session = await paymentsApi.createSession(orderNumber, form.paymentMethod);

      // Zero-total order: backend auto-confirmed it, redirect straight to confirmation.
      if (session.confirmed) {
        window.location.href = `/${locale}/checkout/confirmation?order_number=${orderNumber}`;
        return;
      }

      window.location.href = session.checkout_url!;
    } catch (err) {
      // If the order was created but payment session setup failed, preserve it so the
      // user can retry payment without needing a cart (cart was already cleared).
      if (createdOrder) {
        setRetryOrder(createdOrder);
      }
      const message = err instanceof Error ? err.message : 'Something went wrong. Please try again.';
      addToast(message, 'error');
      setSubmitting(false);
    }
  }, [form, cart, retryOrder, locale, addToast, refreshCart, t]);

  if (!cart && !submitting) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-20 flex flex-col items-center gap-4">
        <Spinner size="lg" />
        <p className="font-body text-sm text-gray-400">{t('checkout.loading_cart')}</p>
      </div>
    );
  }

  if (cart && cart.items.length === 0 && !submitting && !retryOrder) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-16 flex flex-col items-center text-center gap-4">
        <span className="material-icons text-5xl text-secondary">shopping_bag</span>
        <h1 className="font-display text-2xl text-primary uppercase tracking-widest">{t('checkout.cart_empty')}</h1>
        <Link href={`/${locale}`}><Button variant="primary">{t('cart.continue_shopping')}</Button></Link>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-10">
      <Breadcrumb items={[{ label: t('breadcrumb.home'), href: `/${locale}` }, { label: t('breadcrumb.cart'), href: `/${locale}/cart` }, { label: t('breadcrumb.checkout') }]} />

      <header className="mb-2">
        <h1 className="font-display text-3xl sm:text-4xl text-primary uppercase tracking-widest">{t('breadcrumb.checkout')}</h1>
      </header>

      <StepIndicator step={step} />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-10">
        <section className="lg:col-span-2">
          {step === 1 && (
            <StepInformation
              form={form}
              onChange={onChange}
              onNext={() => goToStep(2)}
              savedAddresses={savedAddresses}
              loadingAddresses={loadingAddresses}
              regions={deliveryRates?.regions ?? []}
            />
          )}
          {step === 2 && (
            <StepDelivery
              form={form}
              onChange={onChange}
              onBack={() => goToStep(1)}
              onNext={() => goToStep(3)}
              subtotal={cart?.subtotal ?? 0}
              deliveryRates={deliveryRates}
            />
          )}
          {step === 3 && (
            <StepPayment
              form={form}
              onChange={onChange}
              onBack={() => goToStep(2)}
              cart={cart}
              retryOrder={retryOrder}
              onSubmit={handleSubmit}
              isSubmitting={submitting}
              deliveryRates={deliveryRates}
            />
          )}
        </section>

        <aside className="lg:col-span-1 order-first lg:order-last">
          <OrderSummarySidebar cart={cart} form={form} step={step} retryOrder={retryOrder} deliveryRates={deliveryRates} />
        </aside>
      </div>
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
