"""Menu-group PDF: builder logic, theming, and a guarded render smoke test.

The pricing/localization/column logic is pure and tested here against light
attribute fakes (no DB). The render pass needs WeasyPrint's Pango libraries, which
the CI Python runner does not carry, so that test skips itself where they are
missing rather than turning the whole job red.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.catalog.menu_pdf import builder as B
from app.services.catalog.menu_pdf.icons import icon_key_for
from app.services.catalog.menu_pdf.theme import (
    ATTIBASSI_THEME,
    DEFAULT_THEME,
    MELTING_MOMENTS_THEME,
    theme_for_entity_reference,
)
from app.services.catalog.menu_pdf.view_models import MenuItem, MenuSection


def _opt(name, price, order=0, active=True, translations=None):
    return SimpleNamespace(
        name=name,
        price=Decimal(str(price)),
        display_order=order,
        is_active=active,
        translations=translations or {},
    )


def _pm(options, *, minimum=0, maximum=1, order=0, mod_name="Choice", mod_tr=None):
    modifier = SimpleNamespace(
        name=mod_name, translations=mod_tr or {}, options=options
    )
    return SimpleNamespace(
        minimum_options=minimum,
        maximum_options=maximum,
        display_order=order,
        modifier=modifier,
    )


def _product(
    *,
    name="Item",
    base=0,
    mods=None,
    translations=None,
    images=None,
    description=None,
    name_localized=None,
    calories=None,
):
    return SimpleNamespace(
        name=name,
        base_price=Decimal(str(base)),
        product_modifiers=mods or [],
        translations=translations or {},
        image_urls=images or [],
        description=description,
        name_localized=name_localized,
        calories=calories,
    )


# ── money ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        (15, "15"),
        (Decimal("18.00"), "18"),
        (Decimal("18.50"), "18.50"),
        (0, "0"),
        (None, ""),
    ],
)
def test_money_formats_whole_vs_fractional(value, expected):
    assert B._money(value) == expected


# ── localization ───────────────────────────────────────────────────────────────


def test_localized_en_reads_base_column():
    p = _product(name="Latte", translations={"ar": {"name": "لاتيه"}})
    assert B._localized(p, "name", "en") == "Latte"


def test_localized_ar_prefers_translation_then_legacy_then_base():
    p = _product(name="Latte", translations={"ar": {"name": "لاتيه"}})
    assert B._localized(p, "name", "ar") == "لاتيه"

    legacy = _product(name="Latte", translations={}, name_localized="لاتيه قديم")
    assert B._localized(legacy, "name", "ar") == "لاتيه قديم"

    bare = _product(name="Latte", translations={})
    assert B._localized(bare, "name", "ar") == "Latte"


# ── pricing ────────────────────────────────────────────────────────────────────


def test_size_modifier_becomes_variants_absolute_priced():
    # Modifier-priced (base 0): the option price is the whole price.
    p = _product(
        base=0,
        mods=[_pm([_opt("Small", 14, 0), _opt("Large", 18, 1)], minimum=1, maximum=1)],
    )
    item = B._build_item(p, "en")
    assert [(v.label, v.price) for v in item.variants] == [
        ("Small", "14"),
        ("Large", "18"),
    ]
    assert item.single_price is None


def test_size_modifier_adds_surcharge_over_base():
    p = _product(
        base=10,
        mods=[_pm([_opt("Regular", 0, 0), _opt("Large", 5, 1)], minimum=1, maximum=1)],
    )
    item = B._build_item(p, "en")
    assert [(v.label, v.price) for v in item.variants] == [
        ("Regular", "10"),
        ("Large", "15"),
    ]


def test_fixed_price_no_modifiers_has_no_prefix():
    item = B._build_item(_product(base=15), "en")
    assert item.single_price == "15"
    assert item.price_prefix is None
    assert item.variants == []


def test_modifier_priced_without_required_group_is_a_from_price():
    # Optional add-ons only (min 0): the cheapest non-zero option is a "From".
    p = _product(
        base=0,
        mods=[
            _pm([_opt("Extra shot", 3, 0), _opt("Syrup", 5, 1)], minimum=0, maximum=3)
        ],
    )
    item = B._build_item(p, "en")
    assert item.single_price == "3"
    assert item.price_prefix == "From"


def test_inactive_options_are_ignored():
    p = _product(
        base=0,
        mods=[
            _pm(
                [_opt("Small", 14, 0, active=False), _opt("Large", 18, 1)],
                minimum=1,
                maximum=1,
            )
        ],
    )
    item = B._build_item(p, "en")
    # Only one active option left -> not a size grid; falls to from-price.
    assert item.variants == []
    assert item.single_price == "18"


# ── column layout ──────────────────────────────────────────────────────────────


def _variant_item(pairs):
    from app.services.catalog.menu_pdf.view_models import Variant

    return MenuItem(
        name="x",
        description=None,
        image_url=None,
        image_data_uri=None,
        single_price=None,
        price_prefix=None,
        variants=[Variant(label, price) for label, price in pairs],
        column_prices=None,
        calories=None,
    )


def test_column_layout_applies_when_sizes_line_up():
    section = MenuSection(
        title="Hot",
        icon_key="coffee",
        columns=None,
        items=[
            _variant_item([("S", "14"), ("M", "16"), ("L", "18")]),
            _variant_item([("S", "16"), ("M", "18"), ("L", "20")]),
        ],
    )
    B._apply_column_layout(section)
    assert section.columns == ["S", "M", "L"]
    assert section.items[0].column_prices == ["14", "16", "18"]
    assert section.items[1].column_prices == ["16", "18", "20"]


def test_column_layout_skipped_when_sizes_disagree():
    section = MenuSection(
        title="Mixed",
        icon_key="coffee",
        columns=None,
        items=[
            _variant_item([("S", "14"), ("M", "16"), ("L", "18")]),
            _variant_item([("Single", "12"), ("Double", "14")]),
        ],
    )
    B._apply_column_layout(section)
    assert section.columns is None
    assert all(it.column_prices is None for it in section.items)


# ── section ordering (extras demoted) ──────────────────────────────────────────


def _named_section(title):
    return MenuSection(title=title, icon_key="default", items=[], columns=None)


def test_extras_detection():
    assert B._is_extras("Extras")
    assert B._is_extras("Add-ons")
    assert B._is_extras("Sauces & Dips")
    assert not B._is_extras("Cakes")
    assert not B._is_extras("Hot Beverages")


def test_order_sections_sinks_extras_but_keeps_the_rest_in_order():
    pairs = [
        ("Extras", _named_section("Extras")),
        ("Desserts", _named_section("Desserts")),
        ("Cakes", _named_section("Cakes")),
        ("Add-ons", _named_section("Add-ons")),
    ]
    ordered = B._order_sections(pairs)
    assert [s.title for s in ordered] == ["Desserts", "Cakes", "Extras", "Add-ons"]


# ── theme + icons ──────────────────────────────────────────────────────────────


def test_theme_selection_by_entity_reference():
    assert theme_for_entity_reference("najm") is ATTIBASSI_THEME
    assert theme_for_entity_reference("fatema") is MELTING_MOMENTS_THEME
    assert theme_for_entity_reference("someone-new") is DEFAULT_THEME
    assert theme_for_entity_reference(None) is DEFAULT_THEME


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Hot Beverages", "coffee"),
        ("Iced Coffee", "iced"),
        ("Freshly Made Smoothies", "smoothie"),
        ("Juices and Mocktails", "juice"),
        ("Brownies", "brownie"),
        ("Cookies", "cookie"),
        ("Cakes", "cake"),
        ("Random Section", "default"),
    ],
)
def test_icon_key_guessing(name, expected):
    assert icon_key_for(name) == expected


# ── render smoke (guarded) ─────────────────────────────────────────────────────


def test_render_smoke_produces_pdf():
    try:
        from weasyprint import HTML  # noqa: F401
    except Exception:
        pytest.skip("WeasyPrint/Pango not available in this environment")

    from app.services.catalog.menu_pdf.render import render_menu_pdf
    from app.services.catalog.menu_pdf.view_models import MenuDocument

    doc = MenuDocument(
        lang="en",
        direction="ltr",
        theme=DEFAULT_THEME,
        brand_name="Test",
        logo_url=None,
        logo_data_uri=None,
        title="Menu",
        branch_name="Branch",
        whatsapp_number="+971 50 000 0000",
        whatsapp_url="https://wa.me/971500000000",
        qr_svg=None,
        sections=[
            MenuSection(
                title="Coffee",
                icon_key="coffee",
                columns=None,
                items=[
                    MenuItem(
                        name="Latte",
                        description="Smooth",
                        image_url=None,
                        image_data_uri=None,
                        single_price="15",
                        price_prefix=None,
                        variants=[],
                        column_prices=None,
                        calories=None,
                    )
                ],
            )
        ],
    )
    try:
        pdf = render_menu_pdf(doc)
    except OSError:
        pytest.skip("Pango runtime libraries not installed")
    assert pdf[:5] == b"%PDF-"
