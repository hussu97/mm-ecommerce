'use client';

import { useState } from 'react';
import { legalEntitiesApi } from '@/lib/pos-api';
import type { LegalEntity, LegalEntityWrite } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Button, Input, Textarea } from '@/components/ui';
import { ImageField, Modal } from '@/components/pos/ResourcePage';

/**
 * Create a legal entity or edit its profile — identity, receipt branding, and
 * what an A4 invoice prints (registered address, bank details, CC list). The
 * VAT registration details, licence and documents are edited on the entity's
 * page, each in its own card.
 */

type Draft = {
  reference: string;
  legal_name: string;
  brand_name: string;
  vat_registered: boolean;
  tax_number: string;
  invoice_title: string;
  logo_url: string | null;
  registered_address: string;
  bank_name: string;
  bank_account_name: string;
  bank_account_number: string;
  iban: string;
  swift_code: string;
  invoice_cc_emails: string;
  is_active: boolean;
};

function draftFrom(e: LegalEntity | null): Draft {
  return {
    reference: e?.reference ?? '',
    legal_name: e?.legal_name ?? '',
    brand_name: e?.brand_name ?? '',
    vat_registered: e?.vat_registered ?? true,
    tax_number: e?.tax_number ?? '',
    invoice_title: e?.invoice_title ?? 'Tax Invoice',
    logo_url: e?.logo_url ?? null,
    registered_address: e?.registered_address ?? '',
    bank_name: e?.bank_name ?? '',
    bank_account_name: e?.bank_account_name ?? '',
    bank_account_number: e?.bank_account_number ?? '',
    iban: e?.iban ?? '',
    swift_code: e?.swift_code ?? '',
    invoice_cc_emails: (e?.invoice_cc_emails ?? []).join(', '),
    is_active: e?.is_active ?? true,
  };
}

/** The CC list is typed as one line; the API stores a list. */
export function parseCcEmails(text: string): string[] | null {
  const emails = text
    .split(/[,;\s]+/)
    .map((e) => e.trim())
    .filter(Boolean);
  return emails.length ? emails : null;
}

export function LegalEntityForm({
  entity,
  onClose,
  onSaved,
}: {
  /** Editing this entity; omit to create one. */
  entity?: LegalEntity | null;
  onClose: () => void;
  onSaved: (saved: LegalEntity) => void;
}) {
  const editing = Boolean(entity);
  const [d, setD] = useState<Draft>(() => draftFrom(entity ?? null));
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  const text = (k: keyof Draft) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setD((prev) => ({ ...prev, [k]: e.target.value }));
  const blank = (v: string) => v.trim() || null;

  async function save() {
    if (!d.reference.trim() || !d.legal_name.trim() || !d.brand_name.trim() || !d.invoice_title.trim()) {
      setError('Reference, legal name, brand name and invoice title are required.');
      return;
    }
    setSaving(true);
    setError('');
    const body: LegalEntityWrite = {
      reference: d.reference.trim(),
      legal_name: d.legal_name.trim(),
      brand_name: d.brand_name.trim(),
      vat_registered: d.vat_registered,
      tax_number: blank(d.tax_number),
      invoice_title: d.invoice_title.trim(),
      logo_url: d.logo_url,
      registered_address: blank(d.registered_address),
      bank_name: blank(d.bank_name),
      bank_account_name: blank(d.bank_account_name),
      bank_account_number: blank(d.bank_account_number),
      iban: blank(d.iban),
      swift_code: blank(d.swift_code),
      invoice_cc_emails: parseCcEmails(d.invoice_cc_emails),
      is_active: d.is_active,
    };
    try {
      const saved = entity
        ? await legalEntitiesApi.update(entity.id, body)
        : await legalEntitiesApi.create(body);
      onSaved(saved);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the legal entity.');
      setSaving(false);
    }
  }

  return (
    <Modal title={editing ? 'Edit legal entity' : 'New legal entity'} onClose={onClose} wide>
      <div className="space-y-5 font-body text-sm">
        <fieldset className="grid gap-3 sm:grid-cols-2">
          <legend className="mb-2 text-[11px] uppercase tracking-widest text-gray-400">Identity</legend>
          <Input label="Legal name (licence holder) *" value={d.legal_name} onChange={text('legal_name')} />
          <Input label="Brand name (on the receipt) *" value={d.brand_name} onChange={text('brand_name')} />
          <Input
            label="Reference *"
            value={d.reference}
            onChange={text('reference')}
            helper="A short slug, e.g. fatema — names its files in storage."
          />
          <Input label="Invoice title *" value={d.invoice_title} onChange={text('invoice_title')} helper="Tax Invoice / Invoice" />
          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={d.vat_registered}
              onChange={(e) => setD((p) => ({ ...p, vat_registered: e.target.checked }))}
              className="h-4 w-4 accent-primary"
            />
            VAT-registered
          </label>
          <Input
            label="TRN"
            value={d.tax_number}
            onChange={text('tax_number')}
            disabled={!d.vat_registered}
            helper={d.vat_registered ? '15 digits' : 'Not registered — no TRN'}
          />
          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={d.is_active}
              onChange={(e) => setD((p) => ({ ...p, is_active: e.target.checked }))}
              className="h-4 w-4 accent-primary"
            />
            Active
          </label>
        </fieldset>

        <ImageField
          field={{
            name: 'logo_url',
            label: 'Receipt logo',
            type: 'image',
            folder: 'logos',
            helper: 'Printed on this entity’s receipts. A clean, high-contrast PNG thresholds best on a thermal printer.',
          }}
          value={d.logo_url}
          onChange={(v) => setD((p) => ({ ...p, logo_url: (v as string | null) ?? null }))}
        />

        <fieldset className="grid gap-3 sm:grid-cols-2">
          <legend className="mb-2 text-[11px] uppercase tracking-widest text-gray-400">On invoices</legend>
          <Textarea
            label="Registered address"
            rows={2}
            value={d.registered_address}
            onChange={text('registered_address')}
            className="sm:col-span-2"
          />
          <Input label="Bank name" value={d.bank_name} onChange={text('bank_name')} />
          <Input label="Account name" value={d.bank_account_name} onChange={text('bank_account_name')} />
          <Input label="Account number" value={d.bank_account_number} onChange={text('bank_account_number')} />
          <Input label="IBAN" value={d.iban} onChange={text('iban')} />
          <Input label="SWIFT / BIC" value={d.swift_code} onChange={text('swift_code')} />
          <Input
            label="Invoice CC emails"
            value={d.invoice_cc_emails}
            onChange={text('invoice_cc_emails')}
            placeholder="owner@example.com, accounts@example.com"
          />
        </fieldset>

        {error && <p className="text-xs text-red-600">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={() => void save()} loading={saving}>
            {editing ? 'Save' : 'Create'}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
