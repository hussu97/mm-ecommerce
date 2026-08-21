'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { languagesApi, translationsApi } from '@/lib/api';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import type { Language } from '@/lib/types';
import { Button, Input, Select, Spinner } from '@/components/ui';
import { useToast } from '@/components/ui/feedback';

const NAMESPACES = [
  'common', 'nav', 'home', 'product', 'category', 'cart', 'checkout',
  'confirmation', 'auth', 'account', 'order', 'search', 'footer', 'seo',
  'faq', 'about', 'contact', 'privacy', 'terms', 'error',
];
// `promo_banner` was here and held one key that nothing rendered. Somebody
// edited it in the console expecting a banner to change, which is the cost of
// listing a namespace the storefront does not read: the screen looked like the
// place to fix the text, and it was not. Migration 122 removed the key.

type TranslationMap = Record<string, Record<string, string>>; // locale -> key -> value

export default function TranslationsPage() {
  const toast = useToast();
  const [languages, setLanguages] = useState<Language[]>([]);
  const [namespace, setNamespace] = useState('');
  const [allTranslations, setAllTranslations] = useState<TranslationMap>({});
  const [edits, setEdits] = useState<TranslationMap>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [search, setSearch] = useState('');
  const debouncedSearch = useDebouncedValue(search, 250);

  // Fetch languages once
  useEffect(() => {
    languagesApi.listAll().then(setLanguages).catch(() => {});
  }, []);

  // Fetch translations when namespace or languages change
  const fetchTranslations = useCallback(async () => {
    if (languages.length === 0) return;
    setLoading(true);
    setEdits({});
    try {
      const results = await Promise.all(
        languages.map(lang => translationsApi.get(lang.code).then(data => ({ code: lang.code, data }))),
      );
      const map: TranslationMap = {};
      for (const { code, data } of results) {
        map[code] = {};
        for (const [k, v] of Object.entries(data)) {
          if (!namespace || k.startsWith(`${namespace}.`)) {
            map[code][k] = v;
          }
        }
      }
      setAllTranslations(map);
    } catch {
      setAllTranslations({});
    } finally {
      setLoading(false);
    }
  }, [languages, namespace]);

  useEffect(() => { fetchTranslations(); }, [fetchTranslations]);

  // Collect all keys across locales for current namespace
  const allKeys = useMemo(() => {
    const keySet = new Set<string>();
    for (const localeMap of Object.values(allTranslations)) {
      for (const k of Object.keys(localeMap)) keySet.add(k);
    }
    for (const localeMap of Object.values(edits)) {
      for (const k of Object.keys(localeMap)) keySet.add(k);
    }
    return [...keySet].sort();
  }, [allTranslations, edits]);

  const q = debouncedSearch.trim().toLowerCase();
  const filteredKeys = useMemo(() => {
    if (!q) return allKeys;
    return allKeys.filter(k => {
      if (k.toLowerCase().includes(q)) return true;
      for (const locale of Object.keys(allTranslations)) {
        const val = edits[locale]?.[k] ?? allTranslations[locale]?.[k] ?? '';
        if (val.toLowerCase().includes(q)) return true;
      }
      return false;
    });
  }, [allKeys, q, allTranslations, edits]);

  // Resolve displayed value (edit overrides original)
  function getValue(locale: string, key: string) {
    if (edits[locale]?.[key] !== undefined) return edits[locale][key];
    return allTranslations[locale]?.[key] ?? '';
  }

  function handleEdit(locale: string, key: string, value: string) {
    setEdits(prev => ({
      ...prev,
      [locale]: { ...prev[locale], [key]: value },
    }));
  }

  const isDirty = useMemo(() => {
    for (const [locale, map] of Object.entries(edits)) {
      for (const [key, val] of Object.entries(map)) {
        if ((allTranslations[locale]?.[key] ?? '') !== val) return true;
      }
    }
    return false;
  }, [edits, allTranslations]);

  async function handleSave() {
    setSaving(true);
    try {
      const promises: Promise<unknown>[] = [];
      for (const [locale, map] of Object.entries(edits)) {
        const changed: { key: string; value: string }[] = [];
        for (const [key, val] of Object.entries(map)) {
          if ((allTranslations[locale]?.[key] ?? '') !== val) {
            changed.push({ key, value: val });
          }
        }
        if (changed.length > 0) {
          if (namespace) {
            promises.push(translationsApi.bulkUpsert(locale, namespace, changed));
          } else {
            const byNs: Record<string, { key: string; value: string }[]> = {};
            for (const item of changed) {
              const ns = item.key.split('.')[0];
              (byNs[ns] ??= []).push(item);
            }
            for (const [ns, items] of Object.entries(byNs)) {
              promises.push(translationsApi.bulkUpsert(locale, ns, items));
            }
          }
        }
      }
      await Promise.all(promises);
      await fetchTranslations();
      toast.success('Saved successfully.');
    } catch {
      toast.error('Save failed. Please try again.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-display text-2xl text-gray-800">Translations</h1>
        {isDirty && (
          <Button onClick={handleSave} loading={saving}>
            <span className="material-icons text-[14px]">save</span>
            Save Changes
          </Button>
        )}
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-end gap-4 mb-6">
        <div className="w-52">
          <Select
            label="Namespace"
            value={namespace}
            onChange={e => setNamespace(e.target.value)}
            options={[
              { value: '', label: 'All Namespaces' },
              ...NAMESPACES.map(ns => ({ value: ns, label: ns })),
            ]}
          />
        </div>
        <div className="w-64">
          <Input
            placeholder="Search keys or values..."
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
      </div>

      {/* Table */}
      {loading ? (
        <div className="flex items-center justify-center py-20">
          <Spinner />
        </div>
      ) : (
        <>
          {/* A key-by-language matrix is the one list here whose columns are
              not known in advance, so it cannot use `DataTable`. It follows the
              same rule anyway: below `md` a row becomes a card — the key as the
              title, one labelled field per language under it. The table needs
              700px of minimum column widths to be legible at all, which is
              nearly twice a phone. */}
          <ul className="md:hidden space-y-2">
            {filteredKeys.length === 0 ? (
              <li className="rounded border border-gray-200 bg-white px-4 py-10 text-center text-sm text-gray-400 font-body">
                No translations found for this namespace.
              </li>
            ) : (
              filteredKeys.map(key => (
                <li key={key} className="rounded border border-gray-200 bg-white px-3.5 py-3">
                  <p className="text-xs font-body text-gray-800 break-all">
                    {namespace ? key.replace(`${namespace}.`, '') : key}
                  </p>
                  <div className="mt-2.5 space-y-2 border-t border-gray-100 pt-2.5">
                    {languages.map(lang => (
                      <label key={lang.code} className="block">
                        <span className="mb-1 block text-[11px] font-body uppercase tracking-widest text-gray-400">
                          {lang.name} ({lang.code})
                        </span>
                        <input
                          type="text"
                          className="w-full px-2 py-2 min-h-11 text-sm border border-gray-200 rounded focus:border-primary focus:outline-none font-body"
                          dir={lang.direction}
                          value={getValue(lang.code, key)}
                          onChange={e => handleEdit(lang.code, key, e.target.value)}
                          placeholder={`${lang.code}...`}
                        />
                      </label>
                    ))}
                  </div>
                </li>
              ))
            )}
          </ul>

          {/* Deliberate at a desk: the matrix needs 220px for the key and
              240px per language to be legible, so folding it would be worse
              than dragging it. The phone shape is the card list above. */}
          <div
            data-scroll-intent="table"
            className="hidden md:block bg-white border border-gray-200 overflow-x-auto"
          >
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 bg-gray-50">
                <th className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 min-w-[220px] sticky left-0 bg-gray-50">
                  Key
                </th>
                {languages.map(lang => (
                  <th
                    key={lang.code}
                    className="px-4 py-3 text-left text-[11px] font-body uppercase tracking-widest text-gray-500 min-w-[240px]"
                  >
                    {lang.name} ({lang.code})
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filteredKeys.length === 0 ? (
                <tr>
                  <td
                    colSpan={1 + languages.length}
                    className="px-4 py-10 text-center text-sm text-gray-400 font-body"
                  >
                    No translations found for this namespace.
                  </td>
                </tr>
              ) : (
                filteredKeys.map(key => (
                  <tr key={key} className="hover:bg-gray-50 transition-colors">
                    <td className="px-4 py-1.5 font-body text-xs text-gray-600 sticky left-0 bg-white align-top pt-3">
                      <span className="break-all">{namespace ? key.replace(`${namespace}.`, '') : key}</span>
                    </td>
                    {languages.map(lang => (
                      <td key={lang.code} className="px-3 py-1.5">
                        <input
                          type="text"
                          className="w-full px-2 py-1.5 text-sm border border-gray-200 rounded focus:border-primary focus:outline-none font-body"
                          dir={lang.direction}
                          value={getValue(lang.code, key)}
                          onChange={e => handleEdit(lang.code, key, e.target.value)}
                          placeholder={`${lang.code}...`}
                        />
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
          </div>
        </>
      )}

      {!loading && filteredKeys.length > 0 && (
        <div className="mt-3 text-xs font-body text-gray-400">
          {filteredKeys.length} key{filteredKeys.length !== 1 ? 's' : ''} in{' '}
          <strong>{namespace || 'all namespaces'}</strong>
        </div>
      )}
    </div>
  );
}
