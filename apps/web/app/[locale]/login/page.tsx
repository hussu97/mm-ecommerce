import type { Metadata } from 'next';
import { Suspense } from 'react';

import { createT, getTranslations } from '@/lib/i18n/server';
import { LoginForm } from './LoginForm';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://meltingmomentscakes.com';

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = createT(await getTranslations(locale));
  return {
    title: t('auth.welcome_back'),
    description: t('auth.sign_in_subtitle'),
    alternates: {
      canonical: `${SITE_URL}/${locale}/login`,
      languages: {
        en: `${SITE_URL}/en/login`,
        ar: `${SITE_URL}/ar/login`,
        'x-default': `${SITE_URL}/en/login`,
      },
    },
  };
}

/**
 * A server component wrapping a client form, and the split is the whole point.
 *
 * The page used to be `'use client'` end to end, with the heading rendered
 * inside the same component that calls `useSearchParams()` for the `?redirect`.
 * That hook cannot run during a prerender, so Next renders the enclosing
 * `<Suspense>` boundary's fallback into the static HTML and defers the subtree
 * to the browser — and the boundary had no fallback. The served document was
 * therefore an empty shell: fine for a customer, whose browser fills it in a
 * moment later, and nothing at all for a crawler, which is why Bing reported
 * `/en/login` as a page with no `<h1>`.
 *
 * Only the form needs the search params. Keeping the heading out here puts it
 * in the HTML that leaves the server, and the boundary now has a fallback that
 * holds the layout instead of collapsing it.
 */
export default async function LoginPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  const t = createT(await getTranslations(locale));

  return (
    <div className="min-h-[70vh] flex items-center justify-center px-4 py-16">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="font-display text-3xl text-primary mb-2">{t('auth.welcome_back')}</h1>
          <p className="text-sm text-gray-500 font-body">{t('auth.sign_in_subtitle')}</p>
        </div>

        <Suspense fallback={<div className="min-h-[22rem]" />}>
          <LoginForm />
        </Suspense>
      </div>
    </div>
  );
}
