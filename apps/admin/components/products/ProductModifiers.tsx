'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { modifiersApi, productsApi, ApiError } from '@/lib/api';
import type { Modifier, ModifierOption, Product, ProductModifier } from '@/lib/types';
import { ModifierOptionStockGrid } from '@/components/products/BranchStock';
import { Button } from '@/components/ui';
import { useConfirm, useToast } from '@/components/ui/feedback';

/*
  The product's modifier groups, editable where they sit.

  Everything here saves on its own, per group, and never waits for the
  product form's Save: a group's rules live on the product↔modifier link and
  its option prices on the modifier itself, and both are written by their own
  endpoints. Inputs live inside the product <form>, so Enter is caught and
  turned into "save this group" rather than submitting the whole product.
*/

interface RulesDraft {
  minimum_options: string;
  maximum_options: string;
  free_options: string;
  unique_options: boolean;
}

interface OptionDraft {
  price?: string;
  is_active?: boolean;
}

type RuleKey = 'minimum_options' | 'maximum_options' | 'free_options';

const RULE_FIELDS: { key: RuleKey; label: string; hint: string }[] = [
  { key: 'minimum_options', label: 'Min', hint: 'Fewest the customer must choose (0 = optional)' },
  { key: 'maximum_options', label: 'Max', hint: 'Most the customer may choose' },
  { key: 'free_options', label: 'Free', hint: 'The first N chosen are free of their price' },
];

const PRICE_PATTERN = /^\d+(\.\d{1,2})?$/;

function rulesOf(pm: ProductModifier): RulesDraft {
  return {
    minimum_options: String(pm.minimum_options),
    maximum_options: String(pm.maximum_options),
    free_options: String(pm.free_options),
    unique_options: pm.unique_options,
  };
}

function priceText(price: number | string): string {
  return Number(price).toFixed(2);
}

function withModifier(product: Product, modifier: Modifier): Product {
  return {
    ...product,
    product_modifiers: product.product_modifiers.map((pm) =>
      pm.modifier_id === modifier.id ? { ...pm, modifier } : pm,
    ),
  };
}

