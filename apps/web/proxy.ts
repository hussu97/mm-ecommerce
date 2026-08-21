import { NextRequest, NextResponse, type NextFetchEvent } from "next/server";

const SUPPORTED_LOCALES = (process.env.NEXT_PUBLIC_SUPPORTED_LOCALES ?? "en,ar").split(",");

/**
 * Where a *visitor* lands when their device asks for a language we do not serve.
 *
 * Arabic, because the shop is in Sharjah and serves the UAE: a browser asking
 * for French here is far likelier to be read by someone who reads Arabic than
 * by someone who chose English deliberately. A device that *does* ask for
 * English still gets English — this is the fallback, not the preference.
 */
const FALLBACK_LOCALE = "ar";

/**
 * Where a request with **no** `accept-language` header at all lands.
 *
 * English, because the only clients that send no language are crawlers, link
 * previewers and scripts — and every page on this site declares
 * `hreflang="x-default"` as its English URL. The two disagreed until now:
 * `meltingmomentscakes.com` 307'd Googlebot to `/ar` while the markup told it
 * the default was `/en`, which is why the English brand result on Google was
 * showing an Arabic description of the shop.
 *
 * Deliberately not the same constant as `FALLBACK_LOCALE`. They answer
 * different questions — "we cannot serve what you asked for" and "you did not
 * ask" — and collapsing them is what caused this. No human browser reaches
 * here: every one of them sends the header.
 */
const NO_PREFERENCE_LOCALE = "en";

const COOKIE_NAME = "mm_locale";

/**
 * The device's own language, honoured in the order the device ranked it.
 *
 * `accept-language` arrives pre-sorted by quality, so the first entry we
 * actually serve is the closest thing to what the person asked for. Region is
 * dropped — `ar-AE`, `ar-EG` and `ar` are all Arabic to us.
 */
function getLocaleFromHeaders(request: NextRequest): string {
  const acceptLang = request.headers.get("accept-language");
  if (!acceptLang) return NO_PREFERENCE_LOCALE;

  const preferred = acceptLang
    .split(",")
    .map((lang) => lang.split(";")[0].trim().split("-")[0])
    .find((code) => SUPPORTED_LOCALES.includes(code));

  return preferred ?? FALLBACK_LOCALE;
}

// ─── Redirects ────────────────────────────────────────────────────────────────

/**
 * Where the API lives from in here.
 *
 * Computed inline rather than imported from `lib/api-server.ts`: that module is
 * for React Server Components and pulls in things this runtime does not have.
 * Same two env vars, same precedence.
 */
const API_BASE = process.env.NEXT_PUBLIC_API_URL?.startsWith("http")
  ? process.env.NEXT_PUBLIC_API_URL
  : `${process.env.NEXT_PRIVATE_API_HOST ?? "http://localhost:8000"}/api/v1`;

type RedirectRule = {
  from_path: string;
  to_path: string;
  is_prefix: boolean;
  status_code: number;
};

/**
 * The rule table, held in memory between requests.
 *
 * Redirects have to be answered *before* the response starts, which rules out
 * doing this in a page: `app/[locale]/[category]/loading.tsx` used to make Next
 * commit a 200 and stream the shell before the page component ran, so the
 * `permanentRedirect()` in the product page never produced a redirect at all —
 * it rendered the product under the wrong URL with a canonical tag and hoped.
 * That is fixed, but the fix is one `loading.tsx` away from being undone, and a
 * URL with no route behind it never reaches a page in the first place.
 *
 * So: one fetch per cold instance per TTL, a lookup per request after that. The
 * table is small — the eight category renames plus whatever the console has
 * added — and the endpoint is public, CDN-cacheable and unauthenticated,
 * because a list of URLs that have moved is the least secret thing the shop
 * owns.
 */
let rulesCache: { rules: RedirectRule[]; expires: number } | null = null;
const RULES_TTL_MS = 60_000;

/**
 * Fails open, on purpose.
 *
 * If the API is unreachable this returns the rules it last had, or none. A
 * storefront that serves every page and forgets some redirects is a bad
 * morning; a storefront that 500s because a redirect table could not be read is
 * an outage, and the thing that broke would be a feature for handling *old*
 * URLs.
 */
