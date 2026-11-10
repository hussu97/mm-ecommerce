'use client';

import { useCallback, useEffect, useState } from 'react';
import { legalEntitiesApi, inventoryApi } from '@/lib/pos-api';
import type {
  Emirate,
  LegalEntity,
  LegalEntityDocumentUrl,
  TradeLicense,
  TradeLicenseAuthorityOption,
  TradeLicenseWrite,
} from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button, Input, Select, Spinner } from '@/components/ui';
import { useConfirm, useToast } from '@/components/ui/feedback';

/**
 * A legal entity's registration paperwork, opened under its row on the Legal
 * entities page: the VAT registration (TRN, the e-invoicing TIN + Peppol ID, the
 * issuing authority and emirate, the certificate) and the trade licence's full
 * history — one row per issue→expiry cycle, each with its own scan. The cycle
 * that expires last is the current one, mirrored onto the entity's list row.
 *
 * Documents sit in the private finance bucket and open through a short-lived
 * signed link; nothing here is public.
 */

export const EMIRATES: Emirate[] = [
  'Abu Dhabi',
  'Dubai',
  'Sharjah',
  'Ajman',
  'Umm Al Quwain',
  'Ras Al Khaimah',
  'Fujairah',
];
const EMIRATE_OPTIONS = EMIRATES.map((e) => ({ value: e, label: e }));

type TradeLicenseType = NonNullable<TradeLicenseWrite['license_type']>;
/** Labels for the API's licence-type codes; a `Record` over the generated
 *  union, so a code added server-side fails the typecheck until labelled. */
export const TRADE_LICENSE_TYPE_LABELS: Record<TradeLicenseType, string> = {
  commercial: 'Commercial',
  professional: 'Professional',
  industrial: 'Industrial',
  tourism: 'Tourism',
  crafts: 'Crafts',
  agricultural: 'Agricultural',
  e_trader: 'e-Trader',
  freelance: 'Freelance',
  other: 'Other',
};
const LICENSE_TYPE_OPTIONS = (Object.keys(TRADE_LICENSE_TYPE_LABELS) as TradeLicenseType[]).map((value) => ({
  value,
  label: TRADE_LICENSE_TYPE_LABELS[value],
}));
const ACCEPT = 'image/jpeg,image/png,image/webp,application/pdf';
/** Days before expiry a licence starts showing as due for renewal. */
export const RENEWAL_WARNING_DAYS = 60;

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  });
}

/** Whole days from today (Dubai) to an ISO date; negative once it has passed. */
export function daysUntil(iso: string): number {
  const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Dubai' });
  const ms = Date.parse(`${iso}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`);
  return Math.round(ms / 86_400_000);
}

export function ExpiryBadge({ expiry }: { expiry: string | null | undefined }) {
  if (!expiry) return <span className="text-gray-400">—</span>;
  const days = daysUntil(expiry);
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span>{formatDate(expiry)}</span>
      {days < 0 ? (
        <Badge variant="danger">Expired</Badge>
      ) : days <= RENEWAL_WARNING_DAYS ? (
        <Badge variant="warning">{days === 0 ? 'Expires today' : `${days}d left`}</Badge>
      ) : null}
    </span>
  );
}

/** The emirate a licensing authority sits in, read from its label ("Dubai — DMCC"). */
function emirateOf(option: TradeLicenseAuthorityOption | undefined): Emirate | '' {
  const prefix = option?.label.split(' — ')[0];
  return (EMIRATES as string[]).includes(prefix ?? '') ? (prefix as Emirate) : '';
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

/** Open a signed URL in a new tab. The tab opens synchronously so the popup
 *  blocker allows it, then follows the URL once the API returns it. */
async function openSigned(fetchUrl: () => Promise<LegalEntityDocumentUrl>, onError: (m: string) => void) {
  const tab = window.open('', '_blank');
  try {
    const res = await fetchUrl();
    if (tab) tab.location.href = res.url;
    else window.location.href = res.url;
  } catch (err) {
    tab?.close();
    onError(errorText(err, 'Could not open the document.'));
  }
}

function FileButton({
  label,
  onFile,
  busy,
}: {
  label: string;
  onFile: (file: File) => void;
  busy?: boolean;
}) {
  return (
    <label
      className={
        'inline-block cursor-pointer whitespace-nowrap rounded border border-gray-300 bg-white px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50' +
        (busy ? ' pointer-events-none opacity-50' : '')
      }
    >
      {busy ? 'Uploading…' : label}
      <input
        type="file"
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = '';
          if (file) onFile(file);
        }}
      />
    </label>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="mb-2 text-[11px] font-medium uppercase tracking-widest text-gray-500 font-body">{children}</h3>
  );
}

