import Image from 'next/image';

import { InfoTip } from '@/components/ui/InfoTip';
import { localizedField } from '@/lib/i18n/entity';
import type { Cart, CartItem, Order, OrderPreview } from '@/lib/types';

/**
 * What the customer is about to pay, line by line.
 *
 * **Every figure here is server-authoritative.** This component computes no
 * money: it reads `preview`, which is `POST /orders/preview` — the same
 * `order_pricing.compute_order_totals` the order is written from. It used to
 * work the grand total out itself (`subtotal + fee + lowOrderFee - discount`),
 * duplicating a computation the page above it was *also* doing with slightly
 * different inputs, and it printed the VAT line from `(subtotal - discount) *
 * 5 / 105` — a formula that ignored both fees, so the "VAT included" figure was
 * not 5/105 of the total printed two lines above it.
 *
 * The one exception is `retryOrder`: a customer coming back to pay for an order
 * that already exists owes what *that order* was priced at, not what today's
 * settings would charge for the same basket. Those figures are read off the
 * order row, which is equally authoritative and rather more binding.
 */
export function OrderSummary({
  cart, retryOrder, preview, lowOrderThreshold, lowOrderFeeAmount,
  hasPin, deliveryMethod, unserviceable, locale, t,
}: {
  cart: Cart | null;
  /** An order that exists and has not been paid for. Its own figures win. */
  retryOrder: Order | null;
  /** Null until the first preview lands, and while one is in flight. */
  preview: OrderPreview | null;
  /**
   * The live threshold and amount, so the explanation quotes the numbers the
   * server actually charges on. From `/delivery/rates`, not a constant here: a
   * commercial figure written in two places is a figure that will eventually
   * disagree with what is charged, on the one screen where that is visible.
   */
  lowOrderThreshold: number;
  lowOrderFeeAmount: number;
  /** True once a pin exists, so "not in this area" is a fact and not a guess. */
  hasPin: boolean;
  deliveryMethod: 'delivery' | 'pickup';
  /** Nothing can be delivered to this pin, so there is no total to commit to. */
  unserviceable: boolean;
  locale: string;
  t: (k: string, p?: Record<string, string | number>) => string;
}) {
  const quote = preview?.delivery;

  // Read, never derived. The `??` chains are a rendering order, not a
  // calculation: an existing order first, then the server's preview, then the
  // basket's own subtotal standing in for the one round trip before the first
  // preview lands. That last fallback is exactly what this screen showed on its
  // first paint before — no fee, no surcharge, no discount, because none of
  // them were known yet.
  const subtotal = retryOrder
    ? Number(retryOrder.subtotal)
    : (preview?.subtotal ?? cart?.subtotal ?? 0);
  const discount = retryOrder
    ? Number(retryOrder.discount_amount)
    : (preview?.discount_amount ?? 0);
  const promoCode = retryOrder?.promo_code_used ?? preview?.promo?.code ?? '';
  /** Null while the fee is unknown — no pin yet, or nowhere we can deliver. */
  const deliveryFee = retryOrder
    ? Number(retryOrder.delivery_fee)
    : (preview?.delivery_fee ?? null);
  const lowOrderFee = retryOrder
    ? Number(retryOrder.low_order_fee ?? 0)
    : (preview?.low_order_fee ?? 0);
  const total = retryOrder
    ? Number(retryOrder.total)
    : (preview?.total ?? subtotal);
  const vatAmount = retryOrder
    ? Number(retryOrder.vat_amount)
    : (preview?.vat_amount ?? 0);
  const vatRate = retryOrder ? Number(retryOrder.vat_rate) : (preview?.vat_rate ?? 0);

  /** What delivery would have cost — struck through once it is waived. */
  const baseFee = quote?.base_fee ?? 0;
  const freeApplied = quote?.free_delivery_applied ?? false;
  // Assumed available until the first answer arrives: the copy for that state
  // says "in selected areas", which is exactly what we know at that point.
  const freeAvailable = quote?.free_delivery_available ?? true;
  const remainingForFree = quote?.remaining_for_free ?? 0;

  const knownFee = deliveryFee ?? 0;
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
        {/* The rate and the amount both come from the server, which reads each
            product's own tax group. Both used to be written here: a hardcoded
            "5%" next to `(subtotal - discount) * 5 / 105`, a figure that
            ignored both fees and would have charged VAT on a zero-rated cake.
            The wording is still English-only, as it is on the confirmation and
            the account order page — one sentence, three places, and a
            translation key is its own change. */}
        <p className="text-[11px] text-gray-400 text-end">
          VAT included ({(vatRate * 100).toFixed(0)}%) · {vatAmount.toFixed(2)} AED
        </p>
      </div>
    </div>
  );
}
