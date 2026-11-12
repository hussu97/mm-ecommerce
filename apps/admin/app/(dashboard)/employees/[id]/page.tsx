'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import {
  employeesApi,
  type Employee,
  type EmployeeDocType,
  type EmployeeDocument,
  type EmployeeDocumentTypeSpec,
  type EmployeeFile,
  type SignedDocumentUrl,
} from '@/lib/people-api';
import { branchesApi, legalEntitiesApi, staffApi } from '@/lib/pos-api';
import type { Branch, LegalEntity, Staff } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button, LoadError, Spinner } from '@/components/ui';
import { cn } from '@/lib/utils';
import { Page } from '@/components/ui/Page';
import { useConfirm, useToast } from '@/components/ui/feedback';
import { formatDate } from '@/components/pos/LegalEntityPaperwork';
import { DocumentForm } from '@/components/people/DocumentForm';
import { DocumentCard, OtherFiles, PaperworkSummary, type TypeState } from '@/components/people/EmployeePaperwork';
import { EmployeeForm } from '@/components/people/EmployeeForm';
import { FileForm } from '@/components/people/FileForm';
import { useExpiringDocuments } from '@/components/people/ExpiringDocuments';
import { useFilePreview } from '@/components/ui/FilePreview';

/**
 * One employee, laid out to answer "is their paperwork in order?" first:
 *
 * - a header with the photo, name and the facts most asked for;
 * - a status strip, one tile per document type (valid / due / expired /
 *   missing — the expiry popup's thresholds), each jumping to its card or,
 *   when missing, straight to the add form;
 * - a compact card per document on file: number (copyable), expiry, issue,
 *   authority up front, type-specific fields behind "Details", earlier cycles
 *   behind "N previous". Renew is the filled button only when it is due;
 * - no empty card for a type not on file: its tile in the strip adds it;
 * - other files, searchable once there are more than a handful;
 * - the profile beside it all on a desktop, after it on a phone.
 *
 * `?add=<type>` (from the expiry popup) opens the add form for that type.
 */

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

function initials(name: string): string {
  const words = name.trim().split(/\s+/);
  return ((words[0]?.[0] ?? '') + (words.length > 1 ? words[words.length - 1][0] : '')).toUpperCase();
}

/** "4 yrs 8 mos" since a date, for the header. */
function tenure(since: string): string | null {
  const [y, m] = since.split('-').map(Number);
  const now = new Date();
  let months = (now.getFullYear() - y) * 12 + (now.getMonth() + 1 - m);
  if (months < 0) return null;
  const years = Math.floor(months / 12);
  months %= 12;
  const parts = [years && `${years} yr${years === 1 ? '' : 's'}`, months && `${months} mo${months === 1 ? '' : 's'}`];
  return parts.filter(Boolean).join(' ') || 'less than a month';
}

