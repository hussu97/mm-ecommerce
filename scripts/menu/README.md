# Printable menus, generated from the live catalogue

    python3 scripts/menu/generate_menu.py --out scripts/menu --date $(date +%F)

Produces three A4 HTML files; open in a browser and print to PDF.

| File | Brownies & cookies | Prices |
|---|---|---|
| `menu-shop.html` | single piece | yes |
| `menu-online.html` | boxes of 3 / 6 / 9 | yes |
| `menu-noprice.html` | single piece | no |

Everything — names, descriptions, images, prices, section order — comes from
`GET /products?channel=web`, so the menu cannot drift from what the site sells.
It drifted before: the Canva menu was still printing Cookie Melt at 32/60 while
the website charged 40/70.

The one figure not in the web feed is the single-piece price for brownies and
cookies. Those singles are POS-only products, so `SINGLE_PIECE_PRICE` in the
script carries the register's price (AED 15). Change it there if the register
price changes.
