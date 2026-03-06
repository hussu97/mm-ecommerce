'use client';

import { usePathname, useRouter } from 'next/navigation';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import type { Language } from '@/lib/types';

export function LanguageSwitcher({ languages }: { languages: Language[] }) {
  const { locale } = useTranslation();
  const pathname = usePathname();
  const router = useRouter();

  if (languages.length <= 1) return null;

  function switchLocale(newLocale: string) {
    // Replace current locale prefix with new one
    const pathWithoutLocale = pathname.replace(/^\/[a-z]{2}/, '') || '/';
    document.cookie = `mm_locale=${newLocale};path=/;max-age=${365 * 24 * 60 * 60}`;
    router.push(`/${newLocale}${pathWithoutLocale}`);
  }

  const otherLang = languages.find((l) => l.code !== locale);
  if (!otherLang) return null;

  return (
    <button
      onClick={() => switchLocale(otherLang.code)}
      className="p-2 text-gray-600 hover:text-primary transition-colors text-xs font-body uppercase tracking-wider"
      aria-label={`Switch to ${otherLang.name}`}
    >
      {otherLang.native_name}
    </button>
  );
}
