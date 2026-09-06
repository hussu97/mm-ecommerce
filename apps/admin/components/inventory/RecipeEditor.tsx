'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import { Badge, Button, Input, Select } from '@/components/ui';
import { ApiError } from '@/lib/api';
import { inventoryApi, type RecipeExpansion, type RecipeVersion, type VersionedRecipe } from '@/lib/pos-api';
import type { InventoryItem } from '@/lib/pos-types';

type OwnerKind = 'product' | 'modifier_option' | 'inventory_item';

interface RecipeEditorProps {
  ownerId: string;
  ownerKind: OwnerKind;
  ownerLabel: string;
  /** Use when a row action revealed the editor below a long table. */
  focusOnMount?: boolean;
}

type DraftLine = Omit<RecipeVersion['lines'][number], 'id'>;

function toDraftLine(line: RecipeVersion['lines'][number]): DraftLine {
  return {
    display_order: line.display_order,
    inactive_in_order_types: line.inactive_in_order_types,
    ingredient_unit: line.ingredient_unit,
    item_id: line.item_id,
    quantity: line.quantity,
    source_metadata: line.source_metadata,
    yield_percentage: line.yield_percentage,
  };
}

interface TreeNode {
  id: string;
  children: Map<string, TreeNode>;
}

function buildTree(preview: RecipeExpansion): TreeNode[] {
  const roots = new Map<string, TreeNode>();
  for (const line of preview.lines) {
    for (const path of line.paths) {
      let siblings = roots;
      for (const step of path) {
        const existing = siblings.get(step.item_id) ?? { id: step.item_id, children: new Map() };
        siblings.set(step.item_id, existing);
        siblings = existing.children;
      }
    }
  }
  return [...roots.values()];
}

function RecipeTree({ preview, items }: { preview: RecipeExpansion; items: InventoryItem[] }) {
  const byId = useMemo(() => new Map(items.map((item) => [item.id, item])), [items]);
  const renderNodes = (nodes: TreeNode[], depth = 0): ReactNode => nodes.map((node) => {
    const item = byId.get(node.id);
    const isStocked = item?.tracking_mode === 'stocked';
    return (
      <li key={node.id} className="relative">
        <div className="flex min-h-8 items-center gap-2 text-sm" style={{ paddingLeft: depth * 18 }}>
          <span className="text-gray-300">{depth ? '↳' : '•'}</span>
          <span className="font-medium text-gray-800">{item?.name ?? node.id}</span>
          {item && <Badge variant={isStocked ? 'success' : 'warning'}>{isStocked ? 'stocked boundary' : 'phantom'}</Badge>}
        </div>
        {node.children.size > 0 && <ul className="border-l border-gray-200">{renderNodes([...node.children.values()], depth + 1)}</ul>}
      </li>
    );
  });
  const roots = buildTree(preview);
  return (
    <div className="rounded border border-gray-200 bg-gray-50 p-3">
      <p className="mb-2 text-xs text-gray-500">Dependency tree. Phantom items expand; a stocked boundary is consumed once and its production ingredients are not consumed again by this sale.</p>
      {roots.length ? <ul>{renderNodes(roots)}</ul> : <p className="text-sm text-gray-500">No recursive path to show yet.</p>}
    </div>
  );
}

