'use client';

/**
 * Take a custom order.
 *
 * Only the lines and the delivery date are required — an order agreed in a DM
 * may have nothing else yet, and each missing piece just keeps a later step
 * closed (no courier without a pin, name and phone; no invoice without a name
 * and email), which the order page then says in so many words.
 *
 * `?enquiry=<id>` converts a website enquiry: its name, phone, request and
 * wanted-by date prefill the form, and the order records which enquiry it came
 * from.
 *
 * The API prices the order on save — nothing here adds anything up (rule 10).
 * A `client_request_id` minted once per visit makes a double-click or a retried
 * request return the order already made instead of making a second.
 */

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import {
  ApiError,
  customOrderEnquiriesApi,
  customOrdersApi,
  type CustomCakeItem,
  type CustomOrdersStatus,
} from '@/lib/api';
import type { CustomOrderEnquiry } from '@/lib/types';
import { Button, Textarea } from '@/components/ui';
import { useToast } from '@/components/ui/feedback';
import { Page } from '@/components/ui/Page';
import { LinesEditor, linesError, newLine, toLinesIn, type LineDraft } from '@/components/custom-orders/LinesEditor';
import { RecipeEditor, recipeError, toRecipeIn, type RecipeDraft } from '@/components/custom-orders/RecipeEditor';
import { ContactFields, EMPTY_CONTACT, toCustomerIn, type ContactDraft } from '@/components/custom-orders/ContactFields';
import { AddressPicker, EMPTY_ADDRESS, toAddressIn, type AddressDraft } from '@/components/custom-orders/AddressPicker';
import {
  PaymentFields,
  paymentError,
  toPaymentFields,
  type CardFeeMode,
  type PaymentType,
} from '@/components/custom-orders/PaymentFields';
import { SetupNotice } from '@/components/custom-orders/SetupNotice';
import { Section } from '@/components/custom-orders/Section';
import { formatDate } from '@/lib/utils';

const DATE_INPUT =
  'w-full px-3 py-2 min-h-[var(--tap-min)] md:min-h-0 text-sm font-body bg-white border border-gray-300 rounded-sm outline-none focus:border-primary focus:ring-1 focus:ring-primary/30';

function newRequestId(): string {
  // `crypto.randomUUID` is on every browser the console supports (secure context).
  return crypto.randomUUID();
}

