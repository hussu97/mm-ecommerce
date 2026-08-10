// ─── Microsoft Clarity ───────────────────────────────────────────────────────
//
// Session recordings, heatmaps and the *why* behind the numbers Umami counts.
// `window.clarity` is installed by the tag loaded in `app/[locale]/layout.tsx`.
// Everything here is a no-op when it is absent (no project id, dev, or a content
// blocker), and nothing here throws.
//
// The client API is four calls, all documented at
// learn.microsoft.com/en-us/clarity/setup-and-installation/clarity-api:
//
//   clarity('event', name)              a filterable event, listed beside Clarity's
//                                       own no-code smart events
//   clarity('set', key, value)          a custom tag — a filter over recordings
//   clarity('upgrade', reason)          keep this session when sampling
//   clarity('identify', id, …)          tie sessions to a customer
//   clarity('consentv2', { … })         cookie consent, when a banner exists
//
// Why this is a mirror of `analytics.ts` rather than a second set of call sites:
// there is exactly one function in this codebase that every storefront event
// passes through, and hooking it there means all 66 events — and every event
// added after this — reach both tools, with no way for the two dashboards to
// disagree about what happened.

import { createDeferredDispatch } from './deferred-dispatch';

type ClarityCall =
  | ['event', string]
  | ['set', string, string]
  | ['upgrade', string]
  | ['identify', string]
  | ['consentv2', { ad_Storage: 'granted' | 'denied'; analytics_Storage: 'granted' | 'denied' }];

type ClarityFn = (...args: ClarityCall) => void;

declare global {
  interface Window {
    clarity?: ClarityFn;
  }
}

/**
 * The tag is `afterInteractive` and pages report on mount, so the same race
 * Umami has applies here — see `deferred-dispatch.ts` for what that cost once.
 *
 * `resolve` reads `window.clarity` fresh on every attempt rather than closing
 * over it, which matters more here than it does for Umami: the tag *replaces*
 * the global when it finishes loading.
 */
const dispatch = createDeferredDispatch<ClarityFn, ClarityCall>({
  resolve: () => (typeof window === 'undefined' ? undefined : window.clarity),
  send: (clarity, call) => {
    try {
      clarity(...call);
    } catch {
      // A third party's script failing is not this page's problem to surface.
      // Nothing here is worth an error boundary or a Sentry event.
    }
  },
});

/**
 * The value of a tag, made safe to send.
 *
 * Clarity takes a string or an array of strings and nothing else. A number or a
 * boolean arriving unconverted is silently dropped, which is the worst outcome:
 * a filter that exists in the code, does not exist in the dashboard, and says
 * nothing about why.
 */
function tagValue(value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : null;
  if (typeof value === 'string') return value.length > 255 ? value.slice(0, 255) : value;
  return null;
}

export const clarity = {
  /** A custom event. Filterable, and usable as a heatmap/recording segment. */
  event: (name: string) => dispatch(['event', name]),

  /** A custom tag — one of the filters offered above the recordings list. */
  set: (key: string, value: unknown) => {
    const safe = tagValue(value);
    if (safe !== null) dispatch(['set', key, safe]);
  },

  /** Keep this session if Clarity ever starts sampling. */
  upgrade: (reason: string) => dispatch(['upgrade', reason]),

  /**
   * Tie a session to a customer.
   *
   * The opaque user id and nothing else. Clarity's `identify` also takes a
   * `friendly-name`, which is where an email or a real name would go, and this
   * deliberately does not pass one: a recording is watched by whoever has the
   * dashboard, and the id is enough to find the customer in the admin.
   */
  identify: (userId: string) => {
    if (userId) dispatch(['identify', userId]);
  },

  /**
   * Cookie consent, for when there is a banner to call it from.
   *
   * Nothing calls this today — the site has no cookie banner, the shop is UAE
   * based, and Clarity's consent enforcement applies to EEA/UK/CH traffic. It is
   * implemented rather than omitted because the alternative is discovering on
   * the day a banner is added that the tracker has no way to be told. See
   * `docs/microsoft-clarity-setup.md`.
   */
  consent: (granted: boolean) =>
    dispatch([
      'consentv2',
      {
        ad_Storage: granted ? 'granted' : 'denied',
        analytics_Storage: granted ? 'granted' : 'denied',
      },
    ]),
};

