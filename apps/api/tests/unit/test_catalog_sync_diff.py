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
    # "boxes" folds to "boxe" (symmetric matching-only artefact, never shown).
    assert normalize_name("  Mix  Boxes!! ") == "mix boxe"


def test_normalize_folds_plurals():
    # Aggregator names that differ from MM only by plural/singular must match.
    assert normalize_name("Fudge Brownies") == normalize_name("Fudge Brownie")
    assert normalize_name("Cookies and Cream Cookies") == normalize_name(
        "Cookies and Cream Cookie"
    )
    assert normalize_name("Red Velvet and Nutella Cookies") == normalize_name(
        "Red Velvet and Nutella Cookie"
    )
    # Ampersand + plural together.
    assert normalize_name("Dark Chocolate & Walnut Brownies") == normalize_name(
        "Dark Chocolate and Walnut Brownie"
    )
    # Guard: genuinely distinct items still differ.
    assert normalize_name("Fudge Brownie") != normalize_name("Nutella Brownie")


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


def test_careem_customization_groups_from_list_product():
    # Real customizationGroups shape (VM, 2026-09-05), embedded in the list product:
    # group min/max in attributes.selection; options carry id + price. DSO option
    # names are often empty (data-quality gap) — a named option still resolves.
    from app.services.aggregators.menu_readers import careem_modifier_groups

    product = {
        "id": 3147240407,
        "name": "Pistachio Kunafa Brownies",
        "customizationGroups": [
            {
                "id": 1282104241,
                "name": "Options (Max 3)",
                "nameLocalized": {"en": "Options (Max 3)"},
                "attributes": {"selection": {"min": 1, "max": 3, "multiSelect": True}},
                "options": [
                    {"id": 3147240408, "name": "", "price": 0},
                    {"id": 3147240409, "name": "Fudge Brownie", "price": 0},
                ],
            }
        ],
    }
    groups = careem_modifier_groups(product)
    assert len(groups) == 1
    g = groups[0]
    assert g.name == "Options (Max 3)"
    assert g.external_ref == "1282104241"
    assert (g.min_options, g.max_options) == (1, 3)
    assert [o.external_ref for o in g.options] == ["3147240408", "3147240409"]
    # empty name preserved (unmappable), a named option carried through
    assert g.options[0].name == ""
    assert g.options[1].name == "Fudge Brownie"
    # no groups → no crash
    assert careem_modifier_groups({"id": 1, "name": "x"}) == []


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


