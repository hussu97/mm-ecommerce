"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";

/**
 * The last page that can render when everything above it has thrown.
 *
 * It used to render `<NextError statusCode={0} />` from `next/error` — the
 * Pages Router's error page, kept around inside App Router purely as a
 * fallback renderer. On this deploy target that component reaches for a
 * static `pages/500.html` that this app has never had (there is no `pages/`
 * directory at all), so it threw a **second** time trying to render the
 * error page: `ENOENT: no such file or directory, open '.../pages/500.html'`.
 * A three-hour production outage on 2026-09 never showed the branded error
 * page a customer could have made sense of — every request into
 * `app/[locale]/layout.tsx` that hit a rejected `getTranslations`/
 * `getActiveCategories` request — which used to throw on any non-2xx, even at
 * runtime — ended here and then crashed again.
 *
 * This file has to survive that: it renders `<html>`/`<body>` itself, styles
 * everything with inline styles rather than Tailwind classes (Tailwind's
 * cascade layers live in `globals.css`, which this route never imports —
 * see `global-not-found.tsx`, the sibling this shell is copied from), and
 * imports nothing from the app beyond Sentry. No fonts, no translations, no
 * API call. It has to work when the thing that failed is everything else.
 */
export default function GlobalError({ error }: { error: Error & { digest?: string } }) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <html lang="en" dir="ltr">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: "1rem",
          textAlign: "center",
          fontFamily:
            "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
          backgroundColor: "#ffffff",
          color: "#3a2e28",
        }}
      >
        <p
          style={{
            fontSize: "3rem",
            color: "rgba(166, 124, 82, 0.6)",
            lineHeight: 1,
            margin: 0,
            userSelect: "none",
          }}
        >
          Oops
        </p>
        <h1
          style={{
            fontSize: "1.5rem",
            textTransform: "uppercase",
            letterSpacing: "0.15em",
            margin: "0.5rem 0 1rem",
          }}
        >
          Something Went Wrong
        </h1>
        <p style={{ fontSize: "0.875rem", color: "#6b6b6b", maxWidth: "24rem", margin: "0 0 2rem" }}>
          We hit a snag loading the page. Please try again in a moment — the
          brownies, at least, are unaffected.
        </p>
        <button
          type="button"
          onClick={() => window.location.assign("/")}
          style={{
            padding: "0.75rem 1.5rem",
            backgroundColor: "#a67c52",
            color: "#ffffff",
            fontSize: "0.75rem",
            textTransform: "uppercase",
            letterSpacing: "0.15em",
            border: "none",
            cursor: "pointer",
          }}
        >
          Back to Home
        </button>
        <div
          style={{
            marginTop: "3rem",
            width: "4rem",
            height: "2px",
            backgroundColor: "rgba(166, 124, 82, 0.4)",
          }}
        />
        <p style={{ fontSize: "0.75rem", color: "#9b9b9b", fontStyle: "italic", marginTop: "0.75rem" }}>
          Melting Moments Cakes
        </p>
      </body>
    </html>
  );
}
