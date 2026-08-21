# Readable category URLs, and a redirects table that keeps the old ones alive

## Goal

`/en/cat-brownies` is what the Foodics importer left behind — production derives
slugs from the POS `reference`, so `cat_brownies` became `cat-brownies`. Nobody
searches for it, no answer engine repeats it, and `/en/brownies` was not a page
on this site. Renaming breaks every indexed URL, so the rename ships with a
redirect table an operator can edit without a deploy.

Two other defects came with it, because the rename could not be done correctly
without the first and the second lives in the same file.

## The three defects

**1. Unknown URLs answered 200, and in-page redirects never happened.**
Not PPR — that is not enabled. `loading.tsx`. The implicit Suspense boundary made
Next commit a 200 and stream the shell before the page component ran, so
`notFound()` and `permanentRedirect()` arrived after the status was already sent.
Measured on production, and the segment without a `loading.tsx` is the control:

| URL | `loading.tsx` | before |
|---|---|---|
| `/en/blog/zzz-nope` | no | 404 ✓ |
| `/en/zzz-nope` | yes | 200 ✗ |
| `/en/cat-brownies/mix-cookies-box-of-9` | yes | 200, rendered the product ✗ |

**2. The bare domain served Arabic to crawlers.** `FALLBACK_LOCALE` answered both
"we cannot serve what you asked for" and "you did not ask", and the second is how
Googlebot crawls. Every page declares `x-default` as its English URL, so the
markup and the redirect disagreed — which is why the English brand result on
Google carried an Arabic description.

**3. `cat-` slugs.** As above.

## Plan

- [x] Split the boundary below the existence check so `notFound()` can set a
      status, without losing the category skeleton
- [x] `url_redirects` — locale-agnostic paths, `is_prefix` for the 36 product
      URLs nested under the eight categories, 301/308 only
- [x] Resolve in `proxy.ts`, before the response commits. Module-scope map on a
      short TTL, fails open
- [x] Collapse chains and refuse loops on write, in `redirect_service`
- [x] Renaming a category in the console writes its own rule
- [x] Reserved-slug guard, now that `/en/brownies` and `/en/about` are the same shape
- [x] `hit_count` / `last_hit_at`, reported from `waitUntil` after the redirect
- [x] Console screen under Online store
- [x] Split "no Accept-Language" from "a language we do not serve"

## Review

**Migrations, on a throwaway Postgres 17** — the API suite mocks the DB, so a
broken migration passes every test:

- `001` → `120` clean.
- All eight slugs renamed, `reference` untouched (`brownies` still `cat_brownies`),
  so the Foodics upsert key and the register survive.
- Nine redirect rows seeded; the eight category ones are prefix rules.
- CMS hrefs followed the rename, including the nested one:
  `/ar/cat-mixboxes/mix-cookies-box-of-9` → `/ar/mix-boxes/mix-cookies-box-of-9`.
  `/all-products` untouched.
- Re-running `120` after `stamp 119` changed nothing (identical md5).
- Guard holds: a category hand-renamed to `fudgy-brownies` first was left exactly
  as the operator set it.

**Tests.** 1831 API tests pass, including 20 new ones — chain collapse both ways,
self-redirect refused, target cleared, rename-and-rename-back proven loop-free,
`record_hit` never raising. `test_the_reserved_list_matches_the_storefront_routes`
reads `app/[locale]/` and fails if a new page is added without a line in
`RESERVED_SLUGS`, which is the drift this change creates. Web 465, admin 51,
`tsc` clean on both, eslint 0 errors, `@mm/types` regenerated with all four
redirect endpoints.

**Status codes, end to end on a dev server** — the assertion is the code, because
that is what the old build could not produce:

```
/en/zzz-nope                            404   (was 200)
/en/brownies/zzz-nope                   404   (was 200)
/en/brownies/mix-cookies-box-of-9       308 → /en/mix-boxes/...   (was 200)
/en/cat-brownies                        308 → /en/brownies
/ar/cat-brownies                        308 → /ar/brownies
/cat-brownies                           308 → /en/brownies        (one hop)
/en/cat-mixboxes/mix-cookies-box-of-9   308 → /en/mix-boxes/mix-cookies-box-of-9
/en/cat-brownies?sort=price_asc         308 → /en/brownies?sort=price_asc
/en/about-me                            308 → /en/about           (no next.config rule)
/ with no Accept-Language               307 → /en                 (was /ar)
/ with Accept-Language: fr              307 → /ar                 (unchanged)
```

`hit_count` incremented for each — the `waitUntil` report reaches the API. The
category page still paints its skeleton and renders correctly under the new slug.

**Not clicked through:** the console rename → auto-redirect flow was verified at
the service level rather than through the admin UI, because the throwaway
database has no admin user. The HTTP wiring between them is one call in
`category_service.update`.

## Trade-off taken

The product route loses its loading skeleton. Everything on that page needs the
product, so there was nothing to stream behind a boundary — the skeleton was
buying a paint, not a fetch, and it cost every 404 and every cross-category
redirect on the route. The category route keeps its skeleton via an in-page
`<Suspense>` around the grid, which genuinely can stream.

## Still open

- Third-party citations. Melting Moments is in none of the Sharjah/Dubai bakery
  listicles that answer engines synthesise from, and the Google Business Profile
  is unclaimed. Neither is a code change.
- Zomato still shows the shop as "Temporarily closed".
