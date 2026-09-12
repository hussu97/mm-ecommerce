'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ApiError, uploadsApi } from '@/lib/api';
import { Badge, Button, Input, Pagination, Select, Spinner } from '@/components/ui';
import {
  DataTable,
  RowAction,
  sortByAccessor,
  sortKeyOf,
  type ColumnPriority,
  type SortState,
} from '@/components/ui/DataTable';
import { cn } from '@/lib/utils';

/**
 * A configuration-driven CRUD screen.
 *
 * The POS side adds roughly a dozen admin-managed lists (taxes, payment methods,
 * charges, reasons, tags, suppliers, …) that are all "table plus modal form".
 * Describing them as data keeps each page to a few dozen lines and guarantees
 * they behave identically — same empty state, same error handling, same
 * confirm-before-delete.
 */

export type FieldType =
  | 'text'
  | 'number'
  | 'password'
  | 'select'
  | 'checkbox'
  | 'textarea'
  | 'image';

export interface FieldDef {
  /**
   * Plain string rather than `keyof T` — forms legitimately carry write-only
   * fields (password, pin, branch_ids) that never appear on the response type.
   */
  name: string;
  label: string;
  type?: FieldType;
  options?: Array<{ value: string; label: string }>;
  placeholder?: string;
  helper?: string;
  required?: boolean;
  /** Hide from the create form (e.g. server-assigned values). */
  createOnly?: boolean;
  editOnly?: boolean;
  step?: string;
  /** For `type: 'image'` — the GCS folder to upload into (default `logos`). */
  folder?: string;
}

export interface ColumnDef<T> {
  header: string;
  /** Cell renderer. Return a string, number, or any node. */
  render: (row: T) => React.ReactNode;
  className?: string;
  /**
   * How this column behaves on a phone, where the row is drawn as a card
   * rather than as a table row. See `components/ui/DataTable`.
   *
   * Left unset it is a labelled line in the card body, which is always
   * correct and never surprising. Naming one column `primary` is what turns
   * a card from a list of labels into a thing with a title, and is worth
   * doing on every list somebody reads on a phone.
   */
  priority?: ColumnPriority;
  /** Make the header a sort control (↑/↓). The whole filtered list is ordered
   *  by `sortAccessor` before pagination, so it sorts the resource, not a page. */
  sortable?: boolean;
  /** Sort-state identity; defaults to `header`. */
  sortKey?: string;
  /** The comparable value for sorting — number (numeric) or string (locale);
   *  null/undefined sort last. */
  sortAccessor?: (row: T) => string | number | null | undefined;
}

export interface ResourcePageProps<T extends { id: string }> {
  title: string;
  description?: string;
  columns: ColumnDef<T>[];
  fields: FieldDef[];
  load: () => Promise<T[]>;
  create?: (data: Record<string, unknown>) => Promise<T>;
  update?: (id: string, data: Record<string, unknown>) => Promise<T>;
  remove?: (id: string) => Promise<void>;
  /** Seed values for a brand-new record. */
  defaults?: Record<string, unknown>;
  searchKeys?: Array<keyof T & string>;
  emptyMessage?: string;
  /** Extra controls rendered next to the "New" button. */
  toolbar?: React.ReactNode;
  rowActions?: (row: T, reload: () => void) => React.ReactNode;
  /** Contextual detail kept with a selected row (for example, that item's recipe). */
  belowTable?: React.ReactNode;
  /**
   * Detail revealed directly beneath a specific row (its recipe, say). Return
   * `null` for a row with nothing open. Preferred over `belowTable` when the
   * detail belongs to one row: it opens where the eye already is, not at the
   * bottom of a long list.
   */
  expandedRow?: (row: T, reload: () => void) => React.ReactNode;
  /** Page-specific client-side filters, composed with the standard text search. */
  filterRows?: (row: T) => boolean;
  /** Page-specific stable ordering applied after filters and before pagination. */
  sortRows?: (rows: T[]) => T[];
  /**
   * Client-side pagination via the shared `Pagination` component (`load` still
   * fetches everything in one call). Off by default — most of these lists are
   * a dozen configuration rows — switch it on for the unbounded ones.
   */
  paginated?: boolean;
}

