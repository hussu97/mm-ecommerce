'use client';

import { useCallback, useState } from 'react';
import { redirectApi, ApiError } from '@/lib/api';
import type { UrlRedirect } from '@/lib/types';
import { Button, Input, Pagination, Select, TabBar, LoadError, Spinner } from '@/components/ui';
import { DataTable } from '@/components/ui/DataTable';
import { useConfirm, useToast } from '@/components/ui/feedback';
import { useApiList } from '@/hooks/useApiList';
import { formatDateTime } from '@/lib/utils';

/**
 * Where old URLs are kept alive.
 *
 * Most rows here write themselves: renaming a category in Catalogue leaves a
 * rule behind so every link to the old address — and every product underneath
 * it — keeps working. This screen is where you see that happen, and where you
 * add the ones nothing else knows about: a URL from the old Wix site, an
 * address printed on a card, a page that moved for editorial reasons.
 *
 * The storefront reads these through a cached feed, so a new rule takes up to a
 * minute to start firing. That is worth knowing before you test one.
 */

const STATUS_OPTIONS = [
  { value: '308', label: '308 — Permanent (recommended)' },
  { value: '301', label: '301 — Permanent (legacy clients)' },
];

interface FormState {
  from_path: string;
  to_path: string;
  is_prefix: boolean;
  status_code: '301' | '308';
  is_active: boolean;
  note: string;
}

const EMPTY_FORM: FormState = {
  from_path: '',
  to_path: '',
  is_prefix: false,
  status_code: '308',
  is_active: true,
  note: '',
};

