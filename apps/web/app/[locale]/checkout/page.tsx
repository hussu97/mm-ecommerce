'use client';

import Image from 'next/image';
import Link from 'next/link';
import { Suspense, useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'next/navigation';
import { useCart } from '@/lib/cart-context';
import {
  ordersApi, paymentsApi, promoApi, addressesApi,
  getToken, getSessionId, ensureSessionId, authApi, setToken,
} from '@/lib/api';
import { Breadcrumb } from '@/components/ui/Breadcrumb';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { Spinner } from '@/components/ui/Spinner';
import { useToast } from '@/components/ui/Toast';
import type { Address, Cart, CartItem, EmirateEnum } from '@/lib/types';

// ─── Constants ────────────────────────────────────────────────────────────────

const EMIRATES: EmirateEnum[] = [
  'Dubai', 'Sharjah', 'Ajman', 'Abu Dhabi', 'Ras Al Khaimah', 'Fujairah', 'Umm Al Quwain',
];
const EMIRATE_OPTIONS = EMIRATES.map((e) => ({ value: e, label: e }));

const DELIVERY_FEES: Record<string, number> = {
  Dubai: 35, Sharjah: 35, Ajman: 35,
  'Abu Dhabi': 50, 'Ras Al Khaimah': 50, 'Fujairah': 50, 'Umm Al Quwain': 50,
};
const FREE_THRESHOLD = 200;

function calcDeliveryFee(method: string, emirate: string, subtotal: number): number {
  if (method === 'pickup') return 0;
  if (subtotal >= FREE_THRESHOLD) return 0;
  return DELIVERY_FEES[emirate] ?? 50;
}

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
  city: string;
  emirate: string;
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
  addressLine1: '', addressLine2: '', city: '', emirate: 'Dubai',
  selectedAddressId: '',
  deliveryMethod: 'delivery',
  promoCode: '', promoDiscount: 0, promoMessage: '',
  paymentMethod: 'stripe',
  notes: '',
};

// ─── Step indicator ───────────────────────────────────────────────────────────

const STEPS = ['Information', 'Delivery', 'Payment'];

function StepIndicator({ step }: { step: number }) {
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
  cart, form, step,
}: {
  cart: Cart | null;
  form: CheckoutForm;
  step: number;
}) {
  const subtotal = cart?.subtotal ?? 0;
  const deliveryFee = step >= 2
    ? calcDeliveryFee(form.deliveryMethod, form.emirate, subtotal)
    : null;
  const discount = form.promoDiscount;
  const total = subtotal + (deliveryFee ?? 0) - discount;

  return (
    <div className="bg-gray-50 border border-gray-100 rounded-sm p-5 space-y-4 sticky top-24">
      <h2 className="font-display text-base text-primary uppercase tracking-widest">Order Summary</h2>

      {/* Items */}
      {cart && cart.items.length > 0 ? (
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
                <p className="font-body text-xs text-gray-800 leading-snug truncate">{item.product_name}</p>
                {item.selected_options && item.selected_options.length > 0 && (
                  <p className="font-body text-[11px] text-gray-400">
                    {item.selected_options.map(o => o.option_name).join(', ')}
                  </p>
                )}
              </div>
              <p className="font-body text-xs text-gray-700 shrink-0">
                {(item.line_total ?? 0).toFixed(2)} AED
              </p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="font-body text-xs text-gray-400">No items</p>
      )}

      <div className="h-px bg-gray-200" />

      {/* Totals */}
      <div className="space-y-1.5 font-body text-xs">
        <div className="flex justify-between">
          <span className="text-gray-500">Subtotal</span>
          <span>{subtotal.toFixed(2)} AED</span>
        </div>
        {discount > 0 && (
          <div className="flex justify-between text-green-700">
            <span>Discount{form.promoCode ? ` (${form.promoCode})` : ''}</span>
            <span>-{discount.toFixed(2)} AED</span>
          </div>
        )}
        <div className="flex justify-between">
          <span className="text-gray-500">Delivery</span>
          {deliveryFee === null ? (
            <span className="text-gray-400 italic">Next step</span>
          ) : deliveryFee === 0 ? (
            <span className="text-green-600">Free</span>
          ) : (
            <span>{deliveryFee.toFixed(2)} AED</span>
          )}
        </div>
      </div>

      <div className="h-px bg-gray-200" />

      <div className="flex justify-between font-body font-semibold text-sm">
        <span>Total</span>
        <span className="text-primary">{Math.max(0, total).toFixed(2)} AED</span>
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

function validateStep1(form: CheckoutForm): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!isValidEmail(form.email))
    errors.email = 'Valid email address is required';
  if (!form.firstName.trim()) errors.firstName = 'First name is required';
  if (!form.lastName.trim()) errors.lastName = 'Last name is required';
  if (!form.phone.trim() || !/^\+?[0-9\s()\-+]{7,15}$/.test(form.phone.trim())) errors.phone = 'Valid phone number is required';
  return errors;
}

