'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { branchesApi, inventoryApi, legalEntitiesApi } from '@/lib/pos-api';
import { employeesApi, type EmployeeListItem } from '@/lib/people-api';
import type {
  Branch,
  EntityDocType,
  EntityDocument,
  EntityDocumentTypeSpec,
  EntityFile,
  EntityPaperwork,
  LegalEntity,
  TradeLicense,
  TradeLicenseAuthorityOption,
} from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/utils';
import { Badge, Button, LoadError, Spinner } from '@/components/ui';
import { Page } from '@/components/ui/Page';
import { Modal } from '@/components/pos/ResourcePage';
import { useConfirm, useToast } from '@/components/ui/feedback';
import { useFilePreview } from '@/components/ui/FilePreview';
import {
  formatDate,
  LicenseForm,
  licenseDraft,
  licenseToWrite,
  TRADE_LICENSE_TYPE_LABELS,
  VatSection,
  type LicenseDraft,
  type TradeLicenseType,
} from '@/components/pos/LegalEntityPaperwork';
import {
  DocumentCard,
  initials,
  OtherFiles,
  PaperworkSummary,
  type CardDoc,
  type CardSpec,
  type TypeState,
} from '@/components/people/EmployeePaperwork';
import { FileForm, type FileFormApi } from '@/components/people/FileForm';
import { useExpiringDocuments } from '@/components/people/ExpiringDocuments';
import { EntityDocumentForm } from '@/components/legal-entities/EntityDocumentForm';
import { LegalEntityForm } from '@/components/legal-entities/LegalEntityForm';

/**
 * One legal entity, laid out like an employee's page to answer "is its
 * paperwork in order?" first:
 *
 * - a header with the logo, brand, legal name and VAT status;
 * - a status strip, one tile per company-wide document (trade licence, VAT
 *   certificate, establishment card, labour card, chamber, Corporate Tax, MoA,
 *   and the shop's tenancy, food permit, Civil Defence, signage and pest
 *   control), each jumping to its card or adding it;
 * - cards grouped — registration, visas & labour, premises — each with its
 *   earlier cycles behind "N previous". Each entity manages one shop, so its
 *   premises documents are one per entity like the rest;
 * - other files; the profile, branches and sponsored staff beside it all.
 *
 * `?add=<type>` (from the expiry popup) opens the add form — `trade_license`
 * included.
 */

const TRADE_LICENSE_SPEC: CardSpec = {
  doc_type: 'trade_license',
  label: 'Trade licence',
  number_label: 'Licence number',
  authority_label: 'Licensing authority',
  details: {
    registration_number: 'Register no.',
    emirate: 'Emirate',
    license_type: 'Licence type',
    legal_form: 'Legal form',
  },
};

type LicenseCardDoc = CardDoc & { row: TradeLicense };

function errorText(err: unknown, fallback: string) {
  return err instanceof ApiError ? err.message : fallback;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1.5 lg:block">
      <dt className="shrink-0 text-[10px] uppercase tracking-widest text-gray-400">{label}</dt>
      <dd className="min-w-0 break-words text-right text-sm text-gray-800 lg:mt-0.5 lg:text-left">{children}</dd>
    </div>
  );
}

function GroupHeading({ children, action }: { children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <h2 className="text-[11px] font-medium uppercase tracking-widest text-gray-500">{children}</h2>
      {action}
    </div>
  );
}


