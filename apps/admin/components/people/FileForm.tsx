'use client';

import { useEffect, useRef, useState } from 'react';
import { employeesApi, type Employee, type EmployeeFile } from '@/lib/people-api';
import { extractText } from '@/lib/ocr/extract';
import { suggestFromText, titleFromFilename } from '@/lib/ocr/classify';
import { ApiError } from '@/lib/api';
import { Button, Input, Textarea } from '@/components/ui';
import { Modal } from '@/components/pos/ResourcePage';

/**
 * Add or edit one of an employee's "other" files — a contract, a certificate,
 * a letter. No fixed fields: a name and a free-text type, both filled in for
 * the super-admin when a file is picked (the name from the document's title
 * line or the file's own name, the type from what the text says it is — see
 * `lib/ocr/classify`) and never over anything they have typed. The type box
 * offers the types already in use, so a kind of paper keeps one spelling.
 */

const ACCEPT = 'image/jpeg,image/png,image/webp,application/pdf';

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : 'network error';
}

export function FileForm({
  employee,
  file: existing,
  onClose,
  onSaved,
}: {
  employee: Employee;
  /** Editing this file; omit to add a new one. */
  file?: EmployeeFile | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const editing = Boolean(existing);
  const [title, setTitle] = useState(existing?.title ?? '');
  const [category, setCategory] = useState(existing?.category ?? '');
  const [notes, setNotes] = useState(existing?.notes ?? '');
  // A field the super-admin has typed in is theirs: suggestions stop there.
  // A ref, so a suggestion arriving after a slow OCR read sees the latest.
  const typed = useRef({ title: editing, category: editing });
  const [picked, setPicked] = useState<File | null>(null);
  const [reading, setReading] = useState<string | null>(null);
  const [suggested, setSuggested] = useState(false);
  const [categories, setCategories] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [createdId, setCreatedId] = useState<string | null>(null);

  useEffect(() => {
    employeesApi.fileCategories().then(setCategories).catch(() => {});
  }, []);

  async function onFile(file: File | null) {
    setPicked(file);
    setSuggested(false);
    setError('');
    if (!file) return;
    if (!typed.current.title) setTitle(titleFromFilename(file.name) ?? '');
    if (editing) return;
    try {
      setReading('Reading the document…');
      const { text } = await extractText(file, {
        onProgress: (stage, fraction) =>
          setReading(fraction !== undefined ? `${stage} ${Math.round(fraction * 100)}%` : stage),
      });
      const hint = suggestFromText(text, file.name);
      if (!typed.current.title && hint.title) setTitle(hint.title);
      if (!typed.current.category && hint.category) setCategory(hint.category);
      setSuggested(Boolean(hint.category));
    } catch {
      // Unreadable is fine here: the file name stands and the type is typed.
    } finally {
      setReading(null);
    }
  }

  async function save() {
    if (!title.trim()) {
      setError('A name is required.');
      return;
    }
    if (!editing && !picked && !createdId) {
      setError('Pick the file to upload.');
      return;
    }
    setSaving(true);
    setError('');
    const body = {
      title: title.trim(),
      category: category.trim() || null,
      notes: notes.trim() || null,
      ...(picked ? { original_filename: picked.name.slice(0, 255) } : {}),
    };
    // Once the row exists (a first Save whose upload then failed), a retry
    // updates it instead of adding a second one.
    const existingId = existing?.id ?? createdId;
    let savedId = existingId;
    try {
      if (existingId) {
        await employeesApi.updateFile(employee.id, existingId, body);
      } else {
        savedId = (await employeesApi.createFile(employee.id, body)).id;
        setCreatedId(savedId);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save.');
      setSaving(false);
      return;
    }
    if (picked && savedId) {
      try {
        await employeesApi.uploadFile(employee.id, savedId, picked);
      } catch (err) {
        setError(`The details are saved, but the file did not upload (${errorText(err)}). Save again to retry.`);
        setSaving(false);
        return;
      }
    }
    onSaved();
  }

  return (
    <Modal title={editing ? 'Edit file' : 'Add a file'} onClose={onClose} wide>
      <div className="space-y-4 font-body text-sm">
        <div className="rounded border border-dashed border-gray-300 bg-gray-50 p-3">
          <label className="block text-xs uppercase tracking-widest text-gray-500">
            {editing ? 'Replace the file (optional)' : 'File — PDF or photo'}
          </label>
          <input
            type="file"
            accept={ACCEPT}
            className="mt-2 block w-full text-xs"
            data-testid="employee-file"
            onChange={(e) => void onFile(e.target.files?.[0] ?? null)}
          />
          {reading && <p className="mt-2 text-xs text-primary">{reading}</p>}
          {suggested && (
            <p className="mt-2 text-xs text-green-700">Name and type suggested from the document — change them freely.</p>
          )}
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <Input
            label="Name *"
            value={title}
            maxLength={200}
            onChange={(e) => {
              setTitle(e.target.value);
              typed.current.title = true;
            }}
          />
          <Input
            label="Type"
            value={category}
            maxLength={80}
            list="employee-file-categories"
            placeholder="e.g. Employment contract"
            onChange={(e) => {
              setCategory(e.target.value);
              typed.current.category = true;
            }}
          />
          <datalist id="employee-file-categories">
            {categories.map((c) => (
              <option key={c} value={c} />
            ))}
          </datalist>
        </div>
        <Textarea label="Notes" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />

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
