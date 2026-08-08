'use client';

import Link from 'next/link';

import { NotFoundTracker } from '@/components/analytics/NotFoundTracker';
import { useTranslation } from '@/lib/i18n/TranslationProvider';

/**
 * The body of the 404, split out so the page beside it can stay a server
 * component and keep exporting `metadata` — a client component cannot.
 *
 * Client rather than server because of where the language comes from.
 * `not-found.tsx` is never handed `params`, and this now renders under
 * `[locale]/layout.tsx`, so the translation context is the only thing that
 * knows which language to apologise in. It used to read `cookies()` and fetch
 * the whole translation map itself, which is what made the entire route tree
 * dynamic.
 */
export function NotFoundView() {
  const { t, locale } = useTranslation();

  return (
    <div className="min-h-[70vh] flex flex-col items-center justify-center px-4 text-center">
      <NotFoundTracker />
      <p className="font-display text-8xl sm:text-[10rem] text-secondary/60 leading-none select-none">
        404
      </p>
      <h1 className="font-display text-2xl sm:text-3xl text-primary uppercase tracking-widest mt-2 mb-4">
        {t('error.not_found_title')}
      </h1>
      <p className="font-body text-sm text-gray-500 max-w-sm mb-8">
        {t('error.not_found_body')}
      </p>
      <div className="flex flex-col sm:flex-row gap-3">
        <Link
          href={`/${locale}`}
          className="px-6 py-3 bg-primary text-white text-xs font-body uppercase tracking-widest hover:opacity-90 transition-opacity"
        >
          {t('error.back_to_home')}
        </Link>
        <Link
          href={`/${locale}/contact`}
          className="px-6 py-3 border border-gray-300 text-gray-600 text-xs font-body uppercase tracking-widest hover:bg-gray-50 transition-colors"
        >
          {t('error.contact_us')}
        </Link>
      </div>
      {/* Decorative line */}
      <div className="mt-12 w-16 h-0.5 bg-secondary/40" />
      <p className="font-body text-xs text-gray-400 italic mt-3">{t('error.tagline')}</p>
    </div>
  );
}