export function ProductModifiers({ product: initial }: { product: Product }) {
  const toast = useToast();
  const confirm = useConfirm();
  const [product, setProduct] = useState<Product>(initial);
  const [allModifiers, setAllModifiers] = useState<Modifier[]>([]);
  const [showLinkForm, setShowLinkForm] = useState(false);
  const [linkForm, setLinkForm] = useState({ modifier_id: '', minimum_options: 0, maximum_options: 1, free_options: 0, unique_options: false });
  const [linking, setLinking] = useState(false);
  const [linkError, setLinkError] = useState('');

  useEffect(() => {
    modifiersApi.list().then(setAllModifiers).catch(() => {});
  }, []);

  const links = product.product_modifiers;

  async function link() {
    if (!linkForm.modifier_id) { setLinkError('Select a modifier.'); return; }
    setLinking(true);
    setLinkError('');
    try {
      setProduct(await productsApi.linkModifier(product.slug, linkForm));
      setShowLinkForm(false);
      setLinkForm({ modifier_id: '', minimum_options: 0, maximum_options: 1, free_options: 0, unique_options: false });
    } catch (err) {
      setLinkError(err instanceof ApiError ? err.message : 'Failed to link modifier.');
    } finally {
      setLinking(false);
    }
  }

  return (
    <section className="bg-white border border-gray-200 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
        <div>
          <h2 className="font-display text-lg text-gray-800">Modifiers ({links.length})</h2>
          <p className="text-xs font-body text-gray-400 mt-0.5 max-w-xl">
            What the customer chooses with this product. Edit prices and rules here and press
            Save on the group — it saves on its own, nothing waits for Save Changes below.
          </p>
        </div>
        {!showLinkForm && (
          <Button type="button" size="sm" variant="ghost" onClick={() => { setShowLinkForm(true); setLinkError(''); }}>
            <span className="material-icons text-[14px]">add</span>
            Link Modifier
          </Button>
        )}
      </div>

      {showLinkForm && (
        <div className="border border-gray-200 bg-gray-50 p-4 mb-4">
          {linkError && <p className="text-xs text-red-500 mb-3">{linkError}</p>}
          <div className="grid sm:grid-cols-2 gap-3 mb-3">
            <div>
              <label className="text-[11px] font-body uppercase tracking-widest text-gray-500 block mb-1">Modifier</label>
              <select
                value={linkForm.modifier_id}
                onChange={e => setLinkForm(f => ({ ...f, modifier_id: e.target.value }))}
                className="w-full border border-gray-300 text-xs font-body px-2 py-1.5 bg-white focus:outline-none focus:border-primary"
              >
                <option value="">— Select modifier —</option>
                {allModifiers
                  .filter(m => !links.some(pm => pm.modifier_id === m.id))
                  .map(m => <option key={m.id} value={m.id}>{m.name} ({m.reference})</option>)}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {RULE_FIELDS.map(({ key, label }) => (
                <div key={key}>
                  <label className="text-[11px] font-body uppercase tracking-widest text-gray-500 block mb-1">{label}</label>
                  <input
                    type="number"
                    min="0"
                    value={linkForm[key]}
                    onChange={e => setLinkForm(f => ({ ...f, [key]: Number(e.target.value) }))}
                    className="w-full border border-gray-300 text-xs font-body px-2 py-1.5 focus:outline-none focus:border-primary"
                  />
                </div>
              ))}
              <label className="flex items-center gap-2 text-xs font-body text-gray-600 pt-4">
                <input
                  type="checkbox"
                  checked={linkForm.unique_options}
                  onChange={e => setLinkForm(f => ({ ...f, unique_options: e.target.checked }))}
                  className="accent-primary"
                />
                Unique
              </label>
            </div>
          </div>
          <div className="flex gap-2">
            <Button type="button" size="sm" loading={linking} onClick={link}>Link</Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setShowLinkForm(false)}>Cancel</Button>
          </div>
        </div>
      )}

      <div className="space-y-4">
        {links.map(pm => (
          <ModifierGroupCard
            key={pm.id}
            slug={product.slug}
            link={pm}
            onSaved={setProduct}
            onModifierSaved={modifier => setProduct(p => withModifier(p, modifier))}
            onUnlink={async () => {
              if (!(await confirm({
                title: 'Unlink modifier',
                message: `Unlink "${pm.modifier.name}" from this product?`,
                confirmLabel: 'Unlink',
                danger: true,
              }))) return;
              try {
                setProduct(await productsApi.unlinkModifier(product.slug, pm.modifier_id));
              } catch (err) {
                toast.error(err instanceof ApiError ? err.message : 'Failed to unlink.');
              }
            }}
          />
        ))}
        {links.length === 0 && (
          <p className="text-xs text-gray-400 font-body">No modifiers linked.</p>
        )}
      </div>

      {/* Per-option, per-branch stock. A filling is 86'd per branch — that is
          what pulls a box off one emirate's website — so the "Active" switch
          above is the menu everywhere; this is where each one can actually be
          made right now, and where a manager puts it back. */}
      {links.length > 0 && (
        <div className="mt-6 pt-5 border-t border-gray-100">
          <h3 className="text-xs font-body uppercase tracking-widest text-gray-500 mb-3">
            Option stock by branch
          </h3>
          <ModifierOptionStockGrid productModifiers={links} />
        </div>
      )}
    </section>
  );
}

