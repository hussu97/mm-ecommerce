'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { cmsApi, ApiError } from '@/lib/api';
import type { CmsPage } from '@/lib/types';
import { Spinner } from '@/components/ui';

const PAGE_META: Record<string, { label: string; description: string; icon: string }> = {
  home: { label: 'Home', description: 'Hero, bestsellers, baker section and occasions', icon: 'home' },
  about: { label: 'About', description: 'Our story, values and CTA', icon: 'person' },
  faq: { label: 'FAQ', description: 'Frequently asked questions', icon: 'help_outline' },
  contact: { label: 'Contact', description: 'Contact info and header text', icon: 'mail' },
  privacy: { label: 'Privacy Policy', description: 'Privacy policy sections and legal text', icon: 'shield' },
};

export default function ContentPage() {
  const [pages, setPages] = useState<CmsPage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    cmsApi.list()
      .then(setPages)
      .catch(err => setError(err instanceof ApiError ? err.message : 'Failed to load pages.'))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <div className="mb-6">
        <h1 className="font-display text-2xl text-gray-800">Content</h1>
        <p className="text-xs font-body text-gray-500 mt-1">Edit the content of your static pages.</p>
      </div>

      {loading && <div className="flex justify-center py-12"><Spinner /></div>}

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-600 text-xs px-4 py-3">{error}</div>
      )}

      {!loading && !error && (
        <div className="grid sm:grid-cols-3 gap-4">
          {pages.map(page => {
            const meta = PAGE_META[page.slug] ?? { label: page.slug, description: '', icon: 'article' };
            return (
              <Link
                key={page.slug}
                href={`/content/${page.slug}`}
                className="bg-white border border-gray-200 hover:border-primary p-5 flex items-start gap-4 transition-colors group"
              >
                <span className="material-icons text-secondary group-hover:text-primary text-2xl mt-0.5 transition-colors">
                  {meta.icon}
                </span>
                <div className="min-w-0">
                  <p className="font-body text-sm font-medium text-gray-800 group-hover:text-primary transition-colors">
                    {meta.label}
                  </p>
                  <p className="font-body text-xs text-gray-400 mt-0.5">{meta.description}</p>
                  <p className="font-body text-[11px] text-gray-300 mt-2">
                    Updated {new Date(page.updated_at).toLocaleDateString()}
                  </p>
                </div>
                <span className="material-icons text-gray-300 group-hover:text-primary ml-auto self-center text-[18px] transition-colors">
                  chevron_right
                </span>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