export function ResourcePage<T extends { id: string }>({
  title,
  description,
  columns,
  fields,
  load,
  create,
  update,
  remove,
  defaults = {},
  searchKeys = [],
  emptyMessage = 'Nothing here yet.',
  toolbar,
  rowActions,
  belowTable,
  expandedRow,
  filterRows,
  sortRows,
  paginated = false,
}: ResourcePageProps<T>) {
  const [rows, setRows] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);
  // Column-header sort. When a sortable column is active it orders the whole
  // filtered list (before pagination slices a page); otherwise the page's own
  // `sortRows` ordering stands.
  const [sort, setSort] = useState<SortState | null>(null);

  const [editing, setEditing] = useState<T | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');
  const [confirmingDelete, setConfirmingDelete] = useState<T | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setRows(await load());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `Failed to load ${title.toLowerCase()}.`);
    } finally {
      setLoading(false);
    }
  }, [load, title]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const filtered = rows.filter((row) => {
      const matchesSearch = !needle || searchKeys.length === 0 || searchKeys.some(
        (key) => String(row[key] ?? '').toLowerCase().includes(needle),
      );
      return matchesSearch && (filterRows?.(row) ?? true);
    });
    const activeColumn = sort
      ? columns.find((c) => sortKeyOf(c) === sort.key)
      : undefined;
    if (sort && activeColumn?.sortAccessor) {
      return sortByAccessor(filtered, activeColumn.sortAccessor, sort.direction);
    }
    return sortRows ? sortRows(filtered) : filtered;
  }, [columns, filterRows, rows, search, searchKeys, sort, sortRows]);

  // A search that shrinks the result set below the current page would show an
  // empty table with rows still there — land back on the first page instead.
  useEffect(() => {
    setPage(1);
  }, [filterRows, search, sortRows, sort]);

  const pages = Math.max(1, Math.ceil(visible.length / perPage));
  const pageRows = paginated ? visible.slice((page - 1) * perPage, page * perPage) : visible;

  function openCreate() {
    setForm({ ...defaults });
    setFormError('');
    setCreating(true);
    setEditing(null);
  }

  function openEdit(row: T) {
    const seed: Record<string, unknown> = {};
    fields.forEach((f) => {
      seed[f.name] = (row as Record<string, unknown>)[f.name];
    });
    setForm(seed);
    setFormError('');
    setEditing(row);
    setCreating(false);
  }

  function closeForm() {
    setEditing(null);
    setCreating(false);
    setFormError('');
  }

  async function submit() {
    const missing = fields
      .filter((f) => f.required && !f.editOnly)
      .filter((f) => form[f.name] === undefined || form[f.name] === '' || form[f.name] === null);
    if (missing.length > 0) {
      setFormError(`${missing.map((f) => f.label).join(', ')} required`);
      return;
    }

    setSaving(true);
    setFormError('');
    try {
      // Only send the fields this form declares, so a partial edit never
      // clobbers a column the page does not know about.
      const payload: Record<string, unknown> = {};
      fields.forEach((f) => {
        if (creating && f.editOnly) return;
        if (editing && f.createOnly) return;
        payload[f.name] = f.type === 'number' && form[f.name] !== '' && form[f.name] !== undefined
          ? Number(form[f.name])
          : form[f.name];
      });

      if (editing && update) await update(editing.id, payload);
      else if (create) await create(payload);
      closeForm();
      await reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : 'Save failed.');
    } finally {
      setSaving(false);
    }
  }

  async function confirmDelete() {
    if (!confirmingDelete || !remove) return;
    setSaving(true);
    try {
      await remove(confirmingDelete.id);
      setConfirmingDelete(null);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Delete failed.');
      setConfirmingDelete(null);
    } finally {
      setSaving(false);
    }
  }

  const formOpen = creating || editing !== null;
  const activeFields = fields.filter((f) => (creating ? !f.editOnly : !f.createOnly));

  return (
    // No padding of its own: the dashboard shell already sets the page gutter,
    // and setting it twice cost 96px of a 390px screen.
    <div className="max-w-[1400px]">
      <header className="mb-5 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="font-display text-xl text-primary tracking-wide">{title}</h1>
          {description && <p className="text-xs text-gray-500 font-body mt-1">{description}</p>}
        </div>
        {/* Search takes the width it can get on a phone and a fixed 12rem on a
            desktop; "New" stays beside it rather than dropping to its own line,
            because the pair is one thought. */}
        <div className="flex items-center gap-2">
          {toolbar}
          {searchKeys.length > 0 && (
            <Input
              placeholder="Search…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full sm:w-48"
            />
          )}
          {create && <Button className="shrink-0" onClick={openCreate}>New</Button>}
        </div>
      </header>

      {error && (
        <div className="mb-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : (
        <DataTable<T>
          columns={columns}
          rows={pageRows}
          rowKey={(row) => row.id}
          sort={sort}
          onSortChange={setSort}
          expanded={expandedRow ? (row) => expandedRow(row, reload) : undefined}
          empty={
            <p className="py-16 text-center text-sm text-gray-400 font-body">{emptyMessage}</p>
          }
          actions={
            update || remove || rowActions
              ? (row) => (
                  <>
                    {rowActions?.(row, reload)}
                    {update && <RowAction onClick={() => openEdit(row)}>Edit</RowAction>}
                    {remove && (
                      <RowAction danger onClick={() => setConfirmingDelete(row)}>
                        Delete
                      </RowAction>
                    )}
                  </>
                )
              : undefined
          }
        />
      )}

      {paginated && !loading && visible.length > 0 && (
        <Pagination
          page={page}
          pages={pages}
          total={visible.length}
          perPage={perPage}
          onPageChange={setPage}
          onPerPageChange={setPerPage}
          label={title.toLowerCase()}
        />
      )}

      {belowTable}

      {formOpen && (
        <Modal title={editing ? `Edit ${title.replace(/s$/, '')}` : `New ${title.replace(/s$/, '')}`} onClose={closeForm}>
          <div className="space-y-3">
            {activeFields.map((f) => (
              <FormField
                key={f.name}
                field={f}
                value={form[f.name]}
                onChange={(v) => setForm((prev) => ({ ...prev, [f.name]: v }))}
              />
            ))}
            {formError && <p className="text-xs text-red-600 font-body">{formError}</p>}
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="secondary" onClick={closeForm} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={submit} loading={saving}>
              Save
            </Button>
          </div>
        </Modal>
      )}

      {confirmingDelete && (
        <Modal title="Confirm delete" onClose={() => setConfirmingDelete(null)}>
          <p className="text-sm font-body text-gray-600">
            Delete this record? Anything already referencing it keeps its historical copy.
          </p>
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setConfirmingDelete(null)} disabled={saving}>
              Cancel
            </Button>
            <Button variant="danger" onClick={confirmDelete} loading={saving}>
              Delete
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}

// ─── Building blocks ──────────────────────────────────────────────────────────

export function Modal({
  title,
  children,
  onClose,
  wide,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div
        className={cn(
          'w-full rounded-lg bg-white p-5 shadow-xl max-h-[90vh] overflow-y-auto',
          wide ? 'max-w-3xl' : 'max-w-md',
        )}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-display text-base text-primary">{title}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700">
            <span className="material-icons text-[20px]">close</span>
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function FormField({
  field,
  value,
  onChange,
}: {
  field: FieldDef;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  if (field.type === 'checkbox') {
    return (
      <label className="flex items-center gap-2 text-sm font-body text-gray-700">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
          className="h-4 w-4 accent-primary"
        />
        <span>{field.label}</span>
        {field.helper && <span className="text-xs text-gray-400">— {field.helper}</span>}
      </label>
    );
  }

  if (field.type === 'select') {
    return (
      <Select
        label={field.label}
        value={String(value ?? '')}
        onChange={(e) => onChange(e.target.value)}
        options={field.options ?? []}
        placeholder="Choose…"
      />
    );
  }

  if (field.type === 'image') {
    return <ImageField field={field} value={value} onChange={onChange} />;
  }

  if (field.type === 'textarea') {
    return (
      <label className="block">
        <span className="mb-1 block text-xs uppercase tracking-widest text-gray-500 font-body">
          {field.label}
        </span>
        <textarea
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.placeholder}
          rows={3}
          className="w-full rounded border border-gray-300 px-3 py-2 text-sm font-body focus:border-primary focus:outline-none"
        />
      </label>
    );
  }

  return (
    <Input
      label={field.label}
      // A password or PIN must be masked, and never offered up by the browser's
      // saved-password autofill for a different account (F-ADM-10).
      type={field.type === 'number' ? 'number' : field.type === 'password' ? 'password' : 'text'}
      autoComplete={field.type === 'password' ? 'new-password' : undefined}
      step={field.step}
      value={value === undefined || value === null ? '' : String(value)}
      onChange={(e) => onChange(e.target.value)}
      placeholder={field.placeholder}
      helper={field.helper}
    />
  );
}

/**
 * An inline image field: shows the current image, and an Upload button that
 * sends the chosen file to GCS (via `/uploads/image`) and stores the returned
 * public URL. The URL is what is saved on the record, so nothing else changes —
 * a receipt/report reads the same `logo_url` whether it was pasted or uploaded.
 */
function ImageField({
  field,
  value,
  onChange,
}: {
  field: FieldDef;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const url = value === undefined || value === null ? '' : String(value);

  const upload = async (file: File | undefined) => {
    if (!file) return;
    setUploading(true);
    setError('');
    try {
      const res = await uploadsApi.uploadImage(file, field.folder ?? 'logos');
      onChange(res.url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  return (
    <div>
      <span className="mb-1 block text-xs uppercase tracking-widest text-gray-500 font-body">
        {field.label}
      </span>
      <div className="flex items-center gap-3">
        {url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={url}
            alt=""
            className="h-12 w-12 rounded border border-gray-200 object-contain bg-white"
          />
        ) : (
          <div className="h-12 w-12 rounded border border-dashed border-gray-300" />
        )}
        <label className="cursor-pointer rounded border border-gray-300 px-3 py-1.5 text-xs font-body text-gray-700 hover:bg-gray-50">
          {uploading ? 'Uploading…' : url ? 'Replace' : 'Upload'}
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="hidden"
            disabled={uploading}
            onChange={(e) => void upload(e.target.files?.[0])}
          />
        </label>
        {url && !uploading && (
          <button
            type="button"
            onClick={() => onChange(null)}
            className="text-xs font-body text-gray-400 hover:text-red-600"
          >
            Remove
          </button>
        )}
      </div>
      {url && <p className="mt-1 text-[11px] font-body text-gray-400 break-all">{url}</p>}
      {error && <p className="mt-1 text-xs font-body text-red-600">{error}</p>}
      {field.helper && (
        <p className="mt-1 text-[11px] font-body text-gray-400">{field.helper}</p>
      )}
    </div>
  );
}

export function StatusBadge({ active }: { active: boolean }) {
  return <Badge variant={active ? 'success' : 'neutral'}>{active ? 'Active' : 'Inactive'}</Badge>;
}

// `money()` used to live here, printing "1,234.50 AED" while the rest of the
// console printed "AED 1234.50". There is one formatter now — `formatCurrency`
// in `lib/utils.ts` — and the POS screens accept its shape.