def test_talabat_sizes_attach_from_product_detail():
    # Real product-detail shape (VM, 2026-09-05): a SIZED_PRODUCT's sizes live in
    # `nestedProducts` (each type "SIZE", named + unitPrice), not in the list call.
    from app.services.aggregators.menu_readers import parse_talabat_catalog

    catalogs = {
        "catalogs": [
            {"id": "1334277", "categories": [{"id": 20241870, "name": "Brownies"}]}
        ]
    }
    products = {
        "20241870": [
            {
                "id": "2747116483",
                "name": "Pistachio Kunafa Brownies",
                "unitPrice": 0,
                "type": "SIZED_PRODUCT",
                "availability": {"available": True},
                "active": True,
            }
        ]
    }
    details = {
        "2747116483": {
            "id": "2747116483",
            "type": "SIZED_PRODUCT",
            "nestedProducts": [
                {
                    "id": "s3",
                    "name": "3 Pieces",
                    "unitPrice": 55,
                    "type": "SIZE",
                    "availability": {"available": True},
                },
                {
                    "id": "s6",
                    "name": "6 Pieces",
                    "unitPrice": 100,
                    "type": "SIZE",
                    "availability": {"available": False},
                },
                {
                    "id": "s9",
                    "name": "9 Pieces",
                    "unitPrice": 145,
                    "type": "SIZE",
                    "availability": {"available": True},
                },
            ],
        }
    }
    menu = parse_talabat_catalog(catalogs, products, details)
    item = menu.categories[0].items[0]
    assert len(item.modifier_groups) == 1
    grp = item.modifier_groups[0]
    assert grp.name == "Size"
    assert (grp.min_options, grp.max_options) == (1, 1)
    assert {(o.name, o.price) for o in grp.options} == {
        ("3 Pieces", Decimal("55")),
        ("6 Pieces", Decimal("100")),
        ("9 Pieces", Decimal("145")),
    }
    # size availability carried through (6 Pieces is off)
    assert {o.name: o.is_available for o in grp.options}["6 Pieces"] is False


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
                # A variant-priced brownie (₿0 base) whose real prices live in a
                # "Your Choice of Quantity" customization group (real shape, 2026-09-05).
                {
                    "itemCode": "I900000001A",
                    "nameEn": "Pistachio Kunafa Brownies",
                    "price": 0,
                    "isActive": True,
                    "isOos": False,
                    "modifiers": ["MD793413946A"],
                },
                # The group's options are non-`main` items referenced by itemCode.
                {"itemCode": "OPT3", "nameEn": "3 Pieces", "itemType": "modifier"},
                {"itemCode": "OPT6", "nameEn": "6 Pieces", "itemType": "modifier"},
                {"itemCode": "OPT9", "nameEn": "9 Pieces", "itemType": "modifier"},
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
                {
                    "categoryCode": "C3",
                    "nameEn": "Brownies",
                    "position": 2,
                    "items": ["I900000001A"],
                },
            ],
            "modifiers": [
                {
                    "modifierCode": "MD793413946A",
                    "nameEn": "Your Choice of Quantity",
                    "minTotalOptions": 1,
                    "maxTotalOptions": 1,
                    "options": [
                        {"itemCode": "OPT3", "price": 55},
                        {"itemCode": "OPT6", "price": 100},
                        {"itemCode": "OPT9", "price": 145},
                    ],
                }
            ],
        }
    }
    menu = parse_noon_menu(details)
    assert [c.name for c in menu.categories] == ["Cakes", "New In", "Brownies"]
    assert menu.categories[0].items[0].price == Decimal("35.0")
    assert menu.categories[0].items[0].is_available is True
    # isOos=True -> unavailable even though isActive
    assert menu.categories[1].items[0].is_available is False
    # The variant-priced brownie carries its quantity group with resolved option
    # names + prices (the price lived in the modifier, not the item).
    brownie = menu.categories[2].items[0]
    assert brownie.price == Decimal("0")
    assert len(brownie.modifier_groups) == 1
    grp = brownie.modifier_groups[0]
    assert grp.name == "Your Choice of Quantity"
    assert grp.external_ref == "MD793413946A"
    assert (grp.min_options, grp.max_options) == (1, 1)
    assert {(o.name, o.price) for o in grp.options} == {
        ("3 Pieces", Decimal("55")),
        ("6 Pieces", Decimal("100")),
        ("9 Pieces", Decimal("145")),
    }


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
async def test_create_dispatch_gates_unverified_and_worker_channels(
    mock_db, monkeypatch
):
    import app.core.config as cfg
    from app.core.exceptions import BadRequestError

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", True)

    # A product must resolve first; mock_db returns one.
    class _P:
        id = "p1"
        name = "X"
        sku = "s"
        base_price = Decimal("10")
        category = None

    mock_db.execute.return_value.scalar_one_or_none.return_value = _P()
    # Keeta/Deliveroo → headed worker. Careem/Noon/Talabat are httpx (need branch_id).
    for target in ("keeta", "deliveroo"):
        with pytest.raises(BadRequestError, match="headed worker"):
            await catalog_sync.create_menu_item(mock_db, product_id="p1", target=target)
    with pytest.raises(BadRequestError, match="branch_id"):
        await catalog_sync.create_menu_item(mock_db, product_id="p1", target="talabat")


@pytest.mark.asyncio
async def test_talabat_create_dry_run_uses_captured_add_product_shape(
    mock_db, monkeypatch
):
    import app.core.config as cfg
    from app.services.aggregators import session_store
    from app.services.providers import talabat_provider as tp

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", True)

    class _Cat:
        name = "Cakes"

    class _P:
        id = "p1"
        name = "ZZ Test Slice"
        sku = "s"
        base_price = Decimal("35")
        category = _Cat()

    mock_db.execute.return_value.scalar_one_or_none.return_value = _P()

    async def fake_vendor(_db, branch_id):
        assert branch_id == "karama"
        return "793319"

    async def fake_load(_db, channel):
        assert channel == "talabat"
        return object()

    async def fake_prepare(_db, session):
        return session

    async def fake_list(_session, vendor):
        assert vendor == "793319"
        return {
            "catalogs": [
                {
                    "id": "1334277",
                    "name": "Menu",
                    "categories": [{"id": 20241871, "name": "Cakes"}],
                }
            ]
        }

    posted = []

    async def fake_create(*_a, **_k):
        posted.append(1)
        return {"commandId": "should-not-run"}

    monkeypatch.setattr(
        "app.services.aggregators.menu_readers._talabat_vendor", fake_vendor
    )
    monkeypatch.setattr(session_store, "load", fake_load)
    monkeypatch.setattr(tp.provider, "prepare_session", fake_prepare)
    monkeypatch.setattr(tp.provider, "list_catalogs", fake_list)
    monkeypatch.setattr(tp.provider, "create_menu_item", fake_create)

    plan = await catalog_sync.create_menu_item(
        mock_db,
        product_id="p1",
        target="talabat",
        branch_id="karama",
        dry_run=True,
    )
    assert plan["dry_run"] is True
    assert plan["talabat_create"] == {
        "vendor": "793319",
        "name": "ZZ Test Slice",
        "unitPrice": 35.0,
        "catalogIds": ["1334277"],
        "category": "20241871",
        "type": "Simple",
        "active": False,
    }
    assert posted == []


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


