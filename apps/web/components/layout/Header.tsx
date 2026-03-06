'use client';

import { useState } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { useCart } from '@/lib/cart-context';
import { useAuth } from '@/lib/auth-context';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { MobileMenu } from './MobileMenu';
import { LanguageSwitcher } from './LanguageSwitcher';
import type { Category, Language } from '@/lib/types';

export function Header({ languages = [], categories = [], locale: localeProp }: {
  languages?: Language[];
  categories?: Category[];
  locale?: string;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const { itemCount } = useCart();
  const { user } = useAuth();
  const { locale: ctxLocale, t } = useTranslation();
  const locale = localeProp ?? ctxLocale;

  return (
    <>
      <header className="sticky top-0 z-30 bg-white border-b border-gray-100 shadow-sm">
        <div className="max-w-7xl mx-auto px-4 h-16 grid grid-cols-3 items-center">

          {/* Left — hamburger */}
          <div className="flex items-center">
            <button
              onClick={() => setMenuOpen(true)}
              className="p-2 -ml-2 text-gray-600 hover:text-primary transition-colors"
              aria-label="Open menu"
              aria-expanded={menuOpen}
            >
              <span className="material-icons">menu</span>
            </button>
          </div>

          {/* Center — logo */}
          <div className="flex justify-center">
            <Link href={`/${locale}`} aria-label="Melting Moments home">
              <div className="relative w-20 h-14">
                <Image
                  src="/images/logos/black_and_white_logo.jpeg"
                  alt="Melting Moments"
                  fill
                  sizes="80px"
                  className="object-contain"
                  priority
                />
              </div>
            </Link>
          </div>

          {/* Right — lang switch + account + cart */}
          <div className="flex items-center justify-end gap-0.5">
            <LanguageSwitcher languages={languages} />
            <Link
              href={user ? `/${locale}/account` : `/${locale}/login`}
              className="p-2 text-gray-600 hover:text-primary transition-colors"
              aria-label={user ? t('nav.my_account') : t('nav.sign_in')}
            >
              <span className="material-icons">{user ? 'person' : 'person_outline'}</span>
            </Link>
            <Link
              href={`/${locale}/cart`}
              className="relative p-2 -me-2 text-gray-600 hover:text-primary transition-colors"
              aria-label={`${t('nav.cart')}, ${itemCount}`}
            >
              <span className="material-icons">shopping_bag</span>
              {itemCount > 0 && (
                <span className="absolute top-0.5 right-0.5 min-w-[18px] h-[18px] flex items-center justify-center bg-primary text-white text-[10px] font-bold rounded-full leading-none px-0.5">
                  {itemCount > 99 ? '99+' : itemCount}
                </span>
              )}
            </Link>
          </div>

        </div>
      </header>

      <MobileMenu isOpen={menuOpen} onClose={() => setMenuOpen(false)} languages={languages} categories={categories} locale={locale} />
    </>
  );
}
