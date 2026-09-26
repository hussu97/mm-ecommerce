'use client';

/**
 * One custom order: what it is, what it costs, what it uses, where it is going,
 * and the buttons that move it along.
 *
 * Every button's availability comes from the order's `actions`, which the API
 * computes once for the console and both registers — this page never decides
 * for itself whether an order may be packed, edited or sent. Where an action is
 * closed and the API says why (`delivery_unavailable_reason`,
 * `invoice_unavailable_reason`), the reason is shown next to it.
 *
 * Money is rendered exactly as returned (rule 10): lines, charges, VAT and the
 * total are the API's stored figures.
 */

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import {
  ApiError,
  customOrdersApi,
  type CustomCakeItem,
  type CustomOrder,
  type CustomOrderDeliveryQuotes,
} from '@/lib/api';
import { Badge, Button, Input, Spinner, Textarea } from '@/components/ui';
import { useConfirm, useToast } from '@/components/ui/feedback';
import { Page } from '@/components/ui/Page';
import { cn, formatCurrency, formatDateTime } from '@/lib/utils';
import { Section } from '@/components/custom-orders/Section';
import {
  CARD_FEE_MODE_LABEL,
  CustomOrderStatusBadge,
  PAYMENT_TYPE_LABEL,
  deliveryLabel,
  providerLabel,
} from '@/components/custom-orders/display';
import { LinesEditor, linesError, newLine, toLinesIn, type LineDraft } from '@/components/custom-orders/LinesEditor';
import {
  RecipeEditor,
  RecipeReadOnly,
  recipeError,
  toRecipeIn,
  type RecipeDraft,
} from '@/components/custom-orders/RecipeEditor';
import { ContactFields, toCustomerIn, type ContactDraft } from '@/components/custom-orders/ContactFields';
import { AddressPicker, addressDraftFrom, toAddressIn, type AddressDraft } from '@/components/custom-orders/AddressPicker';
import {
  PaymentFields,
  paymentError,
  toPaymentFields,
  type CardFeeMode,
  type PaymentType,
} from '@/components/custom-orders/PaymentFields';

const DATE_INPUT =
  'w-full px-3 py-2 min-h-[var(--tap-min)] md:min-h-0 text-sm font-body bg-white border border-gray-300 rounded-sm outline-none focus:border-primary focus:ring-1 focus:ring-primary/30';

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