function validateStep1Address(form: CheckoutForm): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!form.addressLine1.trim()) errors.addressLine1 = 'Address is required';
  if (!form.city.trim()) errors.city = 'City is required';
  if (!form.emirate) errors.emirate = 'Emirate is required';
  return errors;
}

// ─── Step 1: Information ──────────────────────────────────────────────────────

function StepInformation({
  form, onChange, onNext, savedAddresses, loadingAddresses,
}: {
  form: CheckoutForm;
  onChange: (patch: Partial<CheckoutForm>) => void;
  onNext: () => void;
  savedAddresses: Address[];
  loadingAddresses: boolean;
}) {
  const [errors, setErrors] = useState<Record<string, string>>({});

  const handleNext = () => {
    const contactErrors = validateStep1(form);
    // Only validate address if no saved address selected
    const needsAddress = !form.selectedAddressId;
    const addressErrors = needsAddress ? validateStep1Address(form) : {};
    const all = { ...contactErrors, ...addressErrors };
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
      <div>
        <h2 className="font-display text-xl text-primary uppercase tracking-widest mb-1">Contact Information</h2>
        <div className="h-px bg-secondary/30 mb-5" />
        <div className="space-y-4">
          <Input
            label="Email"
            type="email"
            placeholder="you@example.com"
            value={form.email}
            onChange={field('email')}
            error={errors.email}
          />
          <div className="grid grid-cols-2 gap-4">
            <Input
              label="First Name"
              placeholder="Fatema"
              value={form.firstName}
              onChange={field('firstName')}
              error={errors.firstName}
            />
            <Input
              label="Last Name"
              placeholder="Abbasi"
              value={form.lastName}
              onChange={field('lastName')}
              error={errors.lastName}
            />
          </div>
          <Input
            label="Phone"
            type="tel"
            placeholder="+971 50 000 0000"
            value={form.phone}
            onChange={field('phone')}
            error={errors.phone}
          />
        </div>
      </div>

      {/* Delivery address */}
      <div>
        <h2 className="font-display text-xl text-primary uppercase tracking-widest mb-1">Delivery Address</h2>
        <p className="font-body text-xs text-gray-400 mb-4">Required for home delivery. Skip if you plan to pick up.</p>
        <div className="h-px bg-secondary/30 mb-5" />

        {/* Saved addresses for authenticated users */}
        {loadingAddresses ? (
          <div className="flex items-center gap-2 text-gray-400 mb-4">
            <Spinner size="sm" /> <span className="font-body text-xs">Loading saved addresses…</span>
          </div>
        ) : savedAddresses.length > 0 ? (
          <div className="space-y-2 mb-5">
            {savedAddresses.map((addr) => (
              <label key={addr.id} className="flex gap-3 items-start p-3 border rounded-sm cursor-pointer hover:border-primary/50 transition-colors">
                <input
                  type="radio"
                  name="savedAddress"
                  value={addr.id}
                  checked={form.selectedAddressId === addr.id}
                  onChange={() => {
                    onChange({
                      selectedAddressId: addr.id,
                      firstName: addr.first_name,
                      lastName: addr.last_name,
                      phone: addr.phone,
                      addressLine1: addr.address_line_1,
                      addressLine2: addr.address_line_2 ?? '',
                      city: addr.city,
                      emirate: addr.emirate,
                    });
                    setErrors((prev) => {
                      const next = { ...prev };
                      delete next.addressLine1; delete next.city; delete next.emirate;
                      return next;
                    });
                  }}
                  className="mt-0.5 accent-primary"
                />
                <div className="font-body text-xs">
                  <p className="font-medium text-gray-800">{addr.label}</p>
                  <p className="text-gray-500">{addr.address_line_1}, {addr.city}, {addr.emirate}</p>
                </div>
              </label>
            ))}
            <label className="flex gap-3 items-start p-3 border rounded-sm cursor-pointer hover:border-primary/50 transition-colors">
              <input
                type="radio"
                name="savedAddress"
                value=""
                checked={form.selectedAddressId === ''}
                onChange={() => onChange({ selectedAddressId: '' })}
                className="mt-0.5 accent-primary"
              />
              <span className="font-body text-xs text-gray-600">+ Enter a new address</span>
            </label>
          </div>
        ) : null}

        {/* New address form */}
        {form.selectedAddressId === '' && (
          <div className="space-y-4">
            <Input
              label="Address Line 1"
              placeholder="Building, Street"
              value={form.addressLine1}
              onChange={field('addressLine1')}
              error={errors.addressLine1}
            />
            <Input
              label="Address Line 2 (optional)"
              placeholder="Apartment, floor"
              value={form.addressLine2}
              onChange={field('addressLine2')}
            />
            <div className="grid grid-cols-2 gap-4">
              <Input
                label="City"
                placeholder="Dubai"
                value={form.city}
                onChange={field('city')}
                error={errors.city}
              />
              <Select
                label="Emirate"
                options={EMIRATE_OPTIONS}
                value={form.emirate}
                onChange={field('emirate')}
                error={errors.emirate}
              />
            </div>
          </div>
        )}
      </div>

      <Button variant="primary" size="lg" fullWidth onClick={handleNext}>
        Continue to Delivery →
      </Button>
    </div>
  );
}

