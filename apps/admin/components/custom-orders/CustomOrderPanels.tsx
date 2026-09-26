'use client';

/**
 * The custom-order parts of the one order page (`/orders/[orderNumber]`): the
 * buttons that move a custom order along, what makes it custom (the promise,
 * payment terms, charges, notes), its recipe, the courier chooser and the
 * invoice. Everything else — timeline, items, totals, customer, branch, P&L,
 * stock used — is the order page's own, shared with every other channel.
 *
 * Every button's availability comes from the order's `actions`, which the API
 * computes once for the console and both registers — nothing here decides for
 * itself whether an order may be packed, edited or sent. Where an action is
 * closed and the API says why (`delivery_unavailable_reason`,
 * `invoice_unavailable_reason`), the reason is shown next to it.
 *
 * Money is rendered exactly as returned (rule 10).
 */

import { useState } from 'react';
import { customOrdersApi, type CustomOrder, type CustomOrderDeliveryQuotes } from '@/lib/api';
import { Badge, Button, Input } from '@/components/ui';
import { useConfirm, useToast } from '@/components/ui/feedback';
import { cn, formatCurrency, formatDateTime } from '@/lib/utils';
import { Section } from './Section';
import { CARD_FEE_MODE_LABEL, PAYMENT_TYPE_LABEL, deliveryLabel, providerLabel } from './display';
import { RecipeReadOnly } from './RecipeEditor';
import { EditContactModal, EditOrderModal, EditRecipeModal } from './EditModals';
import { errorText, type RunCustomAction } from './useCustomOrder';

interface CardProps {
  order: CustomOrder;
  busy: string | null;
  run: RunCustomAction;
}

// ─── Actions: pack, collected, cancel ─────────────────────────────────────────

export function CustomOrderActionBar({ order, busy, run, error }: CardProps & { error?: string }) {
  const confirm = useConfirm();
  const a = order.actions;
  const n = order.order_number;

  async function pack() {
    const ok = await confirm({
      title: 'Mark packed?',
      message: 'The recipe is taken from the kitchen’s stock now and cannot be edited afterwards.',
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
    <>
      {error && (
        <div role="alert" className="mb-4 border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 font-body">
          {error}
        </div>
      )}
      {anyAction && (
        <div className="mb-4 flex flex-wrap gap-2">
          {a.can_pack && (
            <Button size="sm" onClick={pack} loading={busy === 'pack'} disabled={busy !== null}>
              <span className="material-icons text-[14px]">inventory</span>
              Mark Packed
            </Button>
          )}
          {a.can_collect && (
            <Button size="sm" variant="ghost" onClick={collected} loading={busy === 'collected'} disabled={busy !== null}>
              <span className="material-icons text-[14px]">storefront</span>
              Customer Collected
            </Button>
          )}
          {a.can_cancel && (
            <Button size="sm" variant="danger" onClick={cancel} loading={busy === 'cancel'} disabled={busy !== null}>
              <span className="material-icons text-[14px]">cancel</span>
              Cancel Order
            </Button>
          )}
        </div>
      )}
    </>
  );
}

// ─── What makes it custom: promise, payment, charges, notes ────────────────────

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-[11px] uppercase tracking-widest text-gray-400">{label}</p>
      {children}
    </div>
  );
}

