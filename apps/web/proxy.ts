import { NextRequest, NextResponse } from "next/server";

const SUPPORTED_LOCALES = (process.env.NEXT_PUBLIC_SUPPORTED_LOCALES ?? "en,ar").split(",");

/**
 * Where someone lands when their device tells us nothing we can use.
 *
 * Arabic, because the shop is in Sharjah and serves the UAE: a visitor whose
 * browser asks for French is far likelier to read Arabic here than to have
 * chosen English deliberately. A device that *does* ask for English still gets
 * English — this is the fallback, not the preference.
 */
const FALLBACK_LOCALE = "ar";
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
  if (!acceptLang) return FALLBACK_LOCALE;

  const preferred = acceptLang
    .split(",")
    .map((lang) => lang.split(";")[0].trim().split("-")[0])
    .find((code) => SUPPORTED_LOCALES.includes(code));

  return preferred ?? FALLBACK_LOCALE;
}

export function proxy(request: NextRequest) {
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
  if (
    pathname.startsWith("/_next") ||
    pathname.startsWith("/api") ||
    pathname.startsWith("/vague/") ||
    pathname.startsWith("/images") ||
    pathname.startsWith("/favicon") ||
    pathname.includes(".")
  ) {
    return NextResponse.next();
  }

  // Check if pathname already has a locale prefix
  const pathnameSegments = pathname.split("/");
  const firstSegment = pathnameSegments[1];

  if (SUPPORTED_LOCALES.includes(firstSegment)) {
    // Valid locale prefix — set cookie and continue
    const response = NextResponse.next();
    response.cookies.set(COOKIE_NAME, firstSegment, { path: "/", maxAge: 365 * 24 * 60 * 60 });
    return response;
  }

  // No locale prefix — detect and redirect
  const cookieLocale = request.cookies.get(COOKIE_NAME)?.value;
  const locale =
    cookieLocale && SUPPORTED_LOCALES.includes(cookieLocale)
      ? cookieLocale
      : getLocaleFromHeaders(request);

  const url = request.nextUrl.clone();
  url.pathname = `/${locale}${pathname}`;

  const response = NextResponse.redirect(url);
  response.cookies.set(COOKIE_NAME, locale, { path: "/", maxAge: 365 * 24 * 60 * 60 });
  return response;
}

export const config = {
  matcher: ["/((?!_next|api|vague/|images|favicon|.*\\..*).*)"],
};
