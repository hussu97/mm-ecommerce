"""The optional aggregator price, and integrator categories live at some branches.

Two rules the marketplace sync now carries, tested without a database:

* the marketplaces quote an item's **aggregator price** when it has one and its
  counter price otherwise — for products and for options, since a sized drink is
  priced entirely by its options;
* an integrator category limited to some branches is written only where it is
  sold: active at those Foodics branches and inactive at the rest, never created
  on a non-Foodics portal it is not sold at, never pushed to another GrubOps
  location.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.exceptions import BadRequestError
from app.services.aggregators import catalog_mapping, catalog_sync
from app.services.catalog import aggregator_pricing, menu_group_service
from app.services.grubops import grubops_service
from app.services.providers import foodics_provider

BARSHA, SHARJAH, DSO, KARAMA = uuid4(), uuid4(), uuid4(), uuid4()


def _branch(bid, *, foodics: bool, aggregators: list[str]):
    return SimpleNamespace(id=bid, has_foodics=foodics, aggregators=aggregators)


BRANCHES = [
    _branch(BARSHA, foodics=True, aggregators=["careem", "keeta", "noon", "talabat"]),
    _branch(SHARJAH, foodics=True, aggregators=["keeta", "noon", "talabat"]),
    _branch(DSO, foodics=False, aggregators=["careem", "deliveroo", "keeta", "noon"]),
    _branch(KARAMA, foodics=False, aggregators=["keeta", "noon", "talabat"]),
]


# ── the price ────────────────────────────────────────────────────────────────


def test_an_unset_aggregator_price_is_the_counter_price():
    item = SimpleNamespace(base_price=Decimal("35.00"), aggregator_price=None)
    assert aggregator_pricing.product_price(item) == Decimal("35.00")
    option = SimpleNamespace(price=Decimal("14.00"), aggregator_price=None)
    assert aggregator_pricing.option_price(option) == Decimal("14.00")


def test_a_set_aggregator_price_wins_even_when_it_is_zero():
    item = SimpleNamespace(base_price=Decimal("35.00"), aggregator_price=Decimal("0"))
    assert aggregator_pricing.product_price(item) == Decimal("0")
    option = SimpleNamespace(price=Decimal("14.00"), aggregator_price=Decimal("22.00"))
    assert aggregator_pricing.option_price(option) == Decimal("22.00")


def test_the_desired_marketplace_menu_quotes_aggregator_prices():
    """A sized coffee: base 0, the +8 on each size. The menu every diff and push
    is built from must carry 22/24/26, not the counter's 14/16/18."""
    options = [
        SimpleNamespace(
            name=name,
            translations={},
            sku=sku,
            price=Decimal(p),
            aggregator_price=Decimal(p) + 8,
            is_active=True,
            display_order=i,
        )
        for i, (name, sku, p) in enumerate(
            [
                ("Small", "FM0158", "14"),
                ("Medium", "FM0159", "16"),
                ("Large", "FM0160", "18"),
            ]
        )
    ]
    modifier = SimpleNamespace(
        name="Size", translations={}, reference="mod_capp_cf", options=options
    )
    product = SimpleNamespace(
        id=uuid4(),
        name="Cappuccino",
        translations={},
        sku="FG0088",
        description=None,
        image_urls=[],
        base_price=Decimal("0"),
        aggregator_price=None,
        is_active=True,
        category_id=None,
        product_modifiers=[
            SimpleNamespace(
                display_order=0, modifier=modifier, minimum_options=1, maximum_options=1
            )
        ],
    )
    item = catalog_sync._product_to_item(product)
    assert item.price == Decimal("0")
    assert [o.price for o in item.modifier_groups[0].options] == [
        Decimal("22"),
        Decimal("24"),
        Decimal("26"),
    ]


@pytest.mark.asyncio
async def test_channel_options_match_on_the_marketplace_price_first():
    oid = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                all=lambda: [(oid, "Medium", Decimal("15.00"), Decimal("23.00"))]
            )
        )
    )
    rows = await catalog_mapping._option_rows(db)
    # Marketplace key first, counter key as the fallback for a stale channel.
    assert rows == [
        (oid, "Medium", Decimal("23.00")),
        (oid, "Medium", Decimal("15.00")),
    ]


@pytest.mark.asyncio
async def test_an_option_without_an_aggregator_price_has_one_key():
    oid = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                all=lambda: [(oid, "3 Pieces", Decimal("55.00"), None)]
            )
        )
    )
    assert await catalog_mapping._option_rows(db) == [
        (oid, "3 Pieces", Decimal("55.00"))
    ]


# ── where it is sold ─────────────────────────────────────────────────────────


