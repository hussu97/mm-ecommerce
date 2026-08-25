'use client';

import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { ordersApi, ApiError } from '@/lib/api';
import type {
  FulfilmentOptions,
  FulfilmentProvider,
  FulfilmentQuote,
  Order,
  OrderDelivery,
  OrderEconomics,
  OrderStatus,
} from '@/lib/types';
import { Badge, Button } from '@/components/ui';
import { CourierLogo } from '@/components/orders/CourierLogo';
import { useConfirm, useToast } from '@/components/ui/feedback';
import { cn, formatCurrency, formatDateTime } from '@/lib/utils';

import {
  ADDRESS_FIELDS,
  MOVABLE_STATUSES,
  SETTLED_STATUSES,
  STATUS_LABEL,
  STATUS_STEPS,
  STATUS_VARIANT,
  humanizeCancelReason,
  promisedFor,
  recipientName,
  stampFor,
} from './order-status';
import { ChangeFulfilmentDialog } from './components/ChangeFulfilmentDialog';
import { DeliveryPanel } from './components/DeliveryPanel';
import { NetPayment } from './components/NetPayment';
import { PROVIDER_LABEL } from './components/courier-labels';

export default function OrderDetailPage() {
  const toast = useToast();
  const confirmDialog = useConfirm();
  const { orderNumber } = useParams<{ orderNumber: string }>();
  const [order, setOrder] = useState<Order | null>(null);
  const [delivery, setDelivery] = useState<OrderDelivery | null>(null);
  // What the shop kept. Admin-only and its own request, so a screen that fails
  // to load it still shows the order — the economics are context, not the page.
  const [economics, setEconomics] = useState<OrderEconomics | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [notes, setNotes] = useState('');
  const [error, setError] = useState('');
  // Where this order may go. Non-null means the dialog is open — it is fetched
  // on opening rather than with the order, because every answer in it depends
  // on where the order is right now and a value cached from page load would be
  // stale by exactly the amount that matters.
  const [options, setOptions] = useState<FulfilmentOptions | null>(null);
  // The courier picked in the dialog, and its price. The quote is what will be
  // booked and nothing else.
  const [target, setTarget] = useState<FulfilmentProvider | null>(null);
  const [quote, setQuote] = useState<FulfilmentQuote | null>(null);
  // A courier that would not price the job — out of range, unreachable. Shown
  // in the dialog rather than as a toast, because it is an answer about the
  // option they just picked and belongs next to it.
  const [quoteError, setQuoteError] = useState<string | null>(null);
  // Set when a quote lapsed and a fresh one replaced it mid-dialog, so the
  // second confirm is visibly a second decision rather than the same click
  // going through at a different number.
  const [quoteExpired, setQuoteExpired] = useState(false);

  useEffect(() => {
    ordersApi.get(orderNumber)
      .then(o => { setOrder(o); setNotes(o.admin_notes ?? ''); })
      .catch(() => setError('Order not found.'))
      .finally(() => setLoading(false));
  }, [orderNumber]);

  // A fulfilment record is only ever opened for delivery orders, so asking for
  // one on a pickup order — every POS check included — is a guaranteed 404.
  // Delivery orders placed before fulfilment was tracked still 404, hence the
  // catch: that one is genuinely expected and not worth logging.
  const isDeliveryOrder = order?.delivery_method === 'delivery';

  const loadDelivery = useCallback(() => {
    if (!isDeliveryOrder) return;
    ordersApi.getDelivery(orderNumber)
      .then(setDelivery)
      .catch(err => { if (!(err instanceof ApiError && err.status === 404)) console.error(err); });
  }, [orderNumber, isDeliveryOrder]);

  useEffect(() => { loadDelivery(); }, [loadDelivery]);

  // ── while somebody is on their way ────────────────────────────────────────
  //
  // A distance is only useful if it moves. Fetched once, the card would say
  // "~4.2 km away" for as long as the tab stayed open, and the number would go
  // from helpful to actively misleading without changing.
  //
  // Only while there is a driver still coming to the branch — the one state in
  // which the figure exists at all — so an order sitting on somebody's second
  // monitor all afternoon is not polling for a delivery that finished at noon.
  // Half a minute, against a position the server refreshes each minute.
  const isDriverInbound = delivery?.driver_distance_km !== null && delivery !== null;
  useEffect(() => {
    if (!isDriverInbound) return;
    const timer = setInterval(loadDelivery, 30_000);
    return () => clearInterval(timer);
  }, [isDriverInbound, loadDelivery]);

  // Reloaded alongside the order rather than once: a refund, a re-dispatch or a
  // courier finally invoicing all move the net, and a stale figure here is
  // worse than none — somebody would price against it.
  useEffect(() => {
    ordersApi.getEconomics(orderNumber).then(setEconomics).catch(() => setEconomics(null));
  }, [orderNumber, order?.status, order?.refunded_amount, delivery?.cost_total]);

  async function updateStatus(newStatus: OrderStatus) {
    if (!order) return;
    // Cancelling a live order goes through on the first click — it is the one
    // status the counter changes while the customer is still on the phone.
    //
    // Cancelling an *undelivered* one does not, and the difference is money.
    // That is the write-off: it is where the refund happens now, and a refund
    // is not something to fire off a single click on a screen somebody is
    // scrolling. Every other move still asks too.
    const straightThrough = newStatus === 'cancelled' && !isUndelivered;
    if (!straightThrough && !(await confirmDialog(
      // Correcting a settled order is not the same act as advancing a live
      // one, and the difference is money: cancelling refunds automatically,
      // and marking one delivered afterwards does not take the refund back.
      // Say so here rather than let somebody find it in a reconciliation.
      SETTLED_STATUSES.includes(order.status as OrderStatus)
        ? {
            title: 'Correct this order',
            message:
              `${order.order_number} is recorded as ` +
              `"${STATUS_LABEL[order.status as OrderStatus]}". Marking it delivered ` +
              'corrects that record. Any refund already issued stays issued — ' +
              'check it before continuing.',
            confirmLabel: 'Mark delivered',
          }
      // A second attempt at a failed handover. Worth its own words because the
      // one thing that can refuse it is invisible from here: an order refunded
      // while `undelivered` was still an ending is not ours to deliver, and the
      // API says so rather than this screen guessing at it.
      : isUndelivered && newStatus === 'packed'
        ? {
            title: 'Try this order again',
            message:
              `${order.order_number} goes back to packed and a courier is asked ` +
              'for again. The customer is charged nothing more. If the order was ' +
              'already refunded this will be refused — they have their money.',
            confirmLabel: 'Try again',
          }
      // Writing one off, which is where the refund actually happens now.
      : isUndelivered && newStatus === 'cancelled'
        ? {
            title: 'Write this order off',
            message:
              `${order.order_number} is cancelled and the customer is refunded ` +
              'for the goods. The delivery and small-order fees are kept — the ' +
              'van was booked and usually already drove.',
            confirmLabel: 'Write off & refund',
          }
        : {
            title: 'Change status',
            message: `Set order to "${STATUS_LABEL[newStatus]}"?`,
            confirmLabel: 'Change status',
          },
    ))) return;
    setActionLoading(true);
    try {
      const updated = await ordersApi.updateStatus(orderNumber, newStatus, notes || undefined);
      setOrder(updated);
      loadDelivery();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setActionLoading(false);
    }
  }

  async function redispatch() {
    if (!(await confirmDialog({
      title: 'Book courier again',
      message: 'Book the courier again for this order?',
      confirmLabel: 'Book courier',
    }))) return;
    setActionLoading(true);
    try {
      setDelivery(await ordersApi.dispatchDelivery(orderNumber));
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setActionLoading(false);
    }
  }

  async function openFulfilment() {
    setActionLoading(true);
    setQuoteExpired(false);
    setQuote(null);
    setQuoteError(null);
    setTarget(null);
    try {
      const found = await ordersApi.fulfilmentOptions(orderNumber);
      setOptions(found);
      // One option and nothing in the way is the overwhelmingly common case —
      // a third-party order going to Lalamove, or the reverse. Pricing it
      // straight away saves a click that has no decision in it.
      const only = found.targets.filter(t => t.available);
      if (only.length === 1 && !found.blocked) await pickTarget(only[0].provider);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setActionLoading(false);
    }
  }

  async function pickTarget(provider: FulfilmentProvider) {
    setTarget(provider);
    setQuote(null);
    setQuoteError(null);
    setQuoteExpired(false);
    setActionLoading(true);
    try {
      setQuote(await ordersApi.quoteFulfilment(orderNumber, provider));
    } catch (err) {
      setQuoteError((err as Error).message);
    } finally {
      setActionLoading(false);
    }
  }

  async function confirmFulfilment() {
    if (!target) return;
    setActionLoading(true);
    try {
      setDelivery(
        await ordersApi.reassignFulfilment(orderNumber, target, quote?.quotation_id),
      );
      // The order itself does not move — it stays `packed` until a rider
      // collects — but its history has, and the stepper reads that.
      setOrder(await ordersApi.get(orderNumber));
      setOptions(null);
      setTarget(null);
      setQuote(null);
      setQuoteExpired(false);
      toast.success(`Moved to ${PROVIDER_LABEL[target] ?? target}.`);
    } catch (err) {
      // A lapsed quotation comes back as a 409 carrying the current price.
      // Showing it and requiring another click is the whole point of the two
      // steps: nothing gets booked at a number nobody agreed to.
      const fresh = (err as ApiError)?.detail as { quote?: FulfilmentQuote } | undefined;
      if (err instanceof ApiError && err.status === 409 && fresh?.quote) {
        setQuote(fresh.quote);
        setQuoteExpired(true);
      } else {
        // Kept in the dialog rather than closed behind a toast: the message is
        // usually a reason the move was refused, and the person reading it is
        // about to pick a different courier.
        setQuoteError((err as Error).message);
      }
    } finally {
      setActionLoading(false);
    }
  }

  async function abandonBooking() {
    const exposure = options?.exposure;
    const confirmed = await confirmDialog({
      title: 'Abandon this booking?',
      message: exposure
        ? `${exposure.reason} The order stays where it is and keeps its courier, but loses the booking — so it can then be moved.`
        : 'The order keeps its courier but loses the booking, and can then be moved.',
      confirmLabel: 'Abandon booking',
    });
    if (!confirmed) return;
    setActionLoading(true);
    try {
      setDelivery(
        // The acknowledgement is a fact the API records, not a thing the dialog
        // claims — it refuses without it whenever a fee is likely.
        await ordersApi.abandonBooking(orderNumber, exposure?.will_be_charged ?? false),
      );
      setOrder(await ordersApi.get(orderNumber));
      // Re-read rather than closed: the whole point was to unblock the move, and
      // the person is still standing in the dialog waiting to make it.
      setOptions(await ordersApi.fulfilmentOptions(orderNumber));
      toast.success('Booking abandoned. This order now needs a courier.');
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setActionLoading(false);
    }
  }

  async function refreshCourier() {
    setActionLoading(true);
    try {
      setDelivery(await ordersApi.refreshDelivery(orderNumber));
      const fresh = await ordersApi.get(orderNumber);
      // The status may have moved with it — a pull that finds "delivered" walks
      // the order there, and leaving the header showing "packed" would make the
      // refresh look like it did nothing.
      setOrder(fresh);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setActionLoading(false);
    }
  }

  async function saveNotes() {
    if (!order) return;
    setActionLoading(true);
    try {
      const updated = await ordersApi.updateStatus(orderNumber, order.status, notes);
      setOrder(updated);
      toast.success('Notes saved.');
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setActionLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48">
        <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (error || !order) {
    return (
      <div className="text-sm text-red-500 font-body">{error || 'Order not found.'}</div>
    );
  }

  const isCancelled = order.status === 'cancelled';
  const isUndelivered = order.status === 'undelivered';
  // A marketplace order is fulfilled by the aggregator's own rider and its
  // status is mirrored in from GrubOps — MM does not drive it. So the shop
  // neither packs, dispatches, delivers nor marks it undelivered from here; the
  // fulfilment actions are hidden and the page is a read-only record.
  const isAggregator = order.source === 'aggregator';
  const currentStepIdx = STATUS_STEPS.indexOf(order.status as OrderStatus);
  const promisedLabel = promisedFor(order);
  // Built here rather than in the JSX so the guard and the URL stay together:
  // a missing pin renders no button rather than a link to the middle of the sea.
  const snapshot = order.shipping_address_snapshot;
  const mapsHref =
    snapshot?.latitude && snapshot?.longitude
      ? `https://www.google.com/maps/search/?api=1&query=${snapshot.latitude},${snapshot.longitude}`
      : null;
  // The customer, from the order first and the address snapshot second. A
  // website order carries its name and number on the snapshot; an aggregator or
  // counter order has no snapshot and carries them on the order itself — which
  // is why an aggregator order used to show only an email here. The phone shows
  // wherever the name does, and for a Deliveroo order it already carries the
  // access code, joined onto the number server-side.
  const customerName = order.customer_name || (snapshot ? recipientName(snapshot) : null);
  const customerPhone = order.customer_phone || snapshot?.phone || null;
  // The access code joins the number only for display (Deliveroo); the type is a
  // readable label ("mobile" / "landline") beside it.
  const customerPhoneShown = customerPhone
    ? customerPhone + (order.customer_phone_access_code ? ` (Access code ${order.customer_phone_access_code})` : '')
    : null;
  // The email only when it is one — an aggregator order's may be blank, and a
  // blank line reads as a missing field rather than "no email given".
  const customerEmail = order.email && order.email.includes('@') ? order.email : null;

  return (
    <div className="max-w-3xl">
      {options && (
        <ChangeFulfilmentDialog
          options={options}
          quote={quote}
          target={target}
          expired={quoteExpired}
          busy={actionLoading}
          error={quoteError}
          onPick={pickTarget}
          onConfirm={confirmFulfilment}
          onAbandon={abandonBooking}
          onCancel={() => {
            setOptions(null);
            setTarget(null);
            setQuote(null);
            setQuoteError(null);
            setQuoteExpired(false);
          }}
        />
      )}

      {/* Back + header */}
      <div className="flex items-center gap-3 mb-6">
        <Link href="/orders" className="inline-flex items-center justify-center min-h-11 min-w-11 -ml-2 md:min-h-0 md:min-w-0 md:ml-0 text-gray-400 hover:text-primary transition-colors">
          <span className="material-icons text-[20px]">arrow_back</span>
        </Link>
        <div className="flex-1">
          <h1 className="font-display text-xl text-gray-800">{order.order_number}</h1>
          <p className="text-xs text-gray-400 font-body">{formatDateTime(order.created_at)}</p>
        </div>
        <Badge variant={STATUS_VARIANT[order.status]}>{order.status}</Badge>
      </div>

      {/* A rider got there and came back with the box. Said before the
          timeline, because the timeline shows a journey this order has
          stepped out of. */}
      {isUndelivered && (
        <div className="bg-red-50 border border-red-200 p-4 mb-4">
          <p className="text-[11px] font-body uppercase tracking-widest text-red-500 mb-1">Undelivered</p>
          <p className="text-sm font-body text-red-800">
            A rider reached the address and could not hand the order over. It is paid for
            and still ours to deliver — re-dispatch it below, or cancel and refund.
          </p>
        </div>
      )}

      {/* Status timeline */}
      {!isCancelled && !isUndelivered && (
        <div className="bg-white border border-gray-200 p-4 mb-4">
          <div className="flex items-baseline justify-between mb-3">
            <p className="text-[11px] font-body uppercase tracking-widest text-gray-400">Status</p>
            {/* The promise, rendered at the precision it was made at. A
                third-party order has a date and no hour, and printing one would
                borrow a precision belonging to somebody else's van. */}
            {promisedLabel && (
              <p className="text-[11px] font-body text-gray-500">
                Estimated delivery{' '}
                <span className="text-gray-800">{promisedLabel}</span>
              </p>
            )}
          </div>
          <div className="flex items-center gap-0">
            {STATUS_STEPS.map((step, idx) => {
              const done = currentStepIdx >= idx;
              return (
                <div key={step} className="flex items-center flex-1">
                  <div className="flex flex-col items-center">
                    <div className={cn(
                      'w-7 h-7 rounded-full flex items-center justify-center text-xs font-body transition-colors',
                      done ? 'bg-primary text-white' : 'bg-gray-100 text-gray-400'
                    )}>
                      {done ? <span className="material-icons text-[14px]">check</span> : idx + 1}
                    </div>
                    <span className={cn('text-[10px] mt-1 font-body capitalize', done ? 'text-primary' : 'text-gray-400')}>
                      {STATUS_LABEL[step]}
                    </span>
                    {/* When it actually happened. A tick tells you an order got
                        somewhere; the stamp tells you when, which is the
                        question anyone opening this page at 9pm is asking. */}
                    <span className="text-[9px] mt-0.5 font-body text-gray-400 text-center leading-tight min-h-[1.2em]">
                      {stampFor(order, step)}
                    </span>
                  </div>
                  {idx < STATUS_STEPS.length - 1 && (
                    <div className={cn('flex-1 h-0.5 mx-1 mb-4', done && currentStepIdx > idx ? 'bg-primary' : 'bg-gray-100')} />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* A marketplace order carries its channel, its short pickup code and the
          delivery fee the customer paid the aggregator. The shop still drives it
          through our own lifecycle from here: Packed calls the rider (via GrubOps
          force-complete) and the order auto-closes to delivered a few minutes
          later; Cancel calls force-cancel. Delivery itself is the aggregator's
          rider — its name and number are shown once GrubOps assigns one. */}
      {isAggregator && (
        <div className="bg-white border border-gray-200 p-4 mb-4">
          <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-2">
            Marketplace
          </p>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            {order.courier && <CourierLogo courier={order.courier} size={28} showName />}
            {order.aggregator_display_code && (
              <span className="text-sm font-body text-gray-700">
                Pickup code{' '}
                <span className="font-medium text-gray-900">
                  {order.aggregator_display_code}
                </span>
              </span>
            )}
            {order.external_reference && (
              <span className="text-sm font-body text-gray-500">
                Ref {order.external_reference}
              </span>
            )}
            {order.aggregator_delivery_fee != null && (
              <span className="text-sm font-body text-gray-500">
                Customer delivery {formatCurrency(order.aggregator_delivery_fee)}
                <span className="text-gray-400"> (theirs, not in sales)</span>
              </span>
            )}
          </div>

          {/* The aggregator's rider, once assigned. No live GPS in the payload,
              so no distance — a name and a number is all GrubOps gives us. */}
          {(order.aggregator_driver_name || order.aggregator_driver_phone) && (
            <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-1 border-t border-gray-100 pt-3">
              <span className="text-[11px] font-body uppercase tracking-widest text-gray-400">
                Driver
              </span>
              {order.aggregator_driver_name && (
                <span className="text-sm font-body text-gray-800">
                  {order.aggregator_driver_name}
                </span>
              )}
              {order.aggregator_driver_phone && (
                <a
                  href={`tel:${order.aggregator_driver_phone}`}
                  className="text-sm font-body text-primary hover:underline"
                >
                  {order.aggregator_driver_phone}
                </a>
              )}
              {order.aggregator_driver_status && (
                <span className="text-xs font-body text-gray-400">
                  {order.aggregator_driver_status.replace(/_/g, ' ').toLowerCase()}
                </span>
              )}
            </div>
          )}

          {/* Why the marketplace cancelled it, when GrubOps gave a reason. Only
              a marketplace-side cancel fills this — a cancel from the shop here
              leaves it null — so it reads as "the aggregator called it off, and
              this is what they said". */}
          {isCancelled && order.aggregator_cancel_reason && (
            <div className="mt-3 border-t border-gray-100 pt-3">
              <span className="text-[11px] font-body uppercase tracking-widest text-red-500">
                Cancelled by marketplace
              </span>
              <p className="mt-1 text-sm font-body text-red-800">
                {humanizeCancelReason(order.aggregator_cancel_reason)}
              </p>
            </div>
          )}

          {/* The two standardized actions. Packed while the order is at the shop
              (arrived_at_pos, or confirmed before the sweep lands it); Cancel
              from either the shop or packed — GrubOps decides whether force-cancel
              still lands once the rider has moved. */}
          <div className="mt-4 flex gap-2">
            {(order.status === 'confirmed' || order.status === 'arrived_at_pos') && (
              <Button size="sm" onClick={() => updateStatus('packed')} loading={actionLoading}>
                <span className="material-icons text-[14px]">inventory</span>
                Mark Packed
              </Button>
            )}
            {(order.status === 'confirmed' ||
              order.status === 'arrived_at_pos' ||
              order.status === 'packed') && (
              <Button
                variant="danger"
                size="sm"
                onClick={() => updateStatus('cancelled')}
                loading={actionLoading}
              >
                <span className="material-icons text-[14px]">cancel</span>
                Cancel Order
              </Button>
            )}
          </div>

          {!(
            order.status === 'confirmed' ||
            order.status === 'arrived_at_pos' ||
            order.status === 'packed'
          ) && (
            <p className="mt-3 text-xs font-body text-gray-400">
              This order is {order.status}. The aggregator delivered or cancelled it;
              there is nothing left to mark.
            </p>
          )}
        </div>
      )}

      {/* Action buttons — hidden for a marketplace order, which MM does not
          drive. */}
      {!isAggregator && (
      <div className="flex gap-2 mb-4">
        {order.status === 'created' && (
          <Button size="sm" onClick={() => updateStatus('confirmed')} loading={actionLoading}>
            <span className="material-icons text-[14px]">check_circle</span>
            Confirm
          </Button>
        )}
        {order.status === 'confirmed' && (
          <Button size="sm" onClick={() => updateStatus('packed')} loading={actionLoading}>
            <span className="material-icons text-[14px]">inventory</span>
            Mark Packed
          </Button>
        )}
        {order.status === 'packed' && (
          <Button size="sm" onClick={() => updateStatus('out_for_delivery')} loading={actionLoading}>
            <span className="material-icons text-[14px]">local_shipping</span>
            Mark On The Way
          </Button>
        )}
        {/* The last of these two is a correction, not a step: `undelivered`
            and `cancelled` are endings, and the API opens the way back out of
            them for this screen alone (`ADMIN_RECOVERABLE`) — a courier's late
            "delivered" push must not resurrect an order the shop wrote off,
            but a person who knows the customer has the cake may say so. */}
        {(order.status === 'packed' ||
          order.status === 'out_for_delivery' ||
          isUndelivered ||
          isCancelled) && (
          <Button size="sm" onClick={() => updateStatus('delivered')} loading={actionLoading}>
            <span className="material-icons text-[14px]">done_all</span>
            {isUndelivered || isCancelled ? 'Correct To Delivered' : 'Mark Delivered'}
          </Button>
        )}
        {/* A courier reports this itself. The button is for the times nobody
            did — a third-party zone, or a driver who phoned the shop. */}
        {(order.status === 'packed' || order.status === 'out_for_delivery') && (
          <Button variant="danger" size="sm" onClick={() => updateStatus('undelivered')} loading={actionLoading}>
            <span className="material-icons text-[14px]">report_problem</span>
            Mark Undelivered
          </Button>
        )}
        {/* The two ways out of a failed handover, and they are a pair: try
            again, or stop. Both were removed while `undelivered` was an ending
            — the API refused either, so they could only produce a red toast —
            and both come back with it.

            "Try Again" rather than "Return To Packed": the second describes our
            column, the first describes what the shop is doing. It goes to
            `packed` because that is where the box actually is and where
            dispatch hangs off, so a second attempt runs through the ordinary
            machinery rather than a path of its own. */}
        {isUndelivered && (
          <Button size="sm" onClick={() => updateStatus('packed')} loading={actionLoading}>
            <span className="material-icons text-[14px]">replay</span>
            Try Again
          </Button>
        )}
        {isUndelivered && (
          <Button variant="danger" size="sm" onClick={() => updateStatus('cancelled')} loading={actionLoading}>
            <span className="material-icons text-[14px]">cancel</span>
            Write Off &amp; Refund
          </Button>
        )}
        {(order.status === 'created' || order.status === 'confirmed') && (
          <Button variant="danger" size="sm" onClick={() => updateStatus('cancelled')} loading={actionLoading}>
            <span className="material-icons text-[14px]">cancel</span>
            Cancel Order
          </Button>
        )}
      </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
        {/* Customer info */}
        <div className="bg-white border border-gray-200 p-4">
          <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-2">Customer</p>
          {customerName && (
            <p className="text-sm font-body text-gray-800">{customerName}</p>
          )}
          {/* The number sits with the name — for a marketplace order it is often
              the only way to reach the customer, and it carries any Deliveroo
              access code. */}
          {customerPhoneShown && (
            <p className="text-sm font-body text-gray-700">
              {customerPhoneShown}
              {order.customer_phone_type && (
                <span className="ml-2 text-xs text-gray-400">{order.customer_phone_type}</span>
              )}
            </p>
          )}
          {customerEmail && (
            <p className="text-xs font-body text-gray-500 mt-0.5">{customerEmail}</p>
          )}
          {!customerName && !customerPhone && !customerEmail && (
            <p className="text-sm font-body text-gray-400">No customer details given.</p>
          )}
          {snapshot && (
            <div className="mt-2 text-xs font-body text-gray-500">
              {/* Broken out rather than stacked into a paragraph. Every one of
                  these is a field the customer typed into its own box, and
                  running them together is how `unit_number` — the flat, the
                  villa, the office — came to be invisible on the one screen
                  somebody opens when a driver rings to say they cannot find
                  the door. `address_format.one_line` fixed the same bug in the
                  emails, the ticket and both couriers; this page was the fifth
                  copy and the last one still dropping it. */}
              <dl className="mt-2 space-y-1.5">
                {ADDRESS_FIELDS.map(({ key, label }) => {
                  const value = snapshot[key];
                  if (!value) return null;
                  return (
                    <div key={key}>
                      <dt className="text-[10px] uppercase tracking-wider text-gray-400">{label}</dt>
                      <dd className="text-gray-700">{value}</dd>
                    </div>
                  );
                })}
              </dl>
              {/* The pin, not the typed address. A UAE address line is often
                  unsearchable — "villa 12, behind the mosque" is a real one —
                  and the coordinates are what the courier is sent to. */}
              {mapsHref && (
                <a
                  href={mapsHref}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-3 inline-flex items-center gap-1.5 border border-gray-300 px-2.5 py-1.5 text-[11px] font-body text-gray-700 hover:border-primary hover:text-primary transition-colors"
                >
                  <span className="material-icons text-[14px]">place</span>
                  Open the pin in Google Maps
                </a>
              )}
            </div>
          )}
        </div>

        {/* Delivery + Payment */}
        <div className="bg-white border border-gray-200 p-4">
          <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-2">Delivery & Payment</p>
          <dl className="text-xs font-body space-y-1">
            <div className="flex justify-between">
              <dt className="text-gray-500">Method</dt>
              <dd className="text-gray-700 capitalize">{order.delivery_method}</dd>
            </div>
            {/* Two lines, because they are two facts. "Card" is what the
                customer chose; the gateway is which processor settled it, and
                that is the one worth seeing when reconciling a payout or
                chasing a payment that a failover moved. A cash order has no
                gateway, so the second line stays off it. */}
            <div className="flex justify-between">
              <dt className="text-gray-500">Payment</dt>
              <dd className="text-gray-700 capitalize">{order.payment_method ?? '—'}</dd>
            </div>
            {order.payment_method !== 'cod' && (
              <div className="flex justify-between">
                <dt className="text-gray-500">Gateway</dt>
                <dd className="text-gray-700 capitalize">{order.payment_provider ?? '—'}</dd>
              </div>
            )}
            {order.promo_code_used && (
              <div className="flex justify-between">
                <dt className="text-gray-500">Promo</dt>
                <dd className="text-gray-700">{order.promo_code_used}</dd>
              </div>
            )}
            {/* Why the card was refused, on a failed order. The normalised
                bucket is the first thing to read; the gateway's own message
                (when present) is the raw detail underneath it, which is the
                line worth having when a customer writes in. Off every order
                that did not fail at payment. */}
            {order.status === 'payment_failed' &&
              (order.payment_failure_reason || order.payment_failure_message) && (
              <div className="flex justify-between gap-4">
                <dt className="text-gray-500 shrink-0">Decline reason</dt>
                <dd className="text-red-700 text-right">
                  {order.payment_failure_reason && (
                    <span className="capitalize">
                      {order.payment_failure_reason.replace(/_/g, ' ')}
                    </span>
                  )}
                  {order.payment_failure_message && (
                    <span className="block text-gray-500">
                      {order.payment_failure_message}
                    </span>
                  )}
                </dd>
              </div>
            )}
          </dl>
        </div>
      </div>

      {delivery && (
        <DeliveryPanel
          delivery={delivery}
          busy={actionLoading}
          onRedispatch={redispatch}
          onChangeFulfilment={openFulfilment}
          canChangeFulfilment={MOVABLE_STATUSES.includes(order.status)}
          isSettled={SETTLED_STATUSES.includes(order.status)}
          onRefresh={refreshCourier}
        />
      )}

      {/* Items */}
      <div className="bg-white border border-gray-200 mb-4">
        <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 px-4 pt-4 pb-2">Items</p>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50">
              <th className="px-4 py-2 text-left text-[11px] font-body uppercase tracking-widest text-gray-400">Product</th>
              <th className="px-4 py-2 text-center text-[11px] font-body uppercase tracking-widest text-gray-400">Qty</th>
              <th className="px-4 py-2 text-right text-[11px] font-body uppercase tracking-widest text-gray-400">Unit</th>
              <th className="px-4 py-2 text-right text-[11px] font-body uppercase tracking-widest text-gray-400">Total</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {order.items.map(item => (
              <tr key={item.id}>
                <td className="px-4 py-2.5">
                  <div className="text-xs font-body text-gray-800">{item.product_name}</div>
                  <div className="text-[11px] font-body text-gray-400">{item.product_sku}</div>
                  {/*
                    Each option carries its own count, and the count is the
                    whole content of a mixed box: "Fudge Brownie, Lindor
                    Brownie" describes a box of six no better than a box of two.
                    Printed for every option rather than only the plural ones —
                    beside a 5x, a bare name reads as an unknown quantity rather
                    than as one.
                  */}
                  {item.selected_options_snapshot && item.selected_options_snapshot.length > 0 && (
                    <div className="text-[11px] font-body text-gray-400 mt-0.5">
                      {item.selected_options_snapshot.map((o, i) => (
                        <span key={i}>{i > 0 ? ', ' : ''}{o.quantity ?? 1} &times; {o.option_name}{o.option_price > 0 ? ` (+${o.option_price.toFixed(2)})` : ''}</span>
                      ))}
                    </div>
                  )}
                  {/*
                    Louder than the options above it, and quoted. This is the
                    line somebody copies onto a card by hand — it has to be
                    legible and its edges have to be obvious, so a trailing
                    space or a deliberate lower-case name survives the reading.
                  */}
                  {item.personalisation_note && (
                    <div className="mt-1 border-s-2 border-primary/40 ps-2" dir="auto">
                      <div className="text-[10px] font-body uppercase tracking-widest text-gray-400">Handwritten note</div>
                      <div className="text-xs font-body text-gray-800 whitespace-pre-wrap">&ldquo;{item.personalisation_note}&rdquo;</div>
                    </div>
                  )}
                </td>
                <td className="px-4 py-2.5 text-center text-xs font-body text-gray-600">{item.quantity}</td>
                <td className="px-4 py-2.5 text-right text-xs font-body text-gray-600">{formatCurrency(item.unit_price)}</td>
                <td className="px-4 py-2.5 text-right text-xs font-body text-gray-800">{formatCurrency(item.total_price)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {/* Totals */}
        <div className="border-t border-gray-100 px-4 py-3 space-y-1.5">
          <div className="flex justify-between text-xs font-body text-gray-500">
            <span>Subtotal</span><span>{formatCurrency(order.subtotal)}</span>
          </div>
          {order.discount_amount > 0 && (
            <div className="flex justify-between text-xs font-body text-green-600">
              <span>Discount</span><span>-{formatCurrency(order.discount_amount)}</span>
            </div>
          )}
          <div className="flex justify-between text-xs font-body text-gray-500">
            <span>Delivery</span>
            <span>{order.delivery_fee > 0 ? formatCurrency(order.delivery_fee) : 'Free'}</span>
          </div>
          {order.low_order_fee > 0 && (
            <div className="flex justify-between text-xs font-body text-gray-500">
              <span>Small order fee</span><span>{formatCurrency(order.low_order_fee)}</span>
            </div>
          )}
          <div className="flex justify-between text-sm font-body font-medium text-gray-800 pt-1 border-t border-gray-100">
            <span>Total</span><span>{formatCurrency(order.total)}</span>
          </div>
          <div className="flex justify-between text-xs font-body text-gray-400 mt-1">
            <span>VAT included ({Math.round(order.vat_rate * 100)}%)</span><span>{formatCurrency(order.vat_amount)}</span>
          </div>
        </div>
        {economics && <NetPayment economics={economics} order={order} />}
      </div>

      {/* Admin Notes */}
      <div className="bg-white border border-gray-200 p-4">
        <p className="text-[11px] font-body uppercase tracking-widest text-gray-400 mb-2">Admin Notes</p>
        <textarea
          value={notes}
          onChange={e => setNotes(e.target.value)}
          rows={3}
          placeholder="Internal notes (not shown to customer)…"
          className="w-full px-3 py-2 text-xs font-body bg-white border border-gray-300 rounded-sm outline-none focus:border-primary focus:ring-1 focus:ring-primary/30 resize-none"
        />
        <div className="flex justify-end mt-2">
          <Button size="sm" variant="ghost" onClick={saveNotes} loading={actionLoading}>
            Save Notes
          </Button>
        </div>
      </div>
    </div>
  );
}

// ── Fulfilment ────────────────────────────────────────────────────────────────

// Each courier's own vocabulary, stored verbatim and translated only here.