// ─── Step 2: Delivery ─────────────────────────────────────────────────────────

function StepDelivery({
  form, onChange, onBack, onNext, subtotal,
}: {
  form: CheckoutForm;
  onChange: (patch: Partial<CheckoutForm>) => void;
  onBack: () => void;
  onNext: () => void;
  subtotal: number;
}) {
  const deliveryFee = calcDeliveryFee('delivery', form.emirate, subtotal);
  const isFree = subtotal >= FREE_THRESHOLD;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="font-display text-xl text-primary uppercase tracking-widest mb-1">Delivery Method</h2>
        <div className="h-px bg-secondary/30 mb-5" />

        <div className="space-y-3">
          {/* Home Delivery */}
          <label
            className={`flex gap-4 p-4 border rounded-sm cursor-pointer transition-colors ${
              form.deliveryMethod === 'delivery'
                ? 'border-primary bg-primary/5'
                : 'border-gray-200 hover:border-primary/40'
            }`}
          >
            <input
              type="radio"
              name="deliveryMethod"
              value="delivery"
              checked={form.deliveryMethod === 'delivery'}
              onChange={() => onChange({ deliveryMethod: 'delivery' })}
              className="mt-0.5 accent-primary"
            />
            <div className="flex-1">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="material-icons text-xl text-primary">local_shipping</span>
                  <p className="font-body font-medium text-sm text-gray-800">Home Delivery</p>
                </div>
                <p className="font-body font-semibold text-sm">
                  {isFree ? (
                    <span className="text-green-600">Free</span>
                  ) : (
                    <span>{deliveryFee} AED</span>
                  )}
                </p>
              </div>
              <p className="font-body text-xs text-gray-500 mt-1 ml-7">
                {isFree
                  ? 'Free delivery — your order qualifies!'
                  : `Delivered to ${form.emirate || 'your address'} · 2–3 business days`}
              </p>
              {!isFree && subtotal > 0 && (
                <p className="font-body text-xs text-secondary mt-0.5 ml-7">
                  Add {(FREE_THRESHOLD - subtotal).toFixed(2)} AED more for free delivery
                </p>
              )}
            </div>
          </label>

          {/* Store Pickup */}
          <label
            className={`flex gap-4 p-4 border rounded-sm cursor-pointer transition-colors ${
              form.deliveryMethod === 'pickup'
                ? 'border-primary bg-primary/5'
                : 'border-gray-200 hover:border-primary/40'
            }`}
          >
            <input
              type="radio"
              name="deliveryMethod"
              value="pickup"
              checked={form.deliveryMethod === 'pickup'}
              onChange={() => onChange({ deliveryMethod: 'pickup' })}
              className="mt-0.5 accent-primary"
            />
            <div className="flex-1">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="material-icons text-xl text-primary">storefront</span>
                  <p className="font-body font-medium text-sm text-gray-800">Store Pickup</p>
                </div>
                <p className="font-body font-semibold text-sm text-green-600">Free</p>
              </div>
              <p className="font-body text-xs text-gray-500 mt-1 ml-7">
                Pickup from our location · We&apos;ll notify you when your order is ready
              </p>
            </div>
          </label>
        </div>

        {/* Delivery time note */}
        <div className="mt-4 p-3 bg-secondary/10 rounded-sm flex gap-2">
          <span className="material-icons text-base text-secondary mt-0.5">info</span>
          <p className="font-body text-xs text-gray-600">
            Orders placed before 12 PM are prepared the next day. We&apos;ll send you a WhatsApp confirmation once your order is packed.
          </p>
        </div>
      </div>

      <div className="flex gap-3">
        <Button variant="ghost" size="lg" onClick={onBack} className="flex-1">
          ← Back
        </Button>
        <Button variant="primary" size="lg" onClick={onNext} className="flex-1">
          Continue to Payment →
        </Button>
      </div>
    </div>
  );
}

