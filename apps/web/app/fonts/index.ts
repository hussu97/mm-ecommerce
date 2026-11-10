import localFont from "next/font/local";

/**
 * The storefront's four faces, self-hosted.
 *
 * These were `next/font/google`, which downloads the fonts from Google during
 * `next build`. That fetch failed intermittently in CI and on Vercel ("next/font/
 * google queries have exactly one entry" / "Can't resolve '@vercel/turbopack-next/
 * internal/font/google/font'") and took the whole build with it, so every build
 * was wrapped in a three-attempt retry. The files are the same ones Google
 * served (fonts.gstatic.com, SIL Open Font License — see the OFL-*.txt beside
 * them), committed, so a build no longer touches the network for fonts.
 *
 * Each face keeps the one subset it used before, with Google's unicode-range for
 * it, so a page still downloads only the faces whose glyphs it renders — the
 * Arabic pages never fetch the Latin faces and the reverse. Raleway, Jost and
 * Cairo are variable (one file covers every weight); Tajawal has no variable
 * cut, hence three files.
 *
 * `preload: false` on all four, as before: the layout serves both scripts, and
 * a preload would put every face in the <head> of every page.
 *
 * next/font requires literal options, so the unicode ranges are repeated rather
 * than shared through a constant.
 */

export const raleway = localFont({
  src: [{ path: "./raleway-latin-300-700.woff2", weight: "400 700", style: "normal" }],
  variable: "--font-raleway",
  display: "swap",
  preload: false,
  declarations: [
    {
      prop: "unicode-range",
      value:
        "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD",
    },
  ],
});

export const jost = localFont({
  src: [{ path: "./jost-latin-300-600.woff2", weight: "400 600", style: "normal" }],
  variable: "--font-jost",
  display: "swap",
  preload: false,
  declarations: [
    {
      prop: "unicode-range",
      value:
        "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD",
    },
  ],
});

export const tajawal = localFont({
  src: [
    { path: "./tajawal-arabic-400.woff2", weight: "400", style: "normal" },
    { path: "./tajawal-arabic-500.woff2", weight: "500", style: "normal" },
    { path: "./tajawal-arabic-700.woff2", weight: "700", style: "normal" },
  ],
  variable: "--font-tajawal",
  display: "swap",
  preload: false,
  declarations: [
    {
      prop: "unicode-range",
      value:
        "U+0600-06FF, U+0750-077F, U+0870-0891, U+0897-08E1, U+08E3-08FF, U+200C-200E, U+2010-2011, U+204F, U+2E41, U+FB50-FDFF, U+FE70-FE74, U+FE76-FEFC, U+102E0-102FB, U+10E60-10E7E, U+10EC2-10EC7, U+10EC9-10EEE, U+10EF0-10EFF, U+1EE00-1EE03, U+1EE05-1EE1F, U+1EE21-1EE22, U+1EE24, U+1EE27, U+1EE29-1EE32, U+1EE34-1EE37, U+1EE39, U+1EE3B, U+1EE42, U+1EE47, U+1EE49, U+1EE4B, U+1EE4D-1EE4F, U+1EE51-1EE52, U+1EE54, U+1EE57, U+1EE59, U+1EE5B, U+1EE5D, U+1EE5F, U+1EE61-1EE62, U+1EE64, U+1EE67-1EE6A, U+1EE6C-1EE72, U+1EE74-1EE77, U+1EE79-1EE7C, U+1EE7E, U+1EE80-1EE89, U+1EE8B-1EE9B, U+1EEA1-1EEA3, U+1EEA5-1EEA9, U+1EEAB-1EEBB, U+1EEF0-1EEF1",
    },
  ],
});

export const cairo = localFont({
  src: [{ path: "./cairo-arabic-400-700.woff2", weight: "400 700", style: "normal" }],
  variable: "--font-cairo",
  display: "swap",
  preload: false,
  declarations: [
    {
      prop: "unicode-range",
      value:
        "U+0600-06FF, U+0750-077F, U+0870-0891, U+0897-08E1, U+08E3-08FF, U+200C-200E, U+2010-2011, U+204F, U+2E41, U+FB50-FDFF, U+FE70-FE74, U+FE76-FEFC, U+102E0-102FB, U+10E60-10E7E, U+10EC2-10EC7, U+10EC9-10EEE, U+10EF0-10EFF, U+1EE00-1EE03, U+1EE05-1EE1F, U+1EE21-1EE22, U+1EE24, U+1EE27, U+1EE29-1EE32, U+1EE34-1EE37, U+1EE39, U+1EE3B, U+1EE42, U+1EE47, U+1EE49, U+1EE4B, U+1EE4D-1EE4F, U+1EE51-1EE52, U+1EE54, U+1EE57, U+1EE59, U+1EE5B, U+1EE5D, U+1EE5F, U+1EE61-1EE62, U+1EE64, U+1EE67-1EE6A, U+1EE6C-1EE72, U+1EE74-1EE77, U+1EE79-1EE7C, U+1EE7E, U+1EE80-1EE89, U+1EE8B-1EE9B, U+1EEA1-1EEA3, U+1EEA5-1EEA9, U+1EEAB-1EEBB, U+1EEF0-1EEF1",
    },
  ],
});