export default function NewCustomOrderPage() {
  const router = useRouter();
  const params = useSearchParams();
  const toast = useToast();
  const enquiryId = params.get('enquiry');

  const [status, setStatus] = useState<CustomOrdersStatus | null>(null);
  const [items, setItems] = useState<CustomCakeItem[]>([]);
  const [itemsError, setItemsError] = useState('');

  const [lines, setLines] = useState<LineDraft[]>(() => [newLine()]);
  const [date, setDate] = useState('');
  const [time, setTime] = useState('');
  const [contact, setContact] = useState<ContactDraft>(EMPTY_CONTACT);
  const [address, setAddress] = useState<AddressDraft>(EMPTY_ADDRESS);
  const [payType, setPayType] = useState<PaymentType>('');
  const [feeMode, setFeeMode] = useState<CardFeeMode>('');
  const [recipe, setRecipe] = useState<RecipeDraft[]>([]);
  const [notes, setNotes] = useState('');

  const [enquiry, setEnquiry] = useState<CustomOrderEnquiry | null>(null);
  const [enquiryMissing, setEnquiryMissing] = useState(false);

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const requestId = useRef<string | null>(null);

  useEffect(() => {
    customOrdersApi.status().then(setStatus).catch(() => setStatus(null));
    customOrdersApi
      .items()
      .then(setItems)
      .catch(err => setItemsError((err as Error).message));
  }, []);

  // Converting an enquiry: there is no by-id read, and enquiries are a handful
  // of rows, so the list is read whole and the one wanted picked out.
  useEffect(() => {
    if (!enquiryId) return;
    customOrderEnquiriesApi
      .list({ page: 1, per_page: 2000 })
      .then(res => {
        const found = res.items.find(e => e.id === enquiryId) ?? null;
        if (!found) {
          setEnquiryMissing(true);
          return;
        }
        setEnquiry(found);
        setContact(c => ({ ...c, name: found.customer_name, phone: found.customer_phone }));
        const extra = found.approx_kg != null ? `\n\nApprox. size: ${found.approx_kg} kg` : '';
        setNotes(`${found.description}${extra}`);
        if (found.delivery_by) setDate(found.delivery_by.slice(0, 10));
      })
      .catch(() => setEnquiryMissing(true));
  }, [enquiryId]);

  async function submit() {
    const problem =
      linesError(lines) ??
      (!date ? 'Choose the delivery date.' : null) ??
      paymentError(payType, feeMode) ??
      recipeError(recipe, items);
    if (problem) {
      setError(problem);
      return;
    }
    setError('');
    setSaving(true);
    // Minted on the first attempt and kept for retries, so a retry after a
    // lost response returns the order the first attempt made.
    requestId.current ??= newRequestId();
    try {
      const order = await customOrdersApi.create({
        lines: toLinesIn(lines),
        delivery_date: date,
        delivery_time: time || null,
        customer: toCustomerIn(contact),
        address: toAddressIn(address),
        ...toPaymentFields(payType, feeMode),
        recipe: toRecipeIn(recipe),
        notes: notes.trim() || null,
        enquiry_id: enquiry?.id ?? null,
        client_request_id: requestId.current,
      });
      toast.success(`${order.order_number} created.`);
      router.push(`/orders/${encodeURIComponent(order.order_number)}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the order.');
      setSaving(false);
    }
  }

  return (
    <Page maxWidth="reading">
      <div className="flex items-center gap-3 mb-6">
        <Link
          href="/custom-orders"
          className="inline-flex items-center justify-center min-h-11 min-w-11 -ml-2 md:min-h-0 md:min-w-0 md:ml-0 text-gray-400 hover:text-primary transition-colors"
          aria-label="Back to custom orders"
        >
          <span className="material-icons text-[20px]">arrow_back</span>
        </Link>
        <div>
          <h1 className="font-display text-xl text-gray-800">New custom order</h1>
          <p className="text-xs text-gray-400 font-body">
            Made at {status?.branch_name ?? 'the custom-orders branch'}. Only the lines and the
            delivery date are required.
          </p>
        </div>
      </div>

      {status && !status.enabled && <SetupNotice />}

      {enquiryId && (enquiry || enquiryMissing) && (
        <div
          className={
            enquiry
              ? 'mb-4 border border-blue-200 bg-blue-50 px-4 py-3 text-sm font-body text-blue-900'
              : 'mb-4 border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-body text-amber-900'
          }
        >
          {enquiry ? (
            <>
              Converting the enquiry from <strong>{enquiry.customer_name}</strong>, received{' '}
              {formatDate(enquiry.created_at)}. Its details are filled in below — check the date and
              add the price. An enquiry has no email, so add one for the invoice.
            </>
          ) : (
            'That enquiry could not be found, so nothing was prefilled.'
          )}
        </div>
      )}

      <Section title="What is being made" hint="Each line is priced as agreed with the customer, VAT inclusive.">
        <LinesEditor value={lines} onChange={setLines} />
      </Section>

      <Section title="Delivery date">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="co-date" className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1">
              Date <span className="text-red-500">*</span>
            </label>
            <input id="co-date" type="date" value={date} onChange={e => setDate(e.target.value)} className={DATE_INPUT} />
          </div>
          <div>
            <label htmlFor="co-time" className="block text-xs font-medium uppercase tracking-wider text-gray-600 mb-1">
              Time (optional)
            </label>
            <input id="co-time" type="time" value={time} onChange={e => setTime(e.target.value)} className={DATE_INPUT} />
            <p className="mt-1 text-xs text-gray-400">Leave blank for &ldquo;any time that day&rdquo;.</p>
          </div>
        </div>
      </Section>

      <Section title="Customer" hint="Optional. A courier needs a name and phone; the invoice needs a name and email.">
        <ContactFields value={contact} onChange={setContact} />
      </Section>

      <Section title="Address" hint="Optional. Without a pin the order finishes by collection or a third-party courier.">
        <AddressPicker value={address} onChange={setAddress} />
      </Section>

      <Section title="Payment" hint="Optional. The card fee, when it is a separate line, is priced by the server.">
        <PaymentFields
          type={payType}
          feeMode={feeMode}
          onChange={(t, f) => {
            setPayType(t);
            setFeeMode(f);
          }}
        />
      </Section>

      <Section
        title="Recipe"
        hint="Optional. Taken from the kitchen's stock when the order is packed; editable until then."
      >
        {itemsError ? (
          <p className="text-xs font-body text-red-600">Couldn&rsquo;t load the recipe items. {itemsError}</p>
        ) : (
          <RecipeEditor items={items} value={recipe} onChange={setRecipe} />
        )}
      </Section>

      <Section title="Notes">
        <Textarea
          id="co-notes"
          rows={4}
          value={notes}
          maxLength={2000}
          placeholder="Anything the kitchen or whoever delivers it should know"
          onChange={e => setNotes(e.target.value)}
        />
      </Section>

      {error && (
        <div role="alert" className="mb-4 border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 font-body">
          {error}
        </div>
      )}

      <div className="flex items-center justify-end gap-2 pb-8">
        <Link
          href="/custom-orders"
          className="inline-flex items-center px-4 py-2 min-h-[var(--tap-min)] md:min-h-0 text-xs font-body font-medium uppercase tracking-wider text-gray-600 hover:text-gray-800"
        >
          Cancel
        </Link>
        <Button onClick={submit} loading={saving} disabled={saving || (status !== null && !status.enabled)}>
          {saving ? 'Creating…' : 'Create custom order'}
        </Button>
      </div>
    </Page>
  );
}
