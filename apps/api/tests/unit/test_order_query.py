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
    # The four dispatch couriers and the five marketplaces, plus counter.
    assert set(codes) == {
        "counter",
        "lalamove",
        "noon_send",
        "slider",
        "third_party",
        "talabat",
        "keeta",
        "noon_food",
        "deliveroo",
        "careem",
    }


def test_courier_code_for_counter_is_the_register():
    assert order_query.courier_code_for("cashier", None, None) == "counter"


def test_courier_code_for_aggregator_reads_the_channel_with_version_noise():
    # "Keeta 2.0" and "Noon Food" resolve through the catalog's aliases.
    assert order_query.courier_code_for("aggregator", "Keeta 2.0", None) == "keeta"
    assert order_query.courier_code_for("aggregator", "Noon Food", None) == "noon_food"
    assert order_query.courier_code_for("aggregator", "Talabat", None) == "talabat"


def test_courier_code_for_website_reads_the_dispatch_provider():
    assert order_query.courier_code_for("online", None, "lalamove") == "lalamove"
    # An online pickup with no dispatch provider has no carrier — counted nowhere.
    assert order_query.courier_code_for("online", None, None) is None
    # An unknown provider is not invented into a courier.
    assert order_query.courier_code_for("online", None, "bicycle") is None


def test_courier_label_names_the_counter():
    assert order_query.courier_label("counter") == "Counter"
    assert order_query.courier_label("noon_food") == "Noon Food"


def test_courier_clause_is_none_for_an_empty_selection():
    assert order_query.courier_clause(None) is None
    assert order_query.courier_clause([]) is None
    # A real selection builds a clause (an OR); it just has to exist.
    assert order_query.courier_clause(["counter", "talabat"]) is not None