export function RecipeEditor({ ownerId, ownerKind, ownerLabel, focusOnMount = false }: RecipeEditorProps) {
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [recipe, setRecipe] = useState<VersionedRecipe | null>(null);
  const [ingredientId, setIngredientId] = useState('');
  const [ingredientSearch, setIngredientSearch] = useState('');
  const [quantity, setQuantity] = useState('1');
  const [preview, setPreview] = useState<RecipeExpansion | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const sectionRef = useRef<HTMLElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // A missing recipe is normal for a new owner. Do not let its expected
      // 404 discard the independently loaded ingredient catalogue.
      const catalogue = await inventoryApi.items();
      setItems(catalogue);
      let existing: VersionedRecipe | null = null;
      try {
        existing = await inventoryApi.versionedRecipe(ownerKind, ownerId);
      } catch (error) {
        if (!(error instanceof ApiError && error.status === 404)) throw error;
      }
      setRecipe(existing);
      if (existing === null) {
        setPreview(null);
        setMessage('No recipe yet. Choose an inventory item, enter the quantity used, then save a draft.');
        return;
      }
      const active = existing.versions.find((version) => version.status === 'active');
      if (active) {
        try {
          setPreview(await inventoryApi.expandRecipe(ownerKind, ownerId));
          setMessage('Active recipe expansion shown below.');
        } catch (error) {
          setPreview(null);
          setMessage(error instanceof ApiError ? error.message : 'The recipe loaded, but its recursive expansion could not be calculated.');
        }
      } else {
        setPreview(null);
        setMessage('');
      }
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : 'Could not load this recipe or its inventory ingredients.');
    } finally {
      setLoading(false);
    }
  }, [ownerId, ownerKind]);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    if (!focusOnMount) return;
    sectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [focusOnMount, ownerId]);

  const editable = recipe?.versions.find((version) => version.status === 'draft')
    ?? recipe?.versions.find((version) => version.status === 'active')
    ?? null;

  const selectableItems = useMemo(() => {
    const needle = ingredientSearch.trim().toLocaleLowerCase();
    return items.filter((item) => {
      if (!item.is_active || item.deleted_at) return false;
      if (ownerKind === 'inventory_item' && item.id === ownerId) return false;
      return !needle || `${item.name} ${item.sku} ${item.ingredient_unit}`.toLocaleLowerCase().includes(needle);
    });
  }, [ingredientSearch, items, ownerId, ownerKind]);

  const saveLines = async (next: DraftLine[]) => {
    setBusy(true);
    try {
      await inventoryApi.saveRecipeDraft(ownerKind, ownerId, {
        ingredients: next.map((line, index) => ({
          item_id: line.item_id,
          quantity: line.quantity,
          yield_percentage: line.yield_percentage,
          inactive_in_order_types: line.inactive_in_order_types,
          display_order: index,
          source_metadata: line.source_metadata,
        })),
        source: 'mm',
        source_metadata: {},
      });
      setPreview(null);
      await load();
      setMessage('Draft saved. It does not affect completed or future sales until activated.');
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : 'Could not save recipe draft.');
    } finally {
      setBusy(false);
    }
  };

  const addIngredient = async () => {
    if (!ingredientId || Number(quantity) <= 0) return;
    const retained: DraftLine[] = (editable?.lines ?? [])
      .filter((line) => line.item_id !== ingredientId)
      .map(toDraftLine);
    await saveLines([
      ...retained,
      { item_id: ingredientId, quantity, ingredient_unit: '', yield_percentage: '1', inactive_in_order_types: [], display_order: retained.length, source_metadata: {} },
    ]);
    setIngredientId('');
  };

  const validate = async () => {
    const draft = recipe?.versions.find((version) => version.status === 'draft');
    if (!draft) return;
    setBusy(true);
    try {
      setPreview(await inventoryApi.previewRecipeVersion(draft.id));
      setMessage('Draft is valid. Review the dependency tree before activation.');
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : 'Recipe validation failed.');
    } finally {
      setBusy(false);
    }
  };

  const activate = async () => {
    const draft = recipe?.versions.find((version) => version.status === 'draft');
    if (!draft) return;
    setBusy(true);
    try {
      await inventoryApi.activateRecipe(draft.id);
      await load();
      setPreview(null);
      setMessage(`Version ${draft.version_number} is active for future accepted orders.`);
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : 'Could not activate recipe.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section ref={sectionRef} className="mt-6 scroll-mt-6 border-t border-gray-200 pt-5">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-lg text-primary">Recipe for {ownerLabel}</h2>
          <p className="text-xs text-gray-500">A recipe links this {ownerKind.replace('_', ' ')} to physical inventory. Products and modifiers cannot be ingredients.</p>
        </div>
        {recipe?.versions.find((version) => version.status === 'draft') && (
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => void validate()} loading={busy}>Validate tree</Button>
            <Button size="sm" onClick={() => void activate()} loading={busy}>Activate draft</Button>
          </div>
        )}
      </div>
      <ol className="mb-3 grid gap-2 rounded border border-blue-100 bg-blue-50 p-3 text-xs text-blue-900 sm:grid-cols-3">
        <li><strong>1. Build draft</strong><br />Add the inventory quantity used for one sale/option/item.</li>
        <li><strong>2. Validate tree</strong><br />Check yields, units, and any recursive phantom sub-recipes.</li>
        <li><strong>3. Activate when reviewed</strong><br />Only new accepted orders use it; history never changes.</li>
      </ol>
      {message && <p className="mb-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">{message}</p>}
      <div className="space-y-2 border border-gray-200 p-3">
        {(editable?.lines ?? []).map((line) => {
          const item = items.find((value) => value.id === line.item_id);
          return <div key={line.item_id} className="flex items-center justify-between gap-3 border-b border-gray-100 py-2 text-sm last:border-0"><span>{item?.name ?? line.item_id}</span><span className="text-gray-500">{line.quantity} {line.ingredient_unit || item?.ingredient_unit}</span><button className="text-xs text-red-600 hover:underline disabled:text-gray-300" disabled={busy || (editable?.lines.length ?? 0) <= 1} onClick={() => void saveLines((editable?.lines ?? []).filter((value) => value.item_id !== line.item_id).map(toDraftLine))}>Remove</button></div>;
        })}
        <div className="grid gap-3 pt-2 md:grid-cols-[1fr_150px_auto] md:items-end">
          <div className="space-y-2">
            <Input label="Find inventory item" value={ingredientSearch} onChange={(event) => setIngredientSearch(event.target.value)} placeholder="Search by name or SKU" />
            <Select label="Inventory ingredient" value={ingredientId} onChange={(event) => setIngredientId(event.target.value)} placeholder={loading ? 'Loading inventory…' : selectableItems.length ? 'Choose inventory item' : 'No matching active inventory items'} options={selectableItems.map((item) => ({ value: item.id, label: `${item.name} · ${item.sku} · ${item.ingredient_unit}` }))} disabled={loading || selectableItems.length === 0} />
          </div>
          <Input label="Quantity" type="number" min="0.0001" step="0.0001" value={quantity} onChange={(event) => setQuantity(event.target.value)} />
          <Button size="sm" onClick={() => void addIngredient()} disabled={!ingredientId || busy || loading} loading={busy}>{editable ? 'Update draft' : 'Create draft'}</Button>
        </div>
        {!loading && items.length === 0 && <div className="flex items-center justify-between rounded bg-red-50 px-3 py-2 text-xs text-red-700"><span>No inventory ingredients were returned. Check inventory.read access or retry.</span><Button size="sm" variant="outline" onClick={() => void load()}>Retry</Button></div>}
      </div>
      {recipe && <div className="mt-3 flex flex-wrap gap-2 text-xs">{recipe.versions.map((version) => <span key={version.id} className="flex items-center gap-1"><Badge variant={version.status === 'active' ? 'success' : version.status === 'draft' ? 'warning' : 'neutral'}>{version.status}</Badge> v{version.version_number}</span>)}</div>}
      {preview && <div className="mt-3"><RecipeTree preview={preview} items={items} /></div>}
    </section>
  );
}
