'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { Badge, Button, Input, Select } from '@/components/ui';
import { ApiError } from '@/lib/api';
import { inventoryApi, type RecipeVersion, type VersionedRecipe } from '@/lib/pos-api';
import type { InventoryItem } from '@/lib/pos-types';

type OwnerKind = 'product' | 'modifier_option' | 'inventory_item';

interface RecipeEditorProps {
  ownerId: string;
  ownerKind: OwnerKind;
  ownerLabel: string;
  /** Use when a row action revealed the editor below a long table. */
  focusOnMount?: boolean;
  /** Ingredient-item ids on the path down to here, so a sub-recipe cannot open
   *  itself and loop. Set only when this editor is a nested sub-recipe. */
  ancestry?: string[];
}

/** One editable line, held locally until the shop presses Save. */
interface EditLine {
  item_id: string;
  quantity: string;
  ingredient_unit: string;
  yield_percentage: string;
  inactive_in_order_types: string[];
  source_metadata: Record<string, unknown>;
}

function toEditLine(line: RecipeVersion['lines'][number]): EditLine {
  return {
    item_id: line.item_id,
    quantity: String(line.quantity),
    ingredient_unit: line.ingredient_unit,
    yield_percentage: String(line.yield_percentage),
    inactive_in_order_types: line.inactive_in_order_types,
    source_metadata: line.source_metadata,
  };
}

const MAX_NESTING = 4;