export default function CustomOrderDetailPage() {
  const params = useParams<{ orderNumber: string }>();
  const orderNumber = decodeURIComponent(params.orderNumber);
  const toast = useToast();
  const confirm = useConfirm();

  const [order, setOrder] = useState<CustomOrder | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState('');

  const load = useCallback(async () => {
    try {
      setOrder(await customOrdersApi.get(orderNumber));
      setLoadError('');
    } catch (err) {
      setLoadError(errorText(err, 'Could not load the order.'));
    } finally {
      setLoading(false);
    }
  }, [orderNumber]);

  useEffect(() => {
    void load();
  }, [load]);

  /** Runs one mutation: the API's answer replaces the order; its refusal is shown. */
  const run = useCallback(
    async (key: string, fn: () => Promise<CustomOrder>, success?: string) => {
      setBusy(key);
      setActionError('');
      try {
        const next = await fn();
        setOrder(next);
        if (success) toast.success(success);
        return true;
      } catch (err) {
        const msg = errorText(err, 'That did not go through.');
        setActionError(msg);
        toast.error(msg);
        return false;
      } finally {
        setBusy(null);
      }
    },
    [toast],
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48">
        <Spinner />
      </div>
    );
  }
  if (!order) {
    return (
      <div className="text-sm text-red-600 font-body">
        {loadError || 'Order not found.'}{' '}
        <Link href="/custom-orders" className="underline">
          Back to custom orders
        </Link>
      </div>
    );
  }

  const a = order.actions;
  const n = order.order_number;

  async function pack() {
    const ok = await confirm({
      title: 'Mark packed?',
      message:
        'The recipe is taken from the kitchen’s stock now and cannot be edited afterwards.',
      confirmLabel: 'Mark packed',
    });
    if (ok) await run('pack', () => customOrdersApi.pack(n), `${n} packed.`);
  }

  async function collected() {
    const ok = await confirm({
      title: 'Customer collected?',
      message: 'This finishes the order as delivered, today. No courier is booked.',
      confirmLabel: 'Mark collected',
    });
    if (ok) await run('collected', () => customOrdersApi.collected(n), `${n} delivered (collected).`);
  }

  async function cancel() {
    const ok = await confirm({
      title: `Cancel ${n}?`,
      message:
        'The order is cancelled and any booked courier is called off. Nothing is refunded automatically — settle any payment with the customer yourself. Stock already used at packing stays used.',
      confirmLabel: 'Cancel order',
      cancelLabel: 'Keep order',
      danger: true,
    });
    if (ok) await run('cancel', () => customOrdersApi.cancel(n), `${n} cancelled.`);
  }

  const anyAction = a.can_pack || a.can_collect || a.can_cancel;

  return (
    <Page maxWidth="reading">
      {/* Back + header */}
      <div className="flex items-center gap-3 mb-4">
        <Link
          href="/custom-orders"
          aria-label="Back to custom orders"
          className="inline-flex items-center justify-center min-h-11 min-w-11 -ml-2 md:min-h-0 md:min-w-0 md:ml-0 text-gray-400 hover:text-primary transition-colors"
        >
          <span className="material-icons text-[20px]">arrow_back</span>
        </Link>
        <div className="flex-1 min-w-0">
          <h1 className="font-display text-xl text-gray-800">{n}</h1>
          <p className="text-xs text-gray-400 font-body">
            Delivery{' '}
            <span className="text-gray-700">{deliveryLabel(order.delivery_date, order.delivery_time)}</span>
            {' · '}taken {order.created_via === 'pos' ? 'at the register' : 'in the console'}{' '}
            {formatDateTime(order.created_at)}
            {order.delivered_at && <> · delivered {formatDateTime(order.delivered_at)}</>}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          aria-label="Refresh"
          title="Refresh"
          className="inline-flex items-center justify-center min-h-11 min-w-11 md:min-h-0 md:min-w-0 text-gray-400 hover:text-primary"
        >
          <span className="material-icons text-[20px]">refresh</span>
        </button>
        <CustomOrderStatusBadge status={order.status} />
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2 text-xs font-body text-gray-500">
        {order.kitchen_printed_at ? (
          <Badge variant="neutral">Docket printed {formatDateTime(order.kitchen_printed_at)}</Badge>
        ) : order.status !== 'cancelled' ? (
          <Badge variant="warning">Docket not printed yet</Badge>
        ) : null}
        {order.enquiry_id && <Badge variant="info">From a website enquiry</Badge>}
        <Link href={`/orders/${encodeURIComponent(n)}`} className="ml-auto underline underline-offset-2 hover:text-primary">
          Timeline, P&amp;L and stock used
        </Link>
      </div>

      {actionError && (
        <div role="alert" className="mb-4 border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 font-body">
          {actionError}
        </div>
      )}

      {anyAction && (
        <div className="mb-4 flex flex-wrap gap-2 border border-gray-200 bg-white p-3">
          {a.can_pack && (
            <Button onClick={pack} loading={busy === 'pack'} disabled={busy !== null}>
              <span className="material-icons text-[16px]">inventory</span>
              Mark packed
            </Button>
          )}
          {a.can_collect && (
            <Button variant="ghost" onClick={collected} loading={busy === 'collected'} disabled={busy !== null}>
              <span className="material-icons text-[16px]">storefront</span>
              Customer collected
            </Button>
          )}
          {a.can_cancel && (
            <Button variant="danger" className="sm:ml-auto" onClick={cancel} loading={busy === 'cancel'} disabled={busy !== null}>
              Cancel order
            </Button>
          )}
        </div>
      )}

      <OrderCard order={order} busy={busy} run={run} />
      <RecipeCard order={order} busy={busy} run={run} />
      <ContactCard order={order} busy={busy} run={run} />
      <DeliveryCard order={order} busy={busy} run={run} />
      <InvoiceCard order={order} busy={busy} run={run} />
    </Page>
  );
}

type Run = (key: string, fn: () => Promise<CustomOrder>, success?: string) => Promise<boolean>;
interface CardProps {
  order: CustomOrder;
  busy: string | null;
  run: Run;
}

// ─── Lines, totals, payment, date, notes ──────────────────────────────────────

function Row({ label, value, strong }: { label: string; value: React.ReactNode; strong?: boolean }) {
  return (
    <div className={cn('flex justify-between gap-4 text-sm font-body', strong ? 'text-gray-900 font-medium' : 'text-gray-600')}>
      <span>{label}</span>
      <span className="tabular-nums">{value}</span>
    </div>
  );
}