export default function LegalEntityPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const toast = useToast();
  const confirm = useConfirm();
  const { refresh: refreshWarnings } = useExpiringDocuments();
  const { preview, node: previewNode } = useFilePreview();

  const [entity, setEntity] = useState<LegalEntity | null>(null);
  const [paper, setPaper] = useState<EntityPaperwork | null>(null);
  const [licenses, setLicenses] = useState<TradeLicense[]>([]);
  const [specs, setSpecs] = useState<EntityDocumentTypeSpec[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [tradingBranchIds, setTradingBranchIds] = useState<string[]>([]);
  const [authorities, setAuthorities] = useState<TradeLicenseAuthorityOption[]>([]);
  const [staff, setStaff] = useState<EmployeeListItem[] | null>(null);
  const [loadError, setLoadError] = useState('');
  const [editingProfile, setEditingProfile] = useState(false);
  const [docForm, setDocForm] = useState<{
    type: EntityDocType;
    doc?: EntityDocument;
    seed?: EntityDocument;
  } | null>(null);
  const [licenseForm, setLicenseForm] = useState<{ row?: TradeLicense; seed?: TradeLicense } | null>(null);
  const [fileForm, setFileForm] = useState<{ file?: EntityFile } | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoadError('');
    try {
      const [all, p, l] = await Promise.all([
        legalEntitiesApi.list(),
        legalEntitiesApi.paperwork(id),
        legalEntitiesApi.tradeLicenses(id),
      ]);
      const e = all.find((x) => x.id === id);
      if (!e) {
        setLoadError('This legal entity does not exist.');
        return;
      }
      setEntity(e);
      setPaper(p);
      setLicenses(l);
    } catch (err) {
      setLoadError(errorText(err, 'Could not load the legal entity.'));
    }
  }, [id]);

  useEffect(() => {
    void load();
    legalEntitiesApi
      .documentTypes()
      .then(setSpecs)
      .catch(() => {});
    inventoryApi
      .tradeLicenseAuthorities()
      .then(setAuthorities)
      .catch(() => {});
    // Sponsored staff — super-admin only on the API; anyone else just sees none.
    employeesApi
      .list('active')
      .then((rows) => setStaff(rows.filter((r) => r.legal_entity_id === id)))
      .catch(() => setStaff(null));
    branchesApi
      .list()
      .then(async (list) => {
        setBranches(list);
        // Which branches trade under this entity on any channel (the profile
        // side panel lists them).
        const hits = await Promise.all(
          list.map((b) =>
            branchesApi
              .channelTaxConfigs(b.id)
              .then((cfgs) => (cfgs.some((c) => c.legal_entity_id === id) ? b.id : null))
              .catch(() => null),
          ),
        );
        setTradingBranchIds(hits.filter((x): x is string => Boolean(x)));
      })
      .catch(() => {});
  }, [load, id]);

  async function afterChange(message?: string) {
    await load();
    refreshWarnings();
    if (message) toast.success(message);
  }

  const docs = useMemo(() => paper?.documents ?? [], [paper]);
  const currentDoc = (type: string) => docs.find((d) => d.doc_type === type && d.is_current);
  const historyOf = (type: string) => docs.filter((d) => d.doc_type === type && !d.is_current);

  // `?add=establishment_card` — arriving from the expiry popup.
  useEffect(() => {
    if (!entity || !paper || specs.length === 0) return;
    const params = new URLSearchParams(window.location.search);
    const add = params.get('add');
    if (!add) return;
    router.replace(`/legal-entities/${entity.id}`);
    if (add === 'trade_license') {
      const current = licenses.find((l) => l.is_current);
      setLicenseForm(current ? { seed: current } : {});
    } else if (specs.some((s) => s.doc_type === add)) {
      setDocForm({ type: add as EntityDocType, seed: docs.find((d) => d.doc_type === add && d.is_current) });
    }
    // Runs once the page has what the form needs; the URL is cleared at once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entity, paper, specs]);

  function openCard(key: string) {
    document.getElementById(`doc-${key}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setHighlight(key);
    window.setTimeout(() => setHighlight((h) => (h === key ? null : h)), 1600);
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast.success('Copied');
    } catch {
      toast.error('Could not copy — select the number instead.');
    }
  }

  // ─── Generic documents ──────────────────────────────────────────────────────

  async function removeDocument(doc: EntityDocument, label: string) {
    if (
      !entity ||
      !(await confirm({
        message: `Delete this ${label.toLowerCase()}${doc.expiry_date ? ` (expiring ${formatDate(doc.expiry_date)})` : ''} and its scan? Only do this for a record entered by mistake — to renew, add the new one instead and this stays in its history.`,
        danger: true,
      }))
    )
      return;
    try {
      await legalEntitiesApi.removeDocument(entity.id, doc.id);
      await afterChange('Document deleted');
    } catch (err) {
      toast.error(errorText(err, 'Could not delete the document.'));
    }
  }

  function docCard(spec: EntityDocumentTypeSpec) {
    const current = currentDoc(spec.doc_type);
    if (!current) return null;
    return (
      <DocumentCard
        key={spec.doc_type}
        spec={spec}
        current={current}
        history={historyOf(spec.doc_type)}
        highlighted={highlight === spec.doc_type}
        renewLabel={spec.expires ? 'Add renewal' : 'Add amended version'}
        onView={(doc) =>
          preview(`${entity!.legal_name} — ${spec.label}`, () => legalEntitiesApi.documentFileUrl(entity!.id, doc.id))
        }
        onEdit={(doc) => setDocForm({ type: spec.doc_type as EntityDocType, doc })}
        onDelete={(doc) => void removeDocument(doc, spec.label)}
        onRenew={() => setDocForm({ type: spec.doc_type as EntityDocType, seed: current })}
        onCopy={(text) => void copy(text)}
      />
    );
  }

  // ─── Trade licence ──────────────────────────────────────────────────────────

  const authorityLabel = (code: string) => authorities.find((a) => a.code === code)?.label ?? code;
  const currentLicense = licenses.find((l) => l.is_current);
  const asCardDoc = (row: TradeLicense): LicenseCardDoc => ({
    id: row.id,
    row,
    document_number: row.license_number,
    issue_date: row.issue_date,
    expiry_date: row.expiry_date,
    issuing_authority: authorityLabel(row.issuing_authority),
    notes: row.notes,
    has_file: row.has_document,
    details: Object.fromEntries(
      Object.entries({
        registration_number: row.registration_number,
        emirate: row.emirate,
        license_type: row.license_type
          ? (TRADE_LICENSE_TYPE_LABELS[row.license_type as TradeLicenseType] ?? row.license_type)
          : null,
        legal_form: row.legal_form,
      }).filter((kv): kv is [string, string] => Boolean(kv[1])),
    ),
  });

  async function saveLicense(d: LicenseDraft, file: File | null) {
    if (!entity) return;
    const row = licenseForm?.row
      ? await legalEntitiesApi.updateTradeLicense(entity.id, licenseForm.row.id, licenseToWrite(d))
      : await legalEntitiesApi.createTradeLicense(entity.id, licenseToWrite(d));
    if (file) {
      try {
        await legalEntitiesApi.uploadTradeLicenseDocument(entity.id, row.id, file);
      } catch (err) {
        toast.error(`Licence saved, but the document did not upload: ${errorText(err, 'upload failed')}`);
      }
    }
    setLicenseForm(null);
    await afterChange('Trade licence saved');
  }

  async function removeLicense(row: TradeLicense) {
    if (
      !entity ||
      !(await confirm({
        message: `Delete the trade licence cycle expiring ${formatDate(row.expiry_date)} and its document? Only do this for a cycle recorded by mistake — renewals keep the old cycle as history.`,
        danger: true,
      }))
    )
      return;
    try {
      await legalEntitiesApi.removeTradeLicense(entity.id, row.id);
      await afterChange('Trade licence cycle deleted');
    } catch (err) {
      toast.error(errorText(err, 'Could not delete the cycle.'));
    }
  }

  // ─── Files ──────────────────────────────────────────────────────────────────

  const fileApi: FileFormApi = useMemo(
    () => ({
      categories: () => legalEntitiesApi.fileCategories(),
      create: (d) => legalEntitiesApi.createFile(id, d),
      update: (fileId, d) => legalEntitiesApi.updateFile(id, fileId, d),
      upload: (fileId, file) => legalEntitiesApi.uploadFile(id, fileId, file),
    }),
    [id],
  );

  async function removeFile(file: EntityFile) {
    if (!entity || !(await confirm({ message: `Delete “${file.title}” and its file?`, danger: true }))) return;
    try {
      await legalEntitiesApi.removeFile(entity.id, file.id);
      await afterChange('File deleted');
    } catch (err) {
      toast.error(errorText(err, 'Could not delete the file.'));
    }
  }

  async function removeEntity() {
    if (
      !entity ||
      !(await confirm({
        message: `Delete ${entity.legal_name}? Orders and branch channels that reference it will refuse the delete; to stop using it, edit it and untick Active instead.`,
        danger: true,
      }))
    )
      return;
    try {
      await legalEntitiesApi.remove(entity.id);
      refreshWarnings();
      toast.success('Legal entity deleted');
      router.push('/legal-entities');
    } catch (err) {
      toast.error(errorText(err, 'Could not delete the legal entity.'));
    }
  }

  if (loadError) return <LoadError message={loadError} onRetry={() => void load()} />;
  if (!entity || !paper) {
    return (
      <div className="flex justify-center py-12">
        <Spinner />
      </div>
    );
  }

  // ─── Status strip ───────────────────────────────────────────────────────────

  const states: TypeState<{ doc_type: string; label: string }, { expiry_date?: string | null }>[] = [
    { spec: { doc_type: 'trade_license', label: 'Trade licence' }, current: currentLicense },
    ...(entity.vat_registered
      ? [
          {
            spec: { doc_type: 'vat_certificate', label: 'VAT certificate' },
            current: entity.has_vat_certificate ? { expiry_date: null } : undefined,
          },
        ]
      : []),
    ...specs.map((spec) => ({ spec, current: currentDoc(spec.doc_type) })),
  ];

  function onStripAdd(key: string) {
    if (key === 'trade_license') setLicenseForm({});
    else if (key === 'vat_certificate') openCard('vat_certificate');
    else setDocForm({ type: key as EntityDocType });
  }

  const inGroup = (group: string) => specs.filter((s) => s.group === group);
  const registration = inGroup('Registration');
  const otherGroups = [...new Set(specs.map((s) => s.group))].filter((g) => g !== 'Registration');
  const facts = [
    entity.legal_name !== entity.brand_name ? entity.legal_name : null,
    currentLicense ? `${authorityLabel(currentLicense.issuing_authority)}` : null,
  ].filter(Boolean);

  return (
    <Page>
      <Link
        href="/legal-entities"
        className="inline-flex items-center gap-1 text-xs font-body uppercase tracking-widest text-gray-500 hover:text-primary"
      >
        <span className="material-icons text-[16px]" aria-hidden>
          arrow_back
        </span>
        Legal entities
      </Link>

      {/* Header */}
      <header className="mt-3 mb-5 flex flex-col gap-4 border border-gray-200 bg-white p-4 font-body sm:flex-row sm:items-center">
        <div className="flex min-w-0 flex-1 items-center gap-4">
          <button
            type="button"
            className={cn(
              'flex h-20 w-20 shrink-0 items-center justify-center overflow-hidden rounded-full border border-gray-200 bg-white',
              entity.logo_url ? 'cursor-zoom-in' : 'cursor-pointer bg-primary/10',
            )}
            title={entity.logo_url ? 'View logo' : 'Add a logo'}
            onClick={() =>
              entity.logo_url ? preview(`${entity.brand_name} — Logo`, { url: entity.logo_url }) : setEditingProfile(true)
            }
          >
            {entity.logo_url ? (
              // A public product-images URL, printed by the till.
              // eslint-disable-next-line @next/next/no-img-element
              <img src={entity.logo_url} alt={entity.brand_name} className="h-full w-full object-contain p-2" />
            ) : (
              <span className="font-display text-2xl text-primary">{initials(entity.brand_name)}</span>
            )}
          </button>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-display text-xl tracking-wide text-primary">{entity.brand_name}</h1>
              <Badge variant={entity.is_active ? 'success' : 'neutral'}>{entity.is_active ? 'Active' : 'Inactive'}</Badge>
              <Badge variant={entity.vat_registered ? 'info' : 'neutral'}>
                {entity.vat_registered ? 'VAT-registered' : 'Not VAT-registered'}
              </Badge>
            </div>
            {facts.length > 0 && <p className="mt-1 text-sm text-gray-600">{facts.join(' · ')}</p>}
            <p className="text-xs text-gray-400">
              {[
                entity.tax_number && `TRN ${entity.tax_number}`,
                entity.trade_license_number && `Licence ${entity.trade_license_number}`,
              ]
                .filter(Boolean)
                .join(' · ') || entity.reference}
            </p>
          </div>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button variant="secondary" onClick={() => setEditingProfile(true)}>
            <span className="material-icons text-[16px]" aria-hidden>
              edit
            </span>
            Edit profile
          </Button>
        </div>
      </header>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div className="min-w-0 space-y-5">
          <PaperworkSummary
            states={states}
            onOpen={(key) => openCard(key)}
            onAdd={onStripAdd}
            emptyText="Nothing on file yet — start with the trade licence"
          />

          {/* Registration */}
          <section className="space-y-3">
            <GroupHeading
              action={
                <Button size="sm" variant="ghost" onClick={() =>
                    setDocForm({
                      type: (specs.find((s) => !currentDoc(s.doc_type))?.doc_type ??
                        specs[0]?.doc_type ??
                        'establishment_card') as EntityDocType,
                    })
                  }>
                  <span className="material-icons text-[16px]" aria-hidden>
                    note_add
                  </span>
                  Add document
                </Button>
              }
            >
              Registration
            </GroupHeading>
            <div className="grid items-start gap-4 xl:grid-cols-2">
              {currentLicense ? (
                <DocumentCard<LicenseCardDoc>
                  spec={TRADE_LICENSE_SPEC}
                  current={asCardDoc(currentLicense)}
                  history={licenses.filter((l) => !l.is_current).map(asCardDoc)}
                  highlighted={highlight === 'trade_license'}
                  onView={(doc) =>
                    preview(`${entity.legal_name} — Trade licence ${doc.row.license_number}`, () =>
                      legalEntitiesApi.tradeLicenseDocumentUrl(entity.id, doc.id),
                    )
                  }
                  onEdit={(doc) => setLicenseForm({ row: doc.row })}
                  onDelete={(doc) => void removeLicense(doc.row)}
                  onRenew={() => setLicenseForm({ seed: currentLicense })}
                  onCopy={(text) => void copy(text)}
                />
              ) : null}
              <article
                id="doc-vat_certificate"
                className={cn(
                  'scroll-mt-24 border border-gray-200 bg-white px-4 py-3 font-body transition-shadow',
                  highlight === 'vat_certificate' && 'ring-2 ring-primary/40',
                )}
              >
                <VatSection entity={entity} onSaved={() => void afterChange()} preview={preview} />
              </article>
              {registration.map((spec) => docCard(spec))}
            </div>
          </section>

          {/* Visas & labour, Premises — a group shows once something in it is on file. */}
          {otherGroups
            .filter((group) => inGroup(group).some((spec) => currentDoc(spec.doc_type)))
            .map((group) => (
              <section key={group} className="space-y-3">
                <GroupHeading>{group}</GroupHeading>
                <div className="grid items-start gap-4 xl:grid-cols-2">{inGroup(group).map((spec) => docCard(spec))}</div>
              </section>
            ))}

          <OtherFiles
            files={paper.files}
            onAdd={() => setFileForm({})}
            onView={(file) =>
              preview(`${entity.legal_name} — ${file.title}`, () => legalEntitiesApi.fileUrl(entity.id, file.id))
            }
            onEdit={(file) => setFileForm({ file })}
            onDelete={(file) => void removeFile(file)}
            emptyText="Power of attorney, UBO filing, insurance policies, contracts — anything else kept for this entity. Click to add the first."
          />
        </div>

        {/* Profile */}
        <aside className="min-w-0 space-y-3 lg:sticky lg:top-4 lg:self-start">
          <section className="border border-gray-200 bg-white p-4 font-body">
            <div className="flex items-center justify-between">
              <h2 className="font-display text-sm tracking-wide text-primary">Profile</h2>
              <button
                type="button"
                className="text-xs uppercase tracking-widest text-gray-500 hover:text-primary"
                onClick={() => setEditingProfile(true)}
              >
                Edit
              </button>
            </div>
            <dl className="mt-2 divide-y divide-gray-100 lg:space-y-2 lg:divide-y-0">
              <Field label="Legal name">{entity.legal_name}</Field>
              <Field label="Brand (receipt)">{entity.brand_name}</Field>
              <Field label="Reference">
                <span className="font-mono">{entity.reference}</span>
              </Field>
              <Field label="Invoice title">{entity.invoice_title}</Field>
              {entity.registered_address && (
                <Field label="Registered address">
                  <span className="whitespace-pre-line">{entity.registered_address}</span>
                </Field>
              )}
              {entity.bank_name && <Field label="Bank">{entity.bank_name}</Field>}
              {entity.bank_account_name && <Field label="Account name">{entity.bank_account_name}</Field>}
              {entity.iban && (
                <Field label="IBAN">
                  <button type="button" className="font-mono hover:text-primary" onClick={() => void copy(entity.iban!)}>
                    {entity.iban}
                  </button>
                </Field>
              )}
              {entity.swift_code && (
                <Field label="SWIFT">
                  <span className="font-mono">{entity.swift_code}</span>
                </Field>
              )}
              {entity.invoice_cc_emails?.length ? (
                <Field label="Invoice CC">{entity.invoice_cc_emails.join(', ')}</Field>
              ) : null}
            </dl>
          </section>

          <section className="border border-gray-200 bg-white p-4 font-body">
            <h2 className="font-display text-sm tracking-wide text-primary">Branches</h2>
            {tradingBranchIds.length === 0 ? (
              <p className="mt-2 text-xs text-gray-400">No branch channel trades under it.</p>
            ) : (
              <ul className="mt-2 space-y-1 text-sm">
                {branches
                  .filter((b) => tradingBranchIds.includes(b.id))
                  .map((b) => (
                    <li key={b.id} className="flex items-center justify-between gap-2">
                      <span className="text-gray-800">{b.name}</span>
                      {!b.is_active && <Badge>Inactive</Badge>}
                    </li>
                  ))}
              </ul>
            )}
            <Link href="/branches" className="mt-2 inline-block text-xs text-primary hover:underline">
              Assign on the Branches page
            </Link>
          </section>

          {staff && (
            <section className="border border-gray-200 bg-white p-4 font-body">
              <h2 className="font-display text-sm tracking-wide text-primary">
                Sponsored staff <span className="text-gray-400">({staff.length})</span>
              </h2>
              {staff.length === 0 ? (
                <p className="mt-2 text-xs text-gray-400">No active employee has this entity as sponsor.</p>
              ) : (
                <ul className="mt-2 space-y-1 text-sm">
                  {staff.map((s) => (
                    <li key={s.id}>
                      <Link href={`/employees/${s.id}`} className="text-gray-800 hover:text-primary hover:underline">
                        {s.full_name}
                      </Link>
                      {s.job_title && <span className="text-xs text-gray-400"> · {s.job_title}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          <button
            type="button"
            className="inline-flex items-center gap-1 px-1 text-xs text-gray-400 hover:text-red-600"
            onClick={() => void removeEntity()}
          >
            <span className="material-icons text-[16px]" aria-hidden>
              delete_outline
            </span>
            Delete legal entity
          </button>
        </aside>
      </div>

      {editingProfile && (
        <LegalEntityForm
          entity={entity}
          onClose={() => setEditingProfile(false)}
          onSaved={() => {
            setEditingProfile(false);
            void afterChange('Profile saved');
          }}
        />
      )}
      {docForm && (
        <EntityDocumentForm
          entity={entity}
          specs={specs}
          document={docForm.doc ?? null}
          seed={docForm.seed ?? null}
          initialType={docForm.type}
          onClose={() => setDocForm(null)}
          onSaved={() => {
            setDocForm(null);
            void afterChange('Document saved');
          }}
        />
      )}
      {licenseForm && (
        <Modal
          title={licenseForm.row ? 'Edit trade licence' : licenseForm.seed ? 'Renew trade licence' : 'Add trade licence'}
          onClose={() => setLicenseForm(null)}
          wide
        >
          <LicenseForm
            initial={licenseDraft(licenseForm.row, licenseForm.seed)}
            authorities={authorities}
            onSubmit={saveLicense}
            onCancel={() => setLicenseForm(null)}
            submitLabel="Save"
          />
        </Modal>
      )}
      {fileForm && (
        <FileForm
          api={fileApi}
          file={fileForm.file ?? null}
          onClose={() => setFileForm(null)}
          onSaved={() => {
            setFileForm(null);
            void afterChange('File saved');
          }}
        />
      )}
      {previewNode}
    </Page>
  );
}
