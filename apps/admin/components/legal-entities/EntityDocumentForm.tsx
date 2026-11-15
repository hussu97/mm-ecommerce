'use client';

import { useState } from 'react';
import { legalEntitiesApi } from '@/lib/pos-api';
import type {
  EntityDocType,
  EntityDocument,
  EntityDocumentTypeSpec,
  LegalEntity,
} from '@/lib/pos-types';
import { extractText } from '@/lib/ocr/extract';
import { parseEntityDocument } from '@/lib/ocr/entity';
import { ApiError } from '@/lib/api';
import { Button, Input, Select, Textarea } from '@/components/ui';
import { Modal } from '@/components/pos/ResourcePage';

/**
 * Add a cycle of one of a legal entity's registration documents (a first one
 * or a renewal) or edit one — the entity-side twin of the employee
 * `DocumentForm`.
 *
 * Picking a scan reads it in the browser and fills the number, dates and
 * issuer (see `lib/ocr/entity`); the super-admin checks each field before
 * saving. A document issued once (Corporate Tax, MoA)
 * has no expiry box. A renewal is seeded from the current cycle — the number,
 * issuer and fields usually carry over, only the dates change.
 */

const ACCEPT = 'image/jpeg,image/png,image/webp,application/pdf';

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : 'network error';
}

type Draft = {
  doc_type: EntityDocType;
  document_number: string;
  issue_date: string;
  expiry_date: string;
  issuing_authority: string;
  details: Record<string, string>;
  notes: string;
};

function draftFrom(
  type: EntityDocType,
  doc: EntityDocument | null,
  seed: EntityDocument | null,
): Draft {
  const from = doc ?? seed;
  return {
    doc_type: (doc?.doc_type as EntityDocType) ?? type,
    document_number: from?.document_number ?? '',
    issue_date: doc?.issue_date ?? '',
    expiry_date: doc?.expiry_date ?? '',
    issuing_authority: from?.issuing_authority ?? '',
    details: { ...(from?.details ?? {}) },
    notes: doc?.notes ?? '',
  };
}

/** Group the type picker the way the page groups its cards. */
function typeOptions(specs: EntityDocumentTypeSpec[]) {
  return specs.map((s) => ({ value: s.doc_type, label: `${s.group} — ${s.label}` }));
}

