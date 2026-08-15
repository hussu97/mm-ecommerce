/**
 * How long the storefront may serve editable content it has already fetched.
 *
 * One number, in one place, because it is a business decision rather than a
 * technical one: it is the longest an admin edit can take to reach a customer.
 * Every fetch of CMS copy, translations, categories and products reads it, so
 * changing that answer is changing this line.
 *
 * Sixty seconds is chosen against a specific history. Both of the fetches this
 * replaced used `cache: 'no-store'`, and both had an incident behind them — a
 * build that snapshotted translations five minutes stale and put a raw
 * `checkout.estimated_delivery` in front of customers, and a data-cache entry
 * that left one locale on the old home page while the other had the new one.
 * `no-store` ruled both out by never caching, and made every route on the site
 * dynamic to do it: nothing reached the CDN, and every visit paid for a render.
 *
 * A minute keeps the worst case of both bounded and self-healing, which neither
 * incident was — the five-minute one only ended when its own TTL did, and the
 * locale one persisted until someone noticed.
 *
 * It is not the end state. Tagging every one of these fetches (`i18n`, `cms`,
 * `catalogue`) means an admin write can call `revalidateTag` and take the delay
 * to zero, leaving this as the safety net rather than the mechanism. That needs
 * a shared secret between the API and this app and is deliberately not done
 * here.
 */
export const CONTENT_TTL = 60;

/**
 * How long the machine-read surfaces may serve a stale copy: sitemaps, the
 * llms.txt feeds, and the delivery-rate figure baked into product JSON-LD.
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
 * editorial fix still lands the same coffee break it was made in.
 */
export const BLOG_TTL = 300;

/**
 * The language list. New languages ship with releases, not admin edits — the
 * slowest-moving content the storefront fetches, and the safest to hold.
 */
export const LANGUAGES_TTL = 300;

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

/** Cache tags, so a later on-demand purge has something to aim at. */
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
} as const;
