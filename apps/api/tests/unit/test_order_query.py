"""The one definition of "by courier" the dashboard and the orders list share.

`order_query` maps a carrier code to the SQL that selects its orders and back
again, so a courier scorecard the operator clicks lands on the same rows. These
pin the code↔order mapping — the part that has no database in it.
"""

from __future__ import annotations

from app.services.orders import order_query


def test_all_codes_lead_with_counter():
    codes = order_query.ALL_COURIER_CODES
    assert codes[0] == "counter"
    # Store Pickup is the second synthetic, carrier-less column, right after the
    # register.
    assert codes[1] == "website_pickup"
    # The dispatch couriers — including Slider's two vehicle tiers (the legacy
    # bare `slider` was retired) — the five marketplaces, counter, and store
    # pickup.
    assert set(codes) == {
        "counter",
        "website_pickup",
        "lalamove",
        "noon_send",
        "slider_bike",
        "slider_car",
        "third_party",
        "talabat",
        "keeta",
        "noon_food",
        "deliveroo",
        "careem",
    }


def test_a_slider_tier_is_its_own_courier_code():
    """A `slider_bike`/`slider_car` delivery is grouped as itself, not dropped as
    carrier-less — the bug that silently excluded tier orders from the per-courier
    scorecard."""
    assert order_query.courier_code_for("online", None, "slider_bike") == "slider_bike"
    assert order_query.courier_code_for("online", None, "slider_car") == "slider_car"


def test_courier_code_for_counter_is_the_register():
    assert order_query.courier_code_for("cashier", None, None) == "counter"


def test_courier_code_for_aggregator_reads_the_channel_with_version_noise():
    # "Keeta 2.0" and "Noon Food" resolve through the catalog's aliases.
    assert order_query.courier_code_for("aggregator", "Keeta 2.0", None) == "keeta"
    assert order_query.courier_code_for("aggregator", "Noon Food", None) == "noon_food"
    assert order_query.courier_code_for("aggregator", "Talabat", None) == "talabat"


def test_courier_code_for_website_reads_the_dispatch_provider():
    assert order_query.courier_code_for("online", None, "lalamove") == "lalamove"
    # An online delivery order with no dispatch provider has no carrier — counted
    # nowhere.
    assert order_query.courier_code_for("online", None, None) is None
    # An unknown provider is not invented into a courier.
    assert order_query.courier_code_for("online", None, "bicycle") is None


def test_courier_code_for_online_pickup_is_store_pickup():
    """An online order collected in store is its own channel, resolved from the
    `pickup` delivery method rather than any dispatch courier."""
    assert (
        order_query.courier_code_for("online", None, None, delivery_method="pickup")
        == "website_pickup"
    )
    assert order_query.courier_label("website_pickup") == "Store Pickup"


def test_courier_label_names_the_counter():
    assert order_query.courier_label("counter") == "Counter"
    assert order_query.courier_label("noon_food") == "Noon Food"


def test_courier_clause_is_none_for_an_empty_selection():
    assert order_query.courier_clause(None) is None
    assert order_query.courier_clause([]) is None
    # A real selection builds a clause (an OR); it just has to exist.
    assert order_query.courier_clause(["counter", "talabat"]) is not None


# ── revenue must not fall out of the reports when a status stops being invented ─


def test_an_aggregator_sale_stands_once_the_parcel_leaves():
    """The auto-close used to assert `delivered` five minutes after packing, and
    the revenue reads leaned on that. With the claim dropped, an aggregator order
    sits at `out_for_delivery` until the channel's own status arrives — which for
    most channels is the next morning — so a read still keyed on `delivered` would
    have quietly emptied the day's aggregator revenue."""
    sql = str(
        order_query.fulfilled_clause().compile(compile_kwargs={"literal_binds": True})
    )
    assert "out_for_delivery" in sql
    assert "aggregator" in sql
    assert "delivered" in sql


def test_a_website_order_is_not_fulfilled_while_it_is_still_on_a_van():
    """Our own courier can still fail one, and `undelivered` is a real outcome
    there — nothing about that changed."""
    sql = str(
        order_query.fulfilled_clause().compile(compile_kwargs={"literal_binds": True})
    )
    # out_for_delivery only ever appears alongside the aggregator source.
    assert "online" not in sql


# ── the courier breakdown counts live + completed, not delivered-only ──────────


def test_active_or_fulfilled_excludes_only_the_terminal_set():
    """The per-courier breakdown counts every live or completed order and drops
    only the terminal ones (cancelled/payment_failed/refunded/disputed)."""
    assert order_query.TERMINAL_STATUSES == (
        "cancelled",
        "payment_failed",
        "refunded",
        "disputed",
    )
    sql = str(
        order_query.active_or_fulfilled_clause().compile(
            compile_kwargs={"literal_binds": True}
        )
    )
    # It is a NOT IN over exactly the terminal set.
    for terminal in order_query.TERMINAL_STATUSES:
        assert terminal in sql
    # A live/completed status is not named — it is included by exclusion, so a
    # delivered or still-in-flight order counts against its carrier.
    assert "delivered" not in sql
    assert "out_for_delivery" not in sql
