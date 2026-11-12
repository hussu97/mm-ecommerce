'use client';

import { useMemo, useState } from 'react';
import {
  employeesApi,
  type Employee,
  type EmployeeDocType,
  type EmployeeDocument,
  type EmployeeDocumentTypeSpec,
  type EmployeeUpdate,
} from '@/lib/people-api';
import type { LegalEntity } from '@/lib/pos-types';
import { combineImages, extractText } from '@/lib/ocr/extract';
import { parseDocument, type DocumentFields, type PersonFields } from '@/lib/ocr/parse';
import { ApiError } from '@/lib/api';
import { Button, Input, Select, Textarea } from '@/components/ui';
import { Modal } from '@/components/pos/ResourcePage';

/**
 * Add a document cycle (a first document or a renewal) or edit one.
 *
 * Picking a scan reads it right here in the browser (see `lib/ocr`) and fills
 * the form; the super-admin checks every field against the scan before saving.
 * A scan can also fill the employee's empty profile fields (name, nationality,
 * date of birth, personal number, sponsor), offered as a ticked checkbox.
 *
 * The Emirates ID is two-sided and the numbers worth checking (the MRZ) are on
 * the back, so for it the picker takes either one PDF/photo showing both sides
 * or two photos of the front and the back, which are combined into the one
 * scan that is read and stored.
 */

const ACCEPT = 'image/jpeg,image/png,image/webp,application/pdf';

/** Document types with a machine-readable zone worth an extra OCR pass. */
const MRZ_TYPES: ReadonlySet<EmployeeDocType> = new Set(['passport', 'emirates_id']);
/** Two-sided cards: two photos are combined, a scanned PDF's page 2 is read. */
const TWO_SIDED_TYPES: ReadonlySet<EmployeeDocType> = new Set(['emirates_id']);

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : 'network error';
}

type Draft = {
  doc_type: EmployeeDocType;
  document_number: string;
  issue_date: string;
  expiry_date: string;
  issuing_authority: string;
  details: Record<string, string>;
  notes: string;
};

function draftFrom(doc: EmployeeDocument | null, type: EmployeeDocType): Draft {
  return {
    doc_type: (doc?.doc_type as EmployeeDocType) ?? type,
    document_number: doc?.document_number ?? '',
    issue_date: doc?.issue_date ?? '',
    expiry_date: doc?.expiry_date ?? '',
    issuing_authority: doc?.issuing_authority ?? '',
    details: { ...(doc?.details ?? {}) },
    notes: doc?.notes ?? '',
  };
}

const PROFILE_LABELS: Record<keyof EmployeeUpdate, string> = {
  full_name: 'Name',
  full_name_ar: 'Arabic name',
  nationality: 'Nationality',
  date_of_birth: 'Date of birth',
  gender: 'Sex',
  job_title: 'Job title',
  personal_number: 'Personal number',
  legal_entity_id: 'Sponsor',
} as Record<keyof EmployeeUpdate, string>;

/** Match a printed establishment ("FATEMA CAKE SWEETS") to a legal entity. */
export function matchLegalEntity(
  printed: string | undefined,
  entities: LegalEntity[],
): LegalEntity | undefined {
  if (!printed) return undefined;
  const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, ' ').replace(/\b(llc|l l c|fze|fzc|est)\b/g, '').trim();
  const want = norm(printed);
  return entities.find((e) => {
    const a = norm(e.legal_name);
    const b = norm(e.brand_name);
    return a === want || b === want || (want.length > 5 && (a.startsWith(want) || want.startsWith(a)));
  });
}

/** The scan's person fields that would fill an empty profile field. */
export function profileFill(
  employee: Employee,
  person: PersonFields,
  entities: LegalEntity[],
): EmployeeUpdate {
  const fill: EmployeeUpdate = {};
  const empty = (v: unknown) => v === null || v === undefined || v === '';
  if (person.full_name_ar && empty(employee.full_name_ar)) fill.full_name_ar = person.full_name_ar;
  if (person.nationality && empty(employee.nationality)) fill.nationality = person.nationality;
  if (person.date_of_birth && empty(employee.date_of_birth)) fill.date_of_birth = person.date_of_birth;
  if (person.gender && empty(employee.gender)) fill.gender = person.gender;
  if (person.job_title && empty(employee.job_title)) fill.job_title = person.job_title;
  if (person.personal_number && empty(employee.personal_number)) fill.personal_number = person.personal_number;
  const entity = matchLegalEntity(person.establishment, entities);
  if (entity && empty(employee.legal_entity_id)) fill.legal_entity_id = entity.id;
  return fill;
}

