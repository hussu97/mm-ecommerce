'use client';

import Link from 'next/link';
import { analytics } from '@/lib/analytics';

type Creative = 'hero' | 'promo_banner' | 'category_tile' | 'cater' | 'usp';

/**
 * A merchandising link that says which piece of merchandising it was.
 *
 * The hero, the promo banners, the category tiles and the catering block are
 * the most expensive space on the homepage and not one click on any of them was
 * recorded — so which slide sells, and whether the fourth one is ever reached at
 * all, were questions answered by taste. Page views cannot answer them either:
 * every one of these links lands on `/all-products` or a category, so the
 * destination says nothing about which creative sent the customer there.
 *
 * A client component wrapping `Link` rather than a hook, because three of the
 * four sections are server components and this keeps them that way — the same
 * shape as `ContactLink`, for the same reason.
 */
export function PromotionLink({
  href,
  creative,
  slot,
  title,
  className,
  children,
}: {
  href: string;
  creative: Creative;
  /** Position within its own section, from zero. */
  slot: number;
  title: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className={className}
      onClick={() =>
        analytics.selectPromotion({ creative, slot, title, target: href })
      }
    >
      {children}
    </Link>
  );
}
