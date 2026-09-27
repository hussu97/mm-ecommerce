/**
 * `<meta name="description">` built to the length a search engine keeps.
 *
 * Bing flagged 17 pages as "too short", and the reason is that the product and
 * category descriptions were the storefront blurb, word for word: "Box of 9 Mix
 * Cookies" is a fine caption under a photo and a twenty-character search
 * snippet. Google quietly rewrites a snippet that short from the page body;
 * Bing reports it. Both keep roughly 150–160 characters, so that is the target.
 *
 * The blurb stays the lead, because it is the one sentence that is actually
 * about this product. What follows it is chosen per page from slots of
 * alternatives: one alternative, or none, from each slot, whichever combination
 * comes closest to the limit without passing it. So a twenty-character lead
 * gets the full "baked in Sharjah, delivered across the UAE" sentence, and a
 * hundred-character lead gets the short one plus whatever else still fits.
 *
 * It searches every combination rather than taking the longest option from
 * each slot in turn. Taking the longest first left `/ar/mix-boxes` at 133: the
 * long origin sentence filled the gap that a slightly shorter one would have
 * shared with the pickup line. There are at most a few dozen combinations.
 */

export const META_DESCRIPTION_MAX = 160;

/** Required lead, then optional slots of alternatives in preference order. */
export type MetaDescriptionSlot = string | readonly string[] | null | undefined;

const TERMINAL = /[.!?؟…]$/;

function sentence(text: string): string {
  const clean = text.replace(/\s+/g, ' ').trim();
  return clean && !TERMINAL.test(clean) ? `${clean}.` : clean;
}

/** Cut at the last word boundary that leaves room for the ellipsis. */
function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  const cut = text.slice(0, max - 1);
  const space = cut.lastIndexOf(' ');
  return `${(space > max / 2 ? cut.slice(0, space) : cut).replace(/[\s,;:—–-]+$/, '')}…`;
}

export function composeMetaDescription(
  lead: string,
  slots: readonly MetaDescriptionSlot[] = [],
  max: number = META_DESCRIPTION_MAX,
): string {
  const first = truncate(sentence(lead), max);
  const choices = slots
    .filter((slot): slot is string | readonly string[] => !!slot)
    .map((slot) => (typeof slot === 'string' ? [slot] : slot).map(sentence).filter(Boolean));

  // Scored on length, less a penalty for every slot left out. Slots come in
  // priority order, so leaving out an earlier one costs more: SKIP_PENALTY
  // for the last slot, and double for each slot before it (10, 20, 40…). A
  // slot therefore costs more than every slot after it put together, so it is
  // never traded away for them. Without
  // the penalty the longest string wins even when it gets there by dropping
  // the slot that matters. "Our bigger and better brownies…" took the long
  // pickup line in place of "delivered across the UAE", which is the phrase
  // people search.
  //
  // Depth-first, with the preferred alternative first and "skip" last. Only a
  // strictly better score replaces the best so far, so ties go to the
  // earlier-listed options.
  let best = first;
  let bestScore = -Infinity;
  const walk = (i: number, acc: string, skipped: number) => {
    if (i === choices.length) {
      const score = acc.length - SKIP_PENALTY * skipped;
      if (score > bestScore) {
        best = acc;
        bestScore = score;
      }
      return;
    }
    for (const option of choices[i]) {
      const next = acc ? `${acc} ${option}` : option;
      if (next.length <= max) walk(i + 1, next, skipped);
    }
    walk(i + 1, acc, skipped + 2 ** (choices.length - 1 - i));
  };
  walk(0, first, 0);
  return best;
}

/** Characters a combination must gain to leave out the last slot (earlier slots cost multiples of it). */
const SKIP_PENALTY = 10;

/** Split copy into its sentences, keeping each one's closing punctuation. */
export function splitSentences(text: string): string[] {
  return (text.replace(/\s+/g, ' ').trim().match(/[^.!?؟]+[.!?؟]*/g) ?? [])
    .map(s => s.trim())
    .filter(Boolean);
}

