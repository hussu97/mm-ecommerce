'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import {
  employeesApi,
  type Employee,
  type EmployeeDocType,
  type EmployeeDocument,
  type EmployeeDocumentTypeSpec,
  type SignedDocumentUrl,
} from '@/lib/people-api';
import { branchesApi, legalEntitiesApi, staffApi } from '@/lib/pos-api';
import type { Branch, LegalEntity, Staff } from '@/lib/pos-types';
import { ApiError } from '@/lib/api';
import { Badge, Button, LoadError, Spinner } from '@/components/ui';
import { Page } from '@/components/ui/Page';
import { useConfirm, useToast } from '@/components/ui/feedback';
import { formatDate } from '@/components/pos/LegalEntityPaperwork';
import { DocumentForm } from '@/components/people/DocumentForm';
import { DocumentExpiry } from '@/components/people/DocumentExpiry';
import { EmployeeForm } from '@/components/people/EmployeeForm';
import { useExpiringDocuments } from '@/components/people/ExpiringDocuments';
import { useFilePreview } from '@/components/ui/FilePreview';

/**
 * One employee: profile, photo, and each document's full history — the cycle
 * expiring last is current, earlier ones stay on file with their scans.
 * `?add=<type>` (from the expiry popup) opens the add form for that type.
 */

function errorText(err: unknown, fallback: string) {
  return err instanceof ApiError ? err.message : fallback;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-widest text-gray-400">{label}</dt>
      <dd className="mt-0.5 text-sm text-gray-800">{children || '—'}</dd>
    </div>
  );
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
  const [form, setForm] = useState<{ type: EmployeeDocType; doc?: EmployeeDocument } | null>(null);
  const [busy, setBusy] = useState(false);
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
    employeesApi.documentTypes().then(setSpecs).catch(() => {});
    legalEntitiesApi.list().then(setEntities).catch(() => {});
    branchesApi.list().then(setBranches).catch(() => {});
    staffApi.list().then(setAccounts).catch(() => {});
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
        message: `Delete this ${label.toLowerCase()} (expiring ${formatDate(doc.expiry_date)}) and its scan? Only do this for a cycle entered by mistake.`,
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
  const viewFile = (doc: EmployeeDocument, label: string) =>
    preview(`${employee.full_name} — ${label}`, () => employeesApi.documentFileUrl(employee.id, doc.id));

  return (
    <Page>
      <Link href="/employees" className="text-xs font-body uppercase tracking-widest text-gray-500 hover:text-primary">
        ← Employees
      </Link>

      <header className="mt-3 mb-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="font-display text-xl text-primary tracking-wide">{employee.full_name}</h1>
          {employee.full_name_ar && (
            <p dir="rtl" className="text-sm text-gray-500 font-body text-left">
              {employee.full_name_ar}
            </p>
          )}
          <div className="mt-1 flex flex-wrap gap-2">
            <Badge variant={employee.status === 'active' ? 'success' : 'neutral'}>{employee.status}</Badge>
            {employee.job_title && <Badge variant="info">{employee.job_title}</Badge>}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => setEditingProfile(true)}>
            Edit profile
          </Button>
          <Button variant="danger" onClick={() => void removeEmployee()}>
            Delete
          </Button>
        </div>
      </header>

      <div className="grid gap-5 lg:grid-cols-[260px_1fr]">
        {/* Profile */}
        <section className="space-y-4 border border-gray-200 bg-white p-4 font-body">
          <div className="flex flex-col items-center gap-2">
            <div className="flex h-40 w-32 items-center justify-center overflow-hidden border border-gray-200 bg-gray-50">
              {photo ? (
                <button
                  type="button"
                  className="h-full w-full cursor-zoom-in"
                  title="View photo"
                  onClick={() => preview(`${employee.full_name} — Photo`, photo)}
                >
                  {/* A short-lived signed URL from the private bucket. */}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={photo.url} alt={employee.full_name} className="h-full w-full object-cover" />
                </button>
              ) : (
                <span className="material-icons text-5xl text-gray-300">person</span>
              )}
            </div>
            <div className="flex gap-3 text-xs uppercase tracking-widest">
              <label className="cursor-pointer text-primary hover:underline">
                {busy ? 'Uploading…' : employee.has_photo ? 'Replace photo' : 'Upload photo'}
                <input
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  className="hidden"
                  onChange={(e) => void uploadPhoto(e.target.files?.[0] ?? null)}
                />
              </label>
              {employee.has_photo && (
                <button className="text-red-600 hover:underline" onClick={() => void removePhoto()}>
                  Remove
                </button>
              )}
            </div>
          </div>
          <dl className="grid grid-cols-2 gap-3 lg:grid-cols-1">
            <Field label="Nationality">{employee.nationality}</Field>
            <Field label="Date of birth">{employee.date_of_birth && formatDate(employee.date_of_birth)}</Field>
            <Field label="Sex">{employee.gender === 'M' ? 'Male' : employee.gender === 'F' ? 'Female' : null}</Field>
            <Field label="Personal number">{employee.personal_number}</Field>
            <Field label="Sponsor">{entity?.legal_name}</Field>
            <Field label="Branch">{branch?.name}</Field>
            <Field label="Till / console account">
              {employee.user_display_name &&
                [employee.user_display_name, employee.user_email !== employee.user_display_name ? employee.user_email : null]
                  .filter(Boolean)
                  .join(' · ')}
            </Field>
            <Field label="Phone">{employee.phone}</Field>
            <Field label="Joined">{employee.joined_on && formatDate(employee.joined_on)}</Field>
            {employee.status === 'left' && (
              <Field label="Left">{employee.left_on && formatDate(employee.left_on)}</Field>
            )}
          </dl>
          {employee.notes && <p className="whitespace-pre-line text-xs text-gray-600">{employee.notes}</p>}
        </section>

        {/* Documents */}
        <div className="space-y-4">
          {specs.map((spec) => {
            const docs = employee.documents.filter((d) => d.doc_type === spec.doc_type);
            const current = docs.find((d) => d.is_current);
            const history = docs.filter((d) => !d.is_current);
            return (
              <section key={spec.doc_type} className="border border-gray-200 bg-white p-4 font-body" data-testid={`doc-${spec.doc_type}`}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="font-display text-sm text-primary tracking-wide">{spec.label}</h2>
                  <Button size="sm" onClick={() => setForm({ type: spec.doc_type as EmployeeDocType })}>
                    {current ? 'Add renewal' : `Add ${spec.label.toLowerCase()}`}
                  </Button>
                </div>
                {current ? (
                  <DocumentRow
                    doc={current}
                    spec={spec}
                    onView={() => viewFile(current, spec.label)}
                    onEdit={() => setForm({ type: spec.doc_type as EmployeeDocType, doc: current })}
                    onDelete={() => void removeDocument(current, spec.label)}
                    showDetails
                  />
                ) : (
                  <p className="mt-2 text-xs text-gray-400">Not on file.</p>
                )}
                {history.length > 0 && (
                  <details className="mt-3">
                    <summary className="cursor-pointer text-xs uppercase tracking-widest text-gray-500">
                      Previous ({history.length})
                    </summary>
                    <div className="mt-2 divide-y divide-gray-100">
                      {history.map((doc) => (
                        <DocumentRow
                          key={doc.id}
                          doc={doc}
                          spec={spec}
                          onView={() => viewFile(doc, spec.label)}
                          onEdit={() => setForm({ type: spec.doc_type as EmployeeDocType, doc })}
                          onDelete={() => void removeDocument(doc, spec.label)}
                        />
                      ))}
                    </div>
                  </details>
                )}
              </section>
            );
          })}
        </div>
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
      {previewNode}
    </Page>
  );
}

