/**
 * Section order on the home page is CMS-owned, but the CMS is not the source of
 * truth for which sections *exist* — the code is. So an order stored before a
 * section shipped must not make that section invisible, and an order naming a
 * section that has since been deleted must not blow up the page.
 */

export const SECTION_ORDER = [
  'hero',
  'usps',
  'featured',
  'categories',
  'promos',
  'baker',
  'cater',
] as const;

export type SectionKey = (typeof SECTION_ORDER)[number];

export interface HomeLayout {
  order?: string[];
  hidden?: string[];
}

/**
 * The sections to render, in order: the CMS order first (unknown keys dropped),
 * then anything the CMS never mentioned, then hidden sections removed.
 */
export function orderedSections(layout: HomeLayout | undefined): SectionKey[] {
  const known = new Set<string>(SECTION_ORDER);
  const hidden = new Set(layout?.hidden ?? []);

  const configured: SectionKey[] = [];
  for (const key of layout?.order ?? []) {
    if (known.has(key) && !configured.includes(key as SectionKey)) {
      configured.push(key as SectionKey);
    }
  }

  const seen = new Set<string>(configured);
  return [...configured, ...SECTION_ORDER.filter(k => !seen.has(k))].filter(k => !hidden.has(k));
}