async function getRules(): Promise<RedirectRule[]> {
  const now = Date.now();
  if (rulesCache && rulesCache.expires > now) return rulesCache.rules;

  try {
    const res = await fetch(`${API_BASE}/redirects/map`, {
      signal: AbortSignal.timeout(2000),
    });
    if (!res.ok) throw new Error(String(res.status));
    const body = (await res.json()) as { rules: RedirectRule[] };
    rulesCache = { rules: body.rules ?? [], expires: now + RULES_TTL_MS };
  } catch {
    // Keep serving the last good table rather than dropping every rule the
    // moment the API hiccups; only a cold instance ends up with nothing.
    rulesCache = { rules: rulesCache?.rules ?? [], expires: now + RULES_TTL_MS };
  }
  return rulesCache.rules;
}

/** The one spelling of a path the table stores: lowercase, no trailing slash. */
function normalise(path: string): string {
  const cleaned = path.toLowerCase().replace(/\/+$/, "");
  return cleaned || "/";
}

/**
 * The rule that covers this path, or null.
 *
 * Rules arrive longest-first from the API, so the first match is the most
 * specific one and there is no scoring to do here. An exact match always beats
 * a prefix match of the same path because the exact rule is at least as long.
 */
function matchRule(path: string, rules: RedirectRule[]): { rule: RedirectRule; to: string } | null {
  for (const rule of rules) {
    if (path === rule.from_path) return { rule, to: rule.to_path };
    if (rule.is_prefix && path.startsWith(`${rule.from_path}/`)) {
      return { rule, to: rule.to_path + path.slice(rule.from_path.length) };
    }
  }
  return null;
}

/**
 * Resolve a path through the table, following at most a few hops.
 *
 * `redirect_service` collapses chains on write, so a table built by this system
 * never needs a second hop. The loop is for one that was not — a hand-written
 * SQL fix, a restored dump — where the alternative to a cap is a request that
 * never ends. The `seen` set is what turns a loop into "stop here" instead of
 * "stop after three".
 */
function resolve(path: string, rules: RedirectRule[]): { to: string; status: number; matched: string } | null {
  let current = path;
  let status = 308;
  let matched: string | null = null;
  const seen = new Set<string>([path]);

  for (let hop = 0; hop < 3; hop++) {
    const hit = matchRule(current, rules);
    if (!hit) break;
    if (seen.has(hit.to)) break;
    if (matched === null) matched = hit.rule.from_path;
    seen.add(hit.to);
    current = hit.to;
    status = hit.rule.status_code;
  }

  return matched === null ? null : { to: current, status, matched };
}

