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

    stored = ["cakes", "cookies"]

    async def fake_call(method, path, *, params=None, json_body=None, attempts=2):
        calls.append((method, json_body))
        if method == "GET":
            return {"data": {"subgroups": [{"id": i} for i in stored]}}
        stored[:] = [s["id"] for s in json_body["payload"]["subgroups"]]
        return {}

    monkeypatch.setattr(client, "_call", fake_call)
    assert await client.ensure_grubtech_subgroup("hot-coffee") is True
    _, body = [c for c in calls if c[0] == "PUT"][-1]
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


@pytest.mark.asyncio
async def test_copying_modifiers_reloads_through_the_async_session(monkeypatch):
    """Regression (2026-11-05 prod run): the reload used `object_session()`, the
    sync Session under the AsyncSession, and executing on it raised
    MissingGreenlet for every product whose Foodics twin lacked an option."""
    option = SimpleNamespace(
        sku="FM0222",
        name="Medium",
        translations={},
        price=Decimal("18"),
        display_order=0,
        is_active=True,
    )
    loaded = SimpleNamespace(
        product_modifiers=[
            SimpleNamespace(
                display_order=0,
                minimum_options=1,
                maximum_options=1,
                free_options=0,
                unique_options=False,
                modifier=SimpleNamespace(
                    reference="mod_mat_lte_cf",
                    name="Size",
                    translations={},
                    options=[option],
                ),
            )
        ]
    )
    reload = AsyncMock(return_value=loaded)
    monkeypatch.setattr(catalog_sync, "_product_with_options", reload)
    copy = AsyncMock(return_value={"FM0222": "created"})
    monkeypatch.setattr(foodics_provider.provider, "copy_modifiers_to_product", copy)

    db = object()
    out = await catalog_sync._foodics_copy_modifiers(
        db, SimpleNamespace(id=uuid4()), "foodics-id"
    )
    assert out == {"FM0222": "created"}
    assert reload.await_args.args[0] is db
    [spec] = copy.await_args.args[1]
    assert spec["reference"] == "mod_mat_lte_cf"
    assert spec["options"][0]["sku"] == "FM0222"


@pytest.mark.asyncio
async def test_grubops_mapping_reads_every_location_not_just_the_first(monkeypatch):
    """A Barsha-only item is on Barsha's GrubOps menu and not on Sharjah's, so a
    Sharjah-first catalogue read would never map it. One location failing to
    read must not stop the others."""
    from app.services.grubops import grubops_mapping as gm
    from app.services.providers.grubops_provider import GrubOpsError

    sharjah = SimpleNamespace(grubops_location_id="loc-shj", grubops_partner_id="p")
    barsha = SimpleNamespace(grubops_location_id="loc-bar", grubops_partner_id="p")
    cappuccino = gm.Candidate(
        item_id="r-capp", name="Cappuccino", brand_id="b", grubops_type="RECIPE"
    )
    cake = gm.Candidate(
        item_id="r-cake", name="Tiramisu", brand_id="b", grubops_type="RECIPE"
    )

    async def fetch(location):
        if location is sharjah:
            return [cake], []
        return [cake, cappuccino], []  # the shared recipe must not double up

    monkeypatch.setattr(gm, "sync_locations", AsyncMock(return_value=(0, [])))
    monkeypatch.setattr(gm, "_fetch_catalogue", fetch)
    monkeypatch.setattr(gm, "_our_menu", AsyncMock(return_value=([], {})))
    answers = [[sharjah, barsha], []]
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=lambda _s: SimpleNamespace(
                scalars=lambda v=answers.pop(0): SimpleNamespace(all=lambda: v)
            )
        ),
        flush=AsyncMock(),
    )
    summary = await gm.sync_mappings(db)
    assert sorted(summary.unmatched_theirs) == ["Cappuccino", "Tiramisu"]

    async def failing(location):
        if location is sharjah:
            raise GrubOpsError("down")
        return [cappuccino], []

    monkeypatch.setattr(gm, "_fetch_catalogue", failing)
    answers[:] = [[sharjah, barsha], []]
    summary = await gm.sync_mappings(db)
    assert summary.unmatched_theirs == ["Cappuccino"]
    assert any("loc-shj" in e for e in summary.errors)


