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

/** Cache tags, so a later on-demand purge has something to aim at. */
export const CACHE_TAGS = {
  /** UI strings and the language list. */
  i18n: 'i18n',
  /** CMS page content — home, about, faq, privacy, contact. */
  cms: 'cms',
  /** Categories and products. */
  catalogue: 'catalogue',
} as const;