type Lang = 'en' | 'ar';

const lang = (locale: string): Lang => (locale === 'ar' ? 'ar' : 'en');

/**
 * Where it is made and where it goes — the claim every product and category
 * page can make. Kept to facts that hold for the whole catalogue: nothing about
 * fees or thresholds, which differ by zone and already went stale once
 * (`118_delivery_copy_matches_map`).
 */
const ORIGIN: Record<Lang, readonly string[]> = {
  en: [
    'Baked fresh to order in our Sharjah kitchen, delivered across Dubai, Sharjah, Ajman and the rest of the UAE.',
    'Baked fresh to order in Sharjah, delivered across Dubai, Sharjah, Ajman and the UAE.',
    'Baked to order in Sharjah and delivered across Dubai, Sharjah, Ajman and the UAE.',
    'Baked to order in Sharjah and delivered across the UAE.',
    'Baked to order in Sharjah, delivered across the UAE.',
    'Delivered across the UAE.',
  ],
  ar: [
    'تُخبز طازجة عند الطلب في مطبخنا بالشارقة وتُوصَّل إلى دبي والشارقة وعجمان وبقية الإمارات.',
    'تُخبز طازجة عند الطلب في الشارقة وتُوصَّل إلى دبي والشارقة وعجمان وبقية الإمارات.',
    'تُخبز عند الطلب في الشارقة وتُوصَّل إلى دبي والشارقة وعجمان وكل الإمارات.',
    'تُخبز عند الطلب في الشارقة وتُوصَّل إلى دبي وعجمان وكل الإمارات.',
    'تُخبز عند الطلب في الشارقة وتُوصَّل إلى كل الإمارات.',
    'توصيل إلى كل الإمارات.',
  ],
};

const CALL_TO_ACTION: Record<Lang, readonly string[]> = {
  en: ['Order online from Melting Moments Cakes.', 'Order online today.', 'Order online.'],
  ar: ['اطلب الآن أونلاين من ملتنج مومنتس.', 'اطلب أونلاين الآن.', 'اطلب الآن.'],
};

/** The other way to get it — collecting from Sharjah costs nothing (FAQ). */
const PICKUP: Record<Lang, readonly string[]> = {
  en: [
    'Delivery, or free pickup from our Sharjah kitchen.',
    'Or free pickup in Sharjah.',
    'Free pickup in Sharjah.',
  ],
  ar: [
    'توصيل، أو استلام مجاني من مطبخنا في الشارقة.',
    'أو استلام مجاني من الشارقة.',
    'والاستلام مجاني من الشارقة.',
    'الاستلام مجاني.',
  ],
};

/** A product page: its own blurb, then origin, pickup and a call to action. */
export function productMetaDescription(opts: {
  name: string;
  description?: string | null;
  locale: string;
}): string {
  const l = lang(opts.locale);
  const lead =
    opts.description?.trim() ||
    (l === 'ar' ? `اطلب ${opts.name} من ملتنج مومنتس` : `Order ${opts.name} from Melting Moments Cakes`);
  return composeMetaDescription(lead, [ORIGIN[l], PICKUP[l], CALL_TO_ACTION[l]]);
}

/**
 * A category page. Its own description when the admin wrote one, otherwise a
 * lead naming the category — in the page's language, which the old fallback
 * was not: every Arabic category page read "Order كوكيز from Melting Moments
 * Cakes…".
 */
export function categoryMetaDescription(opts: {
  name: string;
  description?: string | null;
  locale: string;
}): string {
  const l = lang(opts.locale);
  const lead =
    opts.description?.trim() ||
    (l === 'ar'
      ? `اطلب ${opts.name} أونلاين من ملتنج مومنتس`
      : `Order ${opts.name.toLowerCase()} online from Melting Moments Cakes`);
  // The fallback lead is already a call to action, so it is followed by the
  // pickup line instead of a second "order online".
  return composeMetaDescription(lead, [
    ORIGIN[l],
    opts.description?.trim() ? CALL_TO_ACTION[l] : PICKUP[l],
  ]);
}