function OrderCard({ order, busy, run }: CardProps) {
  const [editing, setEditing] = useState(false);
  const [lines, setLines] = useState<LineDraft[]>([]);
  const [date, setDate] = useState('');
  const [time, setTime] = useState('');
  const [payType, setPayType] = useState<PaymentType>('');
  const [feeMode, setFeeMode] = useState<CardFeeMode>('');
  const [notes, setNotes] = useState('');
  const [error, setError] = useState('');

  function startEdit() {
    setLines(
      order.lines.map(l =>
        newLine({ title: l.title, quantity: String(l.quantity), unit_price: l.unit_price, notes: l.notes ?? '' }),
      ),
    );
    setDate(order.delivery_date ?? '');
    setTime(order.delivery_time ? order.delivery_time.slice(0, 5) : '');
    setPayType((order.payment_type ?? '') as PaymentType);
    setFeeMode((order.card_fee_mode ?? '') as CardFeeMode);
    setNotes(order.notes ?? '');
    setError('');
    setEditing(true);
  }

  async function save() {
    const problem =
      linesError(lines) ?? (!date ? 'Choose the delivery date.' : null) ?? paymentError(payType, feeMode);
    if (problem) {
      setError(problem);
      return;
    }
    setError('');
    const ok = await run(
      'lines',
      () =>
        customOrdersApi.update(order.order_number, {
          lines: toLinesIn(lines),
          delivery_date: date,
          delivery_time: time || null,
          ...toPaymentFields(payType, feeMode),
          notes: notes.trim() || null,
        }),
      'Order updated.',
    );
    if (ok) setEditing(false);
  }

  if (editing) {
    return (
      <Section title="Order" hint="Saving re-prices the order on the server.">
        <div className="space-y-5">
          <LinesEditor value={lines} onChange={setLines} />
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor="edit-date" className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1">
                Delivery date <span className="text-red-500">*</span>
              </label>
              <input id="edit-date" type="date" value={date} onChange={e => setDate(e.target.value)} className={DATE_INPUT} />
            </div>
            <div>
              <label htmlFor="edit-time" className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1">
                Time (optional)
              </label>
              <input id="edit-time" type="time" value={time} onChange={e => setTime(e.target.value)} className={DATE_INPUT} />
            </div>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-gray-600 mb-2">Payment</p>
            <PaymentFields
              name="edit-payment"
              type={payType}
              feeMode={feeMode}
              onChange={(t, f) => {
                setPayType(t);
                setFeeMode(f);
              }}
            />
          </div>
          <Textarea id="edit-notes" label="Notes" rows={3} maxLength={2000} value={notes} onChange={e => setNotes(e.target.value)} />
          {error && <p className="text-sm text-red-600 font-body">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setEditing(false)} disabled={busy === 'lines'}>
              Discard
            </Button>
            <Button onClick={save} loading={busy === 'lines'} disabled={busy !== null}>
              Save changes
            </Button>
          </div>
        </div>
      </Section>
    );
  }

  return (
    <Section
      title="Order"
      action={
        order.actions.can_edit_lines ? (
          <Button variant="ghost" size="sm" onClick={startEdit} disabled={busy !== null}>
            <span className="material-icons text-[14px]">edit</span>
            Edit
          </Button>
        ) : undefined
      }
    >
      <div className="divide-y divide-gray-100">
        {order.lines.map(l => (
          <div key={l.id} className="flex items-start justify-between gap-4 py-2">
            <div className="min-w-0">
              <p className="text-sm font-body text-gray-800">{l.title}</p>
              <p className="text-xs font-body text-gray-400">
                {l.quantity} × {formatCurrency(l.unit_price)}
              </p>
              {l.notes && <p className="mt-0.5 text-xs font-body text-gray-500 whitespace-pre-wrap">{l.notes}</p>}
            </div>
            <span className="text-sm font-body tabular-nums text-gray-800">{formatCurrency(l.total)}</span>
          </div>
        ))}
      </div>

      <div className="mt-3 space-y-1 border-t border-gray-200 pt-3">
        <Row label="Subtotal" value={formatCurrency(order.subtotal)} />
        {order.charges.map(c => (
          <Row key={c.name} label={c.name} value={formatCurrency(c.amount)} />
        ))}
        <Row label="Total excl. VAT" value={formatCurrency(order.total_excl_vat)} />
        <Row label="VAT" value={formatCurrency(order.vat_amount)} />
        <Row label="Total" value={formatCurrency(order.total)} strong />
      </div>

      <div className="mt-4 grid gap-3 border-t border-gray-100 pt-3 text-sm font-body sm:grid-cols-2">
        <div>
          <p className="text-[11px] uppercase tracking-widest text-gray-400">Payment</p>
          <p className="text-gray-800">
            {order.payment_type ? PAYMENT_TYPE_LABEL[order.payment_type] ?? order.payment_type : 'Not set'}
          </p>
          {order.card_fee_mode && (
            <p className="text-xs text-gray-500">{CARD_FEE_MODE_LABEL[order.card_fee_mode] ?? order.card_fee_mode}</p>
          )}
          {order.payment_fee != null && Number(order.payment_fee) > 0 && (
            <p className="text-xs text-gray-400">
              Card processing cost to the shop: {formatCurrency(order.payment_fee)}
            </p>
          )}
        </div>
        <div>
          <p className="text-[11px] uppercase tracking-widest text-gray-400">Delivery</p>
          <p className="text-gray-800">{deliveryLabel(order.delivery_date, order.delivery_time)}</p>
        </div>
      </div>

      {order.notes && (
        <div className="mt-3 border-t border-gray-100 pt-3">
          <p className="text-[11px] font-body uppercase tracking-widest text-gray-400">Notes</p>
          <p className="text-sm font-body text-gray-700 whitespace-pre-wrap">{order.notes}</p>
        </div>
      )}
    </Section>
  );
}

