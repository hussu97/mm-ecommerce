/**
 * Whether the API purges this storefront when what it shows changes.
 *
 * The API POSTs the affected tags to `app/api/revalidate/route.ts` after every
 * committed write to the catalogue, stock, busy mode, the delivery map, a promo
 * code, CMS, blog or translations, and again when the clock alone changes
 * something (a timed stockout lapsing, a coupon's window opening or closing) —
 * see `apps/api/app/services/storefront_cache.py`. Both ends hold the same
 * `STOREFRONT_REVALIDATE_SECRET`, so its presence here is the switch: without
 * it nothing can purge this app, and every TTL below stays what it was.
 *
 * Server-only (no `NEXT_PUBLIC_`): in a client bundle this reads `undefined`,
 * which is false, which is harmless — no client fetch uses the data cache.
 */
export const ON_DEMAND_PURGE = Boolean(process.env.STOREFRONT_REVALIDATE_SECRET);

/**
 * How long the storefront may serve editable content it has already fetched.
 *
 * One number, in one place, because it is a business decision rather than a
 * technical one: it is the longest an admin edit can take to reach a customer
 * when nothing else tells the storefront about it. Every fetch of CMS copy,
 * translations, categories, products and the advertised coupon reads it.
 *
 * **Ten minutes with the on-demand purge, one without.** With the purge, an
 * edit, a stockout or a lapsed coupon reaches the page within seconds whatever
 * this says (`revalidateTag`), and this is only the net under a purge that
 * never arrived. Without it, this *is* the mechanism, and it keeps the minute
 * it was chosen at.
 *
 * The minute was chosen against a specific history. Both of the fetches this
 * replaced used `cache: 'no-store'`, and both had an incident behind them — a
 * build that snapshotted translations five minutes stale and put a raw
 * `checkout.estimated_delivery` in front of customers, and a data-cache entry
 * that left one locale on the old home page while the other had the new one.
 * `no-store` ruled both out by never caching, and made every route on the site
 * dynamic to do it. A bounded, self-healing delay ruled both out without that;
 * the purge keeps them out under the longer number, because the API purges
 * every tag when it boots (after the seed, after a deploy's migrations).
 *
 * Route-segment `export const revalidate` lines quote 600 as a literal (Next
 * requires segment config to be statically analyzable). A route revalidates on
 * the lowest TTL of everything it fetched, so with the purge off these fetches
 * still pull those routes down to the minute.
 */
export const CONTENT_TTL = ON_DEMAND_PURGE ? 600 : 60;

/**
 * Content the purge does not reach, held for the old minute either way. The
 * pickup-point list carries *today's* opening hours, which change at midnight
 * with no write for the API to see.
 */
export const UNPURGED_TTL = 60;

/**
 * How long the machine-read surfaces may serve a stale copy: the llms.txt
 * feeds, and the delivery-rate figure baked into product JSON-LD. The
 * sitemaps quote the same hour, but as a hard CDN `s-maxage` over a live
 * build, not as ISR over the data cache. Those two stale-while-revalidate
 * layers, moved only by sparse crawler visits, left the sitemap hours behind
 * the catalogue. See `app/sitemap.xml/route.ts`.
 *
 * An hour, not `CONTENT_TTL`'s minute, because the reader is a crawler rather
 * than a customer: nobody is standing in front of a sitemap waiting for an
 * admin edit to show up, and these endpoints fan out into paginated fetches of
 * the whole catalogue — a short TTL multiplies that cost for an audience that
 * revisits daily at best.
 *
 * (Route-segment `export const revalidate` lines quote the same number as a
 * literal — Next requires segment config to be statically analyzable, so they
 * cannot import this constant. This is the number they mirror.)
 */
export const FEED_TTL = 3600;

/**
 * Blog content. Posts are published, not live-edited, so minutes-stale is
 * invisible; five of them keeps the per-request cost negligible while an
 * editorial fix still lands the same coffee break it was made in. Tagged
 * `blog` and purged on write, so with the purge on it is an hour.
 */
export const BLOG_TTL = ON_DEMAND_PURGE ? 3600 : 300;

/**
 * The language list. New languages ship with releases, not admin edits — the
 * slowest-moving content the storefront fetches, and the safest to hold. Purged
 * with `i18n` on write, so with the purge on it is an hour.
 */
export const LANGUAGES_TTL = ON_DEMAND_PURGE ? 3600 : 300;

/**
 * Whether this build or runtime has a real API behind it.
 *
 * `NEXT_PUBLIC_API_URL` is an absolute URL wherever there is one to talk to —
 * Vercel production and previews both set it. It is unset in CI, where the
 * build is a type-check and a compile and there is no API on the runner at all,
 * and it is a relative path in local dev, where requests go through the Next
 * rewrite.
 *
 * This exists to decide what a failed fetch *means*. Where an API is configured
 * and does not answer, that is a fault and the fetch should throw, so a build
 * fails rather than prerendering a page of raw translation keys and an empty
 * catalogue. Where there is no API by design, the same failure is expected and
 * a page rendered from empty data is a perfectly good compile check.
 *
 * The distinction is the whole point: without it, adding ISR turned CI's
 * apiless build into a hard failure.
 */
export const HAS_REMOTE_API = (process.env.NEXT_PUBLIC_API_URL ?? '').startsWith('http');

/**
 * Cache tags — what the API's on-demand purge aims at. Mirrored by `WEB_TAGS`
 * in `apps/api/app/services/storefront_cache.py`, and the revalidate route
 * accepts only these; a tag added here and not there lives on its TTL alone.
 */
export const CACHE_TAGS = {
  /** UI strings and the language list. */
  i18n: 'i18n',
  /** CMS page content — home, about, faq, privacy, contact. */
  cms: 'cms',
  /** Categories and products. */
  catalogue: 'catalogue',
  /**
   * The advertised new-customer coupon.
   *
   * Its own tag rather than sharing `catalogue`, because retiring a campaign
   * and editing a product are different acts with different urgency — an
   * expired offer left on the homepage is a promise the checkout will refuse.
   */
  promo: 'promo',
  /** Blog posts and the blog index. */
  blog: 'blog',
} as const;
