'use client';

import { useState } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { useCart } from '@/lib/cart-context';
import { useAuth } from '@/lib/auth-context';
import { MobileMenu } from './MobileMenu';

export function Header() {
  const [menuOpen, setMenuOpen] = useState(false);
  const { itemCount } = useCart();
  const { user } = useAuth();

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

          {/* Center — logo + overlaid brand text */}
          <div className="flex justify-center">
            <Link href="/" className="flex flex-col items-center gap-0.5 group" aria-label="Melting Moments home">
              <div className="relative w-9 h-9">
                <Image
                  src="/images/logos/color_logo.jpeg"
                  alt="Melting Moments"
                  fill
                  sizes="36px"
                  className="object-contain"
                  priority
                />
              </div>
              <span className="font-display text-[11px] tracking-[0.25em] text-primary uppercase leading-none group-hover:opacity-80 transition-opacity">
                Melting Moments
              </span>
            </Link>
          </div>

          {/* Right — account + cart */}
          <div className="flex items-center justify-end gap-1">
            <Link
              href={user ? '/account' : '/login'}
              className="p-2 text-gray-600 hover:text-primary transition-colors"
              aria-label={user ? 'My account' : 'Sign in'}
            >
              <span className="material-icons">{user ? 'person' : 'person_outline'}</span>
            </Link>
            <Link
              href="/cart"
              className="relative p-2 -mr-2 text-gray-600 hover:text-primary transition-colors"
              aria-label={`Cart, ${itemCount} item${itemCount !== 1 ? 's' : ''}`}
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

      <MobileMenu isOpen={menuOpen} onClose={() => setMenuOpen(false)} />
    </>
  );
}
