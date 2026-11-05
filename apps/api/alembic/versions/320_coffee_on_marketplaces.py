"""Hot and cold coffee on the marketplaces — Barsha Heights only, at counter + 8.

The commercial decision this carries: every drink in the Hot Coffee and Cold
Coffee categories is sold on the aggregators, at **Barsha Heights only** (the
coffee counter; no other branch has coffee live), priced at the counter price
**plus a flat AED 8**.

* **Price.** A coffee is priced entirely by its Size options (the product is 0),
  so the +8 is each option's `aggregator_price` = its counter price + 8: a
  Cappuccino is 22/24/26 on the marketplaces against 14/16/18 at the counter.
* **Where.** Two integrator-menu categories, Hot Coffee and Cold Coffee, each
  limited to Barsha (`marketplace_branch_ids`). Their `reference` is the
  Grubtech subgroup created for them in Foodics on 2026-11-05 — account-level
  ids, like the ones `foodics_provider.FOODICS_GRUBTECH_SUBGROUPS` holds. The
  catalog sync then makes each drink active at Barsha and inactive at Sharjah in
  Foodics, creates nothing on the Karama/DSO portals, and GrubOps pushes stock
  to Barsha only.
* **Images.** One listing photo per drink, uploaded to the product-image bucket.

Guarded so it cannot fight the admin, and keyed by SKU (ids differ per
environment):
* an option's aggregator price is set only while it has none;
* an image only on a product that has none;
* a category only when no group already has its reference, only under a live
  integrator root, and only when exactly one live branch is named Barsha — on a
  database without them (dev, CI) the whole migration is a no-op;
* members only into a category this migration just created (an empty one), so a
  drink an operator later removes is never put back.

Nothing is pushed from here: the operator sync (`sync_product_to_integrators`)
fans the change out, and is run deliberately after the deploy.

Revision ID: 320_coffee_on_marketplaces
Revises: 319_aggregator_price_scope
Create Date: 2026-11-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "320_coffee_on_marketplaces"
down_revision: Union[str, None] = "319_aggregator_price_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MARKUP = 8

#: Menu order on the marketplaces — the everyday milk drinks first, the short
#: black drinks last.
HOT = [
    "FG0093",  # Spanish Latte
    "FG0088",  # Cappuccino
    "FG0089",  # Cafe Latte
    "FG0085",  # Flat White
    "FG0091",  # Caramel Macchiato
    "FG0090",  # Cafe Mocha
    "FG0092",  # Pistachio Latte
    "FG0135",  # Matcha Latte
    "FG0086",  # Americano
    "FG0121",  # Hot Chocolate
    "FG0117",  # Espresso
    "FG0082",  # Espresso Macchiato
    "FG0083",  # Espresso Lungo
    "FG0084",  # Ristretto
    "FG0087",  # Turkish Coffee
]
COLD = [
    "FG0123",  # Iced Spanish Latte
    "FG0094",  # Iced Cafe Latte
    "FG0096",  # Iced Caramel Macchiato
    "FG0095",  # Iced Cafe Mocha
    "FG0097",  # Iced Matcha Latte
    "FG0120",  # Iced Americano
    "FG0122",  # Iced Chocolate
]

CATEGORIES = [
    # (name, Arabic, Foodics Grubtech subgroup id, display order, SKUs)
    ("Hot Coffee", "قهوة ساخنة", "a3267d98-c310-468d-9bcb-867d09ae59d1", 19, HOT),
    ("Cold Coffee", "قهوة باردة", "a3267d99-5fec-4f0e-bc60-d76c87dcad9b", 20, COLD),
]

_IMG = "https://storage.googleapis.com/mm-product-images/menu/"
IMAGES = {
    "FG0082": _IMG + "8ca4d5709a9bb3f56d5583519aae2afe.jpg",
    "FG0083": _IMG + "d572fc12e16d749b5de11912d652e77b.jpg",
    "FG0084": _IMG + "4cbdea2c0bc8c4f97401faf7cbd8cb44.jpg",
    "FG0085": _IMG + "9f13b9a11ab649768e5b3ec871f644d0.jpg",
    "FG0086": _IMG + "c438cf48a3d79a4c853caa87d5ce871f.jpg",
    "FG0087": _IMG + "bc327e74373f93a877ccfe03fd4161d5.jpg",
    "FG0088": _IMG + "66e4e712fc8bf52c3a343574c7d6aa03.jpg",
    "FG0089": _IMG + "96a5cc3de26d4c4cac90d1d7a79a8a76.jpg",
    "FG0090": _IMG + "6b1308aff2359144ac5a23fec2e52e3c.jpg",
    "FG0091": _IMG + "195d3c5cc51bcd37ba8046e1c383de76.jpg",
    "FG0092": _IMG + "cfb4309beec180bc1c8741615c709773.jpg",
    "FG0093": _IMG + "922d05f875d3154474fc33012b4e163f.jpg",
    "FG0094": _IMG + "d4dfcb155f92f0180c548f6ca6fdb328.jpg",
    "FG0095": _IMG + "947f46d94496382f5620b7b791edd47a.jpg",
    "FG0096": _IMG + "a7b5cbdb3f15b29049baa8b457bd7fa5.jpg",
    "FG0097": _IMG + "f3e5005a98bcdd0e40e9d41a6fd21a5c.jpg",
    "FG0117": _IMG + "159ebf602b344b93b61a8fa13fdb8269.jpg",
    "FG0120": _IMG + "38e92f20491134facabb63e5e7390c51.jpg",
    "FG0121": _IMG + "808fe5df8652803e233369c0e7af02df.jpg",
    "FG0122": _IMG + "2deabd2d6ac9c302ab33f46b8d1a9230.jpg",
    "FG0123": _IMG + "75e26624eb8438d4aaaee42380d1f239.jpg",
    "FG0135": _IMG + "7f6cb06ceac2d3e2e6b2f339e191105f.jpg",
}

_BARSHA = """
    (SELECT id FROM branches
      WHERE name ILIKE 'barsha%' AND deleted_at IS NULL AND is_active)
