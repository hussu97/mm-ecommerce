import { NextRequest, NextResponse } from "next/server";

const SUPPORTED_LOCALES = (process.env.NEXT_PUBLIC_SUPPORTED_LOCALES ?? "en,ar").split(",");
const DEFAULT_LOCALE = "en";
const COOKIE_NAME = "mm_locale";

function getLocaleFromHeaders(request: NextRequest): string {
  const acceptLang = request.headers.get("accept-language");
  if (!acceptLang) return DEFAULT_LOCALE;

  const preferred = acceptLang
    .split(",")
    .map((lang) => lang.split(";")[0].trim().split("-")[0])
    .find((code) => SUPPORTED_LOCALES.includes(code));

  return preferred ?? DEFAULT_LOCALE;
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Skip static files, API routes, and Next.js internals
  if (
    pathname.startsWith("/_next") ||
    pathname.startsWith("/api") ||
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
  matcher: ["/((?!_next|api|images|favicon|.*\\..*).*)"],
};