export function RecipeEditor({
  ownerId,
  ownerKind,
  ownerLabel,
  focusOnMount = false,
  ancestry = [],
}: RecipeEditorProps) {
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [recipe, setRecipe] = useState<VersionedRecipe | null>(null);
  // The saved lines we loaded (to diff against), and the working copy being edited.
  const [savedLines, setSavedLines] = useState<EditLine[]>([]);
  const [draft, setDraft] = useState<EditLine[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [ingredientId, setIngredientId] = useState('');
  const [ingredientSearch, setIngredientSearch] = useState('');
  const [quantity, setQuantity] = useState('1');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const sectionRef = useRef<HTMLElement>(null);

  const byId = useMemo(() => new Map(items.map((item) => [item.id, item])), [items]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const catalogue = await inventoryApi.items();
      setItems(catalogue);
      let existing: VersionedRecipe | null = null;
      try {
        existing = await inventoryApi.versionedRecipe(ownerKind, ownerId);
      } catch (error) {
        if (!(error instanceof ApiError && error.status === 404)) throw error;
      }
      setRecipe(existing);
      // Seed the editable list from the draft if one exists, else the active version.
      const source =
        existing?.versions.find((version) => version.status === 'draft') ??
        existing?.versions.find((version) => version.status === 'active') ??
        null;
      const lines = (source?.lines ?? []).map(toEditLine);
      setSavedLines(lines);
      setDraft(lines);
      setMessage(
        existing === null
          ? 'No recipe yet. Add the inventory items used to make one, then press Save.'
          : '',
      );
    } catch (error) {
      setMessage(
        error instanceof ApiError
          ? error.message
          : 'Could not load this recipe or its inventory ingredients.',
      );
    } finally {
      setLoading(false);
    }
  }, [ownerId, ownerKind]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!focusOnMount) return;
    sectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [focusOnMount, ownerId]);

  const dirty = useMemo(() => {
    if (draft.length !== savedLines.length) return true;
    return draft.some((line, index) => {
      const saved = savedLines[index];
      return (
        !saved ||
        saved.item_id !== line.item_id ||
        Number(saved.quantity) !== Number(line.quantity)
      );
    });
  }, [draft, savedLines]);

  const activeDraft = recipe?.versions.find((version) => version.status === 'draft') ?? null;

  const selectableItems = useMemo(() => {
    const needle = ingredientSearch.trim().toLocaleLowerCase();
    const chosen = new Set(draft.map((line) => line.item_id));
    return items.filter((item) => {
      if (!item.is_active || item.deleted_at) return false;
      if (ownerKind === 'inventory_item' && item.id === ownerId) return false;
      if (chosen.has(item.id)) return false; // already a line
      return (
        !needle ||
        `${item.name} ${item.sku} ${item.ingredient_unit}`.toLocaleLowerCase().includes(needle)
      );
    });
  }, [ingredientSearch, items, ownerId, ownerKind, draft]);

  // ── Local edits (no network until Save) ────────────────────────────────────
  const setLineQuantity = (index: number, value: string) =>
    setDraft((lines) => lines.map((line, i) => (i === index ? { ...line, quantity: value } : line)));

  const removeLine = (index: number) =>
    setDraft((lines) => lines.filter((_, i) => i !== index));

  const addIngredient = () => {
    if (!ingredientId || Number(quantity) <= 0) return;
    setDraft((lines) => [
      ...lines,
      {
        item_id: ingredientId,
        quantity,
        ingredient_unit: byId.get(ingredientId)?.ingredient_unit ?? '',
        yield_percentage: '1',
        inactive_in_order_types: [],
        source_metadata: {},
      },
    ]);
    setIngredientId('');
    setIngredientSearch('');
    setQuantity('1');
  };

  // ── Persist: one Save creates/updates the single draft version ──────────────
  const save = async () => {
    if (draft.some((line) => Number(line.quantity) <= 0)) {
      setMessage('Every ingredient needs a quantity greater than zero.');
      return;
    }
    setBusy(true);
    try {
      await inventoryApi.saveRecipeDraft(ownerKind, ownerId, {
        ingredients: draft.map((line, index) => ({
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
      await load();
      setMessage('Saved as a draft version. It changes nothing until you activate it.');
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : 'Could not save the recipe.');
    } finally {
      setBusy(false);
    }
  };

  const activate = async () => {
    if (!activeDraft) return;
    setBusy(true);
    try {
      await inventoryApi.activateRecipe(activeDraft.id);
      await load();
      setMessage(`Version ${activeDraft.version_number} is now active for new orders.`);
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : 'Could not activate the recipe.');
    } finally {
      setBusy(false);
    }
  };

  const toggleExpanded = (itemId: string) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });

  return (
    <section ref={sectionRef} className="scroll-mt-6">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-lg text-primary">Recipe for {ownerLabel}</h2>
          <p className="text-xs text-gray-500">
            The inventory items used to make one {ownerKind.replace('_', ' ')}. Edit a
            quantity in place; expand a made item to see and edit its own recipe.
          </p>
        </div>
        <div className="flex gap-2">
          {activeDraft && (
            <Button size="sm" variant="outline" onClick={() => void activate()} loading={busy}>
              Activate v{activeDraft.version_number}
            </Button>
          )}
          <Button size="sm" onClick={() => void save()} disabled={!dirty || busy || loading} loading={busy}>
            {dirty ? 'Save' : 'Saved'}
          </Button>
        </div>
      </div>

      {message && (
        <p className="mb-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {message}
        </p>
      )}

      <div className="divide-y divide-gray-100 rounded border border-gray-200">
        {draft.length === 0 && !loading && (
          <p className="px-3 py-3 text-sm text-gray-500">No ingredients yet.</p>
        )}
        {draft.map((line, index) => {
          const item = byId.get(line.item_id);
          const isMade = item?.kind === 'produced_good' || item?.kind === 'semi_finished';
          const canExpand =
            isMade &&
            ancestry.length < MAX_NESTING - 1 &&
            !ancestry.includes(line.item_id);
          const open = expanded.has(line.item_id);
          return (
            <div key={line.item_id} className="text-sm">
              <div className="flex flex-wrap items-center gap-2 px-3 py-2">
                <button
                  type="button"
                  className={`w-4 text-gray-400 ${canExpand ? 'hover:text-primary' : 'invisible'}`}
                  onClick={() => canExpand && toggleExpanded(line.item_id)}
                  aria-label={open ? 'Collapse sub-recipe' : 'Expand sub-recipe'}
                >
                  {open ? '▾' : '▸'}
                </button>
                <span className="min-w-40 flex-1 font-medium text-gray-800">
                  {item?.name ?? line.item_id}
                  {item && (
                    <Badge variant={isMade ? 'warning' : 'neutral'} className="ml-2">
                      {isMade ? 'made' : item.kind.replace('_', ' ')}
                    </Badge>
                  )}
                </span>
                <Input
                  type="number"
                  min="0.0001"
                  step="0.0001"
                  value={line.quantity}
                  onChange={(event) => setLineQuantity(index, event.target.value)}
                  className="w-28"
                />
                <span className="w-16 text-gray-500">{line.ingredient_unit || item?.ingredient_unit}</span>
                <button
                  type="button"
                  className="text-xs text-red-600 hover:underline"
                  onClick={() => removeLine(index)}
                >
                  Remove
                </button>
              </div>
              {open && canExpand && (
                <div className="border-l-2 border-primary/20 bg-gray-50 px-3 py-3 pl-8">
                  <RecipeEditor
                    ownerId={line.item_id}
                    ownerKind="inventory_item"
                    ownerLabel={item?.name ?? 'sub-recipe'}
                    ancestry={[...ancestry, ownerKind === 'inventory_item' ? ownerId : '', line.item_id].filter(Boolean)}
                  />
                </div>
              )}
            </div>
          );
        })}

        <div className="grid gap-3 px-3 py-3 md:grid-cols-[1fr_150px_auto] md:items-end">
          <div className="space-y-2">
            <Input
              label="Find inventory item"
              value={ingredientSearch}
              onChange={(event) => setIngredientSearch(event.target.value)}
              placeholder="Search by name or SKU"
            />
            <Select
              label="Add ingredient"
              value={ingredientId}
              onChange={(event) => setIngredientId(event.target.value)}
              placeholder={loading ? 'Loading inventory…' : selectableItems.length ? 'Choose inventory item' : 'No matching items'}
              options={selectableItems.map((item) => ({
                value: item.id,
                label: `${item.name} · ${item.sku} · ${item.ingredient_unit}`,
              }))}
              disabled={loading || selectableItems.length === 0}
            />
          </div>
          <Input
            label="Quantity"
            type="number"
            min="0.0001"
            step="0.0001"
            value={quantity}
            onChange={(event) => setQuantity(event.target.value)}
          />
          <Button size="sm" variant="outline" onClick={addIngredient} disabled={!ingredientId || busy || loading}>
            Add row
          </Button>
        </div>
      </div>

      {recipe && (
        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          {recipe.versions.map((version) => (
            <span key={version.id} className="flex items-center gap-1">
              <Badge
                variant={
                  version.status === 'active'
                    ? 'success'
                    : version.status === 'draft'
                      ? 'warning'
                      : 'neutral'
                }
              >
                {version.status}
              </Badge>
              v{version.version_number}
            </span>
          ))}
          <span className="text-gray-400">· editing creates a new draft; Activate makes it live for new orders.</span>
        </div>
      )}
    </section>
  );
}
