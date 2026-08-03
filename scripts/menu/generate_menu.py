#!/usr/bin/env python3
"""
Build the printable menus straight from the live catalogue.

The Cookie Melt prices on the Canva menu were eight months stale — 32/60 against a
website charging 40/70 — because the menu and the catalogue are edited separately.
Reading the API at build time is the only way that cannot drift.

Three variants, matching the three Canva files:
  shop    — brownies and cookies as single pieces, with prices
  online  — brownies and cookies as boxes of 3/6/9, with prices
  noprice — the shop menu with prices stripped
"""
from __future__ import annotations

import argparse
import html
import json
import re
import urllib.request
from collections import OrderedDict

API = "https://api.meltingmomentscakes.com/api/v1"

# Brownie and cookie singles are POS-only, so the web feed cannot price a single
# piece. The register is the source of truth for what one piece costs.
SINGLE_PIECE_PRICE = 15.00

# Sections in the order they should print. Anything not listed still gets a
# section at the end rather than being silently dropped.
SECTION_ORDER = [
    "Brownies", "Eggless", "Cookies", "Cookie Melt",
    "Desserts", "Cakes", "Mix Boxes", "Extras",
]


def fetch(url: str):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def web_products() -> list[dict]:
    d = fetch(f"{API}/products?per_page=2000&channel=web")
    return [p for p in d["items"] if p.get("is_active")]


def quantity_options(product: dict) -> list[tuple[int, float]]:
    """(pieces, price) for a 'Your Choice of Quantity' group, cheapest first."""
    out = []
    for pm in product.get("product_modifiers") or []:
        for opt in pm["modifier"]["options"]:
            if not opt.get("is_active"):
                continue
            m = re.search(r"(\d+)", opt["name"])
            if m and opt["price"] > 0:
                out.append((int(m.group(1)), float(opt["price"])))
    return sorted(set(out))


def price_label(product: dict, variant: str) -> str:
    """
    What to print in the price column.

    A modifier-priced item has no meaningful base_price — it is 0 — so the label
    depends on the variant: the shop menu sells the piece, the online menu sells
    the box.
    """
    opts = quantity_options(product)
    if not opts:
        return f"{float(product['base_price']):.0f}"
    if variant == "online":
        return " / ".join(f"{n}pc {p:.0f}" for n, p in opts)
    return f"{SINGLE_PIECE_PRICE:.0f}"


def display_name(product: dict, variant: str) -> str:
    return product["name"]


def build_sections(products: list[dict], variant: str) -> "OrderedDict[str, list[dict]]":
    by_cat: dict[str, list[dict]] = {}
    for p in products:
        cat = (p.get("category") or {}).get("name") or "Other"
        # Mix Boxes are boxes by definition; they make no sense on a singles menu.
        if variant in ("shop", "noprice") and cat == "Mix Boxes":
            continue
        by_cat.setdefault(cat, []).append(p)

    ordered: "OrderedDict[str, list[dict]]" = OrderedDict()
    for name in SECTION_ORDER:
        if name in by_cat:
            ordered[name] = sorted(by_cat.pop(name), key=lambda x: (x["display_order"], x["name"]))
    for name in sorted(by_cat):
        ordered[name] = sorted(by_cat[name], key=lambda x: (x["display_order"], x["name"]))
    return ordered


CSS = """
:root { --ink:#4a3b35; --muted:#7d6a62; --paper:#e2cec7; --rule:#b9a49c; }
* { box-sizing:border-box; }
body { margin:0; font-family:"DM Sans","Helvetica Neue",Arial,sans-serif; color:var(--ink); }
.page { width:210mm; min-height:297mm; padding:16mm 14mm; background:var(--paper); margin:0 auto 8mm; }
h1 { font-size:30pt; margin:0 0 2mm; letter-spacing:.5px; }
.sub { color:var(--muted); font-size:9.5pt; margin:0 0 8mm; }
h2 { font-size:15pt; margin:8mm 0 3mm; display:flex; align-items:center; gap:4mm; }
h2::after { content:""; flex:1; height:1px; background:var(--rule); }
.item { display:grid; grid-template-columns:16mm 1fr auto; gap:4mm; align-items:start;
        padding:2.4mm 0; break-inside:avoid; }
.item img { width:16mm; height:16mm; object-fit:cover; border-radius:50%; }
.noimg { width:16mm; height:16mm; border-radius:50%; background:#d3bcb4; }
.name { font-weight:700; font-size:11pt; line-height:1.25; }
.desc { font-style:italic; color:var(--muted); font-size:9pt; line-height:1.35; margin-top:.8mm; }
.price { font-weight:700; font-size:11pt; white-space:nowrap; padding-left:3mm; }
.note { margin-top:10mm; font-size:8.5pt; color:var(--muted); }
@media print { .page { margin:0; } @page { size:A4; margin:0; } }
"""


def render(products: list[dict], variant: str, generated: str) -> str:
    sections = build_sections(products, variant)
    show_price = variant != "noprice"
    if variant == "shop":
        sub = "Brownies and cookies are priced per piece."
    elif variant == "online":
        sub = "Brownies and cookies are sold in boxes of 3, 6 and 9."
    else:
        sub = "Brownies and cookies are sold per piece."

    rows = []
    for section, items in sections.items():
        rows.append(f"<h2>{html.escape(section)}</h2>")
        for p in items:
            img = (p.get("image_urls") or [None])[0]
            thumb = (f'<img src="{html.escape(img)}" alt="">' if img else '<div class="noimg"></div>')
            desc = p.get("description") or ""
            price = (f'<div class="price">{html.escape(price_label(p, variant))}</div>'
                     if show_price else "")
            rows.append(
                '<div class="item">'
                f"{thumb}"
                '<div><div class="name">'
                f'{html.escape(display_name(p, variant))}</div>'
                f'<div class="desc">{html.escape(desc)}</div></div>'
                f"{price}</div>"
            )

    count = sum(len(v) for v in sections.values())
    return f"""<!doctype html>
<meta charset="utf-8">
<title>Melting Moments — {html.escape(variant)} menu</title>
<style>{CSS}</style>
<div class="page">
  <h1>Melting Moments</h1>
  <p class="sub">{html.escape(sub)} All prices in AED.</p>
  {''.join(rows)}
  <p class="note">{count} items · generated from the live catalogue on {html.escape(generated)}
  · +971 50 368 7757 · meltingmomentscakes.com</p>
</div>
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".")
    ap.add_argument("--date", required=True, help="stamp for the footer, e.g. 2026-08-03")
    args = ap.parse_args()

    products = web_products()
    for variant in ("shop", "online", "noprice"):
        path = f"{args.out}/menu-{variant}.html"
        with open(path, "w") as fh:
            fh.write(render(products, variant, args.date))
        print(f"{path}  ({len(products)} web products)")


if __name__ == "__main__":
    main()