// ─── Step 3: Payment ──────────────────────────────────────────────────────────

function StepPayment({
  form, onChange, onBack, cart, onSubmit, isSubmitting,
}: {
  form: CheckoutForm;
  onChange: (patch: Partial<CheckoutForm>) => void;
  onBack: () => void;
  cart: Cart | null;
  onSubmit: () => void;
  isSubmitting: boolean;
}) {
  const { addToast } = useToast();
  const [promoLoading, setPromoLoading] = useState(false);
  const [promoError, setPromoError] = useState<string | null>(null);

  const subtotal = cart?.subtotal ?? 0;
  const deliveryFee = calcDeliveryFee(form.deliveryMethod, form.emirate, subtotal);
  const total = Math.max(0, subtotal + deliveryFee - form.promoDiscount);

  const handleApplyPromo = useCallback(async () => {
    const code = form.promoCode.trim().toUpperCase();
    if (!code) return;
    setPromoLoading(true);
    setPromoError(null);
    try {
      const result = await promoApi.validate(code, subtotal);
      if (result.valid) {
        onChange({ promoCode: code, promoDiscount: Number(result.discount_amount), promoMessage: result.message ?? '' });
        addToast(`Promo "${code}" applied!`, 'success');
      } else {
        setPromoError(result.message ?? 'Invalid promo code');
        onChange({ promoDiscount: 0, promoMessage: '' });
      }
    } catch {
      setPromoError('Could not validate promo code. Please try again.');
    } finally {
      setPromoLoading(false);
    }
  }, [form.promoCode, subtotal, onChange, addToast]);

  const handleRemovePromo = () => {
    onChange({ promoCode: '', promoDiscount: 0, promoMessage: '' });
    setPromoError(null);
  };

  const PAYMENT_METHODS = [
    {
      id: 'stripe' as const,
      label: 'Credit / Debit Card',
      sublabel: 'Visa, Mastercard · Apple Pay',
      icon: 'credit_card',
      enabled: true,
    },
  ] as const;

  return (
    <div className="space-y-6">
      {/* Order review */}
      <div>
        <h2 className="font-display text-xl text-primary uppercase tracking-widest mb-1">Review & Pay</h2>
        <div className="h-px bg-secondary/30 mb-5" />

        {/* Totals review */}
        <div className="bg-gray-50 rounded-sm p-4 space-y-2 font-body text-sm mb-5">
          <div className="flex justify-between">
            <span className="text-gray-500">Subtotal</span>
            <span>{subtotal.toFixed(2)} AED</span>
          </div>
          {form.promoDiscount > 0 && (
            <div className="flex justify-between text-green-700">
              <span>Discount ({form.promoCode})</span>
              <span>-{form.promoDiscount.toFixed(2)} AED</span>
            </div>
          )}
          <div className="flex justify-between">
            <span className="text-gray-500">
              {form.deliveryMethod === 'pickup' ? 'Pickup' : 'Delivery'}
            </span>
            <span className={deliveryFee === 0 ? 'text-green-600' : ''}>
              {deliveryFee === 0 ? 'Free' : `${deliveryFee.toFixed(2)} AED`}
            </span>
          </div>
          <div className="h-px bg-gray-200" />
          <div className="flex justify-between font-semibold text-base">
            <span>Total</span>
            <span className="text-primary">{total.toFixed(2)} AED</span>
          </div>
        </div>

        {/* Promo code */}
        {form.promoDiscount > 0 ? (
          <div className="flex items-center justify-between bg-green-50 border border-green-200 rounded-sm px-3 py-2 mb-4">
            <div>
              <p className="font-body text-xs font-medium text-green-800">{form.promoCode}</p>
              {form.promoMessage && <p className="font-body text-xs text-green-600">{form.promoMessage}</p>}
            </div>
            <button onClick={handleRemovePromo} className="text-green-400 hover:text-green-700 transition-colors" aria-label="Remove promo">
              <span className="material-icons text-base">close</span>
            </button>
          </div>
        ) : (
          <div className="flex gap-2 items-start mb-4">
            <div className="flex-1 min-w-0">
              <Input
                placeholder="Promo code"
                value={form.promoCode}
                onChange={(e) => { onChange({ promoCode: e.target.value.toUpperCase() }); setPromoError(null); }}
                onKeyDown={(e) => e.key === 'Enter' && handleApplyPromo()}
                error={promoError ?? undefined}
              />
            </div>
            <Button variant="ghost" size="sm" onClick={handleApplyPromo} loading={promoLoading} disabled={!form.promoCode.trim()} className="shrink-0">
              Apply
            </Button>
          </div>
        )}

        {/* Notes */}
        <div className="mb-1">
          <label className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1.5">
            Order notes (optional)
          </label>
          <textarea
            value={form.notes}
            onChange={(e) => onChange({ notes: e.target.value })}
            placeholder="Any special requests or allergies?"
            rows={2}
            maxLength={500}
            className="w-full px-3.5 py-2.5 text-sm font-body bg-white border border-gray-300 rounded-sm outline-none resize-none focus:border-primary focus:ring-2 focus:ring-primary/20 transition-all"
          />
        </div>
      </div>

      {/* Payment method */}
      <div>
        <h2 className="font-display text-xl text-primary uppercase tracking-widest mb-1">Payment Method</h2>
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
                  {!enabled && (
                    <span className="font-body text-[10px] uppercase tracking-wider bg-gray-200 text-gray-500 px-1.5 py-0.5 rounded-sm">
                      Coming soon
                    </span>
                  )}
                </div>
                <p className="font-body text-xs text-gray-500 mt-0.5 ml-7">{sublabel}</p>
              </div>
            </label>
          ))}
        </div>

        {/* Security note */}
        <div className="mt-4 flex gap-2 items-center text-gray-400">
          <span className="material-icons text-base">lock</span>
          <p className="font-body text-xs">Payments are processed securely via Stripe. We never store your card details.</p>
        </div>
      </div>

      <div className="flex gap-3">
        <Button variant="ghost" size="lg" onClick={onBack} className="flex-1" disabled={isSubmitting}>
          ← Back
        </Button>
        <Button variant="primary" size="lg" onClick={onSubmit} loading={isSubmitting} className="flex-1">
          Pay Now — {total.toFixed(2)} AED
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

  const [step, setStep] = useState(1);
  const [form, setForm] = useState<CheckoutForm>(INITIAL_FORM);
  const [savedAddresses, setSavedAddresses] = useState<Address[]>([]);
  const [loadingAddresses, setLoadingAddresses] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Restore from sessionStorage + handle cancelled Stripe return
  useEffect(() => {
    const stored = loadFromSession();
    if (stored) {
      setForm((prev) => ({ ...prev, ...(stored as Partial<CheckoutForm>) }));
    }

    // Stripe cancelled → resume at payment step
    const returnStep = searchParams.get('step');
    const returnOrder = searchParams.get('order_number');
    if (returnStep === 'payment' && returnOrder) {
      setStep(3);
      addToast('Payment was cancelled. Please try again.', 'warning');
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Load saved addresses for authenticated users
  useEffect(() => {
    if (!getToken()) return;
    setLoadingAddresses(true);
    addressesApi.list()
      .then(setSavedAddresses)
      .catch(() => { /* not authenticated or no addresses */ })
      .finally(() => setLoadingAddresses(false));
  }, []);

  // Pre-fill contact info from API if authenticated (skip system-generated guest emails)
  useEffect(() => {
    if (!form.email && getToken()) {
      import('@/lib/api').then(({ api }) => {
        api.get<{ email: string; first_name: string; last_name: string; phone?: string }>('/auth/me')
          .then((user) => {
            const isGuestEmail = /@guest\.local$/.test(user.email);
            setForm((prev) => ({
              ...prev,
              email: isGuestEmail ? prev.email : user.email,
              firstName: prev.firstName || user.first_name,
              lastName: prev.lastName || user.last_name,
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
    if (!cart || cart.items.length === 0) {
      addToast('Your cart is empty', 'error');
      return;
    }

    setSubmitting(true);
    try {
      // Ensure we have a token (guest session)
      if (!getToken()) {
        ensureSessionId();
        const res = await authApi.guest();
        setToken(res.access_token);
      }

      // Build shipping address snapshot
      const needsAddress = form.deliveryMethod === 'delivery';
      const shippingAddress = needsAddress
        ? {
            first_name: form.firstName,
            last_name: form.lastName,
            phone: form.phone,
            address_line_1: form.addressLine1,
            address_line_2: form.addressLine2 || undefined,
            city: form.city,
            emirate: form.emirate as EmirateEnum,
          }
        : undefined;

      // Create order
      const order = await ordersApi.create({
        email: form.email.trim().toLowerCase(),
        delivery_method: form.deliveryMethod,
        shipping_address: shippingAddress,
        promo_code: form.promoDiscount > 0 ? form.promoCode : undefined,
        payment_method: form.paymentMethod,
        notes: form.notes || undefined,
        session_id: getSessionId() ?? undefined,
      });

      // Create payment session
      const session = await paymentsApi.createSession(order.order_number, form.paymentMethod);

      // Clear persisted checkout state
      clearCheckoutSession();
      await refreshCart();

      // Redirect to payment provider
      window.location.href = session.checkout_url;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Something went wrong. Please try again.';
      addToast(message, 'error');
      setSubmitting(false);
    }
  }, [form, cart, addToast, refreshCart]);

  if (!cart && !submitting) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-20 flex flex-col items-center gap-4">
        <Spinner size="lg" />
        <p className="font-body text-sm text-gray-400">Loading your cart…</p>
      </div>
    );
  }

  if (cart && cart.items.length === 0 && !submitting) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-16 flex flex-col items-center text-center gap-4">
        <span className="material-icons text-5xl text-secondary">shopping_bag</span>
        <h1 className="font-display text-2xl text-primary uppercase tracking-widest">Your cart is empty</h1>
        <Link href="/"><Button variant="primary">Continue Shopping</Button></Link>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-10">
      <Breadcrumb items={[{ label: 'Home', href: '/' }, { label: 'Cart', href: '/cart' }, { label: 'Checkout' }]} />

      {/* Heading */}
      <header className="mb-2">
        <h1 className="font-display text-3xl sm:text-4xl text-primary uppercase tracking-widest">Checkout</h1>
      </header>

      <StepIndicator step={step} />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-10">
        {/* Step content */}
        <section className="lg:col-span-2">
          {step === 1 && (
            <StepInformation
              form={form}
              onChange={onChange}
              onNext={() => goToStep(2)}
              savedAddresses={savedAddresses}
              loadingAddresses={loadingAddresses}
            />
          )}
          {step === 2 && (
            <StepDelivery
              form={form}
              onChange={onChange}
              onBack={() => goToStep(1)}
              onNext={() => goToStep(3)}
              subtotal={cart?.subtotal ?? 0}
            />
          )}
          {step === 3 && (
            <StepPayment
              form={form}
              onChange={onChange}
              onBack={() => goToStep(2)}
              cart={cart}
              onSubmit={handleSubmit}
              isSubmitting={submitting}
            />
          )}
        </section>

        {/* Sidebar */}
        <aside className="lg:col-span-1 order-first lg:order-last">
          <OrderSummarySidebar cart={cart} form={form} step={step} />
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
