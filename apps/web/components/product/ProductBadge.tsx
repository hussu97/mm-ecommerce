/**
 * The corner flag on a product tile — "Bestseller" and friends.
 *
 * Deliberately not `bg-primary`: the add-to-cart button below it is solid
 * primary, and a solid primary flag on the image read as a second button
 * sitting on the picture. A frosted dark chip keeps it legible over the cream
 * (#f9f5f0 / #f4ece4) image backgrounds while reading as a label, not a target.
 */
export function ProductBadge({ children }: { children: React.ReactNode }) {
  return (
    <span className="absolute top-2 start-2 z-10 bg-gray-900/55 text-white backdrop-blur-sm font-body text-[9px] uppercase tracking-[0.18em] px-2 py-1 rounded-sm pointer-events-none">
      {children}
    </span>
  );
}