// ─── VAT registration ────────────────────────────────────────────────────────

interface VatDraft {
  vat_tin: string;
  peppol_id: string;
  vat_issuing_authority: string;
  vat_emirate: string;
  vat_effective_date: string;
}

function vatDraft(e: LegalEntity): VatDraft {
  return {
    vat_tin: e.vat_tin ?? '',
    peppol_id: e.peppol_id ?? '',
    vat_issuing_authority: e.vat_issuing_authority ?? '',
    vat_emirate: e.vat_emirate ?? '',
    vat_effective_date: e.vat_effective_date ?? '',
  };
}

function VatSection({ entity, onSaved }: { entity: LegalEntity; onSaved: () => void }) {
  const toast = useToast();
  const confirm = useConfirm();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<VatDraft>(() => vatDraft(entity));
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);

  useEffect(() => setDraft(vatDraft(entity)), [entity]);

  const suggestedTin = entity.tax_number && /^\d{15}$/.test(entity.tax_number) ? entity.tax_number.slice(0, 10) : '';

  async function save() {
    setSaving(true);
    try {
      await legalEntitiesApi.updateVat(entity.id, {
        vat_tin: draft.vat_tin.trim() || null,
        peppol_id: draft.peppol_id.trim() || null,
        vat_issuing_authority: draft.vat_issuing_authority.trim() || null,
        vat_emirate: (draft.vat_emirate || null) as Emirate | null,
        vat_effective_date: draft.vat_effective_date || null,
      });
      toast.success('VAT details saved');
      setEditing(false);
      onSaved();
    } catch (err) {
      toast.error(errorText(err, 'Could not save the VAT details.'));
    } finally {
      setSaving(false);
    }
  }

  async function upload(file: File) {
    setUploading(true);
    try {
      await legalEntitiesApi.uploadVatCertificate(entity.id, file);
      toast.success('VAT certificate uploaded');
      onSaved();
    } catch (err) {
      toast.error(errorText(err, 'Could not upload the certificate.'));
    } finally {
      setUploading(false);
    }
  }

  async function remove() {
    if (
      !(await confirm({
        title: 'Remove VAT certificate',
        message: `Delete ${entity.legal_name}'s VAT certificate from storage? This cannot be undone.`,
        confirmLabel: 'Remove',
        danger: true,
      }))
    )
      return;
    try {
      await legalEntitiesApi.removeVatCertificate(entity.id);
      onSaved();
    } catch (err) {
      toast.error(errorText(err, 'Could not remove the certificate.'));
    }
  }

  if (!entity.vat_registered) {
    return (
      <section>
        <SectionTitle>VAT registration</SectionTitle>
        <p className="text-sm text-gray-500 font-body">
          Not VAT-registered — no TRN, TIN or Peppol ID. Tick “VAT-registered” on the entity to record one.
        </p>
      </section>
    );
  }

  const set = (k: keyof VatDraft) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setDraft((d) => ({ ...d, [k]: e.target.value }));

  return (
    <section>
      <div className="mb-2 flex items-center justify-between gap-2">
        <SectionTitle>VAT registration</SectionTitle>
        {!editing && (
          <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
            Edit
          </Button>
        )}
      </div>
      {editing ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <Input
            label="TIN (10 digits, for e-invoicing)"
            value={draft.vat_tin}
            onChange={set('vat_tin')}
            inputMode="numeric"
            maxLength={10}
            helper={suggestedTin ? `The first ten digits of the TRN: ${suggestedTin}` : undefined}
          />
          <Input
            label="Peppol ID"
            value={draft.peppol_id}
            onChange={set('peppol_id')}
            placeholder={suggestedTin ? `0235:${suggestedTin}` : '0235:<TIN>'}
            helper="0235 is the UAE scheme. Activated by the e-invoicing provider connected to Zoho Books."
          />
          <Input
            label="Issuing authority"
            value={draft.vat_issuing_authority}
            onChange={set('vat_issuing_authority')}
            placeholder="Federal Tax Authority (FTA)"
          />
          <Select
            label="Emirate"
            value={draft.vat_emirate}
            onChange={set('vat_emirate')}
            options={EMIRATE_OPTIONS}
            placeholder="—"
          />
          <Input
            label="Effective registration date"
            type="date"
            value={draft.vat_effective_date}
            onChange={set('vat_effective_date')}
          />
          <div className="flex items-end gap-2">
            <Button size="sm" onClick={save} loading={saving}>
              Save
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setDraft(vatDraft(entity));
                setEditing(false);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <dl className="grid gap-x-6 gap-y-2 text-sm font-body sm:grid-cols-3">
          <Fact label="TRN" value={entity.tax_number} mono />
          <Fact label="TIN" value={entity.vat_tin} mono />
          <Fact label="Peppol ID" value={entity.peppol_id} mono />
          <Fact label="Issuing authority" value={entity.vat_issuing_authority} />
          <Fact label="Emirate" value={entity.vat_emirate} />
          <Fact label="Effective from" value={formatDate(entity.vat_effective_date)} />
        </dl>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-3 text-sm font-body">
        <span className="text-xs font-medium uppercase tracking-wider text-gray-600">Certificate</span>
        {entity.has_vat_certificate ? (
          <>
            <button
              type="button"
              className="text-primary hover:underline"
              onClick={() => openSigned(() => legalEntitiesApi.vatCertificateUrl(entity.id), toast.error)}
            >
              View
            </button>
            <FileButton label="Replace" onFile={upload} busy={uploading} />
            <button type="button" className="text-gray-500 hover:text-red-600" onClick={remove}>
              Remove
            </button>
          </>
        ) : (
          <>
            <FileButton label="Upload" onFile={upload} busy={uploading} />
            <span className="text-xs text-gray-400">JPEG, PNG, WebP or PDF · max 10 MB</span>
          </>
        )}
      </div>
    </section>
  );
}