/**
 * The payload keys worth carrying into Clarity as a tag.
 *
 * A Clarity tag is a *filter over recordings* — a list you pick a value from
 * before watching a session. That makes it the opposite of a Umami property:
 * useful in proportion to how few distinct values it has. Forwarding the payload
 * wholesale would bury `surface` (7 values, the one you always want) under
 * `subtotal` (one value per basket, and unfilterable in practice).
 *
 * So: an allow-list, of dimensions somebody would actually pick from. Everything
 * numeric is excluded here and re-enters banded, below. Everything free-text is
 * excluded outright — `query` is whatever a customer typed, `error_message` is a
 * translated sentence, and neither belongs in a third party's filter list.
 */
const TAG_KEYS: ReadonlySet<string> = new Set([
  // Where and what
  'surface',
  'list',
  'creative',
  'category',
  'category_name',
  'product_name',
  'group_name',
  'option_name',
  'entry',
  'sort',
  'type',
  'title',
  'path',
  'endpoint',
  'channel',
  // Checkout shape
  'step',
  'field',
  'fields',
  'method',
  'delivery_method',
  'payment_provider',
  'provider',
  'stage',
  'action',
  'direction',
  'code',
  'promo_code',
  'status',
  // Answers, not measurements
  'reason',
  'serviceable',
  'free_applied',
  'free_available',
  'in_stock',
  'has_promo',
  'has_results',
  'has_modifiers',
  'has_saved_address',
  'has_pin',
  'is_new',
  'is_guest',
  'personalised',
  // The join key back to the admin. High cardinality on purpose: "show me the
  // session behind order MM-20260810-0042" is the question this makes possible,
  // and no other tag can answer it.
  'order_number',
]);

/** Where the money on an event lives, in the order we would rather read it. */
const VALUE_KEYS = ['total', 'subtotal', 'value', 'price'] as const;

/**
 * A basket's worth, as something you can filter on.
 *
 * `total: 143.75` is a filter with one value per order, which is no filter at
 * all. Six bands answers the question anyone actually asks of a recording list —
 * "show me what the big baskets did" — and stays a list you can click.
 */
export function valueBand(amount: number): string {
  if (!Number.isFinite(amount) || amount < 0) return 'unknown';
  if (amount === 0) return '0';
  if (amount < 50) return '0-50';
  if (amount < 100) return '50-100';
  if (amount < 200) return '100-200';
  if (amount < 500) return '200-500';
  return '500+';
}

/**
 * Sessions worth keeping when Clarity samples.
 *
 * A pattern rather than a list of event names, and that is the point: this
 * codebase's convention is that a failure event says so in its name and carries
 * a `reason` (see `docs/umami-analytics-setup.md`), so a failure added six
 * months from now is prioritised without anyone remembering to come back here. A
 * hand-maintained list of twenty names would be missing its twenty-first within
 * the quarter.
 */
const FAILURE_EVENT = /(?:failed|error|unavailable|unserviceable|not_found|cancelled)/;

/** The happy-path moments worth the same treatment. Intent, and its payoff. */
const PRIORITY_EVENTS: ReadonlySet<string> = new Set([
  'order_completed',
  'begin_checkout',
  'view_checkout',
]);

function upgradeReason(event: string): string | null {
  if (PRIORITY_EVENTS.has(event) || FAILURE_EVENT.test(event)) return event;
  return null;
}

/**
 * Send one storefront event to Clarity: the event, its filterable dimensions,
 * and a prioritisation if it is a session worth keeping.
 *
 * Called from `track()` in `analytics.ts` and nowhere else. `data` has already
 * been through `clean()` there, so empty properties are gone before they arrive.
 */
export function mirrorToClarity(event: string, data?: Record<string, unknown>) {
  clarity.event(event);

  if (data) {
    for (const [key, value] of Object.entries(data)) {
      if (TAG_KEYS.has(key)) clarity.set(key, value);
    }

    for (const key of VALUE_KEYS) {
      const amount = data[key];
      if (typeof amount === 'number') {
        clarity.set('value_band', valueBand(amount));
        break;
      }
    }
  }

  const reason = upgradeReason(event);
  if (reason) clarity.upgrade(reason);
}
