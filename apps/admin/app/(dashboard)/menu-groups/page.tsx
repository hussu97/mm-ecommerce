'use client';

/**
 * The menus.
 *
 * There is one tree per shop — the menu that branch's terminals render — and one
 * integrator tree, the menu pushed to the marketplaces. Groups nest, and a
 * product reaches a till (or a marketplace) through the tree rather than through
 * the website's category taxonomy. Switching a group off hides everything
 * beneath it, which is why the tree is shown as a tree: the blast radius of that
 * toggle has to be visible before you click it.
 *
 * The integrator tree is exactly two levels — a category, then its items — the
 * shape Foodics' Grubtech menu maps onto; the console blocks a third.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { menuGroupsApi, productsApi, uploadsApi, ApiError } from '@/lib/api';
import type { MenuGroupNode } from '@/lib/api';
import type { Product } from '@/lib/types';
import { branchesApi } from '@/lib/pos-api';
import type { Branch } from '@/lib/pos-types';
import { Button, Input } from '@/components/ui';
import { RowAction } from '@/components/ui/DataTable';
import { useConfirm } from '@/components/ui/feedback';

const BLANK = {
  name: '',
  name_localized: '',
  reference: '',
  image_url: '' as string,
  parent_id: null as string | null,
  is_active: true,
};

/** The API caps `per_page` at 2000, so one request covers today's catalogue. */
const CATALOGUE_PAGE_SIZE = 2000;

const INTEGRATOR_REFERENCE = 'integrator-root';

/** Flattened, depth-annotated, for the parent picker. */
function flatten(nodes: MenuGroupNode[], depth = 0): { node: MenuGroupNode; depth: number }[] {
  return nodes.flatMap(node => [{ node, depth }, ...flatten(node.children, depth + 1)]);
}

/** Ids of a group and everything under it — never valid as its own parent. */
function subtreeIds(node: MenuGroupNode): string[] {
  return [node.id, ...node.children.flatMap(subtreeIds)];
}

