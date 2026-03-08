import Link from 'next/link';
import type { Metadata } from 'next';
import { cookies } from 'next/headers';
import { getTranslations, createT } from '@/lib/i18n/server';

export const metadata: Metadata = { title: '404 — Page Not Found' };

export default async function NotFound() {
  const cookieStore = await cookies();
  const locale = cookieStore.get('mm_locale')?.value ?? 'en';
  const translations = await getTranslations(locale);
  const t = createT(translations);

  return (
    <div className="min-h-[70vh] flex flex-col items-center justify-center px-4 text-center">
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
          href="/"
          className="px-6 py-3 bg-primary text-white text-xs font-body uppercase tracking-widest hover:opacity-90 transition-opacity"
        >
          {t('error.back_to_home')}
        </Link>
        <Link
          href="/contact"
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