export function EntityDocumentForm({
  entity,
  specs,
  document,
  seed,
  initialType,
  onClose,
  onSaved,
}: {
  entity: LegalEntity;
  specs: EntityDocumentTypeSpec[];
  /** Editing this cycle; omit to add a new one. */
  document?: EntityDocument | null;
  /** Adding a renewal: the current cycle, to carry its number and fields over. */
  seed?: EntityDocument | null;
  initialType: EntityDocType;
  onClose: () => void;
  onSaved: () => void;
}) {
  const editing = Boolean(document);
  const [draft, setDraft] = useState<Draft>(() =>
    draftFrom(initialType, document ?? null, seed ?? null),
  );
  const [file, setFile] = useState<File | null>(null);
  const [reading, setReading] = useState<string | null>(null);
  const [scanNote, setScanNote] = useState<{ ok: boolean; text: string; warnings: string[] } | null>(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [createdId, setCreatedId] = useState<string | null>(null);

  const spec = specs.find((s) => s.doc_type === draft.doc_type);
  const expires = spec?.expires ?? true;

  function set<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  async function onFile(picked: File | null) {
    setFile(picked);
    setScanNote(null);
    setError('');
    if (!picked || editing) return;
    try {
      setReading('Reading the document…');
      const { text, source } = await extractText(picked, {
        onProgress: (stage, fraction) =>
          setReading(fraction !== undefined ? `${stage} ${Math.round(fraction * 100)}%` : stage),
      });
      const read = parseEntityDocument(draft.doc_type, text, expires);
      const found = Boolean(read.document_number || read.expiry_date || read.issue_date);
      setDraft((d) => ({
        ...d,
        document_number: read.document_number ?? d.document_number,
        issue_date: read.issue_date ?? d.issue_date,
        expiry_date: read.expiry_date ?? d.expiry_date,
        issuing_authority: read.issuing_authority ?? d.issuing_authority,
      }));
      setScanNote({
        ok: found,
        text: found
          ? `Filled from ${source === 'pdf-text' ? 'the PDF’s text' : 'the scan (OCR)'}. Check each field against the document before saving.`
          : 'Could not find the number or dates on this file — enter them by hand.',
        warnings: read.warnings,
      });
    } catch {
      setScanNote({ ok: false, text: 'Could not read this file — enter the details by hand.', warnings: [] });
    } finally {
      setReading(null);
    }
  }

  async function save() {
    if (expires && !draft.expiry_date) {
      setError('The expiry date is required.');
      return;
    }
    if (draft.issue_date && draft.expiry_date && draft.issue_date > draft.expiry_date) {
      setError('The issue date must be on or before the expiry date.');
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
      expiry_date: expires ? draft.expiry_date : null,
      issuing_authority: draft.issuing_authority.trim() || null,
      details,
      notes: draft.notes.trim() || null,
    };
    // Once the row exists (a first Save whose upload then failed), a retry
    // updates it instead of creating a duplicate cycle.
    const existingId = document?.id ?? createdId;
    let savedId = existingId;
    try {
      if (existingId) {
        await legalEntitiesApi.updateDocument(entity.id, existingId, body);
      } else {
        savedId = (await legalEntitiesApi.createDocument(entity.id, { doc_type: draft.doc_type, ...body })).id;
        setCreatedId(savedId);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the document.');
      setSaving(false);
      return;
    }
    if (file && savedId) {
      try {
        await legalEntitiesApi.uploadDocumentFile(entity.id, savedId, file);
      } catch (err) {
        setError(`The details are saved, but the scan did not upload (${errorText(err)}). Save again to retry the upload.`);
        setSaving(false);
        return;
      }
    }
    onSaved();
  }

  const title = `${editing ? 'Edit' : seed ? 'Renew' : 'Add'} ${spec?.label.toLowerCase() ?? 'document'}`;
  const authorityList = `entity-authorities-${draft.doc_type}`;

  return (
    <Modal title={title.charAt(0).toUpperCase() + title.slice(1)} onClose={onClose} wide>
      <div className="space-y-4 font-body text-sm">
        {!editing && !createdId && !seed && (
          <Select
            label="Document"
            value={draft.doc_type}
            onChange={(e) => {
              setScanNote(null);
              setFile(null);
              setDraft(draftFrom(e.target.value as EntityDocType, null, null));
            }}
            options={typeOptions(specs)}
          />
        )}
        {spec?.hint && <p className="text-xs text-gray-500">{spec.hint}</p>}


        <div className="rounded border border-dashed border-gray-300 bg-gray-50 p-3">
          <label className="block text-xs uppercase tracking-widest text-gray-500">
            {editing ? 'Replace the scan (optional)' : 'Scan or PDF — fills the form for you'}
          </label>
          <input
            type="file"
            accept={ACCEPT}
            className="mt-2 block w-full text-xs"
            data-testid="entity-document-file"
            onChange={(e) => void onFile(e.target.files?.[0] ?? null)}
          />
          {reading && <p className="mt-2 text-xs text-primary">{reading}</p>}
          {scanNote && (
            <div className="mt-2 space-y-1 text-xs">
              <p className={scanNote.ok ? 'text-green-700' : 'text-amber-700'}>{scanNote.text}</p>
              {scanNote.warnings.map((w) => (
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
            label={spec?.authority_label ?? 'Issuing authority'}
            value={draft.issuing_authority}
            list={authorityList}
            onChange={(e) => set('issuing_authority', e.target.value)}
          />
          <datalist id={authorityList}>
            {(spec?.authorities ?? []).map((a) => (
              <option key={a} value={a} />
            ))}
          </datalist>
          <Input
            label={expires ? 'Issue date' : 'Issue / effective date'}
            type="date"
            value={draft.issue_date}
            onChange={(e) => set('issue_date', e.target.value)}
          />
          {expires ? (
            <Input
              label="Expiry date *"
              type="date"
              value={draft.expiry_date}
              onChange={(e) => set('expiry_date', e.target.value)}
            />
          ) : (
            <p className="self-end pb-2 text-xs text-gray-500">Issued once — no expiry to track.</p>
          )}
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
