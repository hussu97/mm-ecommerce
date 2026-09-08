import type { BadgeVariant } from '@/lib/product-badge';

/**
 * The corner flag on a product tile — "Bestseller", "Website Exclusive" and
 * friends. Presentational: the caller resolves the winning variant (see
 * `resolveProductBadge`) and the label text, this styles the chip. Kept free of
 * hooks so it renders in a server component (the PDP) as well as the client
 * tiles.
 *
 * `bestseller` is deliberately not `bg-primary`: the add-to-cart button below it
 * is solid primary, and a solid primary flag on the image read as a second
 * button sitting on the picture. A frosted dark chip keeps it legible over the
 * cream (#f9f5f0 / #f4ece4) image backgrounds while reading as a label.
 *
 * `website_exclusive` is the one that gets to shout: a warm champagne-gold chip
 * that stands apart from both the dark bestseller flag and the plum ATC button,
 * because the whole point of the launch is that this item is special.
 */
const VARIANT_CLASS: Record<BadgeVariant, string> = {
  bestseller: 'bg-gray-900/55 text-white backdrop-blur-sm',
  website_exclusive: 'bg-amber-300/95 text-amber-950 shadow-sm',
  new: 'bg-emerald-600/90 text-white backdrop-blur-sm',
  limited: 'bg-rose-700/90 text-white backdrop-blur-sm',
};

export function ProductBadge({
  variant = 'bestseller',
  children,
}: {
  variant?: BadgeVariant;
  children: React.ReactNode;
}) {
  return (
    <span
      className={`absolute top-2 start-2 z-10 font-body text-[9px] uppercase tracking-[0.18em] px-2 py-1 rounded-sm pointer-events-none ${VARIANT_CLASS[variant]}`}
    >
      {children}
    </span>
  );
}
