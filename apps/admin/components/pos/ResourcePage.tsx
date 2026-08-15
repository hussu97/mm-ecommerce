'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ApiError } from '@/lib/api';
import { Badge, Button, Input, Pagination, Select, Spinner } from '@/components/ui';
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

export type FieldType = 'text' | 'number' | 'select' | 'checkbox' | 'textarea';

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
}

export interface ColumnDef<T> {
  header: string;
  /** Cell renderer. Return a string, number, or any node. */
  render: (row: T) => React.ReactNode;
  className?: string;
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
  paginated = false,
}: ResourcePageProps<T>) {
  const [rows, setRows] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);

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
    if (!search.trim() || searchKeys.length === 0) return rows;
    const needle = search.trim().toLowerCase();
    return rows.filter((row) =>
      searchKeys.some((key) => String(row[key] ?? '').toLowerCase().includes(needle)),
    );
  }, [rows, search, searchKeys]);

  // A search that shrinks the result set below the current page would show an
  // empty table with rows still there — land back on the first page instead.
  useEffect(() => {
    setPage(1);
  }, [search]);

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
    <div className="p-6 max-w-[1400px]">
      <header className="flex flex-wrap items-start justify-between gap-3 mb-5">
        <div>
          <h1 className="font-display text-xl text-primary tracking-wide">{title}</h1>
          {description && <p className="text-xs text-gray-500 font-body mt-1">{description}</p>}
        </div>
        <div className="flex items-center gap-2">
          {toolbar}
          {searchKeys.length > 0 && (
            <Input
              placeholder="Search…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-48"
            />
          )}
          {create && <Button onClick={openCreate}>New</Button>}
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
      ) : visible.length === 0 ? (
        <p className="py-16 text-center text-sm text-gray-400 font-body">{emptyMessage}</p>
      ) : (
        <div className="overflow-x-auto rounded border border-gray-200 bg-white">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 bg-gray-50">
                {columns.map((c) => (
                  <th
                    key={c.header}
                    className={cn(
                      'px-3 py-2 text-left text-[11px] uppercase tracking-widest text-gray-500 font-body',
                      c.className,
                    )}
                  >
                    {c.header}
                  </th>
                ))}
                {(update || remove || rowActions) && <th className="px-3 py-2 w-40" />}
              </tr>
            </thead>
            <tbody>
              {pageRows.map((row) => (
                <tr key={row.id} className="border-b border-gray-100 last:border-0 hover:bg-gray-50">
                  {columns.map((c) => (
                    <td key={c.header} className={cn('px-3 py-2 align-middle', c.className)}>
                      {c.render(row)}
                    </td>
                  ))}
                  {(update || remove || rowActions) && (
                    <td className="px-3 py-2 text-right whitespace-nowrap">
                      <div className="flex items-center justify-end gap-2">
                        {rowActions?.(row, reload)}
                        {update && (
                          <button
                            onClick={() => openEdit(row)}
                            className="text-xs text-primary hover:underline font-body"
                          >
                            Edit
                          </button>
                        )}
                        {remove && (
                          <button
                            onClick={() => setConfirmingDelete(row)}
                            className="text-xs text-red-500 hover:underline font-body"
                          >
                            Delete
                          </button>
                        )}
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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
      type={field.type === 'number' ? 'number' : 'text'}
      step={field.step}
      value={value === undefined || value === null ? '' : String(value)}
      onChange={(e) => onChange(e.target.value)}
      placeholder={field.placeholder}
      helper={field.helper}
    />
  );
}

export function StatusBadge({ active }: { active: boolean }) {
  return <Badge variant={active ? 'success' : 'neutral'}>{active ? 'Active' : 'Inactive'}</Badge>;
}

// `money()` used to live here, printing "1,234.50 AED" while the rest of the
// console printed "AED 1234.50". There is one formatter now — `formatCurrency`
// in `lib/utils.ts` — and the POS screens accept its shape.
