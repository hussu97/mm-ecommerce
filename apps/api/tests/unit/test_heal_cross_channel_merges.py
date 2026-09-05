"""Pure-logic tests for the cross-channel-merge cleanup script."""

from types import SimpleNamespace

from scripts import heal_cross_channel_merges as heal


def _order(**over):
    base = dict(
        aggregator_channel="Deliveroo",
        customer_name=None,
        customer_phone=None,
        customer_phone_country=None,
        customer_phone_type=None,
        customer_phone_access_code=None,
        shipping_address_snapshot=None,
        aggregator_driver_name=None,
        aggregator_driver_phone=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _agg(channel, **over):
    base = dict(
        channel=channel,
        customer_name=None,
        customer_phone=None,
        driver_name=None,
        driver_phone=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_is_masked():
    assert heal._is_masked("***")
    assert heal._is_masked({"address": "***"})
    assert not heal._is_masked("Aisha")
    assert not heal._is_masked("")
    assert not heal._is_masked(None)


def test_split_aggs_by_channel():
    """The agg whose channel's GrubTech spelling is the order's channel is legit;
    a different channel merged in by the old unscoped code is an intruder."""
    order = _order(aggregator_channel="Deliveroo")
    deliveroo = _agg("deliveroo")
    keeta = _agg("keeta")
    legit, intruder = heal._split_aggs(order, [deliveroo, keeta])
    assert legit == [deliveroo]
    assert intruder == [keeta]


def test_split_aggs_honours_channel_aliases():
    """Noon's order label is "Noon Food"; the noon agg must still read as legit."""
    order = _order(aggregator_channel="Noon Food")
    noon = _agg("noon")
    keeta = _agg("keeta")
    legit, intruder = heal._split_aggs(order, [noon, keeta])
    assert legit == [noon]
    assert intruder == [keeta]


def test_clear_suspect_contact_clears_masked_and_intruder_values():
    order = _order(
        customer_name="***",
        customer_phone="+971500000000",
        aggregator_driver_name="Keeta Rider",
    )
    intruder = _agg("keeta", customer_phone="+971500000000", driver_name="Keeta Rider")
    heal._clear_suspect_contact(order, [intruder])
    assert order.customer_name is None  # masked
    assert order.customer_phone is None  # equals the intruder's value
    assert order.aggregator_driver_name is None  # equals the intruder's driver


def test_clear_suspect_contact_keeps_the_orders_own_value():
    """A real value that is NOT the intruder's is left for the legit source to keep."""
    order = _order(customer_name="Deliveroo customer 42")
    intruder = _agg("keeta", customer_name="Someone Else")
    heal._clear_suspect_contact(order, [intruder])
    assert order.customer_name == "Deliveroo customer 42"
