'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { Badge, Button, Input, Select } from '@/components/ui';
import { DataTable, type DataColumn } from '@/components/ui/DataTable';
import { ApiError } from '@/lib/api';
import { inventoryApi, type RecipeQuote, type RecipeVersion, type VersionedRecipe } from '@/lib/pos-api';
import type { InventoryItem } from '@/lib/pos-types';
import { formatCost, formatQuantity } from '@/lib/utils';

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
    quantity: formatQuantity(line.quantity),
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
  const [savedLines, setSavedLines] = useState<EditLine[]>([]);
  const [draft, setDraft] = useState<EditLine[]>([]);
  // Batch basis is a property of the version, held here until Save.
  const [basis, setBasis] = useState<'unit' | 'batch'>('unit');
  const [batchYield, setBatchYield] = useState('');
  const [savedBasis, setSavedBasis] = useState<'unit' | 'batch'>('unit');
  const [savedBatchYield, setSavedBatchYield] = useState('');
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
      const source =
        existing?.versions.find((version) => version.status === 'draft') ??
        existing?.versions.find((version) => version.status === 'active') ??
        null;
      const lines = (source?.lines ?? []).map(toEditLine);
      setSavedLines(lines);
      setDraft(lines);
      const basisValue = source?.basis === 'batch' ? 'batch' : 'unit';
      const yieldValue = source?.batch_yield != null ? String(source.batch_yield) : '';
      setBasis(basisValue);
      setBatchYield(yieldValue);
      setSavedBasis(basisValue);
      setSavedBatchYield(yieldValue);
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
    if (basis !== savedBasis) return true;
    if (basis === 'batch' && Number(batchYield) !== Number(savedBatchYield)) return true;
    if (draft.length !== savedLines.length) return true;
    return draft.some((line, index) => {
      const saved = savedLines[index];
      return (
        !saved ||
        saved.item_id !== line.item_id ||
        Number(saved.quantity) !== Number(line.quantity)
      );
    });
  }, [draft, savedLines, basis, batchYield, savedBasis, savedBatchYield]);

  // What the recipe costs as currently edited — per line and per unit —
  // quoted by the API from current FIFO ingredient costs (nested sub-recipes,
  // line waste and batch yield included). Debounced so typing a quantity asks
  // once; the browser never computes a cost of its own.
  const [quote, setQuote] = useState<RecipeQuote | null>(null);
  useEffect(() => {
    const lines = draft
      .filter((line) => Number(line.quantity) > 0)
      .map((line, index) => ({
        item_id: line.item_id,
        quantity: line.quantity,
        yield_percentage: line.yield_percentage || '1',
        inactive_in_order_types: line.inactive_in_order_types,
        display_order: index,
        source_metadata: {},
      }));
    const yieldNum = Number(batchYield);
    if (lines.length === 0 || (basis === 'batch' && !(yieldNum > 0))) {
      setQuote(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      inventoryApi
        .recipeQuote({
          owner_kind: ownerKind,
          owner_id: ownerId,
          basis,
          batch_yield: basis === 'batch' ? batchYield : null,
          lines,
        })
        .then((next) => { if (!cancelled) setQuote(next); })
        .catch(() => { if (!cancelled) setQuote(null); });
    }, 250);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [draft, basis, batchYield, ownerKind, ownerId]);
  const quotedLine = useMemo(
    () => new Map((quote?.lines ?? []).map((line) => [line.item_id, line])),
    [quote],
  );

  const activeDraft = recipe?.versions.find((version) => version.status === 'draft') ?? null;

  const selectableItems = useMemo(() => {
    const needle = ingredientSearch.trim().toLocaleLowerCase();
    const chosen = new Set(draft.map((line) => line.item_id));
    return items.filter((item) => {
      if (!item.is_active || item.deleted_at) return false;
      if (ownerKind === 'inventory_item' && item.id === ownerId) return false;
      if (chosen.has(item.id)) return false;
      return (
        !needle ||
        `${item.name} ${item.sku} ${item.ingredient_unit}`.toLocaleLowerCase().includes(needle)
      );
    });
  }, [ingredientSearch, items, ownerId, ownerKind, draft]);

  const setLineQuantity = (itemId: string, value: string) =>
    setDraft((lines) => lines.map((line) => (line.item_id === itemId ? { ...line, quantity: value } : line)));

  const removeLine = (itemId: string) =>
    setDraft((lines) => lines.filter((line) => line.item_id !== itemId));

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

  const save = async () => {
    if (draft.some((line) => Number(line.quantity) <= 0)) {
      setMessage('Every ingredient needs a quantity greater than zero.');
      return;
    }
    if (basis === 'batch' && !(Number(batchYield) > 0)) {
      setMessage('A batch recipe needs a batch yield greater than zero.');
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
        basis,
        batch_yield: basis === 'batch' ? batchYield : null,
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

  const canExpand = useCallback(
    (itemId: string) => {
      const item = byId.get(itemId);
      const isMade = item?.kind === 'produced_good' || item?.kind === 'semi_finished';
      return Boolean(isMade) && ancestry.length < MAX_NESTING - 1 && !ancestry.includes(itemId);
    },
    [byId, ancestry],
  );

  const columns: DataColumn<EditLine>[] = [
    {
      header: 'Ingredient',
      render: (line) => {
        const item = byId.get(line.item_id);
        const isMade = item?.kind === 'produced_good' || item?.kind === 'semi_finished';
        const expandable = canExpand(line.item_id);
        return (
          <div className="flex items-center gap-2">
            <button
              type="button"
              className={`w-4 text-gray-400 ${expandable ? 'hover:text-primary' : 'invisible'}`}
              onClick={() => expandable && toggleExpanded(line.item_id)}
              aria-label={expanded.has(line.item_id) ? 'Collapse sub-recipe' : 'Expand sub-recipe'}
            >
              {expanded.has(line.item_id) ? '▾' : '▸'}
            </button>
            <span className="font-medium text-gray-800">{item?.name ?? line.item_id}</span>
            {item && (
              <Badge variant={isMade ? 'warning' : 'neutral'}>
                {isMade ? 'made' : item.kind.replace('_', ' ')}
              </Badge>
            )}
          </div>
        );
      },
    },
    {
      header: 'Quantity',
      className: 'w-40',
      render: (line) => (
        <Input
          type="number"
          min="0.0001"
          step="0.0001"
          value={line.quantity}
          onChange={(event) => setLineQuantity(line.item_id, event.target.value)}
          className="w-32"
        />
      ),
    },
    {
      header: 'Unit',
      className: 'w-20',
      render: (line) => (
        <span className="text-gray-500">{line.ingredient_unit || byId.get(line.item_id)?.ingredient_unit}</span>
      ),
    },
    {
      // The ingredient's current FIFO cost per its recipe unit, as quoted.
      header: 'Unit cost',
      className: 'w-28 text-right whitespace-nowrap',
      render: (line) => (
        <span className="tabular-nums text-gray-500">
          {quotedLine.has(line.item_id) ? formatCost(quotedLine.get(line.item_id)?.unit_cost) : '—'}
        </span>
      ),
    },
    {
      // This line as authored, costed by the API.
      header: 'Line cost',
      className: 'w-28 text-right whitespace-nowrap',
      render: (line) => (
        <span className="tabular-nums text-gray-700">
          {quotedLine.has(line.item_id) ? formatCost(quotedLine.get(line.item_id)?.line_cost) : '—'}
        </span>
      ),
    },
  ];

  return (
    <section ref={sectionRef} className="scroll-mt-6">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-lg text-primary">Recipe for {ownerLabel}</h2>
          <p className="text-xs text-gray-500">
            {basis === 'batch'
              ? `The inventory items used to make one batch${
                  Number(batchYield) > 0 ? ` of ${batchYield}` : ''
                } ${ownerKind.replace('_', ' ')} units. Producing or selling fewer draws a fraction of the batch.`
              : `The inventory items used to make one ${ownerKind.replace('_', ' ')}. Edit a quantity in place; expand a made item to see and edit its own recipe.`}
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

      <div className="mb-3 flex flex-wrap items-end gap-3">
        <div className="w-40">
          <Select
            label="Recipe basis"
            value={basis}
            onChange={(event) => setBasis(event.target.value === 'batch' ? 'batch' : 'unit')}
            options={[
              { value: 'unit', label: 'Per unit' },
              { value: 'batch', label: 'Per batch' },
            ]}
          />
        </div>
        {basis === 'batch' && (
          <Input
            label={`Batch yields (${ownerKind.replace('_', ' ')} units)`}
            type="number"
            min="0.0001"
            step="0.0001"
            value={batchYield}
            onChange={(event) => setBatchYield(event.target.value)}
            className="w-40"
          />
        )}
      </div>

      {message && (
        <p className="mb-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {message}
        </p>
      )}

      <DataTable<EditLine>
        columns={columns}
        rows={draft}
        rowKey={(line) => line.item_id}
        empty={<p className="py-6 text-center text-sm text-gray-400">No ingredients yet.</p>}
        actions={(line) => (
          <button type="button" className="text-xs text-red-600 hover:underline" onClick={() => removeLine(line.item_id)}>
            Remove
          </button>
        )}
        expanded={(line) =>
          canExpand(line.item_id) && expanded.has(line.item_id) ? (
            <RecipeEditor
              ownerId={line.item_id}
              ownerKind="inventory_item"
              ownerLabel={byId.get(line.item_id)?.name ?? 'sub-recipe'}
              ancestry={[...ancestry, ownerKind === 'inventory_item' ? ownerId : '', line.item_id].filter(Boolean)}
            />
          ) : null
        }
      />

      {draft.length > 0 && quote && (
        <div className="mt-3 flex flex-wrap items-baseline justify-end gap-x-6 gap-y-1 rounded border border-primary/20 bg-primary/5 px-4 py-2.5">
          <span className="text-[11px] uppercase tracking-widest text-gray-500 font-body">
            Recipe cost
          </span>
          <span className="font-display text-primary tabular-nums">
            {formatCost(quote.unit_cost)} <span className="text-xs text-gray-400">/ unit</span>
          </span>
          {quote.batch_cost != null && (
            <span className="tabular-nums text-gray-600">
              {formatCost(quote.batch_cost)}{' '}
              <span className="text-xs text-gray-400">
                / batch{Number(batchYield) > 0 ? ` of ${batchYield}` : ''}
              </span>
            </span>
          )}
        </div>
      )}

      <div className="mt-3 grid gap-3 md:grid-cols-[1fr_150px_auto] md:items-end">
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
              {version.basis === 'batch' && version.batch_yield != null && (
                <span className="text-gray-400">· batch/{formatQuantity(version.batch_yield)}</span>
              )}
            </span>
          ))}
          <span className="text-gray-400">· editing creates a new draft; Activate makes it live for new orders.</span>
        </div>
      )}
    </section>
  );
}