@pytest.mark.asyncio
async def test_copying_reuses_an_option_foodics_has_under_another_sku(monkeypatch):
    """Iced Spanish Latte's Large is FM0225 in MM and FM0220 in Foodics. The copy
    must attach the modifier and create nothing — not a second "Large"."""
    client = foodics_provider.FoodicsClient()

    async def list_all(resource, *, cap_pages=20):
        if resource == "/modifiers":
            return [{"id": "mod-1", "reference": "mod_ispn_lte_cf"}]
        return [{"sku": "FM0219"}, {"sku": "FM0220"}]

    calls: list = []

    async def fake_call(method, path, *, params=None, json_body=None, attempts=2):
        calls.append((method, (json_body or {}).get("url") or params.get("url")))
        if method == "GET":
            return {
                "data": {
                    "options": [
                        {"sku": "FM0219", "name": "Medium"},
                        {"sku": "FM0220", "name": "Large"},
                    ]
                }
            }
        return {"data": {}}

    monkeypatch.setattr(client, "_list_all", list_all)
    monkeypatch.setattr(client, "_call", fake_call)
    report = await client.copy_modifiers_to_product(
        "p1",
        [
            {
                "reference": "mod_ispn_lte_cf",
                "name": "Size",
                "name_localized": "مقاس",
                "minimum_options": 1,
                "maximum_options": 1,
                "free_options": 0,
                "unique_options": False,
                "index": 0,
                "options": [
                    {
                        "sku": "FM0219",
                        "name": "Medium",
                        "name_localized": "واسطة",
                        "price": 16,
                        "index": 0,
                    },
                    {
                        "sku": "FM0225",
                        "name": "Large",
                        "name_localized": "كبير",
                        "price": 18,
                        "index": 1,
                    },
                ],
            }
        ],
    )
    assert report == {}  # nothing created
    writes = [url for method, url in calls if method == "POST"]
    assert writes == ["/products/p1/modifiers/mod-1"]  # only the attach


@pytest.mark.asyncio
async def test_a_scoped_away_product_is_held_out_at_that_location(monkeypatch):
    """Foodics' "inactive at Sharjah" reaches GrubOps only as an out-of-stock
    that lapses next morning, so the reconcile itself holds a Barsha-only
    coffee out at Sharjah — the recipe out, its options left alone — while an
    ordinary item keeps its real availability."""
    from app.services.grubops import grubops_reconcile

    coffee, cake = uuid4(), uuid4()
    coffee_size = uuid4()

    def mapping(**kw):
        base = dict(
            id=uuid4(),
            scope="brand",
            external_ref="r",
            external_sub_ref=None,
            external_child_ref=None,
            external_type="RECIPE",
            product_id=None,
            modifier_option_id=None,
        )
        base.update(kw)
        return SimpleNamespace(**base)

    maps = [
        mapping(product_id=coffee, external_ref="r-coffee"),
        mapping(
            modifier_option_id=coffee_size,
            external_ref="r-coffee",
            external_sub_ref="m-size",
            external_type="MODIFIER",
        ),
        mapping(product_id=cake, external_ref="r-cake"),
    ]
    answers = [[], [], maps]  # out products, out options, approved mappings
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=lambda _s: SimpleNamespace(
                scalars=lambda v=answers.pop(0): SimpleNamespace(all=lambda: v)
            )
        )
    )
    monkeypatch.setattr(
        grubops_reconcile, "_return_times", AsyncMock(return_value=({}, {}))
    )
    monkeypatch.setattr(
        grubops_service,
        "scoped_away",
        AsyncMock(return_value=({coffee}, {coffee_size})),
    )

    desired = await grubops_reconcile.desired_state(db, SHARJAH)

    by_recipe = {(d.recipe_id, d.modifier_id): d for d in desired}
    assert by_recipe[("r-coffee", None)].available is False
    assert by_recipe[("r-coffee", None)].until is None  # until further notice
    assert ("r-coffee", "m-size") not in by_recipe
    assert by_recipe[("r-cake", None)].available is True