function DocumentRow({
  doc,
  spec,
  onView,
  onEdit,
  onDelete,
  showDetails = false,
}: {
  doc: EmployeeDocument;
  spec: EmployeeDocumentTypeSpec;
  onView: () => void;
  onEdit: () => void;
  onDelete: () => void;
  showDetails?: boolean;
}) {
  const details = Object.entries(spec.details).filter(([key]) => doc.details[key]);
  return (
    <div className="py-2">
      <div className="grid gap-3 sm:grid-cols-4">
        <Field label={spec.number_label}>{doc.document_number}</Field>
        <Field label="Issued">{doc.issue_date && formatDate(doc.issue_date)}</Field>
        <Field label="Expires">
          <DocumentExpiry expiry={doc.expiry_date} />
        </Field>
        <Field label="Authority">{doc.issuing_authority}</Field>
        {showDetails &&
          details.map(([key, label]) => (
            <Field key={key} label={label}>
              {/^\d{4}-\d{2}-\d{2}$/.test(doc.details[key]) ? formatDate(doc.details[key]) : doc.details[key]}
            </Field>
          ))}
      </div>
      {showDetails && doc.notes && <p className="mt-2 text-xs text-gray-600">{doc.notes}</p>}
      <div className="mt-2 flex flex-wrap gap-4 text-xs uppercase tracking-widest">
        {doc.has_file ? (
          <button className="text-primary hover:underline" onClick={onView}>
            View scan
          </button>
        ) : (
          <span className="text-gray-400">No scan</span>
        )}
        <button className="text-gray-600 hover:underline" onClick={onEdit}>
          Edit
        </button>
        <button className="text-red-600 hover:underline" onClick={onDelete}>
          Delete
        </button>
      </div>
    </div>
  );
}