# ── Careem hours reader (real shape, verified day origin) ─────────────────────


def test_careem_hours_maps_day_origin_and_closed_days():
    from app.services.aggregators.menu_readers import parse_careem_hours

    # The real response shape (read live 2026-09-01): day 1=Sunday … 7=Saturday,
    # active:0 = closed, times are HH:MM:SS.
    rows = [
        {
            "day": 1,
            "active": 1,
            "shifts": [{"start_time": "08:00:00", "end_time": "21:45:00"}],
        },
        {"day": 4, "active": 0, "shifts": []},  # Wednesday closed
        {
            "day": 7,
            "active": 1,
            "shifts": [{"start_time": "12:05:00", "end_time": "21:45:00"}],
        },
    ]
    hours = parse_careem_hours(rows)
    by_weekday = {s.weekday: (s.opens, s.closes) for s in hours.shifts}
    # day 1 (Sunday) → MM weekday 0; day 7 (Saturday) → MM weekday 6.
    assert by_weekday[0] == ("08:00", "21:45")
    assert by_weekday[6] == ("12:05", "21:45")
    # A closed day (Wednesday = day 4 → weekday 3) contributes no shift.
    assert 3 not in by_weekday
    assert hours.source == "careem"


# ── Noon hours parser (real captured shape, day origin from periodsDesc) ──────


def test_noon_hours_parser_maps_day_origin():
    from app.services.aggregators.menu_readers import parse_noon_hours

    # Real shape (captured live 2026-09-01): periods keyed by day index, comma-joined
    # for shared schedules; periodsDesc proves day 0=Mon … 6=Sun.
    details = {
        "data": {
            "schedule": {
                "periods": {
                    "0,1,2,3": [["08:00:00", "22:00:00"]],  # Mon-Thu
                    "4": [["12:01:00", "22:00:00"]],  # Fri
                    "6": [["17:00:00", "22:00:00"]],  # Sun
                }
            }
        }
    }
    hours = parse_noon_hours(details)
    by_weekday = {s.weekday: (s.opens, s.closes) for s in hours.shifts}
    # Noon 0=Mon → MM weekday 1; Noon 4=Fri → MM 5; Noon 6=Sun → MM 0.
    assert by_weekday[1] == ("08:00", "22:00")  # Monday
    assert by_weekday[5] == ("12:01", "22:00")  # Friday
    assert by_weekday[0] == ("17:00", "22:00")  # Sunday
    assert 6 not in by_weekday  # Saturday (noon day 5) not listed → closed
    assert hours.source == "noon"


# ── Keeta menu parser (real captured shapes) ──────────────────────────────────


def test_keeta_menu_parser_uses_real_shapes():
    from app.services.aggregators.menu_readers import parse_keeta_menu

    # The real listShopCategory + listSpu shapes (captured live 2026-09-01).
    raw = {
        "categories": [
            {"id": 24975776, "name": "Brownies", "status": 1},
            {"id": 24225090, "name": "New In", "status": 1},
        ],
        "spus": [
            {
                "id": 116377820,
                "name": "Eggless Fudge Brownies",
                "status": 1,
                "shopCategoryIdList": [24975776],
                "skuList": [{"id": 113254318, "price": "35", "currency": "AED"}],
            },
            {
                "id": 116377999,
                "name": "Snoozed Item",
                "status": 0,  # off-shelf
                "shopCategoryIdList": [24975776],
                "skuList": [{"price": "40"}],
            },
        ],
    }
    menu = parse_keeta_menu(raw)
    assert menu.source == "keeta"
    brownies = next(c for c in menu.categories if c.name == "Brownies")
    names = {i.name: i for i in brownies.items}
    assert names["Eggless Fudge Brownies"].price == Decimal("35")
    assert names["Eggless Fudge Brownies"].is_available is True
    assert names["Snoozed Item"].is_available is False  # status 0


# ── Keeta hours parser (real captured SCM summary shape) ──────────────────────