export default function RedirectsPage() {
  const toast = useToast();
  const confirm = useConfirm();
  const [activeTab, setActiveTab] = useState<'active' | 'inactive'>('active');
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<UrlRedirect | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');

  // Client-side pagination: `/redirects` returns the whole table in one call.
  // It is a list of URLs that have moved — tens of rows, not thousands — and
  // paging it on the server would cost a round trip to save nothing.
  const fetchRedirects = useCallback(() => redirectApi.list(), []);
  const {
    items: redirects, page, perPage, setPage, setPerPage,
    loading, loadError, refetch,
  } = useApiList<UrlRedirect>({ paginate: 'client', fetch: fetchRedirects });

  const filtered = redirects.filter(r => (activeTab === 'active' ? r.is_active : !r.is_active));
  const pages = Math.max(1, Math.ceil(filtered.length / perPage));
  const paginated = filtered.slice((page - 1) * perPage, page * perPage);
  const activeCount = redirects.filter(r => r.is_active).length;
  const inactiveCount = redirects.filter(r => !r.is_active).length;

  function openCreate() {
    setEditing(null);
    setForm(EMPTY_FORM);
    setFormError('');
    setShowForm(true);
  }

  function openEdit(row: UrlRedirect) {
    setEditing(row);
    setForm({
      from_path: row.from_path,
      to_path: row.to_path,
      is_prefix: row.is_prefix,
      status_code: String(row.status_code) as '301' | '308',
      is_active: row.is_active,
      note: row.note ?? '',
    });
    setFormError('');
    setShowForm(true);
  }

  function closeForm() {
    setShowForm(false);
    setEditing(null);
    setFormError('');
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError('');
    setSaving(true);
    const payload = {
      from_path: form.from_path.trim(),
      to_path: form.to_path.trim(),
      is_prefix: form.is_prefix,
      status_code: Number(form.status_code),
      is_active: form.is_active,
      note: form.note.trim() || null,
    };
    try {
      if (editing) {
        await redirectApi.update(editing.id, payload);
        toast.success('Redirect saved');
      } else {
        await redirectApi.create(payload);
        toast.success('Redirect added');
      }
      closeForm();
      refetch();
    } catch (err) {
      // The service refuses loops and duplicates with a sentence explaining
      // which, and that sentence is more useful than "something went wrong".
      setFormError(err instanceof ApiError ? err.message : 'Could not save the redirect');
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(row: UrlRedirect) {
    const ok = await confirm({
      title: 'Delete this redirect?',
      message: `Anyone following ${row.from_path} will get a 404 instead of ${row.to_path}. If the old address is still printed or linked anywhere, deactivate it instead so you can turn it back on.`,
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await redirectApi.delete(row.id);
      toast.success('Redirect deleted');
      refetch();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not delete the redirect');
    }
  }

  return (
    <div>
      <LoadError message={loadError} onRetry={refetch} />

      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-display text-2xl text-gray-800">Redirects</h1>
          <p className="text-xs text-gray-400 font-body mt-0.5">
            {redirects.length} total · changes reach the storefront within a minute
          </p>
        </div>
        {!showForm && (
          <Button onClick={openCreate}>
            <span className="material-icons text-[14px]">add</span>
            New Redirect
          </Button>
        )}
      </div>

      {showForm && (
        <form onSubmit={handleSubmit} className="border border-gray-200 bg-white p-4 mb-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Input
              label="Old path"
              placeholder="/cat-brownies"
              value={form.from_path}
              onChange={e => setForm(f => ({ ...f, from_path: e.target.value }))}
              required
            />
            <Input
              label="New path"
              placeholder="/brownies"
              value={form.to_path}
              onChange={e => setForm(f => ({ ...f, to_path: e.target.value }))}
              required
            />
          </div>

          <p className="font-body text-xs text-gray-500 mt-3">
            Leave the language out and the rule covers both — <code>/brownies</code> works for
            English and Arabic. Write <code>/en/…</code> only when the rule really is about one
            language.
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
            <Select
              label="Status"
              value={form.status_code}
              onChange={e => setForm(f => ({ ...f, status_code: e.target.value as '301' | '308' }))}
              options={STATUS_OPTIONS}
            />
            <Input
              label="Note (optional)"
              placeholder="Why this URL moved"
              value={form.note}
              onChange={e => setForm(f => ({ ...f, note: e.target.value }))}
            />
          </div>

          <label className="flex items-start gap-3 mt-4">
            <input
              type="checkbox"
              className="mt-1"
              checked={form.is_prefix}
              onChange={e => setForm(f => ({ ...f, is_prefix: e.target.checked }))}
            />
            <span>
              <span className="block font-body text-sm text-gray-800">
                Move everything underneath too
              </span>
              <span className="block font-body text-xs text-gray-500">
                Sends <code>/old/anything</code> to <code>/new/anything</code> as well as the page
                itself. This is what a renamed category needs, because its products live below it.
                Leave it off for a one-page move — on, it quietly swallows every URL beneath.
              </span>
            </span>
          </label>

          <label className="flex items-center gap-3 mt-3">
            <input
              type="checkbox"
              checked={form.is_active}
              onChange={e => setForm(f => ({ ...f, is_active: e.target.checked }))}
            />
            <span className="font-body text-sm text-gray-800">Active</span>
          </label>

          {formError && <p className="text-xs text-red-500 mt-3">{formError}</p>}
          <div className="flex gap-2 mt-4">
            <Button type="submit" loading={saving}>
              {editing ? 'Save Changes' : 'Create'}
            </Button>
            <Button type="button" variant="ghost" onClick={closeForm}>Cancel</Button>
          </div>
        </form>
      )}

      <TabBar
        tabs={[
          { key: 'active', label: 'Active', count: activeCount },
          { key: 'inactive', label: 'Inactive', count: inactiveCount },
        ]}
        active={activeTab}
        onChange={key => { setActiveTab(key as 'active' | 'inactive'); setPage(1); }}
      />

      {loading ? (
        <div className="flex justify-center py-16"><Spinner /></div>
      ) : (
        <DataTable<UrlRedirect>
          rows={paginated}
          rowKey={r => r.id}
          empty={
            <p className="py-16 text-center text-sm text-gray-400 font-body">
              {activeTab === 'active'
                ? 'No redirects yet. Renaming a category adds one here by itself.'
                : 'Nothing retired.'}
            </p>
          }
          columns={[
            {
              header: 'Old path',
              priority: 'primary',
              render: r => (
                <span className="font-mono text-xs text-gray-800">
                  {r.from_path}
                  {/* The wildcard suffix a prefix rule implies, written the way
                      an operator would recognise it. Braced because bare `/*`
                      in JSX children reads as the start of a comment. */}
                  {r.is_prefix && <span className="text-gray-400">{'/*'}</span>}
                </span>
              ),
            },
            {
              header: 'Goes to',
              priority: 'secondary',
              render: r => <span className="font-mono text-xs text-gray-600">{r.to_path}</span>,
            },
            {
              header: 'Status',
              render: r => <span className="font-body text-xs text-gray-500">{r.status_code}</span>,
            },
            {
              header: 'Added by',
              render: r => (
                <span className="font-body text-xs text-gray-500">
                  {r.source === 'category_rename'
                    ? 'Category rename'
                    : r.source === 'seed'
                      ? 'Set up with the shop'
                      : 'By hand'}
                </span>
              ),
            },
            {
              // The only question anyone asks of a redirect a year later. A row
              // with no hits since it was written is one you can retire; one
              // that fired this morning is still carrying somebody's bookmark.
              header: 'Used',
              render: r => (
                <span className="font-body text-xs text-gray-500">
                  {r.hit_count === 0
                    ? 'never'
                    : `${r.hit_count}× · ${r.last_hit_at ? formatDateTime(r.last_hit_at) : '—'}`}
                </span>
              ),
            },
          ]}
          actions={r => (
            <div className="flex items-center justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={() => openEdit(r)}>Edit</Button>
              <Button size="sm" variant="ghost" onClick={() => handleDelete(r)}>Delete</Button>
            </div>
          )}
        />
      )}

      <Pagination
        page={page}
        pages={pages}
        total={filtered.length}
        perPage={perPage}
        onPageChange={setPage}
        onPerPageChange={p => { setPerPage(p); setPage(1); }}
        label="redirects"
      />
    </div>
  );
}
