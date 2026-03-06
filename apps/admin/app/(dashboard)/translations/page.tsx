'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { languagesApi, translationsApi } from '@/lib/api';
import type { Language } from '@/lib/types';
import { Button, Input, Select, Spinner } from '@/components/ui';

const NAMESPACES = [
  'common', 'nav', 'home', 'product', 'category', 'cart', 'checkout',
  'confirmation', 'auth', 'account', 'order', 'search', 'footer', 'seo',
  'promo_banner', 'faq', 'about', 'contact', 'privacy', 'terms', 'error',
];

type TranslationMap = Record<string, Record<string, string>>; // locale -> key -> value

export default function TranslationsPage() {
  const [languages, setLanguages] = useState<Language[]>([]);
  const [namespace, setNamespace] = useState(NAMESPACES[0]);
  const [allTranslations, setAllTranslations] = useState<TranslationMap>({});
  const [edits, setEdits] = useState<TranslationMap>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [toast, setToast] = useState('');
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Debounced search
  useEffect(() => {
    debounceRef.current = setTimeout(() => setDebouncedSearch(search), 250);
    return () => clearTimeout(debounceRef.current);
  }, [search]);

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
          if (k.startsWith(`${namespace}.`)) {
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
    setToast('');
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
          promises.push(translationsApi.bulkUpsert(locale, namespace, changed));
        }
      }
      await Promise.all(promises);
      await fetchTranslations();
      setToast('Saved successfully.');
      setTimeout(() => setToast(''), 3000);
    } catch {
      setToast('Save failed. Please try again.');
      setTimeout(() => setToast(''), 4000);
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
            options={NAMESPACES.map(ns => ({ value: ns, label: ns }))}
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

      {/* Toast */}
      {toast && (
        <div className={`text-xs px-3 py-2 mb-4 border ${toast.includes('failed') ? 'bg-red-50 border-red-200 text-red-600' : 'bg-green-50 border-green-200 text-green-700'}`}>
          {toast}
        </div>
      )}

      {/* Table */}
      {loading ? (
        <div className="flex items-center justify-center py-20">
          <Spinner />
        </div>
      ) : (
        <div className="bg-white border border-gray-200 overflow-x-auto">
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
                      <span className="break-all">{key.replace(`${namespace}.`, '')}</span>
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
      )}

      {!loading && filteredKeys.length > 0 && (
        <div className="mt-3 text-xs font-body text-gray-400">
          {filteredKeys.length} key{filteredKeys.length !== 1 ? 's' : ''} in <strong>{namespace}</strong>
        </div>
      )}
    </div>
  );
}