export default function EmployeePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const toast = useToast();
  const confirm = useConfirm();
  const { refresh: refreshWarnings } = useExpiringDocuments();

  const [employee, setEmployee] = useState<Employee | null>(null);
  const [specs, setSpecs] = useState<EmployeeDocumentTypeSpec[]>([]);
  const [entities, setEntities] = useState<LegalEntity[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [accounts, setAccounts] = useState<Staff[]>([]);
  const [photo, setPhoto] = useState<SignedDocumentUrl | null>(null);
  const [loadError, setLoadError] = useState('');
  const [editingProfile, setEditingProfile] = useState(false);
  const [form, setForm] = useState<{
    type: EmployeeDocType;
    doc?: EmployeeDocument;
  } | null>(null);
  const [fileForm, setFileForm] = useState<{ file?: EmployeeFile } | null>(null);
  const [busy, setBusy] = useState(false);
  const [highlight, setHighlight] = useState<string | null>(null);
  const photoInput = useRef<HTMLInputElement>(null);
  const { preview, node: previewNode } = useFilePreview();

  const load = useCallback(async () => {
    setLoadError('');
    try {
      const e = await employeesApi.get(id);
      setEmployee(e);
      setPhoto(null);
      if (e.has_photo) {
        employeesApi
          .photoUrl(e.id)
          .then(setPhoto)
          .catch(() => setPhoto(null));
      }
    } catch (err) {
      setLoadError(errorText(err, 'Could not load the employee.'));
    }
  }, [id]);

  useEffect(() => {
    void load();
    employeesApi
      .documentTypes()
      .then(setSpecs)
      .catch(() => {});
    legalEntitiesApi
      .list()
      .then(setEntities)
      .catch(() => {});
    branchesApi
      .list()
      .then(setBranches)
      .catch(() => {});
    staffApi
      .list()
      .then(setAccounts)
      .catch(() => {});
  }, [load]);

  // `?add=visa` — arriving from the expiry popup.
  useEffect(() => {
    if (!employee || specs.length === 0) return;
    const add = new URLSearchParams(window.location.search).get('add');
    if (add && specs.some((s) => s.doc_type === add)) {
      setForm({ type: add as EmployeeDocType });
      router.replace(`/employees/${employee.id}`);
    }
  }, [employee, specs, router]);

  async function afterChange(message?: string) {
    await load();
    refreshWarnings();
    if (message) toast.success(message);
  }

  async function uploadPhoto(file: File | null) {
    if (!file || !employee) return;
    setBusy(true);
    try {
      await employeesApi.uploadPhoto(employee.id, file);
      await afterChange('Photo saved');
    } catch (err) {
      toast.error(errorText(err, 'Could not upload the photo.'));
    } finally {
      setBusy(false);
    }
  }

  async function removePhoto() {
    if (!employee || !(await confirm({ message: 'Remove this photo?', danger: true }))) return;
    try {
      await employeesApi.removePhoto(employee.id);
      await afterChange('Photo removed');
    } catch (err) {
      toast.error(errorText(err, 'Could not remove the photo.'));
    }
  }

  async function removeDocument(doc: EmployeeDocument, label: string) {
    if (
      !employee ||
      !(await confirm({
        message: `Delete this ${label.toLowerCase()} (expiring ${formatDate(doc.expiry_date)}) and its scan? Only do this for a record entered by mistake — to renew, add the new one instead and this stays in its history.`,
        danger: true,
      }))
    )
      return;
    try {
      await employeesApi.removeDocument(employee.id, doc.id);
      await afterChange('Document deleted');
    } catch (err) {
      toast.error(errorText(err, 'Could not delete the document.'));
    }
  }

  async function removeFile(file: EmployeeFile) {
    if (
      !employee ||
      !(await confirm({
        message: `Delete “${file.title}” and its file?`,
        danger: true,
      }))
    )
      return;
    try {
      await employeesApi.removeFile(employee.id, file.id);
      await afterChange('File deleted');
    } catch (err) {
      toast.error(errorText(err, 'Could not delete the file.'));
    }
  }

  function openCard(docType: string) {
    document.getElementById(`doc-${docType}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setHighlight(docType);
    window.setTimeout(() => setHighlight((h) => (h === docType ? null : h)), 1600);
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast.success('Copied');
    } catch {
      toast.error('Could not copy — select the number instead.');
    }
  }

  async function removeEmployee() {
    if (
      !employee ||
      !(await confirm({
        message: `Delete ${employee.full_name} with every document and scan? To keep the history of someone who has left, edit them and set the status to Left instead.`,
        danger: true,
      }))
    )
      return;
    try {
      await employeesApi.remove(employee.id);
      refreshWarnings();
      toast.success('Employee deleted');
      router.push('/employees');
    } catch (err) {
      toast.error(errorText(err, 'Could not delete the employee.'));
    }
  }

  if (loadError) return <LoadError message={loadError} onRetry={() => void load()} />;
  if (!employee) {
    return (
      <div className="flex justify-center py-12">
        <Spinner />
      </div>
    );
  }

  const entity = entities.find((e) => e.id === employee.legal_entity_id);
  const branch = branches.find((b) => b.id === employee.branch_id);
  const states: TypeState[] = specs.map((spec) => ({
    spec,
    current: employee.documents.find((d) => d.doc_type === spec.doc_type && d.is_current),
  }));
  const viewDoc = (doc: EmployeeDocument, label: string) =>
    preview(`${employee.full_name} — ${label}`, () => employeesApi.documentFileUrl(employee.id, doc.id));
  const since = employee.joined_on ? tenure(employee.joined_on) : null;
  const facts = [employee.job_title, entity?.legal_name, branch?.name].filter(Boolean);
  const account = employee.user_display_name
    ? [employee.user_display_name, employee.user_email !== employee.user_display_name ? employee.user_email : null]
        .filter(Boolean)
        .join(' · ')
    : null;

  return (
    <Page>
      <Link
        href="/employees"
        className="inline-flex items-center gap-1 text-xs font-body uppercase tracking-widest text-gray-500 hover:text-primary"
      >
        <span className="material-icons text-[16px]" aria-hidden>arrow_back</span>
        Employees
      </Link>

      {/* Header */}
      <header className="mt-3 mb-5 flex flex-col gap-4 border border-gray-200 bg-white p-4 font-body sm:flex-row sm:items-center">
        <div className="flex min-w-0 flex-1 items-center gap-4">
          <div className="relative shrink-0">
            <button
              type="button"
              className={cn(
                'flex h-20 w-20 items-center justify-center overflow-hidden rounded-full border border-gray-200 bg-primary/10',
                photo ? 'cursor-zoom-in' : 'cursor-pointer',
              )}
              title={photo ? 'View photo' : 'Upload a photo'}
              onClick={() => (photo ? preview(`${employee.full_name} — Photo`, photo) : photoInput.current?.click())}
            >
              {photo ? (
                // A short-lived signed URL from the private bucket.
                // eslint-disable-next-line @next/next/no-img-element
                <img src={photo.url} alt={employee.full_name} className="h-full w-full object-cover" />
              ) : (
                <span className="font-display text-2xl text-primary">{initials(employee.full_name)}</span>
              )}
            </button>
            <button
              type="button"
              title={employee.has_photo ? 'Replace photo' : 'Upload photo'}
              aria-label={employee.has_photo ? 'Replace photo' : 'Upload photo'}
              disabled={busy}
              onClick={() => photoInput.current?.click()}
              className="absolute -bottom-1 -right-1 flex h-8 w-8 items-center justify-center rounded-full border border-gray-200 bg-white text-gray-500 shadow-sm hover:text-primary"
            >
              <span className="material-icons text-[16px]" aria-hidden>{busy ? 'hourglass_empty' : 'photo_camera'}</span>
            </button>
            <input
              ref={photoInput}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              className="hidden"
              onChange={(e) => {
                void uploadPhoto(e.target.files?.[0] ?? null);
                e.target.value = '';
              }}
            />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-display text-xl tracking-wide text-primary">{employee.full_name}</h1>
              <Badge variant={employee.status === 'active' ? 'success' : 'neutral'}>
                {employee.status === 'active' ? 'Active' : 'Left'}
              </Badge>
            </div>
            {employee.full_name_ar && (
              <p dir="rtl" className="text-left text-sm text-gray-500">
                {employee.full_name_ar}
              </p>
            )}
            {facts.length > 0 && <p className="mt-1 text-sm text-gray-600">{facts.join(' · ')}</p>}
            {since && employee.status === 'active' && (
              <p className="text-xs text-gray-400">
                Joined {formatDate(employee.joined_on)} · {since}
              </p>
            )}
          </div>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button variant="secondary" onClick={() => setEditingProfile(true)}>
            <span className="material-icons text-[16px]" aria-hidden>edit</span>
            Edit profile
          </Button>
        </div>
      </header>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_280px]">
        {/* Paperwork */}
        <div className="min-w-0 space-y-5">
          {specs.length > 0 && (
            <PaperworkSummary
              states={states}
              onOpen={openCard}
              onAdd={(docType) => setForm({ type: docType as EmployeeDocType })}
            />
          )}

          <div className="grid items-start gap-4 xl:grid-cols-2">
            {states
              .filter((s) => s.current)
              .map(({ spec, current }) => (
                <DocumentCard
                  key={spec.doc_type}
                  spec={spec}
                  current={current!}
                  history={employee.documents.filter((d) => d.doc_type === spec.doc_type && !d.is_current)}
                  highlighted={highlight === spec.doc_type}
                  onView={(doc) => viewDoc(doc, spec.label)}
                  onEdit={(doc) => setForm({ type: spec.doc_type as EmployeeDocType, doc })}
                  onDelete={(doc) => void removeDocument(doc, spec.label)}
                  onRenew={() => setForm({ type: spec.doc_type as EmployeeDocType })}
                  onCopy={(text) => void copy(text)}
                />
              ))}
          </div>

          <OtherFiles
            files={employee.files}
            onAdd={() => setFileForm({})}
            onView={(file) =>
              preview(`${employee.full_name} — ${file.title}`, () => employeesApi.fileUrl(employee.id, file.id))
            }
            onEdit={(file) => setFileForm({ file })}
            onDelete={(file) => void removeFile(file)}
          />
        </div>

        {/* Profile */}
        <aside className="min-w-0 lg:sticky lg:top-4 lg:self-start">
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
              {employee.nationality && <Field label="Nationality">{employee.nationality}</Field>}
              {employee.date_of_birth && <Field label="Date of birth">{formatDate(employee.date_of_birth)}</Field>}
              {employee.gender && <Field label="Sex">{employee.gender === 'M' ? 'Male' : 'Female'}</Field>}
              {employee.personal_number && (
                <Field label="Personal number">
                  <span className="font-mono">{employee.personal_number}</span>
                </Field>
              )}
              {employee.phone && (
                <Field label="Phone">
                  <a href={`tel:${employee.phone.replace(/\s+/g, '')}`} className="hover:text-primary hover:underline">
                    {employee.phone}
                  </a>
                </Field>
              )}
              {entity && <Field label="Sponsor">{entity.legal_name}</Field>}
              {branch && <Field label="Branch">{branch.name}</Field>}
              {account && <Field label="Till / console account">{account}</Field>}
              {employee.joined_on && <Field label="Joined">{formatDate(employee.joined_on)}</Field>}
              {employee.status === 'left' && employee.left_on && (
                <Field label="Left">{formatDate(employee.left_on)}</Field>
              )}
            </dl>
            {!employee.nationality && !employee.date_of_birth && !employee.phone && (
              <p className="mt-2 text-xs text-gray-400">
                Most of this fills itself in from the first passport or visa scan.
              </p>
            )}
            {employee.notes && (
              <p className="mt-3 whitespace-pre-line bg-gray-50 px-3 py-2 text-xs text-gray-600">{employee.notes}</p>
            )}
            {employee.has_photo && (
              <button
                type="button"
                className="mt-4 block text-xs text-gray-500 hover:text-red-600 hover:underline"
                onClick={() => void removePhoto()}
              >
                Remove photo
              </button>
            )}
          </section>
          <button
            type="button"
            className="mt-3 inline-flex items-center gap-1 px-1 text-xs text-gray-400 hover:text-red-600"
            onClick={() => void removeEmployee()}
          >
            <span className="material-icons text-[16px]" aria-hidden>delete_outline</span>
            Delete employee
          </button>
        </aside>
      </div>

      {editingProfile && (
        <EmployeeForm
          employee={employee}
          entities={entities}
          branches={branches}
          accounts={accounts}
          onClose={() => setEditingProfile(false)}
          onSaved={() => {
            setEditingProfile(false);
            void afterChange('Profile saved');
          }}
        />
      )}
      {form && (
        <DocumentForm
          employee={employee}
          specs={specs}
          entities={entities}
          document={form.doc ?? null}
          initialType={form.type}
          onClose={() => setForm(null)}
          onSaved={() => {
            setForm(null);
            void afterChange('Document saved');
          }}
        />
      )}
      {fileForm && (
        <FileForm
          employee={employee}
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