function Fact({ label, value, mono }: { label: string; value: string | null | undefined; mono?: boolean }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wider text-gray-500">{label}</dt>
      <dd className={mono ? 'font-mono text-gray-900' : 'text-gray-900'}>{value || '—'}</dd>
    </div>
  );
}

// ─── Trade-licence history ───────────────────────────────────────────────────

interface LicenseDraft {
  license_number: string;
  registration_number: string;
  issuing_authority: string;
  emirate: string;
  license_type: string;
  legal_form: string;
  issue_date: string;
  expiry_date: string;
  notes: string;
}

function licenseDraft(row?: TradeLicense, seed?: TradeLicense): LicenseDraft {
  // A renewal keeps the number, register no., authority and emirate — seed a
  // new cycle from the current one so only the dates need typing.
  const from = row ?? seed;
  return {
    license_number: from?.license_number ?? '',
    registration_number: from?.registration_number ?? '',
    issuing_authority: from?.issuing_authority ?? '',
    emirate: from?.emirate ?? '',
    license_type: from?.license_type ?? '',
    legal_form: from?.legal_form ?? '',
    issue_date: row?.issue_date ?? seed?.issue_date ?? '',
    expiry_date: row?.expiry_date ?? '',
    notes: row?.notes ?? '',
  };
}