// ─── Recipe ────────────────────────────────────────────────────────────────────

function RecipeCard({ order, busy, run }: CardProps) {
  const editable = order.actions.can_edit_recipe;
  const [items, setItems] = useState<CustomCakeItem[] | null>(null);
  const [itemsError, setItemsError] = useState('');
  const [draft, setDraft] = useState<RecipeDraft[]>([]);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState('');

  // Reseed from the order whenever the order's recipe changes (a save, a reload).
  const recipeKey = order.recipe.map(r => `${r.item_id}:${r.quantity}`).join('|');
  useEffect(() => {
    setDraft(order.recipe.map(r => ({ item_id: r.item_id, quantity: r.quantity })));
    setDirty(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recipeKey]);

  useEffect(() => {
    if (!editable || items) return;
    customOrdersApi
      .items()
      .then(setItems)
      .catch(err => setItemsError(errorText(err, 'Could not load the recipe items.')));
  }, [editable, items]);

  async function save() {
    const problem = recipeError(draft, items ?? []);
    if (problem) {
      setError(problem);
      return;
    }
    setError('');
    await run('recipe', () => customOrdersApi.updateRecipe(order.order_number, toRecipeIn(draft)), 'Recipe saved.');
  }

  if (!editable) {
    return (
      <Section title="Recipe">
        <RecipeReadOnly
          lines={order.recipe}
          note="Locked. The recipe is consumed from the kitchen's stock when the order is packed."
        />
      </Section>
    );
  }

  return (
    <Section
      title="Recipe"
      hint="Taken from the kitchen's stock when the order is packed. Quantities are in each item's unit."
    >
      {itemsError ? (
        <p className="text-xs font-body text-red-600">{itemsError}</p>
      ) : !items ? (
        <Spinner className="w-4 h-4" />
      ) : (
        <RecipeEditor
          items={items}
          value={draft}
          onChange={next => {
            setDraft(next);
            setDirty(true);
          }}
        />
      )}
      {error && <p className="mt-2 text-sm text-red-600 font-body">{error}</p>}
      {dirty && (
        <div className="mt-3 flex justify-end gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              setDraft(order.recipe.map(r => ({ item_id: r.item_id, quantity: r.quantity })));
              setDirty(false);
              setError('');
            }}
          >
            Discard
          </Button>
          <Button size="sm" onClick={save} loading={busy === 'recipe'} disabled={busy !== null}>
            Save recipe
          </Button>
        </div>
      )}
    </Section>
  );
}

// ─── Customer & address ────────────────────────────────────────────────────────

