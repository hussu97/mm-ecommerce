'use client';

/**
 * The three edits a custom order offers, each in its own pop-up on the order
 * page: the order itself (lines, date, payment, notes — `PUT …/{n}`), the recipe
 * (`PUT …/recipe`) and the customer + address (`PUT …/contact`).
 *
 * Each is seeded from the order when it opens and saves through the page's
 * `run`, so the API's answer (re-priced lines, a refusal) is what the page shows
 * next. A modal closes only on a save that went through.
 */

import { useEffect, useState } from 'react';
import { customOrdersApi, type CustomCakeItem, type CustomOrder } from '@/lib/api';
import { Button, Spinner, Textarea } from '@/components/ui';
import { Modal } from './Modal';
import { LinesEditor, linesError, newLine, toLinesIn, type LineDraft } from './LinesEditor';
import { RecipeEditor, recipeError, toRecipeIn, type RecipeDraft } from './RecipeEditor';
import { ContactFields, toCustomerIn, type ContactDraft } from './ContactFields';
import { AddressPicker, addressDraftFrom, toAddressIn, type AddressDraft } from './AddressPicker';
import { PaymentFields, paymentError, toPaymentFields, type CardFeeMode, type PaymentType } from './PaymentFields';
import { errorText, type RunCustomAction } from './useCustomOrder';

const DATE_INPUT =
  'w-full px-3 py-2 min-h-[var(--tap-min)] md:min-h-0 text-sm font-body bg-white border border-gray-300 rounded-sm outline-none focus:border-primary focus:ring-1 focus:ring-primary/30';

interface EditProps {
  order: CustomOrder;
  busy: string | null;
  run: RunCustomAction;
  onClose: () => void;
}

function Footer({
  busyKey,
  busy,
  onClose,
  onSave,
  saveLabel,
  error,
}: {
  busyKey: string;
  busy: string | null;
  onClose: () => void;
  onSave: () => void;
  saveLabel: string;
  error: string;
}) {
  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      {error && (
        <p role="alert" className="mr-auto text-sm text-red-600 font-body">
          {error}
        </p>
      )}
      <Button variant="secondary" onClick={onClose} disabled={busy === busyKey}>
        Discard
      </Button>
      <Button onClick={onSave} loading={busy === busyKey} disabled={busy !== null}>
        {saveLabel}
      </Button>
    </div>
  );
}

// ─── Order: lines, date, payment, notes ────────────────────────────────────────

export function EditOrderModal({ order, busy, run, onClose }: EditProps) {
  const [lines, setLines] = useState<LineDraft[]>(() =>
    order.lines.map(l =>
      newLine({ title: l.title, quantity: String(l.quantity), unit_price: l.unit_price, notes: l.notes ?? '' }),
    ),
  );
  const [date, setDate] = useState(order.delivery_date ?? '');
  const [time, setTime] = useState(order.delivery_time ? order.delivery_time.slice(0, 5) : '');
  const [payType, setPayType] = useState<PaymentType>((order.payment_type ?? '') as PaymentType);
  const [feeMode, setFeeMode] = useState<CardFeeMode>((order.card_fee_mode ?? '') as CardFeeMode);
  const [notes, setNotes] = useState(order.notes ?? '');
  const [error, setError] = useState('');

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
    if (ok) onClose();
  }

  return (
    <Modal
      title={`Edit ${order.order_number}`}
      hint="Saving re-prices the order on the server."
      onClose={onClose}
      busy={busy === 'lines'}
      wide
      footer={
        <Footer busyKey="lines" busy={busy} onClose={onClose} onSave={save} saveLabel="Save changes" error={error} />
      }
    >
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
      </div>
    </Modal>
  );
}

// ─── Recipe ────────────────────────────────────────────────────────────────────

export function EditRecipeModal({ order, busy, run, onClose }: EditProps) {
  const [items, setItems] = useState<CustomCakeItem[] | null>(null);
  const [itemsError, setItemsError] = useState('');
  const [draft, setDraft] = useState<RecipeDraft[]>(() =>
    order.recipe.map(r => ({ item_id: r.item_id, quantity: r.quantity })),
  );
  const [error, setError] = useState('');

  useEffect(() => {
    let live = true;
    customOrdersApi
      .items()
      .then(found => live && setItems(found))
      .catch(err => live && setItemsError(errorText(err, 'Could not load the recipe items.')));
    return () => {
      live = false;
    };
  }, []);

  async function save() {
    const problem = recipeError(draft, items ?? []);
    if (problem) {
      setError(problem);
      return;
    }
    setError('');
    const ok = await run(
      'recipe',
      () => customOrdersApi.updateRecipe(order.order_number, toRecipeIn(draft)),
      'Recipe saved.',
    );
    if (ok) onClose();
  }

  return (
    <Modal
      title="Edit recipe"
      hint="Taken from the kitchen's stock when the order is packed. Quantities are in each item's unit."
      onClose={onClose}
      busy={busy === 'recipe'}
      wide
      footer={
        <Footer busyKey="recipe" busy={busy} onClose={onClose} onSave={save} saveLabel="Save recipe" error={error} />
      }
    >
      {itemsError ? (
        <p className="text-sm font-body text-red-600">{itemsError}</p>
      ) : !items ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : (
        // Room below for the item search's drop-down, which opens under the
        // last row.
        <div className="min-h-72">
          <RecipeEditor items={items} value={draft} onChange={setDraft} />
        </div>
      )}
    </Modal>
  );
}

// ─── Customer & address ────────────────────────────────────────────────────────

export function EditContactModal({ order, busy, run, onClose }: EditProps) {
  const [contact, setContact] = useState<ContactDraft>({
    name: order.customer_name ?? '',
    email: order.customer_email ?? '',
    phone: order.customer_phone ?? '',
  });
  const [address, setAddress] = useState<AddressDraft>(() => addressDraftFrom(order.address));

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
    if (ok) onClose();
  }

  return (
    <Modal
      title="Edit customer & address"
      onClose={onClose}
      busy={busy === 'contact'}
      wide
      footer={<Footer busyKey="contact" busy={busy} onClose={onClose} onSave={save} saveLabel="Save" error="" />}
    >
      <div className="space-y-5">
        <ContactFields idPrefix="edit-contact" value={contact} onChange={setContact} />
        <AddressPicker idPrefix="edit-address" value={address} onChange={setAddress} />
      </div>
    </Modal>
  );
}