"""


def upgrade() -> None:
    skus = HOT + COLD

    # 1. The +8: on each option of a coffee's own modifiers. A modifier any
    #    non-coffee product also carries is left alone — its price is not ours
    #    to mark up.
    op.execute(
        sa.text(
            """
            UPDATE modifier_options o
               SET aggregator_price = o.price + CAST(:markup AS numeric)
             WHERE o.aggregator_price IS NULL
               AND EXISTS (
                   SELECT 1 FROM product_modifiers pm
                     JOIN products p ON p.id = pm.product_id
                    WHERE pm.modifier_id = o.modifier_id
                      AND p.sku = ANY(CAST(:skus AS text[])))
               AND NOT EXISTS (
                   SELECT 1 FROM product_modifiers pm
                     JOIN products p ON p.id = pm.product_id
                    WHERE pm.modifier_id = o.modifier_id
                      AND (p.sku IS NULL OR p.sku <> ALL(CAST(:skus AS text[]))))
            """
        ).bindparams(markup=MARKUP, skus=skus)
    )

    # 2. Images, only where there are none.
    for sku, url in IMAGES.items():
        op.execute(
            sa.text(
                """
                UPDATE products
                   SET image_urls = ARRAY[CAST(:url AS varchar)], updated_at = now()
                 WHERE sku = CAST(:sku AS varchar) AND image_urls = '{}'
                """
            ).bindparams(sku=sku, url=url)
        )

    # 3. The two Barsha-only integrator categories, and their drinks.
    for name, name_ar, ref, order, members in CATEGORIES:
        op.execute(
            sa.text(
                f"""
                INSERT INTO menu_groups
                    (id, name, name_localized, translations, reference, root_kind,
                     branch_id, root_id, parent_id, display_order, is_active,
                     marketplace_branch_ids, created_at, updated_at)
                SELECT gen_random_uuid(), CAST(:name AS varchar),
                       CAST(:name_ar AS varchar),
                       jsonb_build_object('ar', jsonb_build_object(
                           'name', CAST(:name_ar AS text))),
                       CAST(:ref AS varchar), 'integrator', NULL, r.id, r.id,
                       CAST(:ord AS integer), true, ARRAY[{_BARSHA}], now(), now()
                  FROM menu_groups r
                 WHERE r.reference = 'integrator-root'
                   AND r.parent_id IS NULL AND r.deleted_at IS NULL
                   AND NOT EXISTS (
                       SELECT 1 FROM menu_groups WHERE reference = CAST(:ref AS varchar))
                   AND (SELECT count(*) FROM branches
                         WHERE name ILIKE 'barsha%'
                           AND deleted_at IS NULL AND is_active) = 1
                """
            ).bindparams(name=name, name_ar=name_ar, ref=ref, ord=order)
        )
        op.execute(
            sa.text(
                """
                INSERT INTO menu_group_products
                    (id, group_id, product_id, display_order, exclude_from_pdf)
                SELECT gen_random_uuid(), g.id, p.id, CAST(x.ord AS integer) - 1, false
                  FROM unnest(CAST(:skus AS text[])) WITH ORDINALITY AS x(sku, ord)
                  JOIN products p ON p.sku = x.sku AND p.is_active
                  JOIN menu_groups g
                    ON g.reference = CAST(:ref AS varchar) AND g.deleted_at IS NULL
                 WHERE NOT EXISTS (
                       SELECT 1 FROM menu_group_products m WHERE m.group_id = g.id)
                """
            ).bindparams(skus=members, ref=ref)
        )


def downgrade() -> None:
    for _name, _ar, ref, _order, _members in CATEGORIES:
        op.execute(
            sa.text(
                "DELETE FROM menu_groups WHERE reference = CAST(:ref AS varchar)"
            ).bindparams(ref=ref)
        )
    # Prices and images stay: after a downgrade they are ordinary catalogue data
    # an operator may already be relying on, and 319's downgrade drops the
    # aggregator-price columns themselves.