function ContactCard({ order, busy, run }: CardProps) {
  const [editing, setEditing] = useState(false);
  const [contact, setContact] = useState<ContactDraft>({ name: '', email: '', phone: '' });
  const [address, setAddress] = useState<AddressDraft>(addressDraftFrom(null));

  function startEdit() {
    setContact({
      name: order.customer_name ?? '',
      email: order.customer_email ?? '',
      phone: order.customer_phone ?? '',
    });
    setAddress(addressDraftFrom(order.address));
    setEditing(true);
  }

  async function save() {
    const ok = await run(
      'contact',
      () =>
        customOrdersApi.updateContact(order.order_number, {
          customer: toCustomerIn(contact),
          address: toAddressIn(address),
        }),
      'Customer and address saved.',
    );
    if (ok) setEditing(false);
  }

  if (editing) {
    return (
      <Section title="Customer & address">
        <div className="space-y-5">
          <ContactFields idPrefix="edit-contact" value={contact} onChange={setContact} />
          <AddressPicker idPrefix="edit-address" value={address} onChange={setAddress} />
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setEditing(false)} disabled={busy === 'contact'}>
              Discard
            </Button>
            <Button onClick={save} loading={busy === 'contact'} disabled={busy !== null}>
              Save
            </Button>
          </div>
        </div>
      </Section>
    );
  }

  const addr = order.address;
  const pin = addr && addr.latitude != null && addr.longitude != null;
  return (
    <Section
      title="Customer & address"
      action={
        order.actions.can_edit_contact ? (
          <Button variant="ghost" size="sm" onClick={startEdit} disabled={busy !== null}>
            <span className="material-icons text-[14px]">edit</span>
            Edit
          </Button>
        ) : undefined
      }
    >
      <div className="grid gap-3 text-sm font-body sm:grid-cols-2">
        <div className="space-y-0.5">
          <p className="text-gray-800">{order.customer_name || <span className="text-gray-400">No name</span>}</p>
          <p className="text-gray-600">{order.customer_email || <span className="text-gray-400">No email</span>}</p>
          <p className="text-gray-600" dir="ltr">
            {order.customer_phone ? (
              <a href={`tel:${order.customer_phone}`} className="text-primary">
                {order.customer_phone}
              </a>
            ) : (
              <span className="text-gray-400">No phone</span>
            )}
          </p>
        </div>
        <div className="space-y-0.5">
          {addr && (addr.address_line_1 || addr.unit_number || pin) ? (
            <>
              {addr.unit_number && <p className="text-gray-800">{addr.unit_number}</p>}
              {addr.address_line_1 && <p className="text-gray-600">{addr.address_line_1}</p>}
              {pin ? (
                <a
                  href={`https://www.google.com/maps/search/?api=1&query=${addr.latitude},${addr.longitude}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                >
                  <span className="material-icons text-[14px]">place</span>
                  Open the pin in Google Maps
                </a>
              ) : (
                <p className="text-xs text-gray-400">No pin</p>
              )}
            </>
          ) : (
            <p className="text-gray-400">No address — collection or a third-party courier.</p>
          )}
        </div>
      </div>
      {!order.actions.can_edit_contact && (
        <p className="mt-3 text-xs font-body text-gray-400">
          Locked: contact and address cannot change once a courier is booked or the order is finished.
        </p>
      )}
    </Section>
  );
}

// ─── Delivery ──────────────────────────────────────────────────────────────────

type Mode = 'slider_car' | 'lalamove' | 'third_party';

function DeliveryCard({ order, busy, run }: CardProps) {
  const a = order.actions;
  const confirm = useConfirm();
  const [quotes, setQuotes] = useState<CustomOrderDeliveryQuotes | null>(null);
  const [quoting, setQuoting] = useState(false);
  const [quoteError, setQuoteError] = useState('');
  const [mode, setMode] = useState<Mode>('third_party');
  const [fee, setFee] = useState('');
  const [error, setError] = useState('');

  // The chooser belongs to a packed (or bounced) order; a finished one shows
  // only what carried it.
  const choosing = order.status === 'packed' || order.status === 'undelivered';
  if (!choosing && !order.delivery) return null;

  async function getFares() {
    setQuoting(true);
    setQuoteError('');
    try {
      const q = await customOrdersApi.deliveryQuotes(order.order_number);
      setQuotes(q);
    } catch (err) {
      setQuoteError(errorText(err, 'Could not get live fares.'));
    } finally {
      setQuoting(false);
    }
  }

  const quoteFor = (p: 'slider_car' | 'lalamove') => quotes?.quotes.find(q => q.provider === p) ?? null;

  async function submit() {
    setError('');
    if (mode === 'third_party') {
      if (!/^\d{1,5}(\.\d{1,2})?$/.test(fee.trim())) {
        setError('Enter what the courier cost, in AED (0 is fine).');
        return;
      }
      const ok = await confirm({
        title: 'Record a third-party delivery?',
        message: `The order is marked delivered now, with a courier cost of AED ${fee.trim()}.`,
        confirmLabel: 'Record and mark delivered',
      });
      if (!ok) return;
      await run(
        'delivery',
        () => customOrdersApi.chooseDelivery(order.order_number, { mode, courier_fee: fee.trim() }),
        'Delivered by a third-party courier.',
      );
      return;
    }
    const q = quoteFor(mode);
    if (!q?.available) {
      setError('Get live fares and pick a courier that can take it.');
      return;
    }
    const ok = await confirm({
      title: `Book ${providerLabel(mode)}?`,
      message: `A ${providerLabel(mode)} courier is booked now${q.fare ? ` at ${formatCurrency(q.fare)}` : ''}. The booking is paid for once made.`,
      confirmLabel: 'Book courier',
    });
    if (!ok) return;
    await run(
      'delivery',
      () =>
        customOrdersApi.chooseDelivery(order.order_number, {
          mode,
          quotation_id: mode === 'lalamove' ? q.quotation_id : null,
        }),
      `${providerLabel(mode)} booked.`,
    );
    // A spent or expired quote is useless either way: price again next time.
    setQuotes(null);
  }

  const d = order.delivery;

  return (
    <Section title="Delivery">
      {d && (
        <div className="mb-4 space-y-1 text-sm font-body">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-gray-800">{providerLabel(d.provider)}</span>
            {d.courier_status && <Badge variant="info">{d.courier_status.replaceAll('_', ' ')}</Badge>}
          </div>
          {(d.driver_name || d.driver_phone) && (
            <p className="text-gray-600">
              Driver: {d.driver_name ?? '—'}
              {d.driver_phone && (
                <>
                  {' · '}
                  <a href={`tel:${d.driver_phone}`} className="text-primary" dir="ltr">
                    {d.driver_phone}
                  </a>
                </>
              )}
            </p>
          )}
          {d.cost != null && <p className="text-gray-600">Courier cost: {formatCurrency(d.cost)}</p>}
          {d.share_link && (
            <a href={d.share_link} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">
              <span className="material-icons text-[14px]">open_in_new</span>
              Live tracking link
            </a>
          )}
          {d.last_error && <p className="text-xs text-red-600">Last courier error: {d.last_error}</p>}
        </div>
      )}

      {choosing && !a.can_choose_delivery && (
        <p className="text-sm font-body text-gray-500">
          {a.delivery_unavailable_reason ?? 'A courier cannot be chosen for this order right now.'}
          {a.can_collect && ' It can still be marked as collected by the customer.'}
        </p>
      )}

      {choosing && a.can_choose_delivery && (
        <div className="space-y-3">
          {order.status === 'undelivered' && (
            <p className="text-sm font-body text-red-700">
              The last courier could not hand it over. Choose how it goes out again.
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="ghost" size="sm" onClick={getFares} loading={quoting} disabled={quoting || busy !== null}>
              <span className="material-icons text-[14px]">local_shipping</span>
              {quotes ? 'Refresh live fares' : 'Get live fares'}
            </Button>
            {quoteError && <span className="text-xs font-body text-red-600">{quoteError}</span>}
            {quotes?.unavailable_reason && (
              <span className="text-xs font-body text-gray-500">{quotes.unavailable_reason}</span>
            )}
          </div>

          <div className="space-y-2" role="radiogroup" aria-label="Courier">
            {(['slider_car', 'lalamove'] as const).map(p => {
              const q = quoteFor(p);
              const enabled = Boolean(q?.available);
              return (
                <label
                  key={p}
                  className={cn(
                    'flex items-start gap-3 border px-3 py-2',
                    mode === p ? 'border-primary bg-primary/5' : 'border-gray-200',
                    enabled ? 'cursor-pointer' : 'cursor-not-allowed opacity-60',
                  )}
                >
                  <input
                    type="radio"
                    name="courier"
                    className="mt-1 accent-primary"
                    checked={mode === p}
                    disabled={!enabled}
                    onChange={() => setMode(p)}
                  />
                  <span className="flex-1">
                    <span className="flex items-baseline justify-between gap-3">
                      <span className="text-sm font-body text-gray-800">{providerLabel(p)}</span>
                      {q?.available && q.fare != null && (
                        <span className="text-sm font-body tabular-nums text-gray-800">{formatCurrency(q.fare)}</span>
                      )}
                    </span>
                    <span className="block text-xs font-body text-gray-400">
                      {!quotes
                        ? 'Get live fares to price it.'
                        : !q
                          ? 'No fare returned.'
                          : q.available
                            ? q.expires_at
                              ? `Quote valid until ${formatDateTime(q.expires_at)}`
                              : 'Live fare, VAT inclusive'
                            : q.reason ?? 'Cannot take this order.'}
                    </span>
                  </span>
                </label>
              );
            })}
            <label
              className={cn(
                'flex cursor-pointer items-start gap-3 border px-3 py-2',
                mode === 'third_party' ? 'border-primary bg-primary/5' : 'border-gray-200',
              )}
            >
              <input
                type="radio"
                name="courier"
                className="mt-1 accent-primary"
                checked={mode === 'third_party'}
                onChange={() => setMode('third_party')}
              />
              <span className="flex-1">
                <span className="block text-sm font-body text-gray-800">Third-party courier</span>
                <span className="block text-xs font-body text-gray-400">
                  A courier we did not book through the console. Recording it marks the order delivered.
                </span>
                {mode === 'third_party' && (
                  <span className="mt-2 block max-w-[12rem]">
                    <Input
                      id="third-party-fee"
                      label="Courier fee (AED)"
                      inputMode="decimal"
                      placeholder="0.00"
                      helper="VAT inclusive; 0 if free"
                      value={fee}
                      onChange={e => setFee(e.target.value.replace(/[^\d.]/g, ''))}
                    />
                  </span>
                )}
              </span>
            </label>
          </div>

          {error && <p className="text-sm text-red-600 font-body">{error}</p>}
          <div className="flex justify-end">
            <Button onClick={submit} loading={busy === 'delivery'} disabled={busy !== null}>
              {mode === 'third_party' ? 'Record third-party delivery' : `Book ${providerLabel(mode)}`}
            </Button>
          </div>
        </div>
      )}
    </Section>
  );
}

// ─── Invoice ───────────────────────────────────────────────────────────────────

function InvoiceCard({ order, busy, run }: CardProps) {
  const confirm = useConfirm();
  const toast = useToast();
  const [opening, setOpening] = useState(false);
  const reason = order.actions.invoice_unavailable_reason;

  async function view() {
    // Opened before the fetch so the browser treats it as the click's own
    // window rather than a popup; pointed at the PDF once it arrives.
    const win = window.open('', '_blank');
    setOpening(true);
    try {
      const blob = await customOrdersApi.invoicePdf(order.order_number);
      const url = URL.createObjectURL(blob);
      if (win) {
        win.location.href = url;
      } else {
        const link = document.createElement('a');
        link.href = url;
        link.download = `${order.order_number}.pdf`;
        link.click();
      }
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (err) {
      win?.close();
      toast.error(errorText(err, 'Could not open the invoice.'));
    } finally {
      setOpening(false);
    }
  }

  async function send() {
    const ok = await confirm({
      title: 'Email the invoice?',
      message: `The tax invoice for ${order.order_number} is emailed to ${order.customer_email ?? 'the customer'}. The owners on the legal entity's CC list are copied.`,
      confirmLabel: 'Send invoice',
    });
    if (ok) await run('invoice', () => customOrdersApi.sendInvoice(order.order_number), 'Invoice sent.');
  }

  return (
    <Section title="Invoice" hint={`Tax invoice ${order.order_number}.`}>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="ghost" onClick={view} loading={opening} disabled={Boolean(reason) || opening}>
          <span className="material-icons text-[16px]">picture_as_pdf</span>
          View
        </Button>
        <Button onClick={send} loading={busy === 'invoice'} disabled={Boolean(reason) || busy !== null}>
          <span className="material-icons text-[16px]">send</span>
          Send to customer
        </Button>
      </div>
      {reason && <p className="mt-2 text-xs font-body text-gray-500">{reason}</p>}
    </Section>
  );
}