def test_sold_at():
    assert menu_group_service.sold_at(None, SHARJAH)  # unscoped: everywhere
    assert menu_group_service.sold_at(frozenset({BARSHA}), BARSHA)
    assert not menu_group_service.sold_at(frozenset({BARSHA}), SHARJAH)
    assert not menu_group_service.sold_at(frozenset(), BARSHA)  # not in the tree


def test_a_barsha_only_item_is_written_to_foodics_and_nowhere_else():
    scope = frozenset({BARSHA})
    assert [b.id for b in catalog_sync._outlets_for("foodics", BRANCHES, scope)] == [
        BARSHA
    ]
    for system in ("careem", "noon", "talabat", "deliveroo", "keeta"):
        # Every direct-write system is for the non-Foodics outlets (Karama, DSO),
        # and neither sells a Barsha-only category.
        assert catalog_sync._outlets_for(system, BRANCHES, scope) == [], system


def test_an_unscoped_item_keeps_today_s_fan_out():
    assert [b.id for b in catalog_sync._outlets_for("foodics", BRANCHES, None)] == [
        BARSHA,
        SHARJAH,
    ]
    assert [b.id for b in catalog_sync._outlets_for("careem", BRANCHES, None)] == [DSO]
    assert [b.id for b in catalog_sync._outlets_for("noon", BRANCHES, None)] == [
        DSO,
        KARAMA,
    ]


@pytest.mark.asyncio
async def test_only_an_integrator_category_can_be_scoped():
    with pytest.raises(BadRequestError, match="integrator menu category"):
        await menu_group_service._validated_marketplace_scope(
            SimpleNamespace(), [BARSHA], root_kind="branch", is_child=True
        )
    with pytest.raises(BadRequestError, match="integrator menu category"):
        await menu_group_service._validated_marketplace_scope(
            SimpleNamespace(), [BARSHA], root_kind="integrator", is_child=False
        )


@pytest.mark.asyncio
async def test_an_empty_scope_is_refused_and_null_means_everywhere():
    with pytest.raises(BadRequestError, match="at least one branch"):
        await menu_group_service._validated_marketplace_scope(
            SimpleNamespace(), [], root_kind="integrator", is_child=True
        )
    assert (
        await menu_group_service._validated_marketplace_scope(
            SimpleNamespace(), None, root_kind="branch", is_child=False
        )
        is None
    )


@pytest.mark.asyncio
async def test_grubops_skips_an_option_only_when_every_carrier_is_scoped_away(
    monkeypatch,
):
    coffee, cake = uuid4(), uuid4()
    coffee_size, shared = uuid4(), uuid4()
    monkeypatch.setattr(
        menu_group_service,
        "marketplace_excluded_product_ids",
        AsyncMock(return_value={coffee}),
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                all=lambda: [(coffee, coffee_size), (coffee, shared), (cake, shared)]
            )
        )
    )
    products, options = await grubops_service.scoped_away(db, SHARJAH)
    assert products == {coffee}
    assert options == {coffee_size}  # `shared` is still sold on the cake


@pytest.mark.asyncio
async def test_grubops_scoping_is_free_when_nothing_is_scoped(monkeypatch):
    monkeypatch.setattr(
        menu_group_service,
        "marketplace_excluded_product_ids",
        AsyncMock(return_value=set()),
    )
    db = SimpleNamespace(execute=AsyncMock(side_effect=AssertionError("no read")))
    assert await grubops_service.scoped_away(db, BARSHA) == (set(), set())


# ── Foodics writes that replace a whole list ─────────────────────────────────


@pytest.mark.asyncio
async def test_joining_a_group_keeps_every_group_the_product_was_in(monkeypatch):
    client = foodics_provider.FoodicsClient()
    monkeypatch.setattr(
        client,
        "get_product",
        AsyncMock(return_value={"groups": [{"id": "pos-hot-coffee"}]}),
    )
    call = AsyncMock()
    monkeypatch.setattr(client, "_call", call)
    assert await client.ensure_product_in_group("p1", "grubtech-hot-coffee") is True
    payload = call.await_args.kwargs["json_body"]["payload"]
    assert [g["id"] for g in payload["groups"]] == [
        "pos-hot-coffee",
        "grubtech-hot-coffee",
    ]


@pytest.mark.asyncio
async def test_attaching_a_subgroup_sends_every_existing_one(monkeypatch):
    client = foodics_provider.FoodicsClient()
    calls: list = []

    async def fake_call(method, path, *, params=None, json_body=None, attempts=2):
        calls.append((method, json_body))
        if method == "GET":
            return {"data": {"subgroups": [{"id": "cakes"}, {"id": "cookies"}]}}
        return {}

    monkeypatch.setattr(client, "_call", fake_call)
    assert await client.ensure_grubtech_subgroup("hot-coffee") is True
    _, body = calls[-1]
    assert [s["id"] for s in body["payload"]["subgroups"]] == [
        "cakes",
        "cookies",
        "hot-coffee",
    ]


