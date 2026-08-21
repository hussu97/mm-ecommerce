# The shop tells everyone the same delivery price

## Goal

A shopper reading the site, an answer engine reading `llms.txt`, and a shopping
surface reading our JSON-LD should all be told what the checkout will actually
charge. Today they are told three different things, and two of them are wrong.

## What is actually true

`085_cost_banded_map` is the live map, and its zone table is the only authority.
`delivery_service.price` reads `zone.free_delivery_threshold` per zone and
qualifies on `subtotal >= threshold` — so the honest word is "from", not "over".

| Zone | Fee | Free from |
|---|---|---|
| Sharjah Central | 0 | always |
| Sharjah Outer | 20 | 75 |
| Ajman City | 10 | 75 |
| Dubai Near / Mid / Far | 20 | 75 |
| Umm al-Quwain City | 30 | 75 |
| Ras al-Khaimah City | 50 | 100 |
| Everywhere else | 80 | 200 |

## What each surface says instead

- **CMS copy** (home, about, FAQ — EN and AR): "free over AED 150 in the Dubai,
  Sharjah and Ajman city areas". This is `058_free_delivery_scope`, which was
  correct when written. `085` moved every threshold and never touched the copy,
  so the site has been quoting a dead number since. Verified present verbatim on
  production, so no admin has edited it.
- **`llms.txt` / `llms-full.txt`**: right on everything except Ajman, which it
  prints as AED 20 (it is 10), and it omits Sharjah Outer entirely — so a
  Sharjah address past the noon Send radius reads "free" and is charged 20.
- **JSON-LD `SHIPPING_BY_REGION`**: same Ajman error, banded in with Dubai.

## Plan

- [x] Establish the source of truth from `085` and `delivery_service.price`
- [x] Confirm the stale strings are live and unedited
- [x] `118_delivery_copy_matches_map` — guarded exact-string CMS rewrite, EN+AR,
      home / about / faq. Guard means an admin edit wins and the migration
      no-ops, per convention 7
- [x] `llms.txt` + `llms-full.txt`: Ajman 10, add Sharjah Outer, "from" not "over"
- [x] `schema.ts`: split Ajman out of the Dubai band at 10.00
- [x] `/about-me` → permanent redirect to `/about` (legacy Wix URL, indexed as
      a duplicate)
- [x] Verify: migration applies to a throwaway Postgres, is idempotent, and
      matches nothing on a second run

## Out of scope, flagged

- Every unknown URL returns **HTTP 200** with the 404 body (`noindex` is set, and
  the title is the homepage's). PPR flushes the static shell before
  `notFound()` runs, so the status is already sent. Real defect, separate change.
- Category slugs are `cat-brownies`, `cat-cookies`. `/en/brownies` is not a page.
  This is most of why nothing ranks on unbranded queries.

## Review

Verified on a throwaway Postgres 17 (the API suite mocks the DB, so a broken
migration passes every test):

- `001` → `118` applies clean.
- Seeded with the exact strings `058` wrote, `118` rewrote all eight — four
  English, four Arabic — and the two FAQ answers still read as sentences where
  the replacement runs on into the clause after it.
- `stamp 117 && upgrade head` a second time changed nothing: md5 of the three
  rows identical before and after.
- Guard holds. A row hand-edited to "Fatema says: free delivery over AED 150 if
  you are in town, ask us." was left exactly as written — the phrase no longer
  matches, so the migration passed over it.
- No `AED 150` / `150 درهم` survives anywhere in `cms_pages`.

Frontend: `tsc --noEmit` clean, 453/453 vitest pass, eslint 0 errors (13
pre-existing warnings, none in the touched files). On the dev server
`/about-me`, `/en/about-me` and `/ar/about-me` all answer 308 to the right
locale's `/about`, and `/en/about` still answers 200.

`llms-full.txt` inlines the FAQ straight from `cms_pages`, so it picks up the
corrected wording from the migration rather than needing its own edit — only its
hand-written fee table did.