function LicenseForm({
  initial,
  authorities,
  onSubmit,
  onCancel,
  submitLabel,
}: {
  initial: LicenseDraft;
  authorities: TradeLicenseAuthorityOption[];
  onSubmit: (d: LicenseDraft, file: File | null) => Promise<void>;
  onCancel: () => void;
  submitLabel: string;
}) {
  const [d, setD] = useState(initial);
  const [file, setFile] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (k: keyof LicenseDraft) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setD((prev) => ({ ...prev, [k]: e.target.value }));

  async function submit() {
    if (!d.license_number.trim() || !d.issuing_authority || !d.emirate || !d.expiry_date) {
      setError('Licence number, issuing authority, emirate and expiry date are required.');
      return;
    }
    if (d.issue_date && d.issue_date > d.expiry_date) {
      setError('The issue date must be on or before the expiry date.');
      return;
    }
    setError(null);
    setSaving(true);
    try {
      await onSubmit(d, file);
    } catch (err) {
      setError(errorText(err, 'Could not save the licence.'));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded border border-gray-200 bg-white p-3">
      <div className="grid gap-3 sm:grid-cols-3">
        <Input label="Licence number" value={d.license_number} onChange={set('license_number')} />
        <Input label="Register no." value={d.registration_number} onChange={set('registration_number')} />
        <Select
          label="Issuing authority"
          value={d.issuing_authority}
          placeholder="Choose…"
          options={authorities.map((a) => ({ value: a.code, label: a.label }))}
          onChange={(e) => {
            const code = e.target.value;
            const emirate = emirateOf(authorities.find((a) => a.code === code));
            setD((prev) => ({ ...prev, issuing_authority: code, emirate: emirate || prev.emirate }));
          }}
        />
        <Select label="Emirate" value={d.emirate} placeholder="Choose…" options={EMIRATE_OPTIONS} onChange={set('emirate')} />
        <Select
          label="Licence type"
          value={d.license_type}
          placeholder="—"
          options={LICENSE_TYPE_OPTIONS}
          onChange={set('license_type')}
        />
        <Input
          label="Legal form"
          value={d.legal_form}
          onChange={set('legal_form')}
          placeholder="e.g. LLC - Single Owner, Sole Establishment"
        />
        <Input label="Issue date (as printed)" type="date" value={d.issue_date} onChange={set('issue_date')} />
        <Input label="Expiry date" type="date" value={d.expiry_date} onChange={set('expiry_date')} />
        <Input label="Notes" value={d.notes} onChange={set('notes')} className="sm:col-span-2" />
        <div className="text-xs font-body">
          <span className="mb-1 block font-medium uppercase tracking-wider text-gray-600">Licence document</span>
          <label className="inline-block cursor-pointer whitespace-nowrap rounded border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50">
            {file ? 'Change file' : 'Choose file'}
            <input type="file" accept={ACCEPT} className="hidden" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
          <span className="ml-2 text-gray-500">{file ? file.name : 'optional'}</span>
        </div>
      </div>
      {error && <p className="mt-2 text-sm text-red-600 font-body">{error}</p>}
      <div className="mt-3 flex gap-2">
        <Button size="sm" onClick={submit} loading={saving}>
          {submitLabel}
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function toWrite(d: LicenseDraft): TradeLicenseWrite {
  return {
    license_number: d.license_number.trim(),
    registration_number: d.registration_number.trim() || null,
    issuing_authority: d.issuing_authority as TradeLicenseWrite['issuing_authority'],
    emirate: d.emirate as Emirate,
    license_type: (d.license_type || null) as TradeLicenseType | null,
    legal_form: d.legal_form.trim() || null,
    issue_date: d.issue_date || null,
    expiry_date: d.expiry_date,
    notes: d.notes.trim() || null,
  };
}

function LicenseHistory({ entity, onChanged }: { entity: LegalEntity; onChanged: () => void }) {
  const toast = useToast();
  const confirm = useConfirm();
  const [rows, setRows] = useState<TradeLicense[] | null>(null);
  const [authorities, setAuthorities] = useState<TradeLicenseAuthorityOption[]>([]);
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [uploadingId, setUploadingId] = useState<string | null>(null);

  const load = useCallback(() => {
    legalEntitiesApi
      .tradeLicenses(entity.id)
      .then(setRows)
      .catch((err) => {
        setRows([]);
        toast.error(errorText(err, 'Could not load the trade licences.'));
      });
  }, [entity.id, toast]);

  useEffect(load, [load]);
  useEffect(() => {
    inventoryApi.tradeLicenseAuthorities().then(setAuthorities).catch(() => setAuthorities([]));
  }, []);

  const authorityLabel = (code: string) => authorities.find((a) => a.code === code)?.label ?? code;
  const refresh = () => {
    load();
    onChanged();
  };

  async function create(d: LicenseDraft, file: File | null) {
    const row = await legalEntitiesApi.createTradeLicense(entity.id, toWrite(d));
    if (file) {
      try {
        await legalEntitiesApi.uploadTradeLicenseDocument(entity.id, row.id, file);
      } catch (err) {
        toast.error(`Licence saved, but the document did not upload: ${errorText(err, 'upload failed')}`);
      }
    }
    toast.success('Trade licence added');
    setAdding(false);
    refresh();
  }

  async function update(row: TradeLicense, d: LicenseDraft, file: File | null) {
    await legalEntitiesApi.updateTradeLicense(entity.id, row.id, toWrite(d));
    if (file) await legalEntitiesApi.uploadTradeLicenseDocument(entity.id, row.id, file);
    toast.success('Trade licence saved');
    setEditingId(null);
    refresh();
  }

  async function upload(row: TradeLicense, file: File) {
    setUploadingId(row.id);
    try {
      await legalEntitiesApi.uploadTradeLicenseDocument(entity.id, row.id, file);
      toast.success('Document uploaded');
      load();
    } catch (err) {
      toast.error(errorText(err, 'Could not upload the document.'));
    } finally {
      setUploadingId(null);
    }
  }

  async function removeDocument(row: TradeLicense) {
    if (
      !(await confirm({
        title: 'Remove licence document',
        message: `Delete the document for the cycle expiring ${formatDate(row.expiry_date)}? This cannot be undone.`,
        confirmLabel: 'Remove',
        danger: true,
      }))
    )
      return;
    try {
      await legalEntitiesApi.removeTradeLicenseDocument(entity.id, row.id);
      load();
    } catch (err) {
      toast.error(errorText(err, 'Could not remove the document.'));
    }
  }

  async function removeCycle(row: TradeLicense) {
    if (
      !(await confirm({
        title: 'Delete licence cycle',
        message: `Delete the cycle expiring ${formatDate(row.expiry_date)} and its document? Only do this for a cycle recorded by mistake — renewals keep the old cycle as history.`,
        confirmLabel: 'Delete',
        danger: true,
      }))
    )
      return;
    try {
      await legalEntitiesApi.removeTradeLicense(entity.id, row.id);
      refresh();
    } catch (err) {
      toast.error(errorText(err, 'Could not delete the cycle.'));
    }
  }

  const current = rows?.find((r) => r.is_current);

  return (
    <section>
      <div className="mb-2 flex items-center justify-between gap-2">
        <SectionTitle>Trade licence history</SectionTitle>
        {!adding && (
          <Button size="sm" variant="ghost" onClick={() => setAdding(true)}>
            {rows?.length ? 'Add renewal' : 'Add licence'}
          </Button>
        )}
      </div>
      {adding && (
        <div className="mb-3">
          <LicenseForm
            initial={licenseDraft(undefined, current)}
            authorities={authorities}
            onSubmit={create}
            onCancel={() => setAdding(false)}
            submitLabel="Add"
          />
        </div>
      )}
      {rows === null ? (
        <Spinner className="h-5 w-5" />
      ) : rows.length === 0 ? (
        <p className="text-sm text-gray-500 font-body">No trade licence recorded yet.</p>
      ) : (
        <ul className="divide-y divide-gray-200 rounded border border-gray-200 bg-white">
          {rows.map((row) =>
            editingId === row.id ? (
              <li key={row.id} className="p-2">
                <LicenseForm
                  initial={licenseDraft(row)}
                  authorities={authorities}
                  onSubmit={(d, file) => update(row, d, file)}
                  onCancel={() => setEditingId(null)}
                  submitLabel="Save"
                />
              </li>
            ) : (
              <li key={row.id} className="flex flex-col gap-2 p-3 text-sm font-body sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0 space-y-0.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono font-medium text-gray-900">{row.license_number}</span>
                    {row.is_current ? <Badge variant="success">Current</Badge> : <Badge>Previous</Badge>}
                    {row.registration_number && (
                      <span className="text-xs text-gray-500">Register no. {row.registration_number}</span>
                    )}
                  </div>
                  <div className="text-gray-700">
                    {authorityLabel(row.issuing_authority)} · {row.emirate}
                    {row.license_type && (
                      <>
                        {' · '}
                        {TRADE_LICENSE_TYPE_LABELS[row.license_type as TradeLicenseType] ?? row.license_type} licence
                      </>
                    )}
                  </div>
                  {row.legal_form && <div className="text-xs text-gray-500">{row.legal_form}</div>}
                  <div className="text-gray-600">
                    Issued {formatDate(row.issue_date)} · Expires{' '}
                    {row.is_current ? <ExpiryBadge expiry={row.expiry_date} /> : formatDate(row.expiry_date)}
                  </div>
                  {row.notes && <div className="text-xs text-gray-500">{row.notes}</div>}
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-3">
                  {row.has_document ? (
                    <>
                      <button
                        type="button"
                        className="text-primary hover:underline"
                        onClick={() =>
                          openSigned(() => legalEntitiesApi.tradeLicenseDocumentUrl(entity.id, row.id), toast.error)
                        }
                      >
                        View document
                      </button>
                      <button type="button" className="text-gray-500 hover:text-red-600" onClick={() => removeDocument(row)}>
                        Remove file
                      </button>
                    </>
                  ) : (
                    <FileButton label="Upload document" onFile={(f) => upload(row, f)} busy={uploadingId === row.id} />
                  )}
                  <button type="button" className="text-gray-600 hover:underline" onClick={() => setEditingId(row.id)}>
                    Edit
                  </button>
                  <button type="button" className="text-gray-500 hover:text-red-600" onClick={() => removeCycle(row)}>
                    Delete
                  </button>
                </div>
              </li>
            ),
          )}
        </ul>
      )}
    </section>
  );
}

export function LegalEntityPaperwork({ entity, onChanged }: { entity: LegalEntity; onChanged: () => void }) {
  return (
    <div className="grid gap-6 bg-gray-50 p-4">
      <LicenseHistory entity={entity} onChanged={onChanged} />
      <VatSection entity={entity} onSaved={onChanged} />
    </div>
  );
}
