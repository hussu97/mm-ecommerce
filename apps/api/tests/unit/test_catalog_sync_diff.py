"""The catalog-&-hours sync read/diff layer — pure logic + flag gating.

The migration is verified on a throwaway Postgres (the suite mocks the DB), so
these cover the parts that must be exactly right without one: the diff engine
(the drift the operator reads), the MM→normalized translation (variant pricing in
particular), the JSONB round-trip, and that every write/read path is shut when its
flag is off.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.exceptions import ServiceUnavailableError
from app.services.aggregators import catalog_sync
from app.services.aggregators.catalog_diff import (
    K_CATEGORY_MISSING,
    K_CATEGORY_RENAME,
    K_HOURS_DAY_CLOSED_CHANNEL,
    K_HOURS_SHIFT,
    K_ITEM_PRICE,
    K_ITEM_UNAVAILABLE,
    K_OPTION_PRICE,
    diff_hours,
    diff_menu,
    normalize_name,
)
from app.services.aggregators.menu_normalized import (
    NormalizedCategory,
    NormalizedHours,
    NormalizedItem,
    NormalizedMenu,
    NormalizedModifierGroup,
    NormalizedOption,
    NormalizedShift,
)

# ── Name normalisation (the observed drift) ───────────────────────────────────


def test_normalize_folds_ampersand_and_punctuation():
    assert normalize_name("Dark Chocolate & Walnut Brownies") == normalize_name(
        "Dark Chocolate and Walnut Brownies"
    )
    assert normalize_name("  Mix  Boxes!! ") == "mix boxes"


# ── Menu diff ─────────────────────────────────────────────────────────────────


def _mm_menu() -> NormalizedMenu:
    return NormalizedMenu(
        source="mm",
        categories=[
            NormalizedCategory(
                "Mix Boxes",
                items=[NormalizedItem("Mix Cookies Box of 9", price=Decimal("135"))],
            ),
            NormalizedCategory(
                "Brownies",
                items=[
                    NormalizedItem(
                        "Dark Chocolate and Walnut Brownies",
                        price=Decimal("0"),
                        modifier_groups=[
                            NormalizedModifierGroup(
                                "Your Choice of Quantity",
                                options=[
                                    NormalizedOption("3 Pieces", price=Decimal("50")),
                                    NormalizedOption("6 Pieces", price=Decimal("90")),
                                    NormalizedOption("9 Pieces", price=Decimal("125")),
                                ],
                            )
                        ],
                    )
                ],
            ),
            NormalizedCategory(
                "Eggless",
                items=[NormalizedItem("Eggless Fudge Brownies", price=Decimal("0"))],
            ),
        ],
    )


def _careem_menu() -> NormalizedMenu:
    # "Boxes" (rename), price drift 135->140, brownie unavailable + 6pc 90->95,
    # no Eggless category, "& Walnut" spelling.
    return NormalizedMenu(
        source="careem",
        categories=[
            NormalizedCategory(
                "Boxes",
                items=[NormalizedItem("Mix Cookies Box of 9", price=Decimal("140"))],
            ),
            NormalizedCategory(
                "Brownies",
                items=[
                    NormalizedItem(
                        "Dark Chocolate & Walnut Brownies",
                        price=Decimal("0"),
                        is_available=False,
                        modifier_groups=[
                            NormalizedModifierGroup(
                                "Sizes",
                                options=[
                                    NormalizedOption("3 Pieces", price=Decimal("50")),
                                    NormalizedOption("6 Pieces", price=Decimal("95")),
                                    NormalizedOption("9 Pieces", price=Decimal("125")),
                                ],
                            )
                        ],
                    )
                ],
            ),
        ],
    )


def test_menu_diff_catches_the_real_drift():
    d = diff_menu(_mm_menu(), _careem_menu(), target="careem")
    kinds = d.summary
    # "Mix Boxes" ↔ "Boxes" is a rename, not a missing+extra pair.
    assert kinds.get(K_CATEGORY_RENAME) == 1
    # 135 vs 140 on the flat-priced box.
    assert kinds.get(K_ITEM_PRICE) == 1
    # 6-piece option 90 vs 95 on the variant-priced brownie.
    assert kinds.get(K_OPTION_PRICE) == 1
    # brownie active in MM but switched off on channel.
    assert kinds.get(K_ITEM_UNAVAILABLE) == 1
    # Eggless missing on channel.
    assert kinds.get(K_CATEGORY_MISSING) == 1
    # "& Walnut" vs "and Walnut" normalises equal → no spurious rename.
    assert "item_name_drift" not in kinds


def test_price_parity_toggle_suppresses_price_deltas():
    d = diff_menu(
        _mm_menu(), _careem_menu(), target="careem", enforce_price_parity=False
    )
    assert d.summary.get(K_ITEM_PRICE, 0) == 0
    assert d.summary.get(K_OPTION_PRICE, 0) == 0
    # non-price drift still reported.
    assert d.summary.get(K_CATEGORY_RENAME) == 1


def test_unknown_channel_price_is_not_drift():
    # A restricted role renders AED 0.00 as "unknown" (None) — never a mismatch.
    mm = NormalizedMenu(
        "mm",
        [
            NormalizedCategory(
                "Cakes", items=[NormalizedItem("Slice", price=Decimal("35"))]
            )
        ],
    )
    ch = NormalizedMenu(
        "talabat",
        [NormalizedCategory("Cakes", items=[NormalizedItem("Slice", price=None)])],
    )
    d = diff_menu(mm, ch, target="talabat")
    assert d.summary.get(K_ITEM_PRICE, 0) == 0


# ── Hours diff ────────────────────────────────────────────────────────────────


def test_hours_diff_flags_wed_closed_distinctly():
    mm = NormalizedHours(
        "mm", shifts=[NormalizedShift(w, "08:00", "22:00") for w in range(7)]
    )
    careem = NormalizedHours(
        "careem",
        shifts=[NormalizedShift(w, "08:00", "21:45") for w in range(7) if w != 3],
    )
    d = diff_hours(mm, careem, target="careem")
    kinds = [x.kind for x in d.deltas]
    assert kinds.count(K_HOURS_DAY_CLOSED_CHANNEL) == 1  # Wednesday
    assert kinds.count(K_HOURS_SHIFT) == 6  # the other six days differ by close time


# ── JSONB round-trip ──────────────────────────────────────────────────────────


def test_menu_roundtrip_preserves_prices():
    original = _mm_menu()
    restored = NormalizedMenu.from_dict(original.to_dict())
    opt = restored.categories[1].items[0].modifier_groups[0].options[2]
    assert opt.name == "9 Pieces"
    assert opt.price == Decimal("125")
    assert restored.categories[0].items[0].price == Decimal("135")


# ── MM → normalized translation ───────────────────────────────────────────────


def test_product_to_item_reads_variant_prices_from_modifier():
    from app.models.modifier import Modifier, ModifierOption, ProductModifier
    from app.models.product import Product

    mod = Modifier(name="Your Choice of Quantity", reference="qty")
    mod.options = [
        ModifierOption(
            name="3 Pieces",
            sku="b3",
            price=Decimal("50"),
            is_active=True,
            display_order=0,
        ),
        ModifierOption(
            name="9 Pieces",
            sku="b9",
            price=Decimal("125"),
            is_active=True,
            display_order=1,
        ),
    ]
    pm = ProductModifier(minimum_options=1, maximum_options=1, display_order=0)
    pm.modifier = mod
    product = Product(
        name="Fudge Brownies", slug="fudge", base_price=Decimal("0"), is_active=True
    )
    product.product_modifiers = [pm]
    product.category_id = None

    item = catalog_sync._product_to_item(product)
    assert item.price == Decimal("0")  # base is 0; price is in the modifier
    prices = {o.name: o.price for o in item.modifier_groups[0].options}
    assert prices == {"3 Pieces": Decimal("50"), "9 Pieces": Decimal("125")}


# ── Flag gating (writes/reads shut when off) ──────────────────────────────────


@pytest.mark.asyncio
async def test_push_is_gated_off_by_default(mock_db, monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", False)
    with pytest.raises(ServiceUnavailableError):
        await catalog_sync.plan_push(mock_db, target="careem", branch_id=None)


@pytest.mark.asyncio
async def test_refresh_is_gated_off_by_default(mock_db, monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_READ_ENABLED", False)
    with pytest.raises(ServiceUnavailableError):
        await catalog_sync.refresh_all(mock_db, branch_id=None)


def test_route_for_integrated_is_foodics():
    assert catalog_sync._route_for("foodics") == (
        "foodics_grubtech_group_and_price_tag"
    )
    assert catalog_sync._route_for("careem") == "channel_portal"


# ── Hours normalisation per channel (writer) ──────────────────────────────────


def test_keeta_caps_five_shifts_a_day_and_warns():
    hours = NormalizedHours(
        "mm",
        shifts=[NormalizedShift(0, f"{h:02d}:00", f"{h + 1:02d}:00") for h in range(6)],
    )
    shaped, warnings = catalog_sync.normalize_hours_for_channel(hours, "keeta")
    assert len([s for s in shaped.shifts if s.weekday == 0]) == 5
    assert warnings and "keeta" in warnings[0]


def test_uncapped_channels_keep_every_shift():
    hours = NormalizedHours(
        "mm",
        shifts=[NormalizedShift(0, f"{h:02d}:00", f"{h + 1:02d}:00") for h in range(6)],
    )
    shaped, warnings = catalog_sync.normalize_hours_for_channel(hours, "careem")
    assert len(shaped.shifts) == 6
    assert warnings == []


# ── Menu write-op resolution ──────────────────────────────────────────────────


# ── Foodics Grubtech price-tag parser (real captured shapes) ──────────────────

# The exact rows the live Foodics console API returned on 2026-08-31 (trimmed):
# `price` is the product's own price, `pivot.price` the aggregator price.
_FOODICS_PRICE_TAG_PRODUCTS = [
    {
        "id": "p1",
        "sku": "FG0131",
        "name": "Chocolate & Whipped Salted Caramel Cake Slice",
        "name_localized": "شريحة كيك",
        "price": 35,
        "pivot": {"price": 35},
        "is_active": True,
    },
    {
        "id": "p2",
        "sku": "FG0127",
        "name": "Ramadan Advent Gift Box (12 Pieces)",
        "name_localized": "علبة",
        "price": 55,
        "pivot": {"price": 70},
        "is_active": True,
    },
    {
        "id": "p3",
        "sku": "FG0033",
        "name": "Fudge Brownies",
        "name_localized": "براونيز",
        "price": 0,
        "pivot": {"price": 0},
        "is_active": True,
    },
]


def test_foodics_parser_uses_aggregator_price_from_pivot():
    from app.services.aggregators.menu_readers import parse_grubtech_price_tag

    menu = parse_grubtech_price_tag(_FOODICS_PRICE_TAG_PRODUCTS)
    items = {i.name: i for i in menu.categories[0].items}
    assert len(items) == 3
    # aggregator price = pivot.price (Ramadan uplifted to 70, not its own 55)
    assert items["Ramadan Advent Gift Box (12 Pieces)"].price == Decimal("70")
    assert items["Chocolate & Whipped Salted Caramel Cake Slice"].price == Decimal("35")
    assert items["Fudge Brownies"].price == Decimal("0")


def test_foodics_parity_violations_flag_the_uplifts():
    from app.services.aggregators.menu_readers import price_tag_parity_violations

    violations = price_tag_parity_violations(_FOODICS_PRICE_TAG_PRODUCTS)
    # Only the Ramadan box (55 product vs 70 tag) violates strict parity.
    assert len(violations) == 1
    assert violations[0]["name"] == "Ramadan Advent Gift Box (12 Pieces)"


def test_careem_parser_uses_real_shapes():
    # The exact shapes the live Careem API returned (2026-09-01): categories are
    # the catalog's `subCategories`; a product's price is `defaultPrice` and its
    # availability is `status == "ACTIVE"`.
    from app.services.aggregators.menu_readers import parse_careem_catalog

    categories = {
        "subCategories": [
            {"id": 111, "name": "Cookie Melt"},
            {"id": 222, "name": "Cakes"},
        ]
    }
    products = {
        "111": {
            "products": [
                {
                    "id": 3147240467,
                    "name": "Nutella Cookie Melt",
                    "status": "ACTIVE",
                    "defaultPrice": 70,
                },
                {
                    "id": 3147240468,
                    "name": "Pistachio Cookie Melt",
                    "status": "INACTIVE",
                    "defaultPrice": 70,
                },
            ],
            "pagination": {},
        },
        "222": {
            "products": [
                {
                    "id": 9,
                    "name": "Matilda Slice",
                    "status": "ACTIVE",
                    "defaultPrice": 55,
                }
            ]
        },
    }
    menu = parse_careem_catalog(categories, products)
    assert [c.name for c in menu.categories] == ["Cookie Melt", "Cakes"]
    cm = {i.name: i for i in menu.categories[0].items}
    assert cm["Nutella Cookie Melt"].price == Decimal("70")
    assert cm["Nutella Cookie Melt"].is_available is True
    assert cm["Pistachio Cookie Melt"].is_available is False
    assert menu.categories[1].items[0].price == Decimal("55")


def test_talabat_parser_uses_real_shapes():
    # The exact shapes the live Talabat/DeliveryHero vendor-api returned via the VM
    # session (2026-09-01): catalogs carry categories inline; a product's price is
    # `unitPrice` and availability is `availability.available` (AND `active`).
    from app.services.aggregators.menu_readers import parse_talabat_catalog

    catalogs = {
        "catalogs": [
            {
                "id": "1334277",
                "name": "Menu",
                "categories": [
                    {"id": 20241870, "name": "Brownies"},
                    {"id": 20241871, "name": "Cakes"},
                ],
            }
        ]
    }
    products = {
        "20241870": [
            {
                "id": "2747116483",
                "name": "Pistachio Kunafa Brownies",
                "unitPrice": 0,
                "availability": {"available": True},
                "active": True,
            },
            {
                "id": "2747116484",
                "name": "Fudge Brownies",
                "unitPrice": 0,
                "availability": {"available": False},
                "active": True,
            },
        ],
        "20241871": [
            {
                "id": "9",
                "name": "Matilda Slice",
                "unitPrice": 55,
                "availability": {"available": True},
                "active": True,
            },
        ],
    }
    menu = parse_talabat_catalog(catalogs, products)
    assert [c.name for c in menu.categories] == ["Brownies", "Cakes"]
    br = {i.name: i for i in menu.categories[0].items}
    assert br["Pistachio Kunafa Brownies"].is_available is True
    assert br["Fudge Brownies"].is_available is False  # availability.available=False
    assert menu.categories[1].items[0].price == Decimal("55")


def test_noon_parser_uses_real_shapes():
    # Real Noon /menu/details shape (VM, 2026-09-01): categories reference items by
    # code; a product's price is `price`, availability is `isActive AND NOT isOos`.
    from app.services.aggregators.menu_readers import parse_noon_menu

    details = {
        "data": {
            "items": [
                {
                    "itemCode": "I684688626A",
                    "nameEn": "Pistachio Kunafa Chocolate Cake Slice",
                    "price": 35.0,
                    "isActive": True,
                    "isOos": False,
                    "posSku": "260898759",
                },
                {
                    "itemCode": "I513758352A",
                    "nameEn": "New Item",
                    "price": 30.0,
                    "isActive": True,
                    "isOos": True,
                },
            ],
            "categories": [
                {
                    "categoryCode": "C1",
                    "nameEn": "Cakes",
                    "position": 0,
                    "items": ["I684688626A"],
                },
                {
                    "categoryCode": "C2",
                    "nameEn": "New In",
                    "position": 1,
                    "items": ["I513758352A"],
                },
            ],
        }
    }
    menu = parse_noon_menu(details)
    assert [c.name for c in menu.categories] == ["Cakes", "New In"]
    assert menu.categories[0].items[0].price == Decimal("35.0")
    assert menu.categories[0].items[0].is_available is True
    # isOos=True -> unavailable even though isActive
    assert menu.categories[1].items[0].is_available is False


def test_menu_ops_resolve_channel_id_from_last_read():
    # The snapshot (actual) carries each item's channel id; a delete op must carry
    # that id so the writer knows what to remove.
    actual = NormalizedMenu(
        "careem",
        [
            NormalizedCategory(
                "Brownies",
                items=[NormalizedItem("Fudge Brownies", external_id="CAREEM-99")],
            )
        ],
    )
    deltas = [
        {
            "kind": "item_extra_on_channel",
            "action": "delete",
            "entity": "Fudge Brownies",
            "mm_value": None,
            "channel_value": None,
            "detail": "",
        }
    ]
    ops = catalog_sync._build_menu_ops(deltas, actual)
    assert ops[0]["channel_external_id"] == "CAREEM-99"
    assert ops[0]["action"] == "delete"


# ── Create (the Foodics master write path) ────────────────────────────────────


@pytest.mark.asyncio
async def test_create_item_is_gated_off_by_default(mock_db, monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", False)
    with pytest.raises(ServiceUnavailableError):
        await catalog_sync.create_menu_item(
            mock_db, product_id="00000000-0000-0000-0000-000000000001"
        )


@pytest.mark.asyncio
async def test_foodics_create_product_enforces_parity_and_grubtech(monkeypatch):
    from app.services.providers import foodics_provider as fp

    captured = {}

    async def fake_create(self, resource, payload):
        captured["resource"] = resource
        captured["payload"] = payload
        return {"data": {"id": "FOODICS-NEW"}}

    monkeypatch.setattr(fp.FoodicsClient, "_create", fake_create)
    client = fp.FoodicsClient()
    subgroup = fp.FOODICS_GRUBTECH_SUBGROUPS["Cakes"]
    out = await client.create_product(
        name="New Cake",
        price=Decimal("40"),
        category_id="CAT-CAKES",
        subgroup_id=subgroup,
    )

    assert captured["resource"] == "/products"
    p = captured["payload"]
    # Strict parity: the price-tag price it would sync equals the product price.
    assert p["price"] == Decimal("40")
    assert p["price_tags"] == [
        {"id": fp.FOODICS_GRUBTECH_PRICE_TAG_ID, "price": Decimal("40")}
    ]
    # Placed in the Grubtech subgroup so Foodics pushes it to the marketplaces.
    assert p["groups"] == [{"id": subgroup, "is_active": True}]
    # The account's own method/tax codes are echoed (a create without them fails).
    assert p["tax_group_id"] == fp.FOODICS_VAT_TAX_GROUP_ID
    assert p["pricing_method"] == fp.FOODICS_PRICING_METHOD
    assert out["data"]["id"] == "FOODICS-NEW"


@pytest.mark.asyncio
async def test_foodics_category_id_by_name_is_case_insensitive(monkeypatch):
    from app.services.providers import foodics_provider as fp

    async def fake_list(self):
        return [{"id": "C1", "name": "Cakes"}, {"id": "C2", "name": "Brownies"}]

    monkeypatch.setattr(fp.FoodicsClient, "list_categories", fake_list)
    client = fp.FoodicsClient()
    assert await client.category_id_by_name("cakes") == "C1"
    assert await client.category_id_by_name("BROWNIES") == "C2"
    assert await client.category_id_by_name("Nope") is None


# ── Autonomous sweep ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_skips_when_reads_disabled(mock_db, monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_READ_ENABLED", False)
    out = await catalog_sync.run_catalog_sync_once(mock_db)
    assert "skipped" in out


@pytest.mark.asyncio
async def test_sweep_scheduler_returns_immediately_when_disabled(monkeypatch):
    import app.core.config as cfg

    # <= 0 disables the loop — it must return, not spin.
    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_SWEEP_MINUTES", 0)
    await catalog_sync.run_catalog_sync_scheduler_forever()
