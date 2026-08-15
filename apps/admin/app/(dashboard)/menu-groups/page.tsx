'use client';

/**
 * The register's menu.
 *
 * Groups nest, and a product reaches the terminal through this tree — the same
 * way Foodics builds a POS menu. Switching a group off hides everything beneath
 * it, which is why the tree is shown as a tree rather than a flat list: the
 * blast radius of that toggle has to be visible before you click it.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { menuGroupsApi, productsApi, ApiError } from '@/lib/api';
import type { MenuGroupNode } from '@/lib/api';
import type { Product } from '@/lib/types';
import { Button, Input } from '@/components/ui';
import { useConfirm } from '@/components/ui/feedback';

const BLANK = { name: '', name_localized: '', parent_id: null as string | null, is_active: true };

/**
 * The API caps `per_page` at 2000 (it was 100 when this page was written), so
 * one request covers today's catalogue — the loop below only matters if the
 * catalogue ever outgrows a single page.
 */
const CATALOGUE_PAGE_SIZE = 2000;

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
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<MenuGroupNode | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(BLANK);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [productSearch, setProductSearch] = useState('');
  const [saving, setSaving] = useState(false);
  const [apiError, setApiError] = useState('');
  const [loadError, setLoadError] = useState('');

  /**
   * The whole catalogue, a page at a time.
   *
   * The API returns 422 for a `per_page` above its cap, so asking for
   * everything in one request fails outright rather than returning a short
   * page — which is how a group editor ends up with no products to pick from.
   * Paging by the cap stays correct whatever the catalogue grows to.
   */
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
      const [groups, catalogue] = await Promise.all([
        menuGroupsApi.tree(true),
        loadEveryProduct(),
      ]);
      setTree(groups);
      setProducts(catalogue);
    } catch (e) {
      // Without this the screen sits on "Loading…" for ever and says nothing
      // about why, which is indistinguishable from the page being broken.
      setLoadError(e instanceof ApiError ? e.message : 'Could not load the menu');
    } finally {
      setLoading(false);
    }
  }, [loadEveryProduct]);

  useEffect(() => { load(); }, [load]);

  const flat = useMemo(() => flatten(tree), [tree]);

  function openAdd(parent: MenuGroupNode | null) {
    setEditing(null);
    setForm({ ...BLANK, parent_id: parent?.id ?? null });
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
      parent_id: node.parent_id ?? null,
      is_active: node.is_active,
    });
    setPicked(new Set(node.product_ids));
    setApiError('');
    setProductSearch('');
    setShowForm(true);
  }

  async function save() {
    setSaving(true);
    setApiError('');
    try {
      const payload = {
        name: form.name.trim(),
        name_localized: form.name_localized.trim() || null,
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

  /** Everything reachable through this node, for the "sells N" count. */
  function reach(node: MenuGroupNode): number {
    return node.product_count + node.children.reduce((n, c) => n + reach(c), 0);
  }

  function Row({ node, depth }: { node: MenuGroupNode; depth: number }) {
    const isOpen = expanded.has(node.id);
    const hasChildren = node.children.length > 0;
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
              <span className="material-symbols-outlined text-base text-gray-400">
                {hasChildren ? 'folder' : 'sell'}
              </span>
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
              {node.is_active ? 'On the register' : 'Hidden'}
            </button>
          </td>
          <td className="px-4 py-2 text-right whitespace-nowrap">
            <button onClick={() => openAdd(node)} className="text-primary text-sm mr-3">Add inside</button>
            <button onClick={() => openEdit(node)} className="text-primary text-sm mr-3">Edit</button>
            <button onClick={() => remove(node)} className="text-red-600 text-sm">Delete</button>
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

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-2">
        <h1 className="text-2xl font-display">Menu Groups</h1>
        <Button onClick={() => openAdd(null)}>New top-level group</Button>
      </div>
      <p className="text-sm text-gray-500 font-body mb-6 max-w-2xl">
        The register builds its menu from these groups, and a product only reaches a
        cashier through one. Groups nest — switching a parent off hides everything
        inside it.
      </p>

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
            {tree.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-gray-500 font-body">
                  No groups yet — every active product shows on the register until you make one.
                </td>
              </tr>
            ) : tree.map(node => <Row key={node.id} node={node} depth={0} />)}
          </tbody>
        </table>
      </div>

      {showForm && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-lg w-full max-w-2xl max-h-[90vh] flex flex-col">
            <div className="p-6 border-b">
              <h2 className="text-xl font-display">
                {editing ? `Edit ${editing.name}` : 'New menu group'}
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

              <label className="block">
                <span className="text-xs uppercase tracking-wider text-gray-600 font-body">
                  Sits inside
                </span>
                <select
                  value={form.parent_id ?? ''}
                  onChange={e => setForm(f => ({ ...f, parent_id: e.target.value || null }))}
                  className="mt-1 w-full border rounded px-3 py-2 font-body"
                >
                  <option value="">— top level —</option>
                  {flat
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
                On the register
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
                      {!p.sales_channels?.includes('pos') && (
                        <span className="text-xs text-amber-700 bg-amber-50 px-1.5 rounded">
                          off register
                        </span>
                      )}
                    </label>
                  ))}
                </div>
                {picked.size > 0 && (
                  <p className="text-xs text-gray-400 mt-1 font-body">
                    Products already in {editing ? 'this' : 'another'} group can belong to several.
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
