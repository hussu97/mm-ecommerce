'use client';

import { useCallback, useState } from 'react';
import Image from 'next/image';
import { legalEntitiesApi } from '@/lib/pos-api';
import type { LegalEntity } from '@/lib/pos-types';
import { ResourcePage, StatusBadge } from '@/components/pos/ResourcePage';
import { RowAction } from '@/components/ui/DataTable';
import { ExpiryBadge, LegalEntityPaperwork } from '@/components/pos/LegalEntityPaperwork';
import { useFilePreview } from '@/components/ui/FilePreview';

/**
 * The trade licences orders are booked under. Melting Moments trades under two:
 * Fatema Cake Sweets (brand "Melting Moments Cakes", VAT-registered) and Najm
 * AlShamal Coffee Shop LLC (brand "Melting Moments Cafe") at the Barsha counter.
 *
 * The receipt shows the brand + TRN + the logo; the legal name is for records
 * and the VAT return. Which channel of which branch uses which entity is set on
 * the Branches page ("Legal entity by channel").
 */
/**
 * The form edits the CC list as one comma-separated line; the API stores a list.
 * `ResourcePage` seeds a text input with `String(row.invoice_cc_emails)`, which
 * already joins with commas, so only the way back needs converting.
 */
function withCcList(d: Record<string, unknown>): Record<string, unknown> {
  const raw = d.invoice_cc_emails;
  const text = Array.isArray(raw) ? raw.join(',') : String(raw ?? '');
  const emails = text
    .split(/[,;\s]+/)
    .map(e => e.trim())
    .filter(Boolean);
  const blank = (v: unknown) => (typeof v === 'string' && v.trim() === '' ? null : v);
  return {
    ...d,
    registered_address: blank(d.registered_address),
    bank_name: blank(d.bank_name),
    bank_account_name: blank(d.bank_account_name),
    bank_account_number: blank(d.bank_account_number),
    iban: blank(d.iban),
    swift_code: blank(d.swift_code),
    invoice_cc_emails: emails.length ? emails : null,
  };
}

export default function LegalEntitiesTab() {
  const load = useCallback(() => legalEntitiesApi.list(), []);
  const [openId, setOpenId] = useState<string | null>(null);
  const { preview, node: previewNode } = useFilePreview();
  return (
    <ResourcePage<LegalEntity>
      title="Legal entities"
      description="The trade licences orders are booked under. The receipt shows the brand + TRN + logo; the legal name drives the VAT return. Assign one per branch channel on the Branches page."
      load={load}
      create={(d) => legalEntitiesApi.create(withCcList(d) as never)}
      update={(id, d) => legalEntitiesApi.update(id, withCcList(d) as never)}
      remove={(id) => legalEntitiesApi.remove(id)}
      searchKeys={['legal_name', 'brand_name', 'reference']}
      // The logo preview: a fixed overlay, so where it mounts does not matter.
      belowTable={previewNode}
      defaults={{ vat_registered: true, invoice_title: 'Tax Invoice', is_active: true }}
      columns={[
        {
          header: 'Brand',
          priority: 'primary',
          sortable: true,
          sortAccessor: (e) => e.brand_name,
          render: (e) => (
            <span className="flex items-center gap-2">
              {e.logo_url ? (
                <button
                  type="button"
                  className="shrink-0 cursor-zoom-in"
                  title="View logo"
                  onClick={(ev) => {
                    ev.stopPropagation();
                    preview(`${e.brand_name} — Logo`, { url: e.logo_url! });
                  }}
                >
                  <Image
                    src={e.logo_url}
                    alt=""
                    width={20}
                    height={20}
                    className="rounded-sm object-contain"
                    unoptimized
                  />
                </button>
              ) : null}
              <span className="font-medium">{e.brand_name}</span>
            </span>
          ),
        },
        { header: 'Legal name', sortable: true, sortAccessor: (e) => e.legal_name, render: (e) => e.legal_name },
        {
          header: 'VAT',
          sortable: true,
          sortAccessor: (e) => (e.vat_registered ? 'Registered' : 'Not registered'),
          render: (e) => (e.vat_registered ? `TRN ${e.tax_number ?? '—'}` : 'Not registered'),
        },
        {
          header: 'Trade licence',
          sortable: true,
          sortAccessor: (e) => e.trade_license_expiry ?? '',
          render: (e) =>
            e.trade_license_number ? (
              <span className="flex flex-col">
                <span className="font-mono">{e.trade_license_number}</span>
                <span className="text-xs text-gray-500">
                  Expires <ExpiryBadge expiry={e.trade_license_expiry} />
                </span>
              </span>
            ) : (
              <span className="text-gray-400">—</span>
            ),
        },
        { header: 'Invoice title', render: (e) => e.invoice_title },
        {
          header: 'Status',
          sortable: true,
          sortAccessor: (e) => (e.is_active ? 'Active' : 'Inactive'),
          render: (e) => <StatusBadge active={e.is_active} />,
        },
      ]}
      rowActions={(e) => (
        <RowAction icon="description" onClick={() => setOpenId(openId === e.id ? null : e.id)}>
          {openId === e.id ? 'Close' : 'Licences & VAT'}
        </RowAction>
      )}
      expandedRow={(e, reload) =>
        openId === e.id ? <LegalEntityPaperwork entity={e} onChanged={reload} /> : null
      }
      fields={[
        { name: 'reference', label: 'Reference (slug, e.g. fatema)', required: true },
        { name: 'legal_name', label: 'Legal name (trade licence holder)', required: true },
        { name: 'brand_name', label: 'Brand name (shown on receipt)', required: true },
        { name: 'vat_registered', label: 'VAT-registered', type: 'checkbox' },
        { name: 'tax_number', label: 'TRN (blank if not registered)' },
        { name: 'invoice_title', label: 'Invoice title (e.g. Tax Invoice / Invoice)', required: true },
        {
          name: 'logo_url',
          label: 'Logo',
          type: 'image',
          folder: 'logos',
          helper: 'Printed on this entity’s receipts. PNG/JPEG; a clean, high-contrast image thresholds best on a thermal printer.',
        },
        // Printed on custom-order tax invoices (the bank block only when the
        // customer pays by bank transfer).
        {
          name: 'registered_address',
          label: 'Registered address (on invoices)',
          type: 'textarea',
          placeholder: 'Shop no. 1, Garden Tower 1, Al Majaz 3, Sharjah, UAE',
        },
        { name: 'bank_name', label: 'Bank name' },
        { name: 'bank_account_name', label: 'Bank account name' },
        { name: 'bank_account_number', label: 'Bank account number' },
        { name: 'iban', label: 'IBAN' },
        { name: 'swift_code', label: 'SWIFT / BIC' },
        {
          name: 'invoice_cc_emails',
          label: 'Invoice CC emails',
          placeholder: 'owner@example.com, accounts@example.com',
          helper: 'Comma-separated. Copied on every custom-order invoice emailed to a customer.',
        },
        { name: 'is_active', label: 'Active', type: 'checkbox' },
      ]}
    />
  );
}
