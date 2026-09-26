'use client';

/**
 * Who the custom order is for — name, email and phone, each optional.
 *
 * An order taken from a DM may arrive with only a first name. What each field
 * unlocks is the API's call, not this form's: a courier needs a name and a
 * phone (plus a pin), the invoice needs a name and an email, and the order's
 * `actions` say which is missing.
 */

import { Input } from '@/components/ui';

export interface ContactDraft {
  name: string;
  email: string;
  phone: string;
}

export const EMPTY_CONTACT: ContactDraft = { name: '', email: '', phone: '' };

/** The request shape: blanks are sent as `null`, never as `''`. */
export function toCustomerIn(c: ContactDraft) {
  return {
    name: c.name.trim() || null,
    email: c.email.trim() || null,
    phone: c.phone.trim() || null,
  };
}

export function ContactFields({
  value,
  onChange,
  disabled,
  idPrefix = 'contact',
}: {
  value: ContactDraft;
  onChange: (next: ContactDraft) => void;
  disabled?: boolean;
  /** Keeps label/input ids unique when two of these share a page. */
  idPrefix?: string;
}) {
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      <Input
        id={`${idPrefix}-name`}
        label="Name"
        placeholder="Customer name"
        value={value.name}
        maxLength={150}
        disabled={disabled}
        autoComplete="off"
        onChange={e => onChange({ ...value, name: e.target.value })}
      />
      <Input
        id={`${idPrefix}-email`}
        label="Email"
        type="email"
        placeholder="For the invoice"
        value={value.email}
        disabled={disabled}
        autoComplete="off"
        onChange={e => onChange({ ...value, email: e.target.value })}
      />
      <Input
        id={`${idPrefix}-phone`}
        label="Phone"
        type="tel"
        placeholder="+971 5x xxx xxxx"
        value={value.phone}
        maxLength={30}
        disabled={disabled}
        autoComplete="off"
        dir="ltr"
        onChange={e => onChange({ ...value, phone: e.target.value })}
      />
    </div>
  );
}