@pytest.mark.asyncio
async def test_an_empty_grubtech_read_never_writes_the_list_back(monkeypatch):
    """Writing back an empty read would detach every category from every
    marketplace; it must fail instead."""
    client = foodics_provider.FoodicsClient()
    writes: list = []

    async def fake_call(method, path, *, params=None, json_body=None, attempts=2):
        if method != "GET":
            writes.append(json_body)
        return {"data": {"subgroups": []}}

    monkeypatch.setattr(client, "_call", fake_call)
    with pytest.raises(foodics_provider.FoodicsError):
        await client.ensure_grubtech_subgroup("hot-coffee")
    assert writes == []


# ── sync_product end to end, collaborators faked ─────────────────────────────


class _SyncDb:
    """Answers sync_product's two reads (the product, the branches) and records
    commits; everything else it does goes through patched collaborators."""

    def __init__(self, product, branches):
        self._answers = [product, branches]
        self.commits = 0

    async def execute(self, _stmt):
        value = self._answers.pop(0)
        if isinstance(value, list):
            return SimpleNamespace(scalars=lambda: iter(value))
        return SimpleNamespace(scalar_one_or_none=lambda: value)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


@pytest.mark.asyncio
async def test_a_barsha_only_coffee_syncs_to_foodics_only(monkeypatch):
    product = SimpleNamespace(id=uuid4(), sku="FG0088", name="Cappuccino")
    monkeypatch.setattr(catalog_sync, "_ensure_write_enabled", lambda: None)
    monkeypatch.setattr(
        menu_group_service,
        "integrator_l1_group_for_product",
        AsyncMock(return_value=SimpleNamespace(reference="sub")),
    )
    monkeypatch.setattr(
        menu_group_service,
        "marketplace_scope_for_product",
        AsyncMock(return_value=frozenset({BARSHA})),
    )

    async def mapped(db, system, product_id):
        return system == "foodics"

    monkeypatch.setattr(catalog_sync, "_is_mapped", mapped)
    enrich = AsyncMock(return_value={"fields": {}})
    create = AsyncMock(side_effect=AssertionError("nothing may be created"))
    monkeypatch.setattr(catalog_sync, "enrich_existing_item", enrich)
    monkeypatch.setattr(catalog_sync, "create_menu_item", create)

    res = await catalog_sync.sync_product(
        _SyncDb(product, BRANCHES), product_id=product.id, dry_run=False
    )

    assert [c.kwargs["target"] for c in enrich.await_args_list] == ["foodics"]
    for system in ("careem", "noon", "talabat", "deliveroo"):
        assert "skipped" in res[system], system
    assert "skipped" in res["keeta"]


@pytest.mark.asyncio
async def test_an_unmapped_item_already_in_foodics_is_adopted_not_recreated(
    monkeypatch,
):
    product = SimpleNamespace(id=uuid4(), sku="FG0088", name="Cappuccino")
    monkeypatch.setattr(catalog_sync, "_ensure_write_enabled", lambda: None)
    monkeypatch.setattr(
        menu_group_service,
        "integrator_l1_group_for_product",
        AsyncMock(return_value=SimpleNamespace(reference="sub")),
    )
    monkeypatch.setattr(
        menu_group_service,
        "marketplace_scope_for_product",
        AsyncMock(return_value=frozenset({BARSHA})),
    )
    adopted: list = []

    async def adopt(db, *, product_id, foodics):
        adopted.append(foodics["id"])
        return foodics["id"]

    async def mapped(db, system, product_id):
        return bool(adopted) and system == "foodics"

    monkeypatch.setattr(catalog_sync, "_is_mapped", mapped)
    monkeypatch.setattr(catalog_sync, "_adopt_foodics_by_sku", adopt)
    monkeypatch.setattr(
        foodics_provider.provider,
        "find_product_by_sku",
        AsyncMock(return_value={"id": "foodics-cappuccino", "name": "Cappuccino"}),
    )
    enrich = AsyncMock(return_value={"fields": {}})
    monkeypatch.setattr(catalog_sync, "enrich_existing_item", enrich)
    monkeypatch.setattr(
        catalog_sync,
        "create_menu_item",
        AsyncMock(side_effect=AssertionError("must adopt, not create")),
    )

    res = await catalog_sync.sync_product(
        _SyncDb(product, BRANCHES), product_id=product.id, dry_run=False
    )
    assert adopted == ["foodics-cappuccino"]
    assert res["foodics_adopted"] == "foodics-cappuccino"
    assert enrich.await_args_list[0].kwargs["target"] == "foodics"
