"""Auto off-sale from produced-good stock — the pure halves.

The decision table, the recipe fan-out, the provenance rules the availability
writers apply, the sweep's respect for a staff override, and the owner's email.
DB-free; the end-to-end lifecycle against real Postgres lives in
`tests/integration/test_auto_availability.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.services import email_service
from app.services.catalog import availability_service as availability
from app.services.inventory import auto_availability_service as auto
from app.services.inventory.recipe_service import ActiveRecipeCatalog

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
CAKE = uuid.uuid4()  # a produced good
SPONGE = uuid.uuid4()  # another produced good


def _row(
    *,
    in_stock: bool = True,
    source: str | None = None,
    until: datetime | None = None,
    override: bool = False,
    auto_state: dict | None = None,
    is_active: bool = True,
):
    return SimpleNamespace(
        is_in_stock=in_stock,
        out_of_stock_until=until,
        unavailable_source=source,
        staff_override_until_restock=override,
        auto_state=auto_state,
        is_active=is_active,
    )


def _state(*item_ids: uuid.UUID) -> dict:
    return {"items": [{"item_id": str(i), "on_hand": "0"} for i in item_ids]}


def _decide(row, *, stock: dict, leaves=frozenset({CAKE}), sold: bool = True):
    return auto.decide(row, sold=sold, leaves=leaves, on_hand=stock, now=NOW)


# ─── The decision table ──────────────────────────────────────────────────────


class TestDecisionTable:
    def test_on_sale_with_a_depleted_produced_good_goes_off(self):
        decision = _decide(None, stock={CAKE: Decimal("0")})
        assert decision.action == "off"
        assert decision.reason == availability.REASON_STOCK_DEPLETED
        assert decision.triggers == (CAKE,)

    def test_negative_stock_counts_as_out(self):
        assert _decide(_row(), stock={CAKE: Decimal("-2")}).action == "off"

    def test_a_missing_level_is_zero(self):
        assert _decide(None, stock={}).action == "off"

    def test_stock_above_zero_changes_nothing(self):
        assert _decide(_row(), stock={CAKE: Decimal("0.5")}) is None

    def test_only_the_depleted_leaves_are_triggers(self):
        decision = _decide(
            None,
            stock={CAKE: Decimal("3"), SPONGE: Decimal("0")},
            leaves=frozenset({CAKE, SPONGE}),
        )
        assert decision.triggers == (SPONGE,)

    def test_auto_off_comes_back_when_stock_recovers(self):
        row = _row(in_stock=False, source="auto", auto_state=_state(CAKE))
        decision = _decide(row, stock={CAKE: Decimal("4")})
        assert decision.action == "on"
        assert decision.reason == availability.REASON_STOCK_RECOVERED
        assert decision.triggers == (CAKE,)

    def test_auto_off_comes_back_when_the_item_left_the_recipe(self):
        row = _row(in_stock=False, source="auto", auto_state=_state(CAKE))
        decision = _decide(row, stock={}, leaves=frozenset())
        assert decision.action == "on"
        assert decision.reason == availability.REASON_REMOVED_FROM_RECIPE

    def test_auto_off_stays_off_while_still_depleted(self):
        row = _row(in_stock=False, source="auto", auto_state=_state(CAKE))
        assert _decide(row, stock={CAKE: Decimal("0")}) is None

    @pytest.mark.parametrize("depleted", [True, False])
    def test_staff_off_is_never_touched(self, depleted):
        row = _row(in_stock=False, source="staff")
        stock = {CAKE: Decimal("0" if depleted else "9")}
        assert _decide(row, stock=stock) is None

    def test_an_off_row_from_before_provenance_is_staffs(self):
        # NULL source on an off row predates migration 283: a person did it.
        assert _decide(_row(in_stock=False), stock={CAKE: Decimal("9")}) is None

    def test_a_lapsed_staff_stockout_is_on_sale_and_can_go_auto_off(self):
        row = _row(in_stock=False, source="staff", until=NOW - timedelta(minutes=1))
        assert _decide(row, stock={CAKE: Decimal("0")}).action == "off"

    def test_override_skips_while_anything_is_still_out(self):
        row = _row(override=True)
        assert _decide(row, stock={CAKE: Decimal("0")}) is None

    def test_override_clears_once_every_trigger_is_back(self):
        row = _row(override=True)
        decision = _decide(row, stock={CAKE: Decimal("1")})
        assert decision.action == "clear_override"

    def test_a_product_not_listed_here_is_not_taken_off(self):
        row = _row(is_active=False)
        assert _decide(row, stock={CAKE: Decimal("0")}) is None

    def test_an_unexpandable_recipe_is_left_alone(self):
        row = _row(in_stock=False, source="auto", auto_state=_state(CAKE))
        assert _decide(row, stock={}, leaves=None) is None

    def test_an_owner_the_branch_no_longer_sells_is_released_not_taken_off(self):
        assert _decide(None, stock={}, sold=False) is None
        row = _row(in_stock=False, source="auto", auto_state=_state(CAKE))
        decision = _decide(row, stock={}, sold=False)
        assert decision.action == "on"
        assert decision.reason == availability.REASON_REMOVED_FROM_RECIPE


# ─── Recipe fan-out ──────────────────────────────────────────────────────────


def _item(kind: str, *, phantom: bool = False):
    return SimpleNamespace(
        id=uuid.uuid4(),
        kind=kind,
        tracking_mode="phantom" if phantom else "stocked",
    )


def _line(item, quantity="1"):
    return SimpleNamespace(
        item_id=item.id,
        quantity=Decimal(quantity),
        yield_percentage=Decimal("1"),
        display_order=0,
        inactive_in_order_types=[],
    )


def _version(*items):
    return SimpleNamespace(
        id=uuid.uuid4(),
        basis="unit",
        batch_yield=None,
        lines=[_line(i) for i in items],
    )


class TestLeavesByOwner:
    def test_only_produced_goods_are_leaves(self):
        cake = _item("produced_good")
        flour = _item("raw_material")
        box = _item("packaging")
        product = uuid.uuid4()
        catalog = ActiveRecipeCatalog(
            versions={("product", product): _version(cake, flour, box)},
            items={i.id: i for i in (cake, flour, box)},
        )
        assert auto.leaves_by_owner(catalog) == {("product", product): {cake.id}}

    def test_a_product_with_only_packaging_has_no_leaves(self):
        box = _item("packaging")
        product = uuid.uuid4()
        catalog = ActiveRecipeCatalog(
            versions={("product", product): _version(box)}, items={box.id: box}
        )
        assert auto.leaves_by_owner(catalog)[("product", product)] == frozenset()

    def test_a_produced_good_reached_through_a_phantom_counts(self):
        cake = _item("produced_good")
        kit = _item("semi_finished", phantom=True)
        option = uuid.uuid4()
        catalog = ActiveRecipeCatalog(
            versions={
                ("modifier_option", option): _version(kit),
                ("inventory_item", kit.id): _version(cake),
            },
            items={i.id: i for i in (cake, kit)},
        )
        leaves = auto.leaves_by_owner(catalog)
        assert leaves == {("modifier_option", option): {cake.id}}

    def test_a_phantom_with_no_recipe_is_unknown_not_empty(self):
        kit = _item("semi_finished", phantom=True)
        product = uuid.uuid4()
        catalog = ActiveRecipeCatalog(
            versions={("product", product): _version(kit)}, items={kit.id: kit}
        )
        assert auto.leaves_by_owner(catalog)[("product", product)] is None

    def test_one_produced_good_fans_out_to_every_owner_that_draws_it(self):
        """A shared filling (one modifier on several products) is one option —
        and a produced good in both a product and an option reaches both."""
        cake = _item("produced_good")
        product, option_a, option_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        catalog = ActiveRecipeCatalog(
            versions={
                ("product", product): _version(cake),
                ("modifier_option", option_a): _version(cake),
                ("modifier_option", option_b): _version(_item("raw_material")),
            },
            items={cake.id: cake},
        )
        catalog.items.update(
            {
                line.item_id: SimpleNamespace(
                    id=line.item_id, kind="raw_material", tracking_mode="stocked"
                )
                for line in catalog.versions[("modifier_option", option_b)].lines
            }
        )
        leaves = auto.leaves_by_owner(catalog)
        drawing = {owner for owner, items in leaves.items() if cake.id in items}
        assert drawing == {("product", product), ("modifier_option", option_a)}


# ─── Provenance, as the writers apply it ────────────────────────────────────


class TestProvenance:
    def _row(self, **kwargs):
        base = {
            "is_in_stock": True,
            "out_of_stock_until": None,
            "unavailable_source": None,
            "auto_state": None,
            "staff_override_until_restock": False,
        }
        base.update(kwargs)
        return SimpleNamespace(**base)

    def test_auto_off_is_indefinite_and_marked_auto(self):
        row = self._row()
        availability._apply(
            row, in_stock=False, until=None, source="auto", auto_state={"x": 1}
        )
        assert row.is_in_stock is False
        assert row.out_of_stock_until is None
        assert row.unavailable_source == "auto"
        assert row.auto_state == {"x": 1}

    def test_auto_writes_never_set_a_clock(self):
        row = self._row(
            is_in_stock=False,
            unavailable_source="staff",
            out_of_stock_until=NOW - timedelta(hours=1),
        )
        availability._apply(
            row, in_stock=False, until=NOW, source="auto", auto_state={}
        )
        assert row.out_of_stock_until is None

    def test_staff_putting_back_an_auto_row_wins_until_restock(self):
        row = self._row(is_in_stock=False, unavailable_source="auto", auto_state={})
        availability._apply(
            row, in_stock=True, until=None, source="staff", auto_state=None
        )
        assert row.is_in_stock is True
        assert row.unavailable_source is None
        assert row.auto_state is None
        assert row.staff_override_until_restock is True

    def test_staff_marking_out_again_clears_the_override(self):
        row = self._row(
            is_in_stock=True,
            unavailable_source=None,
            staff_override_until_restock=True,
        )
        availability._apply(
            row, in_stock=False, until=NOW, source="staff", auto_state=None
        )
        assert row.unavailable_source == "staff"
        assert row.staff_override_until_restock is False

    def test_staff_putting_back_a_staff_row_sets_no_override(self):
        row = self._row(is_in_stock=False, unavailable_source="staff")
        availability._apply(
            row, in_stock=True, until=None, source="staff", auto_state=None
        )
        assert row.staff_override_until_restock is False

    def test_staff_marking_out_clears_auto_state(self):
        row = self._row(
            is_in_stock=False, unavailable_source="auto", auto_state={"a": 1}
        )
        availability._apply(
            row, in_stock=False, until=NOW, source="staff", auto_state=None
        )
        assert row.unavailable_source == "staff"
        assert row.auto_state is None
        assert row.out_of_stock_until == NOW

    def test_an_unknown_source_is_refused(self):
        with pytest.raises(ValueError):
            availability._apply(
                self._row(), in_stock=False, until=None, source="robot", auto_state=None
            )

    def test_the_badge_source_ignores_lapsed_rows(self):
        lapsed = self._row(
            is_in_stock=False,
            unavailable_source="staff",
            out_of_stock_until=NOW - timedelta(seconds=1),
        )
        assert availability.effective_unavailable_source(lapsed, NOW) is None
        auto_row = self._row(is_in_stock=False, unavailable_source="auto")
        assert availability.effective_unavailable_source(auto_row, NOW) == "auto"
        assert availability.effective_unavailable_source(None, NOW) is None


class _RecordingDb:
    def __init__(self):
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(rowcount=0)

    async def flush(self):
        return None


async def test_the_sweep_never_deletes_a_row_carrying_the_override():
    db = _RecordingDb()
    await availability.sweep(db)
    assert len(db.statements) == 2
    for statement in db.statements:
        sql = str(statement.compile(dialect=postgresql.dialect()))
        assert "staff_override_until_restock IS false" in sql


# ─── Audit: every writer, every caller ──────────────────────────────────────


class _WriterDb:
    """Enough session for one writer call: no existing row, a product name."""

    def __init__(self):
        self.added = []

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    async def scalar(self, _statement):
        return "Pistachio Kunafa"

    def add(self, row):
        if getattr(row, "id", None) is None:
            row.id = uuid.uuid4()
        self.added.append(row)

    async def flush(self):
        return None


async def test_a_system_write_is_audited_as_the_system(monkeypatch):
    logged = []

    async def log_actor_action(_db, **kwargs):
        logged.append(kwargs)

    monkeypatch.setattr(
        availability.audit_service, "log_actor_action", log_actor_action
    )
    branch = SimpleNamespace(id=uuid.uuid4(), reference="B001", name="Barsha")
    await availability.set_product_stock(
        _WriterDb(),
        branch=branch,
        product_id=uuid.uuid4(),
        in_stock=False,
        actor=availability.SYSTEM_ACTOR,
        source="auto",
        reason=availability.REASON_STOCK_DEPLETED,
        auto_state={"items": []},
    )
    (entry,) = logged
    assert entry["actor_id"] == uuid.UUID(int=0)
    assert entry["actor_email"] == "system:auto-availability"
    assert entry["entity_type"] == "branch_product"
    assert entry["entity_label"] == "Pistachio Kunafa @ B001"
    assert entry["changes"]["reason"] == "stock_depleted"
    assert entry["changes"]["source"] == "auto"
    assert entry["changes"]["before"]["is_in_stock"] is True
    assert entry["changes"]["after"]["unavailable_source"] == "auto"


async def test_a_staff_write_is_audited_as_the_user(monkeypatch):
    logged = []

    async def log_actor_action(_db, **kwargs):
        logged.append(kwargs)

    monkeypatch.setattr(
        availability.audit_service, "log_actor_action", log_actor_action
    )
    user = SimpleNamespace(id=uuid.uuid4(), email="cashier@example.com")
    branch = SimpleNamespace(id=uuid.uuid4(), reference="B001", name="Barsha")
    await availability.set_option_stock(
        _WriterDb(),
        branch=branch,
        option_id=uuid.uuid4(),
        in_stock=True,
        actor=user,
    )
    (entry,) = logged
    assert entry["actor_id"] == user.id
    assert entry["actor_email"] == "cashier@example.com"
    assert entry["entity_type"] == "branch_modifier_option"
    assert entry["changes"]["reason"] == "staff"


async def test_the_pos_route_passes_the_signed_in_user_to_the_writer(monkeypatch):
    """The terminal's writes used to go unaudited. The route now hands the
    cashier to the writer, which audits."""
    from app.api.v1 import availability as pos_api

    branch = SimpleNamespace(id=uuid.uuid4(), reference="B001", name="Barsha")
    product = SimpleNamespace(id=uuid.uuid4(), name="Kunafa", is_active=True)
    seen = {}

    async def set_product_stock(_db, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(out_of_stock_until=None)

    async def branch_of(_db, _device):
        return branch

    async def one(_db, _device, _pid):
        return "ok"

    async def retire():
        return None

    class _Db:
        async def get(self, _model, _pk):
            return product

    monkeypatch.setattr(
        pos_api.availability_service, "set_product_stock", set_product_stock
    )
    monkeypatch.setattr(pos_api, "_branch_of", branch_of)
    monkeypatch.setattr(pos_api, "_one", one)
    monkeypatch.setattr(pos_api.catalogue_cache, "retire", retire)
    monkeypatch.setattr(
        pos_api.grubops_service, "push_change_in_background", lambda **_: None
    )
    user = SimpleNamespace(id=uuid.uuid4(), email="cashier@example.com")
    await pos_api.set_product_availability(
        product_id=product.id,
        data=pos_api.SetStockRequest(in_stock=False),
        request=SimpleNamespace(),
        db=_Db(),
        device=SimpleNamespace(branch_id=branch.id),
        user=user,
    )
    assert seen["actor"] is user
    assert seen["entity_label"] == "Kunafa @ B001"


# ─── The owner's email ──────────────────────────────────────────────────────


def _stub_send(monkeypatch):
    sent = []

    def fake_send(to, subject, html, attachments=None):
        sent.append({"to": to, "subject": subject, "html": html})
        return {"status": "sent", "resend_id": "stub", "error": None}

    async def fake_log(*_args, **_kwargs):
        return None

    monkeypatch.setattr(email_service, "_send", fake_send)
    monkeypatch.setattr(email_service, "_log", fake_log)
    return sent


async def test_no_changes_no_email(monkeypatch):
    sent = _stub_send(monkeypatch)
    await email_service.send_auto_availability_change(
        branch_name="Al Barsha", branch_reference="B001", changes=[]
    )
    assert sent == []


async def test_one_email_carries_every_change_with_its_trigger(monkeypatch):
    sent = _stub_send(monkeypatch)
    movement = {
        "transaction_id": str(uuid.uuid4()),
        "type": "consumption_from_orders",
        "reference": "CFO-000123",
        "quantity": Decimal("-1"),
        "at": NOW,
        "actor": "Aisha",
        "order_number": "POS-B001-2026-09-23-0042",
        "purchase_order_id": None,
    }
    await email_service.send_auto_availability_change(
        branch_name="Al Barsha",
        branch_reference="B001",
        changes=[
            {
                "direction": "OFF",
                "kind": "Product",
                "name": "Pistachio Kunafa",
                "reason": "stock_depleted",
                "items": [
                    {
                        "name": "Kunafa Tray",
                        "on_hand": Decimal("0"),
                        "movement": movement,
                    }
                ],
            },
            {
                "direction": "ON",
                "kind": "Option",
                "name": "Lotus filling",
                "reason": "stock_recovered",
                "items": [
                    {"name": "Lotus Cream", "on_hand": Decimal("4"), "movement": None}
                ],
            },
        ],
    )
    assert len(sent) == len(email_service.AUTO_AVAILABILITY_RECIPIENTS) == 1
    (message,) = sent
    assert message["to"] == "h_abbasi97@hotmail.com"
    assert "Al Barsha" in message["subject"]
    assert "1 off sale" in message["subject"] and "1 back on sale" in message["subject"]
    html = message["html"]
    for fragment in (
        "Pistachio Kunafa",
        "Lotus filling",
        "Kunafa Tray",
        "Stock ≤ 0",
        "Stock recovered",
        "Consumption from orders",
        "POS-B001-2026-09-23-0042",
        "/orders/POS-B001-2026-09-23-0042",
        "-1.00",
        "Aisha",
        "No movement at this branch",
    ):
        assert fragment in html, fragment


def test_a_movement_links_to_what_caused_it():
    txn = str(uuid.uuid4())
    po = str(uuid.uuid4())
    base = {"transaction_id": txn, "type": "purchasing", "reference": "PUR-1"}
    assert email_service._movement_row({**base, "purchase_order_id": po})[
        "url"
    ].endswith(f"/purchase-orders/{po}")
    assert email_service._movement_row(base)["url"].endswith(
        f"/inventory/transactions/{txn}"
    )


async def test_publish_sends_one_email_per_branch_and_pushes_grubops(monkeypatch):
    emails, pushes = [], []

    async def send(**kwargs):
        emails.append(kwargs)

    async def retire():
        return None

    from app.services.catalog import catalogue_cache
    from app.services.grubops import grubops_service

    monkeypatch.setattr(email_service, "send_auto_availability_change", send)
    monkeypatch.setattr(catalogue_cache, "retire", retire)
    monkeypatch.setattr(
        grubops_service, "push_change_in_background", lambda **kw: pushes.append(kw)
    )

    def change(kind, in_stock):
        return auto.Change(
            owner_kind=kind,
            owner_id=uuid.uuid4(),
            name="x",
            in_stock=in_stock,
            reason="stock_depleted",
            trigger_item_ids=(),
        )

    barsha = auto.BranchReport(
        branch_id=uuid.uuid4(),
        branch_name="Al Barsha",
        branch_reference="B001",
        changes=[change("product", False), change("modifier_option", False)],
        email_rows=[{"direction": "OFF"}, {"direction": "OFF"}],
    )
    quiet = auto.BranchReport(
        branch_id=uuid.uuid4(), branch_name="Quiet", branch_reference="Q"
    )
    await auto.publish([barsha, quiet])

    assert [e["branch_name"] for e in emails] == ["Al Barsha"]
    assert len(emails[0]["changes"]) == 2
    off = next(p for p in pushes if p["in_stock"] is False)
    assert len(off["product_ids"]) == 1 and len(off["option_ids"]) == 1
