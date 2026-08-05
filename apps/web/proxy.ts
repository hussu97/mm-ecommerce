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
  // `/umami` is the analytics proxy, rewritten to Umami Cloud in
  // `next.config.ts`. It is not a page and has no language, but it also has no
  // dot in it, so without naming it here it fell through to the locale rule
  // below: every event the tracker posted to `/umami/api/send` was answered
  // with a 307 to `/en/umami/api/send` and had to be sent a second time. That
  // survived on a fast connection and was pure waste on any other — and the
  // requests most likely to be caught mid-redirect are the ones fired as the
  // page is being replaced, which is exactly `begin_checkout`,
  // `checkout_step_complete` and the hop out to the payment gateway.
  if (
    pathname.startsWith("/_next") ||
    pathname.startsWith("/api") ||
    pathname.startsWith("/umami") ||
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
  matcher: ["/((?!_next|api|umami|images|favicon|.*\\..*).*)"],
};