export function DocumentForm({
  employee,
  specs,
  entities,
  document,
  initialType,
  onClose,
  onSaved,
}: {
  employee: Employee;
  specs: EmployeeDocumentTypeSpec[];
  entities: LegalEntity[];
  /** Editing this cycle; omit to add a new one. */
  document?: EmployeeDocument | null;
  initialType: EmployeeDocType;
  onClose: () => void;
  onSaved: () => void;
}) {
  const editing = Boolean(document);
  const [draft, setDraft] = useState<Draft>(() => draftFrom(document ?? null, initialType));
  const [file, setFile] = useState<File | null>(null);
  const [reading, setReading] = useState<string | null>(null);
  const [scan, setScan] = useState<{ fields: DocumentFields; source: string } | null>(null);
  const [applyProfile, setApplyProfile] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [createdId, setCreatedId] = useState<string | null>(null);

  const spec = specs.find((s) => s.doc_type === draft.doc_type);
  const fill = useMemo(
    () => (scan ? profileFill(employee, scan.fields.person, entities) : {}),
    [scan, employee, entities],
  );
  const fillLabels = Object.keys(fill).map((k) => PROFILE_LABELS[k as keyof EmployeeUpdate] ?? k);

  function set<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  const twoSided = TWO_SIDED_TYPES.has(draft.doc_type);

  async function onFiles(list: FileList | null) {
    const files = Array.from(list ?? []);
    setFile(null);
    setScan(null);
    setError('');
    if (files.length > 2 || (files.length === 2 && !twoSided)) {
      setError(twoSided ? 'Pick one file, or two photos: the front and the back.' : 'Pick one file.');
      return;
    }
    let picked: File | null = files[0] ?? null;
    if (files.length === 2) {
      try {
        picked = await combineImages(files, `${draft.doc_type}-front-and-back.jpg`);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Could not combine the two photos.');
        return;
      }
    }
    setFile(picked);
    if (!picked || editing) return;
    try {
      setReading('Reading the document…');
      const { text, source } = await extractText(picked, {
        mrz: MRZ_TYPES.has(draft.doc_type),
        twoSided,
        onProgress: (stage, fraction) =>
          setReading(fraction !== undefined ? `${stage} ${Math.round(fraction * 100)}%` : stage),
      });
      const fields = parseDocument(draft.doc_type, text, { knownName: employee.full_name });
      setScan({ fields, source: source === 'pdf-text' ? 'the PDF’s text' : 'the scan (OCR)' });
      setDraft((d) => ({
        ...d,
        document_number: fields.document_number ?? d.document_number,
        issue_date: fields.issue_date ?? d.issue_date,
        expiry_date: fields.expiry_date ?? d.expiry_date,
        issuing_authority: fields.issuing_authority ?? d.issuing_authority,
        details: { ...d.details, ...fields.details },
      }));
    } catch (err) {
      setError(err instanceof Error ? `Could not read the document: ${err.message}` : 'Could not read the document.');
    } finally {
      setReading(null);
    }
  }

  async function save() {
    if (!draft.expiry_date) {
      setError('The expiry date is required.');
      return;
    }
    setSaving(true);
    setError('');
    const allowed = Object.keys(spec?.details ?? {});
    const details = Object.fromEntries(
      Object.entries(draft.details).filter(([k, v]) => allowed.includes(k) && v.trim()),
    );
    const body = {
      document_number: draft.document_number.trim() || null,
      issue_date: draft.issue_date || null,
      expiry_date: draft.expiry_date,
      issuing_authority: draft.issuing_authority.trim() || null,
      details,
      notes: draft.notes.trim() || null,
    };
    // Once the row exists (a first Save whose upload then failed), a retry
    // updates that row instead of creating a duplicate cycle.
    const existingId = document?.id ?? createdId;
    let savedId = existingId;
    try {
      if (existingId) {
        await employeesApi.updateDocument(employee.id, existingId, body);
      } else {
        savedId = (await employeesApi.createDocument(employee.id, { doc_type: draft.doc_type, ...body })).id;
        setCreatedId(savedId);
        if (applyProfile && Object.keys(fill).length > 0) {
          await employeesApi.update(employee.id, fill);
        }
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the document.');
      setSaving(false);
      return;
    }
    if (file && savedId) {
      try {
        await employeesApi.uploadDocumentFile(employee.id, savedId, file);
      } catch (err) {
        setError(
          `The details are saved, but the scan did not upload (${errorText(err)}). Save again to retry the upload.`,
        );
        setSaving(false);
        return;
      }
    }
    onSaved();
  }

  const title = editing
    ? `Edit ${spec?.label ?? 'document'}`
    : `Add ${spec?.label ?? 'document'}`;

  return (
    <Modal title={title} onClose={onClose} wide>
      <div className="space-y-4 font-body text-sm">
        {!editing && !createdId && (
          <Select
            label="Document"
            value={draft.doc_type}
            onChange={(e) => {
              setScan(null);
              setFile(null);
              setDraft(draftFrom(null, e.target.value as EmployeeDocType));
            }}
            options={specs.map((s) => ({ value: s.doc_type, label: s.label }))}
          />
        )}

        <div className="rounded border border-dashed border-gray-300 bg-gray-50 p-3">
          <label className="block text-xs uppercase tracking-widest text-gray-500">
            {editing ? 'Replace the scan (optional)' : 'Scan or PDF — fills the form for you'}
          </label>
          {twoSided && (
            <p className="mt-1 text-xs text-gray-500">
              Both sides: one PDF or photo showing the front and the back, or pick two photos together.
            </p>
          )}
          <input
            type="file"
            accept={ACCEPT}
            multiple={twoSided}
            className="mt-2 block w-full text-xs"
            data-testid="document-file"
            onChange={(e) => void onFiles(e.target.files)}
          />
          {reading && <p className="mt-2 text-xs text-primary">{reading}</p>}
          {scan && (
            <div className="mt-2 space-y-1 text-xs">
              <p className="text-green-700">
                Filled from {scan.source}. Check each field against the document before saving.
              </p>
              {scan.fields.warnings.map((w) => (
                <p key={w} className="text-amber-700">
                  {w}
                </p>
              ))}
            </div>
          )}
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <Input
            label={spec?.number_label ?? 'Number'}
            value={draft.document_number}
            onChange={(e) => set('document_number', e.target.value)}
          />
          <Input
            label="Issuing authority"
            value={draft.issuing_authority}
            onChange={(e) => set('issuing_authority', e.target.value)}
          />
          <Input
            label="Issue date"
            type="date"
            value={draft.issue_date}
            onChange={(e) => set('issue_date', e.target.value)}
          />
          <Input
            label="Expiry date *"
            type="date"
            value={draft.expiry_date}
            onChange={(e) => set('expiry_date', e.target.value)}
          />
          {Object.entries(spec?.details ?? {}).map(([key, label]) => (
            <Input
              key={key}
              label={label}
              value={draft.details[key] ?? ''}
              onChange={(e) => set('details', { ...draft.details, [key]: e.target.value })}
            />
          ))}
        </div>
        <Textarea label="Notes" rows={2} value={draft.notes} onChange={(e) => set('notes', e.target.value)} />

        {!editing && !createdId && fillLabels.length > 0 && (
          <label className="flex items-start gap-2 text-xs text-gray-600">
            <input
              type="checkbox"
              checked={applyProfile}
              onChange={(e) => setApplyProfile(e.target.checked)}
              className="mt-0.5"
            />
            <span>Also fill the empty profile fields from this scan: {fillLabels.join(', ')}.</span>
          </label>
        )}

        {error && <p className="text-xs text-red-600">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={() => void save()} loading={saving} disabled={Boolean(reading)}>
            Save
          </Button>
        </div>
      </div>
    </Modal>
  );
}