export default function MenuGroupsPage() {
  const confirm = useConfirm();
  const [tree, setTree] = useState<MenuGroupNode[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedRootId, setSelectedRootId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<MenuGroupNode | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(BLANK);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [productSearch, setProductSearch] = useState('');
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [apiError, setApiError] = useState('');
  const [loadError, setLoadError] = useState('');

  const loadEveryProduct = useCallback(async () => {
    const all: Product[] = [];
    let page = 1;
    for (;;) {
      const chunk = await productsApi.list({
        include_inactive: true,
        per_page: CATALOGUE_PAGE_SIZE,
        page,
      });
      all.push(...chunk.items);
      if (page >= chunk.pages) return all;
      page += 1;
    }
  }, []);

  const load = useCallback(async () => {
    setLoadError('');
    try {
      const [groups, catalogue, branchList] = await Promise.all([
        menuGroupsApi.tree({ includeInactive: true }),
        loadEveryProduct(),
        branchesApi.list(),
      ]);
      setTree(groups);
      setProducts(catalogue);
      setBranches(branchList);
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : 'Could not load the menus');
    } finally {
      setLoading(false);
    }
  }, [loadEveryProduct]);

  useEffect(() => { load(); }, [load]);

  const branchRoots = useMemo(
    () => tree.filter(n => n.root_kind === 'branch'),
    [tree],
  );
  const integratorRoot = useMemo(
    () => tree.find(n => n.root_kind === 'integrator') ?? null,
    [tree],
  );
  const branchName = useCallback(
    (id?: string | null) => branches.find(b => b.id === id)?.name ?? 'Branch',
    [branches],
  );

  // Keep a valid selection: default to the first branch menu, then the integrator.
  const selectedRoot = useMemo(
    () =>
      tree.find(n => n.id === selectedRootId) ?? branchRoots[0] ?? integratorRoot ?? null,
    [tree, selectedRootId, branchRoots, integratorRoot],
  );
  const isIntegrator = selectedRoot?.root_kind === 'integrator';

  const branchesWithoutMenu = useMemo(
    () =>
      branches.filter(
        b => b.is_active && !branchRoots.some(r => r.branch_id === b.id),
      ),
    [branches, branchRoots],
  );

  const rows = selectedRoot?.children ?? [];
  const flat = useMemo(
    () => (selectedRoot ? flatten(selectedRoot.children) : []),
    [selectedRoot],
  );

  function openAdd(parent: MenuGroupNode | null) {
    if (!selectedRoot) return;
    setEditing(null);
    // A new group at the top of a tree sits directly under its root.
    setForm({ ...BLANK, parent_id: parent?.id ?? selectedRoot.id });
    setPicked(new Set());
    setApiError('');
    setProductSearch('');
    setShowForm(true);
  }

  function openEdit(node: MenuGroupNode) {
    setEditing(node);
    setForm({
      name: node.name,
      name_localized: node.name_localized ?? '',
      reference: node.reference ?? '',
      image_url: node.image_url ?? '',
      parent_id: node.parent_id ?? null,
      is_active: node.is_active,
    });
    setPicked(new Set(node.product_ids));
    setApiError('');
    setProductSearch('');
    setShowForm(true);
  }

  async function uploadGroupImage(file: File) {
    setUploading(true);
    setApiError('');
    try {
      const { url } = await uploadsApi.uploadImage(file, 'menu-groups');
      setForm(f => ({ ...f, image_url: url }));
    } catch (e) {
      setApiError(e instanceof ApiError ? e.message : 'Could not upload the image');
    } finally {
      setUploading(false);
    }
  }

  async function save() {
    setSaving(true);
    setApiError('');
    try {
      const payload = {
        name: form.name.trim(),
        name_localized: form.name_localized.trim() || null,
        reference: form.reference.trim() || null,
        image_url: form.image_url.trim() || null,
        parent_id: form.parent_id,
        is_active: form.is_active,
        product_ids: [...picked],
      };
      if (editing) await menuGroupsApi.update(editing.id, payload);
      else await menuGroupsApi.create(payload);
      setShowForm(false);
      await load();
    } catch (e) {
      setApiError(e instanceof ApiError ? e.message : 'Could not save the group');
    } finally {
      setSaving(false);
    }
  }

  async function createBranchMenu(branchId: string) {
    try {
      const root = await menuGroupsApi.create({
        name: 'Menu',
        root_kind: 'branch',
        branch_id: branchId,
      });
      await load();
      setSelectedRootId(root.id);
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : 'Could not create the menu');
    }
  }

  async function createIntegratorMenu() {
    try {
      const root = await menuGroupsApi.create({
        name: 'Integrator Menu',
        root_kind: 'integrator',
        reference: INTEGRATOR_REFERENCE,
      });
      await load();
      setSelectedRootId(root.id);
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : 'Could not create the menu');
    }
  }

  async function cloneInto(branchId: string) {
    if (!selectedRoot || selectedRoot.root_kind !== 'branch') return;
    try {
      const root = await menuGroupsApi.clone(selectedRoot.id, { branch_id: branchId });
      await load();
      setSelectedRootId(root.id);
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : 'Could not clone the menu');
    }
  }

  async function toggleActive(node: MenuGroupNode) {
    const count = subtreeIds(node).length - 1;
    const warning = count
      ? `Switching "${node.name}" off also hides ${count} group${count > 1 ? 's' : ''} nested inside it. Continue?`
      : null;
    if (node.is_active && warning && !(await confirm({
      title: 'Hide nested groups too',
      message: warning,
      confirmLabel: 'Switch off',
      danger: true,
    }))) return;
    try {
      await menuGroupsApi.update(node.id, { is_active: !node.is_active });
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : 'Could not change the group');
    }
    await load();
  }

  async function remove(node: MenuGroupNode) {
    const count = subtreeIds(node).length - 1;
    const extra = count ? ` and ${count} group${count > 1 ? 's' : ''} nested inside it` : '';
    if (!(await confirm({
      title: 'Delete group',
      message: `Delete "${node.name}"${extra}?`,
      confirmLabel: 'Delete',
      danger: true,
    }))) return;
    try {
      await menuGroupsApi.delete(node.id);
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : 'Could not delete the group');
    }
    await load();
  }

  function toggleExpanded(id: string) {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function reach(node: MenuGroupNode): number {
    return node.product_count + node.children.reduce((n, c) => n + reach(c), 0);
  }

  function Row({ node, depth }: { node: MenuGroupNode; depth: number }) {
    const isOpen = expanded.has(node.id);
    const hasChildren = node.children.length > 0;
    // The integrator menu is two levels — a category, then its items — so a
    // category never takes a nested group. Branch trees nest freely.
    const canAddInside = !isIntegrator;
    return (
      <>
        <tr className={node.is_active ? '' : 'opacity-50'}>
          <td className="px-4 py-2">
            <div className="flex items-center gap-1" style={{ paddingLeft: depth * 22 }}>
              {hasChildren ? (
                <button
                  onClick={() => toggleExpanded(node.id)}
                  className="material-symbols-outlined text-base text-gray-500 leading-none"
                  aria-label={isOpen ? 'Collapse' : 'Expand'}
                >
                  {isOpen ? 'expand_more' : 'chevron_right'}
                </button>
              ) : (
                <span className="w-4" />
              )}
              {node.image_url ? (
                <img src={node.image_url} alt="" className="w-6 h-6 rounded object-cover" />
              ) : (
                <span className="material-symbols-outlined text-base text-gray-400">
                  {hasChildren ? 'folder' : 'sell'}
                </span>
              )}
              <span className="font-body">{node.name}</span>
              {node.name_localized && (
                <span className="text-xs text-gray-400" dir="rtl">{node.name_localized}</span>
              )}
            </div>
          </td>
          <td className="px-4 py-2 text-sm text-gray-600">{node.product_count}</td>
          <td className="px-4 py-2 text-sm text-gray-600">
            {hasChildren ? reach(node) : '—'}
          </td>
          <td className="px-4 py-2">
            <button
              onClick={() => toggleActive(node)}
              className={`text-xs px-2 py-0.5 rounded-full ${
                node.is_active ? 'bg-green-100 text-green-800' : 'bg-gray-200 text-gray-600'
              }`}
            >
              {node.is_active ? (isIntegrator ? 'Synced' : 'On the register') : 'Hidden'}
            </button>
          </td>
          <td className="px-4 py-2 text-right whitespace-nowrap">
            {canAddInside && <RowAction onClick={() => openAdd(node)}>Add inside</RowAction>}
            <RowAction onClick={() => openEdit(node)}>Edit</RowAction>
            <RowAction danger onClick={() => remove(node)}>Delete</RowAction>
          </td>
        </tr>
        {isOpen && node.children.map(child => (
          <Row key={child.id} node={child} depth={depth + 1} />
        ))}
      </>
    );
  }

  const q = productSearch.trim().toLowerCase();
  const visibleProducts = products.filter(
    p => !q || p.name.toLowerCase().includes(q) || (p.sku ?? '').toLowerCase().includes(q),
  );

  // A group cannot be moved inside itself or its own descendants.
  const forbidden = editing ? new Set(subtreeIds(editing)) : new Set<string>();

  if (loading) return <div className="p-8 font-body text-gray-500">Loading…</div>;

  const addLabel = isIntegrator ? 'New category' : 'New top-level group';

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-2">
        <h1 className="text-2xl font-display">Menu Groups</h1>
        {selectedRoot && <Button onClick={() => openAdd(null)}>{addLabel}</Button>}
      </div>
      <p className="text-sm text-gray-500 font-body mb-4 max-w-2xl">
        Each shop&apos;s terminals render its own menu, and the integrator menu is
        what goes to the marketplaces. A product reaches a till — or a marketplace —
        only through the tree.
      </p>

      {/* Which menu you are looking at. */}
      <div className="flex flex-wrap items-center gap-2 mb-6">
        {branchRoots.map(r => (
          <button
            key={r.id}
            onClick={() => setSelectedRootId(r.id)}
            className={`px-3 py-1.5 text-sm rounded-full border font-body ${
              selectedRoot?.id === r.id
                ? 'border-primary bg-primary/5 text-primary'
                : 'border-gray-300 text-gray-600 hover:border-gray-400'
            }`}
          >
            {branchName(r.branch_id)}
          </button>
        ))}
        {integratorRoot && (
          <button
            onClick={() => setSelectedRootId(integratorRoot.id)}
            className={`px-3 py-1.5 text-sm rounded-full border font-body ${
              selectedRoot?.id === integratorRoot.id
                ? 'border-primary bg-primary/5 text-primary'
                : 'border-gray-300 text-gray-600 hover:border-gray-400'
            }`}
          >
            Integrator (marketplaces)
          </button>
        )}
        {!integratorRoot && (
          <Button variant="secondary" onClick={createIntegratorMenu}>
            Create integrator menu
          </Button>
        )}
      </div>

      {/* Branches with no menu yet: start one empty, or clone the one on screen. */}
      {branchesWithoutMenu.length > 0 && (
        <div className="mb-6 flex flex-wrap items-center gap-2 text-sm font-body">
          <span className="text-gray-500">No menu yet:</span>
          {branchesWithoutMenu.map(b => (
            <span key={b.id} className="inline-flex items-center gap-1">
              <span className="text-gray-700">{b.name}</span>
              <button className="underline text-primary" onClick={() => createBranchMenu(b.id)}>
                start empty
              </button>
              {selectedRoot?.root_kind === 'branch' && (
                <button className="underline text-primary" onClick={() => cloneInto(b.id)}>
                  clone {branchName(selectedRoot.branch_id)}
                </button>
              )}
            </span>
          ))}
        </div>
      )}

      {loadError && (
        <div className="mb-4 flex items-center gap-3 bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded font-body text-sm">
          <span className="material-symbols-outlined text-base">error</span>
          <span>{loadError}</span>
          <button onClick={load} className="ml-auto underline">Retry</button>
        </div>
      )}

      <div className="bg-white rounded-lg shadow-sm overflow-hidden">
        <table className="w-full">
          <thead className="bg-gray-50 text-left text-xs uppercase tracking-wider text-gray-500">
            <tr>
              <th className="px-4 py-3 font-body">Group</th>
              <th className="px-4 py-3 font-body">Products</th>
              <th className="px-4 py-3 font-body">Incl. nested</th>
              <th className="px-4 py-3 font-body">Status</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {!selectedRoot ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-gray-500 font-body">
                  No menus yet — create one for a branch to begin.
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-gray-500 font-body">
                  This menu is empty. Add a {isIntegrator ? 'category' : 'group'} to begin.
                </td>
              </tr>
            ) : rows.map(node => <Row key={node.id} node={node} depth={0} />)}
          </tbody>
        </table>
      </div>

      {showForm && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-lg w-full max-w-2xl max-h-[90vh] flex flex-col">
            <div className="p-6 border-b">
              <h2 className="text-xl font-display">
                {editing ? `Edit ${editing.name}` : isIntegrator ? 'New category' : 'New menu group'}
              </h2>
            </div>

            <div className="p-6 space-y-4 overflow-y-auto">
              {apiError && (
                <div className="bg-red-50 text-red-700 text-sm p-3 rounded font-body">{apiError}</div>
              )}

              <Input
                label="Name"
                value={form.name}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              />
              <Input
                label="Name (Arabic)"
                value={form.name_localized}
                dir="rtl"
                onChange={e => setForm(f => ({ ...f, name_localized: e.target.value }))}
              />

              {isIntegrator && (
                <Input
                  label="Foodics subgroup id (reference)"
                  value={form.reference}
                  onChange={e => setForm(f => ({ ...f, reference: e.target.value }))}
                />
              )}

              {/* Group image, shown on the register's cards. */}
              <div>
                <span className="block text-xs uppercase tracking-wider text-gray-600 font-body mb-1">
                  Image
                </span>
                <div className="flex items-center gap-3">
                  {form.image_url && (
                    <img src={form.image_url} alt="" className="w-14 h-14 rounded object-cover" />
                  )}
                  <label className="cursor-pointer text-sm text-primary underline font-body">
                    {uploading ? 'Uploading…' : form.image_url ? 'Replace' : 'Upload'}
                    <input
                      type="file"
                      accept="image/*"
                      className="hidden"
                      onChange={e => {
                        const file = e.target.files?.[0];
                        if (file) uploadGroupImage(file);
                      }}
                    />
                  </label>
                  {form.image_url && (
                    <button
                      type="button"
                      className="text-sm text-gray-400 underline font-body"
                      onClick={() => setForm(f => ({ ...f, image_url: '' }))}
                    >
                      Remove
                    </button>
                  )}
                </div>
              </div>

              <label className="block">
                <span className="text-xs uppercase tracking-wider text-gray-600 font-body">
                  Sits inside
                </span>
                <select
                  value={form.parent_id ?? ''}
                  onChange={e => setForm(f => ({ ...f, parent_id: e.target.value || null }))}
                  className="mt-1 w-full border rounded px-3 py-2 font-body"
                  // The integrator menu is two levels: a category cannot sit
                  // inside another category, only at the top.
                  disabled={isIntegrator}
                >
                  <option value={selectedRoot?.id ?? ''}>
                    — top level of {isIntegrator ? 'the integrator menu' : branchName(selectedRoot?.branch_id)} —
                  </option>
                  {!isIntegrator &&
                    flat
                      .filter(({ node }) => !forbidden.has(node.id))
                      .map(({ node, depth }) => (
                        <option key={node.id} value={node.id}>
                          {' '.repeat(depth * 3)}{node.name}
                        </option>
                      ))}
                </select>
              </label>

              <label className="flex items-center gap-2 cursor-pointer text-xs uppercase tracking-wider text-gray-600 font-body">
                <input
                  type="checkbox"
                  checked={form.is_active}
                  onChange={e => setForm(f => ({ ...f, is_active: e.target.checked }))}
                  className="accent-primary"
                />
                {isIntegrator ? 'Synced to marketplaces' : 'On the register'}
              </label>

              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs uppercase tracking-wider text-gray-600 font-body">
                    Products ({picked.size} selected)
                  </span>
                  <input
                    placeholder="Search…"
                    value={productSearch}
                    onChange={e => setProductSearch(e.target.value)}
                    className="border rounded px-2 py-1 text-sm font-body"
                  />
                </div>
                <div className="border rounded max-h-64 overflow-y-auto divide-y divide-gray-100">
                  {visibleProducts.map(p => (
                    <label key={p.id} className="flex items-center gap-2 px-3 py-1.5 cursor-pointer hover:bg-gray-50">
                      <input
                        type="checkbox"
                        checked={picked.has(p.id)}
                        onChange={e => setPicked(prev => {
                          const next = new Set(prev);
                          if (e.target.checked) next.add(p.id); else next.delete(p.id);
                          return next;
                        })}
                        className="accent-primary"
                      />
                      <span className="font-body text-sm">{p.name}</span>
                      <span className="text-xs text-gray-400 ml-auto">{p.sku}</span>
                    </label>
                  ))}
                </div>
                {picked.size > 0 && (
                  <p className="text-xs text-gray-400 mt-1 font-body">
                    A product can belong to several groups.
                  </p>
                )}
              </div>
            </div>

            <div className="p-6 border-t flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setShowForm(false)}>Cancel</Button>
              <Button onClick={save} disabled={saving || !form.name.trim()}>
                {saving ? 'Saving…' : 'Save'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