def test_keeta_today_hours_parser_converts_seconds():
    from app.services.aggregators.menu_readers import (
        _seconds_to_hhmm,
        parse_keeta_today_hours,
    )

    # Real per-shop shape captured live 2026-09-01 from
    # `POST /api/scm/gw/shop/base/summary/list` — times are seconds-from-midnight.
    assert _seconds_to_hhmm(28800) == "08:00"
    assert _seconds_to_hhmm(84600) == "23:30"

    shop = {
        "businessStatus": 1,
        "todayBusinessHours": [{"startTime": 28800, "endTime": 84600}],
    }
    hours = parse_keeta_today_hours(shop, weekday=3)  # e.g. a Wednesday
    assert hours.source == "keeta"
    assert [(s.weekday, s.opens, s.closes) for s in hours.shifts] == [
        (3, "08:00", "23:30")
    ]

    # A temporarily-closed shop (businessStatus != 1) yields no shift.
    closed = parse_keeta_today_hours(
        {"businessStatus": 2, "todayBusinessHours": [{"startTime": 0, "endTime": 100}]},
        weekday=3,
    )
    assert closed.shifts == []


def test_keeta_is_registered_hours_reader():
    from app.services.aggregators.menu_readers import _HOURS_READERS

    assert "keeta" in _HOURS_READERS


# ── Deliveroo menu + hours parsers (real captured shapes, 2026-09-01) ──────────


def test_deliveroo_menu_parser_real_shape():
    from app.services.aggregators.menu_readers import parse_deliveroo_menu

    raw = {
        "categories": [
            {
                "id": 967061056,
                "name": "New In",
                "items": [
                    {
                        "id": 2773044565,
                        "name": "Chocolate & Whipped Salted Caramel Cake Slice",
                        "description": "Chocolate cake…",
                        "price": 35,
                        "status": "ACTIVE",
                    },
                    {
                        "id": 111,
                        "name": "9 Pieces",
                        "price": 145,
                        "status": "ACTIVE",
                    },
                    {
                        "id": 222,
                        "name": "Snoozed Slice",
                        "price": 35,
                        "status": "SNOOZED",
                    },
                ],
            }
        ]
    }
    menu = parse_deliveroo_menu(raw)
    assert menu.source == "deliveroo"
    items = {i.name: i for i in menu.categories[0].items}
    # price is MAJOR AED units — 35 stays 35, never 0.35 or 3500 (money-bug guard).
    assert items["Chocolate & Whipped Salted Caramel Cake Slice"].price == Decimal("35")
    assert items["9 Pieces"].price == Decimal("145")
    assert items["Chocolate & Whipped Salted Caramel Cake Slice"].is_available is True
    # status != ACTIVE (snoozed) reads as unavailable.
    assert items["Snoozed Slice"].is_available is False


def test_deliveroo_hours_parser_day_origin():
    from app.services.aggregators.menu_readers import parse_deliveroo_hours

    # Real shape: day_of_week 0=Sunday…6=Saturday (= MM weekday, no shift).
    raw = {
        "hours": [
            {
                "day_of_week": 0,
                "local_start_time": "17:00:00",
                "local_end_time": "22:00:00",
            },
            {
                "day_of_week": 1,
                "local_start_time": "08:00:00",
                "local_end_time": "22:00:00",
            },
            {
                "day_of_week": 5,
                "local_start_time": "12:00:00",
                "local_end_time": "22:00:00",
            },
        ]
    }
    hours = parse_deliveroo_hours(raw)
    assert hours.source == "deliveroo"
    by_weekday = {s.weekday: (s.opens, s.closes) for s in hours.shifts}
    assert by_weekday[0] == ("17:00", "22:00")  # Sunday
    assert by_weekday[1] == ("08:00", "22:00")  # Monday
    assert by_weekday[5] == ("12:00", "22:00")  # Friday (later start, prayer day)


def test_talabat_hours_parser_day_origin_minutes_and_closed_day():
    from app.services.aggregators.menu_readers import parse_talabat_hours

    # Real DeliveryHero VTS shape: `Normal` calendar, day 0=Monday..6=Sunday
    # (firstDOW=0), from/to in minutes-from-midnight. Karama is closed Friday
    # (DH day 4 absent -> MM weekday 5).
    raw = {
        "calendars": [
            {"name": "Holiday", "schedule": {"openingTimesByDay": []}},
            {
                "name": "Normal",
                "schedule": {
                    "openingTimesByDay": [
                        {"day": 6, "openingTimes": [{"from": 495, "to": 1410}]},
                        {"day": 0, "openingTimes": [{"from": 780, "to": 1365}]},
                        {"day": 4, "openingTimes": []},  # closed
                    ]
                },
            },
        ]
    }
    hours = parse_talabat_hours(raw)
    assert hours.source == "talabat"
    by_weekday = {s.weekday: (s.opens, s.closes) for s in hours.shifts}
    assert by_weekday[0] == ("08:15", "23:30")  # DH day 6 (Sun) -> weekday 0
    assert by_weekday[1] == ("13:00", "22:45")  # DH day 0 (Mon) -> weekday 1
    assert 5 not in by_weekday  # DH day 4 (Fri) empty -> no shift


