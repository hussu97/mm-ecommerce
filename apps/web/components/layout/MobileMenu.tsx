'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { cn } from '@/lib/utils';
import { useAuth } from '@/lib/auth-context';
import { useTranslation } from '@/lib/i18n/TranslationProvider';
import { localizedField } from '@/lib/i18n/entity';
import { LanguageSwitcher } from './LanguageSwitcher';
import type { Category, Language } from '@/lib/types';

interface MobileMenuProps {
  isOpen: boolean;
  onClose: () => void;
  languages?: Language[];
  categories?: Category[];
  locale?: string;
}

export function MobileMenu({ isOpen, onClose, languages = [], categories = [], locale: localeProp }: MobileMenuProps) {
  const { user, logout } = useAuth();
  const router = useRouter();
  const { locale: ctxLocale, t } = useTranslation();
  const locale = localeProp ?? ctxLocale;

  // Only enable the slide transition while the menu is actively opening or closing.
  // Without this, a dir="ltr"→"rtl" change on locale switch causes the closed drawer
  // to animate across the screen (the flash bug).
  const [animate, setAnimate] = useState(false);
  useEffect(() => {
    if (isOpen) {
      setAnimate(true);
    } else {
      const t = setTimeout(() => setAnimate(false), 300);
      return () => clearTimeout(t);
    }
  }, [isOpen]);

  function handleLogout() {
    logout();
    onClose();
    router.push(`/${locale}`);
  }

  // Lock scroll when open
  useEffect(() => {
    if (isOpen) document.body.style.overflow = 'hidden';
    else document.body.style.overflow = '';
    return () => { document.body.style.overflow = ''; };
  }, [isOpen]);

  // Close on Escape
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    if (isOpen) window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose]);

  return (
    <>
      {/* Backdrop */}
      <div
        className={cn(
          'fixed inset-0 z-40 bg-black/40 backdrop-blur-sm transition-opacity duration-300',
          isOpen ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none',
        )}
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer */}
      <aside
        className={cn(
          'fixed top-0 start-0 z-50 h-full w-72 bg-white flex flex-col',
          animate && 'transition-transform duration-300 ease-in-out',
          isOpen ? 'translate-x-0' : 'rtl:translate-x-full -translate-x-full rtl:-translate-x-0',
        )}
        aria-label="Navigation menu"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <span className="font-display text-primary tracking-widest text-sm uppercase">{t('nav.menu')}</span>
          <button
            onClick={onClose}
            className="p-1 text-gray-400 hover:text-primary transition-colors"
            aria-label="Close menu"
          >
            <span className="material-icons">close</span>
          </button>
        </div>

        {/* Nav links */}
        <nav className="flex-1 overflow-y-auto py-4">
          {[
            { href: `/${locale}`, label: t('nav.home') },
            { href: `/${locale}/about`, label: t('nav.about') },
            { href: `/${locale}/contact`, label: t('nav.contact') },
          ].map(({ href, label }) => (
            <Link
              key={href}
              href={href}
              onClick={onClose}
              className="flex items-center px-6 py-3.5 text-sm font-body text-gray-700 hover:text-primary hover:bg-primary/5 transition-colors uppercase tracking-widest"
            >
              {label}
            </Link>
          ))}
          <Link
            href={`/${locale}/all-products`}
            onClick={onClose}
            className="flex items-center px-6 py-3.5 text-sm font-body text-gray-700 hover:text-primary hover:bg-primary/5 transition-colors uppercase tracking-widest"
          >
            {t('nav.all')}
          </Link>
          {categories.map((cat) => (
            <Link
              key={cat.slug}
              href={`/${locale}/${cat.slug}`}
              onClick={onClose}
              className="flex items-center px-6 py-3.5 text-sm font-body text-gray-700 hover:text-primary hover:bg-primary/5 transition-colors uppercase tracking-widest"
            >
              {localizedField(cat, 'name', cat.name, locale)}
            </Link>
          ))}
        </nav>

        {/* Auth links */}
        <div className="border-t border-gray-100 px-5 py-5 space-y-2">
          {user && !user.is_guest ? (
            <>
              <Link
                href={`/${locale}/account`}
                onClick={onClose}
                className="flex items-center justify-center gap-2 w-full py-2.5 text-xs font-body uppercase tracking-widest text-primary border border-primary hover:bg-primary hover:text-white transition-all duration-200"
              >
                <span className="material-icons text-sm">person</span>
                {t('nav.my_account')}
              </Link>
              <button
                onClick={handleLogout}
                className="flex items-center justify-center gap-2 w-full py-2.5 text-xs font-body uppercase tracking-widest text-gray-500 border border-gray-300 hover:bg-gray-50 transition-all duration-200"
              >
                <span className="material-icons text-sm">logout</span>
                {t('nav.sign_out')}
              </button>
            </>
          ) : (
            <>
              <Link
                href={`/${locale}/login`}
                onClick={onClose}
                className="flex items-center justify-center gap-2 w-full py-2.5 text-xs font-body uppercase tracking-widest text-primary border border-primary hover:bg-primary hover:text-white transition-all duration-200"
              >
                <span className="material-icons text-sm">person</span>
                {t('nav.sign_in')}
              </Link>
              <Link
                href={`/${locale}/signup`}
                onClick={onClose}
                className="flex items-center justify-center gap-2 w-full py-2.5 text-xs font-body uppercase tracking-widest bg-primary text-white hover:opacity-90 transition-all duration-200"
              >
                <span className="material-icons text-sm">person_add</span>
                {t('nav.sign_up')}
              </Link>
            </>
          )}
          <div className="pt-2">
            <LanguageSwitcher languages={languages} />
          </div>
        </div>

        {/* Tagline */}
        <p className="text-center text-xs text-gray-400 font-body italic pb-5">
          {t('footer.tagline')}
        </p>
      </aside>
    </>
  );
}
