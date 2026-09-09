'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { cmsApi, languagesApi, ApiError } from '@/lib/api';
import type { Language } from '@/lib/types';
import { Button, Spinner } from '@/components/ui';
import { HomeEditor } from '@/components/content/HomeEditor';
import { AboutEditor } from '@/components/content/AboutEditor';
import { FaqEditor } from '@/components/content/FaqEditor';
import { ContactEditor } from '@/components/content/ContactEditor';
import { PrivacyEditor } from '@/components/content/PrivacyEditor';

type PageContent = Record<string, Record<string, unknown>>;

const PAGE_LABELS: Record<string, string> = {
  home: 'Home',
  about: 'About',
  faq: 'FAQ',
  contact: 'Contact',
};

export default function ContentEditorPage() {
  const { slug } = useParams<{ slug: string }>();
  const [languages, setLanguages] = useState<Language[]>([]);
  const [content, setContent] = useState<PageContent>({});
  const [activeLocale, setActiveLocale] = useState('en');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  // Which locales have edits not yet persisted. Content for every locale lives
  // in `content`, but Save used to write only the active one — so editing EN,
  // switching to AR and saving persisted AR and dropped EN silently (F-ADM-11).
  const [dirty, setDirty] = useState<Set<string>>(new Set());

  useEffect(() => {
    Promise.all([languagesApi.list(), cmsApi.get(slug)])
      .then(([langs, page]) => {
        setLanguages(langs);
        setContent(page.content as PageContent);
        if (langs.length > 0) setActiveLocale(langs[0].code);
      })
      .catch(err => setError(err instanceof ApiError ? err.message : 'Failed to load.'))
      .finally(() => setLoading(false));
  }, [slug]);

  // Don't let a navigation or tab-close throw away edits in any locale.
  useEffect(() => {
    if (dirty.size === 0) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  function handleLocaleContent(localeContent: Record<string, unknown>) {
    setContent(prev => ({ ...prev, [activeLocale]: localeContent }));
    setDirty(prev => (prev.has(activeLocale) ? prev : new Set(prev).add(activeLocale)));
    setSaved(false);
  }

  async function handleSave() {
    if (dirty.size === 0) return;
    setSaving(true);
    setError('');
    setSaved(false);
    try {
      // Persist EVERY locale with unsaved edits, not just the one on screen.
      for (const locale of dirty) {
        await cmsApi.updateLocale(slug, locale, content[locale] ?? {});
      }
      setDirty(new Set());
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Save failed.');
    } finally {
      setSaving(false);
    }
  }

  const localeContent = content[activeLocale] ?? {};

  function renderEditor() {
    if (slug === 'home') return <HomeEditor content={localeContent} onChange={handleLocaleContent} />;
    if (slug === 'about') return <AboutEditor content={localeContent} onChange={handleLocaleContent} />;
    if (slug === 'faq') return <FaqEditor content={localeContent} onChange={handleLocaleContent} />;
    if (slug === 'contact') return <ContactEditor content={localeContent} onChange={handleLocaleContent} />;
    if (slug === 'privacy') return <PrivacyEditor content={localeContent} onChange={handleLocaleContent} />;
    return <p className="text-sm text-gray-500 font-body">No editor for this page.</p>;
  }

  if (loading) return <div className="flex justify-center py-12"><Spinner /></div>;

  return (
    <div>
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <Link href="/content" className="text-gray-400 hover:text-primary transition-colors">
          <span className="material-icons text-[18px]">arrow_back</span>
        </Link>
        <h1 className="font-display text-2xl text-gray-800">{PAGE_LABELS[slug] ?? slug} Page</h1>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-600 text-xs px-4 py-3 mb-4">{error}</div>
      )}

      {/* Language tabs */}
      <div className="flex gap-1 mb-6 border-b border-gray-200">
        {languages.map(lang => (
          <button
            key={lang.code}
            onClick={() => setActiveLocale(lang.code)}
            className={`px-4 py-2 text-xs font-body uppercase tracking-widest border-b-2 transition-colors ${
              activeLocale === lang.code
                ? 'border-primary text-primary'
                : 'border-transparent text-gray-500 hover:text-primary'
            }`}
          >
            {lang.name}
            {dirty.has(lang.code) && <span className="ml-1 text-amber-500" title="Unsaved changes">•</span>}
          </button>
        ))}
      </div>

      {/* Editor */}
      <div className="bg-white border border-gray-200 p-6 mb-6">
        {renderEditor()}
      </div>

      {/* Save */}
      <div className="flex items-center gap-3">
        <Button onClick={handleSave} loading={saving} disabled={dirty.size === 0}>
          <span className="material-icons text-[14px]">save</span>
          {dirty.size > 1
            ? `Save changes (${dirty.size} languages)`
            : 'Save changes'}
        </Button>
        {saved && (
          <span className="text-xs font-body text-green-600 flex items-center gap-1">
            <span className="material-icons text-[14px]">check_circle</span>
            Saved
          </span>
        )}
      </div>
    </div>
  );
}