export async function proxy(request: NextRequest, event?: NextFetchEvent) {
  const { pathname } = request.nextUrl;

  // Skip static files, API routes, and Next.js internals
  //
  // `/vague/` is the analytics proxy — the script rewritten to Umami Cloud in
  // `next.config.ts`, the events handled by `app/vague/api/send/route.ts`. It is
  // not a page and has no language, but `/vague/api/send` also has no dot in it,
  // so without naming it here it fell through to the locale rule below: every
  // event the tracker posted was answered with a 307 to the locale-prefixed
  // path and had to be sent a second time. That survived on a fast connection
  // and was pure waste on any other — and the requests most likely to be caught
  // mid-redirect are the ones fired as the page is being replaced, which is
  // exactly `begin_checkout`, `checkout_step_complete` and the hop out to the
  // payment gateway.
  //
  // The trailing slash matters. Every other entry here is a reserved prefix
  // nothing else can claim, but this one is an ordinary word: without the slash
  // it would also swallow any category or product slug that merely starts with
  // it, and strand that page on a 404 instead of sending it to a language.
  //
  // `/monitoring` is the same failure as `/vague/`, one layer over: it is the
  // Sentry tunnel (`tunnelRoute` in `next.config.ts`, `tunnel` in
  // `instrumentation-client.ts`), the path the browser posts crash reports to
  // so an ad blocker cannot see them going to Sentry. It has no dot and no
  // locale, so it fell through to the rule below and was answered with a 307 to
  // `/ar/monitoring` — which is not the tunnel, it is `[locale]/[category]`
  // rendering a category called "monitoring". Every crash report the storefront
  // tried to file was redirected into a product listing and thrown away, and
  // the listing then asked the API for a category that does not exist: thirty
  // `GET /api/v1/categories/monitoring 404`s in twelve hours, one per report we
  // never received.
  //
  // Matched exactly, not as a prefix. Sentry posts to `/monitoring` itself and
  // carries the destination in the query string, so there is nothing below it
  // to reserve — and an exact match leaves a slug like `monitoring-cakes` free
  // to be an ordinary page, which is the trap described above.
  if (
    pathname.startsWith("/_next") ||
    pathname.startsWith("/api") ||
    pathname.startsWith("/vague/") ||
    pathname === "/monitoring" ||
    pathname.startsWith("/images") ||
    pathname.startsWith("/favicon") ||
    pathname.includes(".")
  ) {
    return NextResponse.next();
  }

  // Check if pathname already has a locale prefix
  const pathnameSegments = pathname.split("/");
  const firstSegment = pathnameSegments[1];
  const hasLocale = SUPPORTED_LOCALES.includes(firstSegment);

  // ── Redirects ──────────────────────────────────────────────────────────────
  //
  // Before the locale rule, so a moved URL is answered in one hop rather than
  // 307'd to a locale and then 308'd again from there — two round trips, and a
  // chain search engines pass less through than a single jump.
  //
  // Looked up twice: once as written, so a rule for `/en/about-me` can be about
  // English only, and once with the locale stripped, because a slug rename
  // breaks both languages at once and the table stores that as one row.
  const rules = await getRules();
  const asWritten = normalise(pathname);
  const localeless = hasLocale ? normalise(`/${pathnameSegments.slice(2).join("/")}`) : asWritten;

  const hit =
    resolve(asWritten, rules) ??
    (localeless !== asWritten ? resolve(localeless, rules) : null);

  // The language this visitor gets: the one in the URL, else the one they were
  // last reading, else what their device asks for. Needed here as well as below
  // because a redirect from an unprefixed URL should land on a real page in one
  // hop rather than bouncing through the locale rule for a second.
  const cookieLocale = request.cookies.get(COOKIE_NAME)?.value;
  const locale = hasLocale
    ? firstSegment
    : cookieLocale && SUPPORTED_LOCALES.includes(cookieLocale)
      ? cookieLocale
      : getLocaleFromHeaders(request);

  if (hit) {
    // A rule that names a locale has said which one it means; one that does not
    // is about both, and answers in the language the visitor was already
    // reading. `/` is spelled as the bare locale rather than `/en/`.
    const targetHasLocale = SUPPORTED_LOCALES.includes(hit.to.split("/")[1] ?? "");
    // `new URL(request.url)` rather than `nextUrl.clone()`: NextURL remembers
    // that the request had a trailing slash and puts it back when it formats the
    // href, so `/en/cat-brownies/` redirected to `/en/brownies/` — a second
    // spelling of the destination, which is the duplicate this table exists to
    // remove. A plain URL takes the pathname it is given.
    const url = new URL(request.url);
    url.pathname = targetHasLocale
      ? hit.to
      : `/${locale}${hit.to === "/" ? "" : hit.to}`;

    const response = NextResponse.redirect(url, hit.status);
    response.cookies.set(COOKIE_NAME, locale, { path: "/", maxAge: 365 * 24 * 60 * 60 });
    // After the response, never in front of it: the count is bookkeeping and
    // must not be able to delay or fail somebody's redirect. `waitUntil` keeps
    // the instance alive for it; without an event we simply do not count.
    event?.waitUntil(
      fetch(`${API_BASE}/redirects/hit`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ from_path: hit.matched }),
        signal: AbortSignal.timeout(2000),
      }).catch(() => {}),
    );
    return response;
  }

  if (hasLocale) {
    // Valid locale prefix — set cookie and continue
    const response = NextResponse.next();
    response.cookies.set(COOKIE_NAME, firstSegment, { path: "/", maxAge: 365 * 24 * 60 * 60 });
    return response;
  }

  // No locale prefix — redirect to the one chosen above
  const url = request.nextUrl.clone();
  url.pathname = `/${locale}${pathname}`;

  const response = NextResponse.redirect(url);
  response.cookies.set(COOKIE_NAME, locale, { path: "/", maxAge: 365 * 24 * 60 * 60 });
  return response;
}

export const config = {
  // `monitoring$` rather than `monitoring`, for the reason spelled out above:
  // anchored, so the Sentry tunnel is skipped and a page whose slug merely
  // starts with the word is not.
  matcher: ["/((?!_next|api|vague/|images|favicon|monitoring$|.*\\..*).*)"],
};
