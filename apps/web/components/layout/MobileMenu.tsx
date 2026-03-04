'use client';

import { useEffect } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { cn } from '@/lib/utils';
import { useAuth } from '@/lib/auth-context';

interface MobileMenuProps {
  isOpen: boolean;
  onClose: () => void;
}

const NAV_LINKS = [
  { href: '/',             label: 'Home' },
  { href: '/brownies',     label: 'Brownies' },
  { href: '/cookies',      label: 'Cookies' },
  { href: '/cookie-melt',  label: 'Cookie Melt' },
  { href: '/mix-boxes',    label: 'Mix Boxes' },
  { href: '/desserts',     label: 'Desserts' },
  { href: '/about',        label: 'About' },
  { href: '/contact',      label: 'Contact' },
];

export function MobileMenu({ isOpen, onClose }: MobileMenuProps) {
  const { user, logout } = useAuth();
  const router = useRouter();

  function handleLogout() {
    logout();
    onClose();
    router.push('/');
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
          'fixed top-0 left-0 z-50 h-full w-72 bg-white flex flex-col transition-transform duration-300 ease-in-out',
          isOpen ? 'translate-x-0' : '-translate-x-full',
        )}
        aria-label="Navigation menu"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <span className="font-display text-primary tracking-widest text-sm uppercase">Menu</span>
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
          {NAV_LINKS.map(({ href, label }) => (
            <Link
              key={href}
              href={href}
              onClick={onClose}
              className="flex items-center px-6 py-3.5 text-sm font-body text-gray-700 hover:text-primary hover:bg-primary/5 transition-colors uppercase tracking-widest"
            >
              {label}
            </Link>
          ))}
        </nav>

        {/* Auth links */}
        <div className="border-t border-gray-100 px-5 py-5 space-y-2">
          {user ? (
            <>
              <Link
                href="/account"
                onClick={onClose}
                className="flex items-center justify-center gap-2 w-full py-2.5 text-xs font-body uppercase tracking-widest text-primary border border-primary hover:bg-primary hover:text-white transition-all duration-200"
              >
                <span className="material-icons text-sm">person</span>
                My Account
              </Link>
              <button
                onClick={handleLogout}
                className="flex items-center justify-center gap-2 w-full py-2.5 text-xs font-body uppercase tracking-widest text-gray-500 border border-gray-300 hover:bg-gray-50 transition-all duration-200"
              >
                <span className="material-icons text-sm">logout</span>
                Sign Out
              </button>
            </>
          ) : (
            <>
              <Link
                href="/login"
                onClick={onClose}
                className="flex items-center justify-center gap-2 w-full py-2.5 text-xs font-body uppercase tracking-widest text-primary border border-primary hover:bg-primary hover:text-white transition-all duration-200"
              >
                <span className="material-icons text-sm">person</span>
                Log In
              </Link>
              <Link
                href="/signup"
                onClick={onClose}
                className="flex items-center justify-center gap-2 w-full py-2.5 text-xs font-body uppercase tracking-widest bg-primary text-white hover:opacity-90 transition-all duration-200"
              >
                <span className="material-icons text-sm">person_add</span>
                Sign Up
              </Link>
            </>
          )}
        </div>

        {/* Tagline */}
        <p className="text-center text-xs text-gray-400 font-body italic pb-5">
          Made with 100% Love
        </p>
      </aside>
    </>
  );
}