@pytest.mark.asyncio
async def test_a_subgroup_write_that_loses_one_is_restored(monkeypatch):
    """Regression (2026-11-05): "New In" fell out of the Grubtech group during
    the coffee rollout. A write that comes back missing any subgroup that was
    there before is immediately rewritten with the union."""
    client = foodics_provider.FoodicsClient()
    reads = [
        ["cakes", "new-in"],  # before
        ["cakes", "hot-coffee"],  # after the write: new-in lost
        ["cakes", "new-in", "hot-coffee"],  # after the restore
    ]
    writes: list = []

    async def fake_call(method, path, *, params=None, json_body=None, attempts=2):
        if method == "GET":
            return {"data": {"subgroups": [{"id": i} for i in reads.pop(0)]}}
        writes.append([s["id"] for s in json_body["payload"]["subgroups"]])
        return {}

    monkeypatch.setattr(client, "_call", fake_call)
    assert await client.ensure_grubtech_subgroup("hot-coffee") is True
    assert writes[0] == ["cakes", "new-in", "hot-coffee"]
    assert set(writes[1]) == {"cakes", "new-in", "hot-coffee"}


@pytest.mark.asyncio
async def test_a_subgroup_loss_that_cannot_be_restored_raises(monkeypatch):
    client = foodics_provider.FoodicsClient()
    reads = [["cakes", "new-in"], ["cakes", "hot-coffee"], ["cakes", "hot-coffee"]]

    async def fake_call(method, path, *, params=None, json_body=None, attempts=2):
        if method == "GET":
            return {"data": {"subgroups": [{"id": i} for i in reads.pop(0)]}}
        return {}

    monkeypatch.setattr(client, "_call", fake_call)
    with pytest.raises(foodics_provider.FoodicsError, match="new-in"):
        await client.ensure_grubtech_subgroup("hot-coffee")


@pytest.mark.asyncio
async def test_a_renamed_category_renames_its_foodics_subgroup_once(monkeypatch):
    client = foodics_provider.FoodicsClient()
    stored = {"name": "Cold Coffee", "name_localized": "قهوة باردة"}
    puts: list = []

    async def fake_call(method, path, *, params=None, json_body=None, attempts=2):
        if method == "GET":
            return {"data": dict(stored)}
        puts.append(json_body["payload"])
        stored.update(json_body["payload"])
        return {}

    monkeypatch.setattr(client, "_call", fake_call)
    assert await client.ensure_group_name("g", "Iced Coffee", "قهوة مثلجة") is True
    assert await client.ensure_group_name("g", "Iced Coffee", "قهوة مثلجة") is False
    assert puts == [{"name": "Iced Coffee", "name_localized": "قهوة مثلجة"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("foodics,expected", [(True, "M-ext"), (False, "M-mm")])
async def test_noon_reads_the_menu_that_serves_the_outlet(
    monkeypatch, foodics, expected
):
    """Barsha/Sharjah (Foodics outlets) are served by the GrubTech-fed
    "Ext. grubtech" menu; Karama/DSO by the MM-managed one. Reading the MM menu
    for Barsha hid the whole coffee publish (2026-11-05)."""
    from app.services.aggregators import menu_readers as mr
    from app.services.providers import noon_provider as np

    monkeypatch.setattr(mr, "_session_for", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        np.provider,
        "list_menus",
        AsyncMock(
            return_value={
                "data": [
                    {"menuName": "Melting Moments", "menuCode": "M-mm"},
                    {"menuName": "Ext. grubtech - 69232", "menuCode": "M-ext"},
                ]
            }
        ),
    )
    details = AsyncMock(return_value={"data": {}})
    monkeypatch.setattr(np.provider, "get_menu_details", details)
    monkeypatch.setattr(mr, "parse_noon_menu", lambda d: "parsed")
    db = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(has_foodics=foodics))
    )

    assert await mr._read_noon_menu(db, uuid4()) == "parsed"
    assert details.await_args.args[1] == expected