export function CustomOrderDetailsCard({ order, busy, run }: CardProps) {
  const [editing, setEditing] = useState<'order' | 'contact' | null>(null);
  const a = order.actions;
  const lineNotes = order.lines.filter(l => l.notes);

  return (
    <Section
      title="Custom order"
      action={
        a.can_edit_lines || a.can_edit_contact ? (
          <div className="flex flex-wrap justify-end gap-1">
            {a.can_edit_lines && (
              <Button variant="ghost" size="sm" onClick={() => setEditing('order')} disabled={busy !== null}>
                <span className="material-icons text-[14px]">edit</span>
                Edit order
              </Button>
            )}
            {a.can_edit_contact && (
              <Button variant="ghost" size="sm" onClick={() => setEditing('contact')} disabled={busy !== null}>
                <span className="material-icons text-[14px]">person</span>
                Edit customer
              </Button>
            )}
          </div>
        ) : undefined
      }
    >
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs font-body">
        {order.kitchen_printed_at ? (
          <Badge variant="neutral">Docket printed {formatDateTime(order.kitchen_printed_at)}</Badge>
        ) : order.status !== 'cancelled' ? (
          <Badge variant="warning">Docket not printed yet</Badge>
        ) : null}
        {order.enquiry_id && <Badge variant="info">From a website enquiry</Badge>}
        <span className="text-gray-400">
          Taken {order.created_via === 'pos' ? 'at the register' : 'in the console'}
        </span>
      </div>

      <div className="grid gap-3 text-sm font-body sm:grid-cols-2">
        <Field label="Delivery">
          <p className="text-gray-800">{deliveryLabel(order.delivery_date, order.delivery_time)}</p>
          {order.delivered_at && (
            <p className="text-xs text-gray-500">Delivered {formatDateTime(order.delivered_at)}</p>
          )}
        </Field>
        <Field label="Payment">
          <p className="text-gray-800">
            {order.payment_type ? PAYMENT_TYPE_LABEL[order.payment_type] ?? order.payment_type : 'Not set'}
          </p>
          {order.card_fee_mode && (
            <p className="text-xs text-gray-500">{CARD_FEE_MODE_LABEL[order.card_fee_mode] ?? order.card_fee_mode}</p>
          )}
          {order.payment_fee != null && Number(order.payment_fee) > 0 && (
            <p className="text-xs text-gray-400">Card processing cost to the shop: {formatCurrency(order.payment_fee)}</p>
          )}
        </Field>
      </div>

      {/* The order's own breakdown: charges (a card fee on its own line) sit
          between the lines and the total, which the items table does not show. */}
      <div className="mt-3 space-y-1 border-t border-gray-100 pt-3 text-xs font-body text-gray-600">
        <div className="flex justify-between gap-4">
          <span>Lines</span>
          <span className="tabular-nums">{formatCurrency(order.subtotal)}</span>
        </div>
        {order.charges.map(c => (
          <div key={c.name} className="flex justify-between gap-4">
            <span>{c.name}</span>
            <span className="tabular-nums">{formatCurrency(c.amount)}</span>
          </div>
        ))}
        <div className="flex justify-between gap-4">
          <span>Total excl. VAT</span>
          <span className="tabular-nums">{formatCurrency(order.total_excl_vat)}</span>
        </div>
        <div className="flex justify-between gap-4">
          <span>VAT</span>
          <span className="tabular-nums">{formatCurrency(order.vat_amount)}</span>
        </div>
        <div className="flex justify-between gap-4 text-sm font-medium text-gray-900">
          <span>Total</span>
          <span className="tabular-nums">{formatCurrency(order.total)}</span>
        </div>
      </div>

      {lineNotes.length > 0 && (
        <div className="mt-3 border-t border-gray-100 pt-3">
          <p className="text-[11px] font-body uppercase tracking-widest text-gray-400">For the kitchen</p>
          <ul className="mt-1 space-y-1">
            {lineNotes.map(l => (
              <li key={l.id} className="text-sm font-body text-gray-700">
                <span className="font-medium text-gray-800">{l.title}:</span>{' '}
                <span className="whitespace-pre-wrap">{l.notes}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {order.notes && (
        <div className="mt-3 border-t border-gray-100 pt-3">
          <p className="text-[11px] font-body uppercase tracking-widest text-gray-400">Notes</p>
          <p className="text-sm font-body text-gray-700 whitespace-pre-wrap">{order.notes}</p>
        </div>
      )}

      {!a.can_edit_contact && order.status !== 'cancelled' && (
        <p className="mt-3 text-xs font-body text-gray-400">
          Contact and address are locked once a courier is booked or the order is finished.
        </p>
      )}

      {editing === 'order' && (
        <EditOrderModal order={order} busy={busy} run={run} onClose={() => setEditing(null)} />
      )}
      {editing === 'contact' && (
        <EditContactModal order={order} busy={busy} run={run} onClose={() => setEditing(null)} />
      )}
    </Section>
  );
}

// ─── Recipe ────────────────────────────────────────────────────────────────────

export function CustomRecipeCard({ order, busy, run }: CardProps) {
  const [editing, setEditing] = useState(false);
  const editable = order.actions.can_edit_recipe;

  return (
    <Section
      title="Recipe"
      hint={
        editable
          ? "Taken from the kitchen's stock when the order is packed. Quantities are in each item's unit."
          : undefined
      }
      action={
        editable ? (
          <Button variant="ghost" size="sm" onClick={() => setEditing(true)} disabled={busy !== null}>
            <span className="material-icons text-[14px]">edit</span>
            Edit recipe
          </Button>
        ) : undefined
      }
    >
      <RecipeReadOnly
        lines={order.recipe}
        note={
          editable
            ? undefined
            : "Locked. The recipe is consumed from the kitchen's stock when the order is packed."
        }
      />
      {editing && <EditRecipeModal order={order} busy={busy} run={run} onClose={() => setEditing(false)} />}
    </Section>
  );
}

// ─── Delivery: live fares, courier choice ─────────────────────────────────────

type Mode = 'slider_car' | 'lalamove' | 'third_party';

/**
 * How a packed (or bounced) custom order goes out: a live-priced Slider car or
 * Lalamove booking, or a third-party courier recorded with what it cost.
 *
 * `showBooked` renders the courier that carried it — off when the order page
 * already shows the fulfilment record in its own delivery panel.
 */
export function CustomDeliveryCard({ order, busy, run, showBooked }: CardProps & { showBooked: boolean }) {
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
  const d = showBooked ? order.delivery : null;
  if (!choosing && !d) return null;

  async function getFares() {
    setQuoting(true);
    setQuoteError('');
    try {
      setQuotes(await customOrdersApi.deliveryQuotes(order.order_number));
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

  return (
    <Section title="Custom delivery">
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
            <a
              href={d.share_link}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-primary hover:underline"
            >
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

export function CustomInvoiceCard({ order, busy, run }: CardProps) {
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
        <Button variant="ghost" size="sm" onClick={view} loading={opening} disabled={Boolean(reason) || opening}>
          <span className="material-icons text-[16px]">picture_as_pdf</span>
          View PDF
        </Button>
        <Button size="sm" onClick={send} loading={busy === 'invoice'} disabled={Boolean(reason) || busy !== null}>
          <span className="material-icons text-[16px]">send</span>
          Send to customer
        </Button>
      </div>
      {reason && <p className="mt-2 text-xs font-body text-gray-500">{reason}</p>}
    </Section>
  );
}
