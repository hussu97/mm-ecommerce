import Link from 'next/link';

const CATEGORIES = [
  { href: '/brownies',    label: 'Brownies' },
  { href: '/cookies',     label: 'Cookies' },
  { href: '/cookie-melt', label: 'Cookie Melt' },
  { href: '/mix-boxes',   label: 'Mix Boxes' },
  { href: '/desserts',    label: 'Desserts' },
];

// Rendered server-side — Next.js <Link> prefetches in viewport automatically.
export function CategoryNav() {
  return (
    <nav
      aria-label="Category navigation"
      className="hidden sm:block border-b border-gray-100 bg-white"
    >
      <ul className="max-w-7xl mx-auto px-4 flex items-center gap-6 h-9 overflow-x-auto scrollbar-none">
        {CATEGORIES.map(({ href, label }) => (
          <li key={href} className="shrink-0">
            <Link
              href={href}
              prefetch={true}
              className="font-body text-[11px] uppercase tracking-widest text-gray-500 hover:text-primary transition-colors whitespace-nowrap"
            >
              {label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