function ModifierGroupCard({
  slug,
  link,
  onSaved,
  onModifierSaved,
  onUnlink,
}: {
  slug: string;
  link: ProductModifier;
  onSaved: (product: Product) => void;
  onModifierSaved: (modifier: Modifier) => void;
  onUnlink: () => Promise<void>;
}) {
  const toast = useToast();
  const [rules, setRules] = useState<RulesDraft>(() => rulesOf(link));
  const [drafts, setDrafts] = useState<Record<string, OptionDraft>>({});
  const [saving, setSaving] = useState(false);
  const [unlinking, setUnlinking] = useState(false);
  const [error, setError] = useState('');

  const saved = rulesOf(link);
  const rulesDirty =
    rules.minimum_options !== saved.minimum_options ||
    rules.maximum_options !== saved.maximum_options ||
    rules.free_options !== saved.free_options ||
    rules.unique_options !== saved.unique_options;

  // Active options first in their menu order, the switched-off ones after.
  const options = [...link.modifier.options].sort(
    (a, b) => Number(b.is_active) - Number(a.is_active) || a.display_order - b.display_order,
  );

  function priceFor(opt: ModifierOption): string {
    return drafts[opt.id]?.price ?? priceText(opt.price);
  }
  function activeFor(opt: ModifierOption): boolean {
    return drafts[opt.id]?.is_active ?? opt.is_active;
  }
  function optionChange(opt: ModifierOption): { price?: number; is_active?: boolean } | null {
    const change: { price?: number; is_active?: boolean } = {};
    const price = priceFor(opt).trim();
    if (PRICE_PATTERN.test(price) && Number(price) !== Number(opt.price)) change.price = Number(price);
    if (activeFor(opt) !== opt.is_active) change.is_active = activeFor(opt);
    return Object.keys(change).length > 0 ? change : null;
  }

  const invalidPrices = options.filter(o => !PRICE_PATTERN.test(priceFor(o).trim()));
  const changedOptions = options.filter(o => optionChange(o) !== null);
  const ruleProblem = (() => {
    const values = RULE_FIELDS.map(({ key }) => rules[key].trim());
    if (values.some(v => !/^\d+$/.test(v))) return 'Min, max and free must be whole numbers.';
    if (Number(rules.minimum_options) > Number(rules.maximum_options)) return 'Min cannot be more than max.';
    return null;
  })();
  const dirty = rulesDirty || changedOptions.length > 0 || invalidPrices.length > 0;
  const changeCount = (rulesDirty ? 1 : 0) + changedOptions.length;

  function discard() {
    setRules(rulesOf(link));
    setDrafts({});
    setError('');
  }

  async function save() {
    if (invalidPrices.length > 0) { setError('Prices must be 0 or more, with at most two decimals.'); return; }
    if (rulesDirty && ruleProblem) { setError(ruleProblem); return; }
    setSaving(true);
    setError('');
    let done = 0;
    try {
      if (rulesDirty) {
        onSaved(await productsApi.updateModifierLink(slug, link.modifier_id, {
          minimum_options: Number(rules.minimum_options),
          maximum_options: Number(rules.maximum_options),
          free_options: Number(rules.free_options),
          unique_options: rules.unique_options,
        }));
        done += 1;
      }
      // One option at a time: a failure part-way leaves the ones before it saved
      // and the rest still marked as changed, rather than an all-or-nothing guess.
      const pending = changedOptions.map(opt => ({ id: opt.id, change: optionChange(opt)! }));
      for (const { id, change } of pending) {
        onModifierSaved(await modifiersApi.updateOption(link.modifier_id, id, change));
        setDrafts(d => {
          const next = { ...d };
          delete next[id];
          return next;
        });
        done += 1;
      }
      toast.success(`${link.modifier.name}: ${done} change${done === 1 ? '' : 's'} saved.`);
    } catch (err) {
      setError(
        `${done > 0 ? `${done} saved, then: ` : ''}${err instanceof ApiError ? err.message : 'Save failed.'}`,
      );
    } finally {
      setSaving(false);
    }
  }

  // Enter in a number field would submit the whole product form; here it saves
  // this group instead. Only from a text field — Enter on Discard means Discard.
  function onKeyDown(e: React.KeyboardEvent) {
    const inField = e.target instanceof HTMLInputElement && e.target.type === 'number';
    if (e.key === 'Enter' && inField) {
      e.preventDefault();
      if (dirty && !saving) void save();
    } else if (e.key === 'Escape' && dirty) {
      e.preventDefault();
      discard();
    }
  }

  return (
    <div className={`border ${dirty ? 'border-amber-300' : 'border-gray-200'}`} onKeyDown={onKeyDown}>
      <div className="flex flex-wrap items-start justify-between gap-3 bg-gray-50 px-4 py-3 border-b border-gray-200">
        <div className="min-w-0">
          <p className="text-sm font-body font-medium text-gray-800">
            {link.modifier.name}{' '}
            <span className="text-xs font-normal text-gray-400">({link.modifier.reference})</span>
          </p>
          <Link
            href="/modifiers"
            className="text-[11px] font-body text-gray-400 hover:text-primary"
            title="Names, translations and new options are edited on the Modifiers page"
          >
            Rename or add options in Modifiers
          </Link>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          {RULE_FIELDS.map(({ key, label, hint }) => (
            <label key={key} className="block" title={hint}>
              <span className="block text-[10px] font-body uppercase tracking-widest text-gray-500 mb-0.5">{label}</span>
              <input
                type="number"
                min="0"
                step="1"
                value={rules[key]}
                onChange={e => setRules(r => ({ ...r, [key]: e.target.value }))}
                className={`w-16 border px-2 py-1 text-sm font-body text-right bg-white focus:outline-none focus:border-primary ${rules[key] !== saved[key] ? 'border-amber-400 bg-amber-50' : 'border-gray-300'}`}
              />
            </label>
          ))}
          <label className="flex items-center gap-1.5 pb-1.5 text-xs font-body text-gray-600" title="Each option at most once">
            <input
              type="checkbox"
              checked={rules.unique_options}
              onChange={e => setRules(r => ({ ...r, unique_options: e.target.checked }))}
              className="accent-primary"
            />
            Unique
          </label>
          <Button
            type="button"
            variant="danger"
            size="sm"
            loading={unlinking}
            onClick={async () => { setUnlinking(true); try { await onUnlink(); } finally { setUnlinking(false); } }}
          >
            Unlink
          </Button>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm font-body">
          <thead>
            <tr className="border-b border-gray-100 text-[11px] uppercase tracking-widest text-gray-500">
              <th className="px-4 py-2 text-left">Option</th>
              <th className="px-4 py-2 text-left hidden sm:table-cell">SKU</th>
              <th className="px-4 py-2 text-right w-40">Price (+AED)</th>
              <th
                className="px-4 py-2 text-center w-24"
                title="Off takes the option off the menu everywhere. For one kitchen running out, use Option stock by branch."
              >
                Active
              </th>
            </tr>
          </thead>
          <tbody>
            {options.map(opt => {
              const price = priceFor(opt);
              const priceInvalid = !PRICE_PATTERN.test(price.trim());
              const priceChanged = !priceInvalid && Number(price) !== Number(opt.price);
              const active = activeFor(opt);
              return (
                <tr
                  key={opt.id}
                  className={`border-b border-gray-50 last:border-b-0 ${priceChanged || priceInvalid || active !== opt.is_active ? 'bg-amber-50/60' : ''}`}
                >
                  <td className={`px-4 py-2 ${active ? 'text-gray-800' : 'text-gray-400 line-through'}`}>{opt.name}</td>
                  <td className="px-4 py-2 text-xs text-gray-400 hidden sm:table-cell">{opt.sku}</td>
                  <td className="px-4 py-2">
                    <div className="flex flex-col items-end">
                      <input
                        type="number"
                        inputMode="decimal"
                        min="0"
                        step="0.01"
                        aria-label={`Price of ${opt.name}`}
                        value={price}
                        onChange={e => setDrafts(d => ({ ...d, [opt.id]: { ...d[opt.id], price: e.target.value } }))}
                        onBlur={() => {
                          // Tidy "5" into "5.00" so an unchanged price reads as unchanged.
                          if (PRICE_PATTERN.test(price.trim())) {
                            setDrafts(d => ({ ...d, [opt.id]: { ...d[opt.id], price: priceText(price) } }));
                          }
                        }}
                        className={`w-28 border px-2 py-1.5 text-right text-sm tabular-nums focus:outline-none focus:border-primary ${priceInvalid ? 'border-red-400 bg-red-50' : priceChanged ? 'border-amber-400 bg-white' : 'border-gray-300 bg-white'}`}
                      />
                      {priceChanged && (
                        <span className="mt-0.5 text-[10px] text-gray-400">was {priceText(opt.price)}</span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-2 text-center">
                    <button
                      type="button"
                      role="switch"
                      aria-checked={active}
                      aria-label={`${opt.name} ${active ? 'active' : 'inactive'}`}
                      onClick={() => setDrafts(d => ({ ...d, [opt.id]: { ...d[opt.id], is_active: !active } }))}
                      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${active ? 'bg-primary' : 'bg-gray-300'}`}
                    >
                      <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${active ? 'translate-x-4' : 'translate-x-0.5'}`} />
                    </button>
                  </td>
                </tr>
              );
            })}
            {options.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-3 text-xs text-gray-400">This modifier has no options yet.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {(dirty || error) && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-amber-200 bg-amber-50 px-4 py-2.5">
          <p className={`text-xs font-body ${error ? 'text-red-600' : 'text-amber-800'}`}>
            {error || (dirty
              ? `${changeCount} unsaved change${changeCount === 1 ? '' : 's'} — Enter saves, Esc discards.${changedOptions.length > 0 ? ' Option prices belong to the modifier, so every product it is linked to sees them.' : ''}`
              : '')}
          </p>
          {dirty && (
            <div className="flex gap-2">
              <Button type="button" size="sm" variant="ghost" onClick={discard} disabled={saving}>Discard</Button>
              <Button type="button" size="sm" loading={saving} onClick={() => void save()}>Save</Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
