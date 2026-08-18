# The admin console on a phone

The console is used from a phone more than anyone designing it expected: a
manager checking today's orders on the way in, a supervisor closing a branch for
Eid, somebody correcting a status while standing at the counter. It was built at
a desk and it showed.

This is what was wrong, what the rules are now, and how to tell whether a new
screen follows them.

## What was actually wrong (2026-08-18, measured at 390px)

Not "it looked cramped". Every list in the console was a `<table>` inside
`overflow-x-auto`, which passes every reasonable check — nothing spills onto the
page, there is no horizontal scrollbar on the document — and is useless on a
phone, because the table scrolls sideways *inside its own box* and nothing says
so. What you actually saw:

| Screen | Of the table's width, hidden behind a sideways drag |
|---|---|
| Translations | 80% |
| Staff | 71% |
| Branches | 65% |
| Devices | 63% |
| Admin Users | 56% |
| POS Reports | 55% |

On Staff that meant two of seven columns. The name column was squeezed until
"Chocolate Fudge Celebration Cake" wrapped over six lines and made every row
250px tall; the PIN, the branches, the role, the status and the actions were all
off-screen to the right.

Three more, each systemic:

- **The page gutter was applied twice.** The dashboard shell set `p-6` and
  `ResourcePage` set `p-6` again, so half the console drew its content in 294px
  of a 390px screen before anything else went wrong.
- **Every text control was under 16px**, and iOS Safari zooms the page when one
  smaller than that takes focus — and does not zoom back. Tapping a filter left
  the console panned sideways with the layout half off-screen. Up to 10 such
  controls on a single screen.
- **Tap targets were desktop-sized.** Row links were 16px tall and 19px wide;
  checkboxes were the 13px browser default. Up to 38 undersized targets on one
  screen.

## The rules

### 1. One gutter, owned by the shell

`app/(dashboard)/layout.tsx` sets `px-4 py-5 md:p-6` on `<main>` and that is the
only page padding in the console. A page component supplies content, never
padding. Full-bleed is the exception and is expressed as a negative margin
(`-mx-4 md:mx-0`), which makes it visible in review.

### 2. A data table becomes cards on a phone

Use `components/ui/DataTable`. It renders a `<table>` at `md` and up, and one
card per row below it, from a single column definition. Never hand-roll a
`<table>` for a list.

Each column declares its own importance once:

| `priority` | On a phone |
|---|---|
| `primary` | The card's title. The row's identity. One per table. |
| `secondary` | A subtitle under it — a slug, an email, a reference. No label. |
| `meta` *(default)* | A labelled line in the card body. |
| `desktop` | Dropped from the card. Scanning aids and correlation ids. |

Both shapes call the same `render`, so a cell cannot drift between them.

Choosing `primary` is the one judgement call worth making deliberately: it is
what the row *is*, not what you sort by. Email Logs leads with the recipient and
not the timestamp; Orders leads with the order number; Webhook Logs leads with
the event type.

### 3. A horizontal scroll must be declared

Below `md`, a horizontal scroller is a defect unless it is inside an element
carrying `data-scroll-intent`. Today three things qualify:

- **`TabBar`** — tabs are peers, the strip is short, and a half-visible fifth
  tab is itself the affordance saying there is more this way.
- **`DataTable`'s desktop table** — at a desk a very wide table is better
  dragged than folded, and below `md` the card list has already replaced it.
- **A wide artefact** — a JSON payload dump, a map, a chart. Something that is
  one object rather than several facts about a row.

A data table on a phone never qualifies. Its columns are separate facts and a
phone can stack them — which is exactly what `DataTable` does.

### 4. Touch targets

`--tap-min: 44px`, in `app/globals.css`. Anything you press is at least that
tall and wide on a phone, and may shrink at `md` where the pointer is precise:

```
min-h-[var(--tap-min)] md:min-h-0     /* or min-h-11 md:min-h-0 */
```

`Button`, `Input`, `Select`, `MultiSelect`, `TabBar`, `Pagination` and
`RowAction` already do this. A text link inside a row is a `RowAction`, not a
bare `<button className="text-xs …">` — that shape is how the console ended up
with 19×16px targets sitting 8px apart.

Checkboxes are the documented exception at 22px: a checkbox is a small control
on every platform, and enlarging the box past that looks broken. Where a
checkbox is a row's *main* control, put it in a padded `<label>` so the hit area
is the whole line.

### 5. Controls are 16px on a phone

`@media (pointer: coarse)` in `app/globals.css` sets every `input`, `select` and
`textarea` to 16px. This is not a taste decision — below 16px iOS Safari zooms on
focus and does not zoom back. Scoped to the pointer type rather than to a width,
because it is a property of the input method: a narrow desktop window has no such
behaviour and keeps the compact scale.

Never override `font-size` downward on a control.

### 6. Long text truncates or wraps; it never pushes

Any flex child holding text the shop controls — an address, a product name, an
admin's email — needs `min-w-0` and then `truncate` or `break-words`. Without
`min-w-0` a flex item refuses to shrink below its content and pushes its
siblings off the screen instead. This is what put the hamburger off the left edge
when an admin had a long address.

### 7. Stack instead of competing for one line

`Pagination` and page headers are `flex-col` on a phone and `sm:flex-row` above
it. `justify-between` across a narrow screen does not compress gracefully — it
pushes the last child off the edge, which is how there was no way to reach page
two of anything.

### 8. Icon-only is a desktop affordance

On a phone an icon-only control is both the smallest target on the screen and
the most ambiguous. Pagination's arrows carry "Prev"/"Next" below `sm`. The
hamburger is the deliberate exception: it is a universal glyph and it gets a full
44px square.

## Checking a screen

The audit is a script, so this is measured rather than eyeballed:

```bash
pnpm --filter admin dev                 # in one shell, port 3100
pnpm --filter admin mobile-audit        # in another
```

It drives every route in a 390×844 iPhone viewport against fixtures generated
from the API's own OpenAPI document — so tables are full, strings are long, and
nothing depends on a database. For each route it reports:

- **`sideways`** — any horizontal scroller not marked `data-scroll-intent`, and
  how much of its content is hidden. Must be `-`.
- **`tap<36`** — controls below the touch minimum. Must be `0`.
- **`zoom`** — controls that would trigger the iOS focus zoom. Must be `0`.

It also writes a full-page screenshot per route, which is the part worth actually
looking at: the numbers tell you nothing is broken, the screenshots tell you
whether it reads well.

Run it at `1280` too (`pnpm --filter admin mobile-audit -- 1280`) before
shipping a layout change — the desktop table is the shape most of these rules
are protecting. Above the `md` breakpoint it enforces the scroll rule only: the
compact type scale and the small pointer targets are deliberate at a desk, and
flagging them there would make the desktop run noise.

It exits non-zero when a route fails, so it can gate a change. If the browser
in your environment does not match the pinned Playwright build, point at one:
`CHROMIUM_PATH=/path/to/chromium pnpm --filter admin mobile-audit`.

As of 2026-08-18 all 31 routes are clean at both widths.

## When you add a screen

1. Page component renders content, no padding.
2. Lists go through `DataTable`, with a `primary` column chosen deliberately.
3. Row links are `RowAction`; page actions are `Button`.
4. Filters use `Input` / `Select` / `MultiSelect` from `components/ui`.
5. Anything else that scrolls sideways carries `data-scroll-intent` and a
   comment saying why.
6. Run the audit. Look at your screenshot, not only the numbers.