# ── Foodics apply executor (price parity + reversible removal) ─────────────────


def _fake_plan(operations):
    async def _plan(db, *, target, branch_id, kind):
        return {"operations": operations}

    return _plan


@pytest.mark.asyncio
async def test_apply_menu_push_gated_off_by_default(mock_db, monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", False)
    with pytest.raises(Exception):
        await catalog_sync.apply_menu_push(mock_db, target="foodics")


@pytest.mark.asyncio
async def test_apply_menu_push_is_foodics_only(mock_db, monkeypatch):
    import app.core.config as cfg
    from app.core.exceptions import BadRequestError

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", True)
    with pytest.raises(BadRequestError):
        await catalog_sync.apply_menu_push(mock_db, target="careem")


@pytest.mark.asyncio
async def test_apply_dry_run_reports_but_writes_nothing(mock_db, monkeypatch):
    import app.core.config as cfg
    from app.services.aggregators import catalog_diff as cd
    from app.services.providers import foodics_provider as fp

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", True)
    ops = [
        {
            "kind": cd.K_ITEM_PRICE,
            "action": cd.ACTION_UPDATE,
            "entity": "Fudge Brownies",
            "channel_external_id": "FP1",
            "mm_value": "30",
            "channel_value": "35",
        },
        {
            "kind": cd.K_ITEM_EXTRA,
            "action": cd.ACTION_DELETE,
            "entity": "Discontinued Slice",
            "channel_external_id": "FP2",
            "mm_value": None,
            "channel_value": None,
        },
    ]
    monkeypatch.setattr(catalog_sync, "plan_push", _fake_plan(ops))
    calls = []
    monkeypatch.setattr(
        fp.provider,
        "set_price_tag_product_price",
        lambda *a, **k: calls.append(("price", a)),
    )
    monkeypatch.setattr(
        fp.provider,
        "remove_price_tag_product",
        lambda *a, **k: calls.append(("del", a)),
    )

    out = await catalog_sync.apply_menu_push(mock_db, target="foodics", dry_run=True)
    assert out["dry_run"] is True
    assert calls == []  # a dry run touches no portal
    assert out["price_updates"][0]["to"] == "30"
    assert out["removals"][0]["reported_only"] is True


@pytest.mark.asyncio
async def test_apply_live_sets_price_but_only_removes_with_flag(mock_db, monkeypatch):
    import app.core.config as cfg
    from app.services.aggregators import catalog_diff as cd
    from app.services.providers import foodics_provider as fp

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", True)
    ops = [
        {
            "kind": cd.K_ITEM_PRICE,
            "action": cd.ACTION_UPDATE,
            "entity": "Fudge Brownies",
            "channel_external_id": "FP1",
            "mm_value": "30",
            "channel_value": "35",
        },
        {
            "kind": cd.K_ITEM_EXTRA,
            "action": cd.ACTION_DELETE,
            "entity": "Discontinued Slice",
            "channel_external_id": "FP2",
            "mm_value": None,
            "channel_value": None,
        },
    ]
    monkeypatch.setattr(catalog_sync, "plan_push", _fake_plan(ops))
    price_calls, del_calls = [], []

    async def fake_price(tag, pid, price):
        price_calls.append((tag, pid, price))

    async def fake_del(tag, pid):
        del_calls.append((tag, pid))

    monkeypatch.setattr(fp.provider, "set_price_tag_product_price", fake_price)
    monkeypatch.setattr(fp.provider, "remove_price_tag_product", fake_del)

    # Live, but apply_deletes not set: price is written, removal is only reported.
    out = await catalog_sync.apply_menu_push(mock_db, target="foodics", dry_run=False)
    assert price_calls == [(fp.FOODICS_GRUBTECH_PRICE_TAG_ID, "FP1", "30")]
    assert del_calls == []
    assert out["removals"][0]["reported_only"] is True

    # Live + apply_deletes: the removal is executed too.
    out = await catalog_sync.apply_menu_push(
        mock_db, target="foodics", dry_run=False, apply_deletes=True
    )
    assert del_calls == [(fp.FOODICS_GRUBTECH_PRICE_TAG_ID, "FP2")]
    assert out["removals"][0]["applied"] is True
