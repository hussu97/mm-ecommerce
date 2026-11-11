import localFont from "next/font/local";

/**
 * The console's two faces, self-hosted.
 *
 * These were `next/font/google`, which downloads the fonts from Google during
 * `next build` — a fetch that failed intermittently ("next/font/google queries
 * have exactly one entry" / "Can't resolve '@vercel/turbopack-next/internal/
 * font/google/font'") and failed the build with it. The files are the ones
 * Google served (fonts.gstatic.com, SIL Open Font License — see the OFL-*.txt
 * beside them), committed, so a build no longer touches the network for fonts.
 * Both are variable, so one file covers every weight the console uses
 * (300–700 Raleway, 300–600 Jost), Latin subset only, as before.
 *
 * next/font requires literal options, so the unicode range is repeated rather
 * than shared through a constant.
 */

export const raleway = localFont({
  src: [{ path: "./raleway-latin-300-700.woff2", weight: "300 700", style: "normal" }],
  variable: "--font-raleway",
  display: "swap",
  declarations: [
    {
      prop: "unicode-range",
      value:
        "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD",
    },
  ],
});

export const jost = localFont({
  src: [{ path: "./jost-latin-300-600.woff2", weight: "300 600", style: "normal" }],
  variable: "--font-jost",
  display: "swap",
  declarations: [
    {
      prop: "unicode-range",
      value:
        "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD",
    },
  ],
});
