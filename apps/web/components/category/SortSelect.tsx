'use client';

import { useRouter } from 'next/navigation';
import { analytics, type Surface } from '@/lib/analytics';
import type { SortOption } from '@/lib/product-sort';

/**
 * The sort control above a product listing.
 *
 * A real `<form method="get">` rather than component state: the listing pages
 * are server components and the sort lives in the URL, so the browser's own
 * submission already produces the right address. The change handler only
 * upgrades that to a client navigation. Anything already narrowing the list
 * (the category chip) rides along as a hidden field, and `page` deliberately
 * does not — re-sorting puts you back on page one, since page 3 of the old
 * order means nothing in the new one.
 */
export function SortSelect({
  action,
  preserved,
  value,
  options,
  label,
  surface = 'category',
}: {
  action: string;
  preserved?: Record<string, string>;
  value?: string;
  options: SortOption[];
  label: string;
  /** Which listing the control is sitting above. */
  surface?: Surface;
}) {
  const router = useRouter();
  const hidden = Object.entries(preserved ?? {});

  return (
    <form
      method="get"
      action={action}
      className="flex items-center gap-2 shrink-0"
      onSubmit={(e) => {
        e.preventDefault();
        const params = new URLSearchParams(preserved ?? {});
        const sort = String(new FormData(e.currentTarget).get('sort') ?? '');
        if (sort) params.set('sort', sort);
        // Which orderings people actually reach for. "Price, low to high" being
        // the overwhelming pick is a different shop from "Newest" being it, and
        // it is the default ordering of every listing that should follow.
        analytics.sortProducts({
          sort: sort || 'default',
          surface,
          category: preserved?.category,
        });
        const qs = params.toString();
        router.push(qs ? `${action}?${qs}` : action);
      }}
    >
      {hidden.map(([name, v]) => (
        <input key={name} type="hidden" name={name} value={v} />
      ))}
      <label
        htmlFor="plp-sort"
        className="sr-only sm:not-sr-only font-body text-[11px] uppercase tracking-widest text-gray-400 whitespace-nowrap"
      >
        {label}
      </label>
      <select
        id="plp-sort"
        name="sort"
        defaultValue={value ?? ''}
        onChange={(e) => e.currentTarget.form?.requestSubmit()}
        // Below `sm` the anti-zoom rule in globals.css forces this to 16px and
        // cannot be argued with, so the width has to come off the letterforms
        // instead: no uppercase, no extra tracking, and a cap so the longest
        // option truncates rather than pushing the heading off its own row.
        className="max-w-[11rem] sm:max-w-full font-body text-[11px] normal-case sm:uppercase tracking-normal sm:tracking-wider text-primary bg-white border border-gray-200 px-2 py-1 cursor-pointer outline-none focus:border-primary/60"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </form>
  );
}
