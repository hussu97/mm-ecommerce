"""
`POST /orders/preview` and `POST /orders` price the same basket identically.

This is the one property the whole unification exists for. The checkout no
longer computes money — it renders what the preview returns — so a preview that
disagrees with the order by a fils is a customer who reads one total, presses a
button, and is charged another. Nothing else in the codebase notices: both
figures are internally consistent, both look right in isolation, and the only
place they meet is a card statement.

There used to be five derivations of these numbers (two backend engines and
three separate formulas on the checkout screen). There is one now —
`order_pricing.compute_order_totals` — and this file is what says so out loud,
on the hardest basket the shop can produce: a coupon, a zone-priced delivery
fee, and the small-basket surcharge, all at once.

The session here is a mock, which is the standing weakness of this suite: it
cannot catch a lazy load that would raise `MissingGreenlet` in production. The
same five comparisons were run once against a migrated throwaway Postgres with
the real zone map and the seeded `MM15` and `FREESHIP` coupons — 105 flat, 35
with the surcharge, and both again with each coupon — and every field agreed.
That is a manual check by nature (it needs a database), so it is recorded here
rather than pretended at.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.delivery_settings import DeliverySettings
from app.models.order import DeliveryMethodEnum
from app.schemas.address import AddressCreate
from app.schemas.order import OrderCreate
from app.schemas.order_preview import OrderPreviewRequest
from app.schemas.promo_code import PromoCodeValidateResponse
from app.services import lalamove_service, order_service
from app.services.delivery_zone_service import Zone

# ── The basket ────────────────────────────────────────────────────────────────
#
# Two cakes at AED 15, so the goods come to 30 — under the 35 threshold, which
# is what puts the small-basket surcharge on the order. A 6-dirham coupon on
# top, because the coupon is exactly what must *not* move that surcharge: it is
# judged on the gross basket, and a preview that judged it on the discounted one
# would quote 45 against an order of 59.

SETTINGS = DeliverySettings(
    pickup_fee=Decimal("0.00"),
    low_order_fee=Decimal("15.00"),
    low_order_threshold=Decimal("35.00"),
)

ZONE = Zone(
    id=uuid.uuid4(),
    name="Dubai Near",
    delivery_fee=Decimal("20.00"),
    fulfilment_provider="lalamove",
    min_lat=24.0,
    max_lat=26.0,
    min_lng=54.0,
    max_lng=57.0,
    rings=(),
    pricing_mode="static",
    free_delivery_eligible=True,
    # Well above this basket, so free delivery is not what is being tested here.
    free_delivery_threshold=Decimal("150.00"),
)

DISCOUNT = Decimal("6.00")
LATITUDE = Decimal("25.1972")
LONGITUDE = Decimal("55.2744")

#: subtotal 30, less 6 discount, plus 20 delivery, plus 15 small-basket fee.
EXPECTED_TOTAL = Decimal("59.00")
#: VAT on the goods alone: 24 inclusive of 5% is 22.86 + 1.14. Neither fee is in
#: it, which is why this is not 5/105 of the total above.
EXPECTED_VAT = Decimal("1.14")
EXPECTED_EXCL_VAT = Decimal("22.86")


def _product() -> MagicMock:
    p = MagicMock()
    p.id = uuid.uuid4()
    p.base_price = Decimal("15.00")
    p.is_active = True
    p.is_stock_product = False
    p.name = "Brookie Cookie Melt"
    p.sku = "BCM-1"
    p.translations = {}
    p.sales_channels = ["web"]
    p.category_id = None
    p.category = None
    p.personalisation_type = None
    return p


def _cart() -> MagicMock:
    item = MagicMock()
    item.product = _product()
    item.quantity = 2
    item.selected_options = []
    item.personalisation_note = None
    cart = MagicMock()
    cart.id = uuid.uuid4()
    cart.session_id = "sess_test"
    cart.items = [item]
    return cart


def _result(**kwargs):
    r = MagicMock()
    r.scalar_one_or_none = MagicMock(return_value=kwargs.get("scalar_one_or_none"))
    r.scalar_one = MagicMock(return_value=kwargs.get("scalar_one"))
    scalars = MagicMock()
    scalars.all.return_value = kwargs.get("scalars_all") or []
    # A stubbed row answers whichever accessor the code reaches for. These
    # helpers used to wire `scalar_one_or_none` alone, which quietly pinned the
    # tests to one SQLAlchemy call — so when `find_priceable_cart` stopped using
    # it (that call asserts a uniqueness `carts` does not have, and 500ed six
    # real customers) thirty-four tests failed for a reason that had nothing to
    # do with what any of them was asserting.
    scalars.first.return_value = kwargs.get(
        "scalars_first", kwargs.get("scalar_one_or_none")
    )
    scalars.unique.return_value.one_or_none.return_value = kwargs.get(
        "scalars_unique_one_or_none"
    )
    r.scalars.return_value = scalars
    return r


def _script(results):
    """Answer these queries in order, then keep answering with the last."""
    remaining = list(results)

    async def answer(*_args, **_kwargs):
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return answer


@pytest.fixture(autouse=True)
def priced_world():
    """
    A real pricing run with no database, no courier and no coupon table.

    The zone lookup, the settings row, the courier estimate and the coupon are
    stubbed. **The arithmetic is not** — the free-delivery test, the surcharge
    rule, the tax split and the rounding all run for real on both paths, which
    is the only way this file proves anything.
    """
    with (
        patch(
            "app.services.delivery_service.get_settings",
            new=AsyncMock(return_value=SETTINGS),
        ),
        patch(
            "app.services.delivery_service.delivery_zone_service.find_zone",
            new=AsyncMock(return_value=ZONE),
        ),
        patch(
            "app.services.delivery_service.courier_service.estimate_for_point",
            new=AsyncMock(
                return_value=(
                    lalamove_service.Estimate(
                        cost=Decimal("31.00"),
                        currency="AED",
                        distance_m=9000,
                        quotation_id="q_1",
                    ),
                    None,
                )
            ),
        ),
        # A static zone charges its published fee; the courier's number is only
        # recorded so the fee table can be argued with later. That recording is
        # a write on the cart and has its own tests.
        patch(
            "app.services.delivery_service.lalamove_service.record_cart_estimate",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.delivery_service.delivery_promise.promise_for_zone",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.order_service.delivery_promise.promise_for_zone",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.order_service.promo_code_service.validate",
            new=AsyncMock(
                return_value=PromoCodeValidateResponse(
                    valid=True,
                    discount_amount=DISCOUNT,
                    message="15% off your first order",
                )
            ),
        ),
        patch(
            "app.services.order_service.promo_code_service.get_promo",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    id=uuid.uuid4(), code="FIRST15", max_uses=None
                )
            ),
        ),
        patch(
            "app.services.order_service.resolve_branch",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(
                id=uuid.uuid4(), reference="K001", is_active=True, deleted_at=None
            ),
        ),
        patch(
            "app.services.order_service.pos_order_service.attach_online_order",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.order_service.lalamove_service.record_order_delivery",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.order_service.fulfilment_service.for_order",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        yield


async def _preview(cart):
    db = AsyncMock()
    db.refresh = AsyncMock()
    # Only the tax-group lookup reaches the session; everything else on this
    # path is stubbed above.
    db.execute = AsyncMock(side_effect=_script([_result()]))
    with patch(
        "app.services.order_service.find_priceable_cart",
        new=AsyncMock(return_value=cart),
    ):
        return await order_service.preview_order(
            db,
            OrderPreviewRequest(
                delivery_method=DeliveryMethodEnum.DELIVERY,
                latitude=LATITUDE,
                longitude=LONGITUDE,
                address="Al Barsha, Dubai",
                promo_code="FIRST15",
                session_id="sess_test",
                email="buyer@example.com",
                phone="+971501234567",
            ),
            user_id=None,
        )


async def _create(cart):
    db = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    db.begin_nested = MagicMock()
    db.execute = AsyncMock(
        side_effect=_script(
            [
                _result(scalar_one_or_none=cart),  # cart lookup
                _result(scalars_unique_one_or_none=None),  # tax group
                _result(scalar_one_or_none=None),  # order number sequence
                _result(scalars_all=[]),  # cart items to clear
                _result(scalar_one=MagicMock()),  # final reload
            ]
        )
    )
    with patch("app.services.order_service.to_response", new_callable=AsyncMock):
        await order_service.create_order(
            db,
            OrderCreate(
                email="buyer@example.com",
                delivery_method=DeliveryMethodEnum.DELIVERY,
                shipping_address=AddressCreate(
                    label="Home",
                    first_name="Aisha",
                    last_name="K",
                    phone="+971501234567",
                    address_line_1="Al Barsha, Dubai",
                    latitude=LATITUDE,
                    longitude=LONGITUDE,
                ),
                promo_code="FIRST15",
                payment_method="card",
                session_id="sess_test",
            ),
            user_id=None,
        )
    # The first thing added is the order row itself.
    return db.add.call_args_list[0][0][0]


async def test_preview_and_creation_agree_on_every_figure():
    """
    Coupon, zone-priced delivery and small-basket surcharge, on one basket.

    Field by field rather than on the total alone: two errors that cancel — a
    fee counted as delivery and a delivery counted as a fee — produce the same
    total and the wrong books, and freight margin is reconciled against
    `delivery_fee` specifically.
    """
    preview = await _preview(_cart())
    order = await _create(_cart())

    assert Decimal(str(preview.subtotal)) == order.subtotal
    assert Decimal(str(preview.discount_amount)) == order.discount_amount
    assert Decimal(str(preview.delivery_fee)) == order.delivery_fee
    assert Decimal(str(preview.low_order_fee)) == order.low_order_fee
    assert Decimal(str(preview.vat_amount)) == order.vat_amount
    assert Decimal(str(preview.total_excl_vat)) == order.total_excl_vat
    assert Decimal(str(preview.total)) == order.total
    assert Decimal(str(preview.vat_rate)) == order.vat_rate


async def test_the_figures_are_the_ones_the_shop_intends():
    """
    Agreeing on the wrong number is not the property we want. This pins what
    that number is, so a change to the rules fails here rather than passing the
    agreement test with two matching mistakes.
    """
    preview = await _preview(_cart())

    assert Decimal(str(preview.subtotal)) == Decimal("30.00")
    assert Decimal(str(preview.discount_amount)) == DISCOUNT
    assert Decimal(str(preview.delivery_fee)) == Decimal("20.00")
    # Judged on the gross 30, not on the discounted 24. Both are under the
    # threshold here; the case where it matters is pinned in
    # `test_low_order_fee.py`.
    assert Decimal(str(preview.low_order_fee)) == Decimal("15.00")
    assert Decimal(str(preview.total)) == EXPECTED_TOTAL
    # VAT is on the goods and nothing else, which is why this is not 5/105 of
    # the total — the line the checkout used to print from its own formula.
    assert Decimal(str(preview.vat_amount)) == EXPECTED_VAT
    assert Decimal(str(preview.total_excl_vat)) == EXPECTED_EXCL_VAT
    assert Decimal(str(preview.total_excl_vat)) + Decimal(
        str(preview.vat_amount)
    ) == Decimal("24.00"), "net and tax must add back to the discounted basket"


async def test_the_promo_the_preview_reports_is_the_one_it_priced():
    preview = await _preview(_cart())

    assert preview.promo is not None
    assert preview.promo.code == "FIRST15"
    assert preview.promo.valid
    assert Decimal(str(preview.promo.discount_amount)) == DISCOUNT


async def test_the_delivery_block_comes_from_the_same_pricing_call():
    """
    The fee on the totals and the fee in the delivery block are one number.

    They used to be two endpoints: the checkout asked `/delivery/quote` for the
    line item and worked the total out itself. Both reach a courier's live
    pricing API in the dynamic zones, so two calls meant two quotes for one
    basket — and a real chance of showing a fee that was never charged.
    """
    preview = await _preview(_cart())

    assert preview.delivery.serviceable
    assert preview.delivery.delivery_fee == preview.delivery_fee
    assert preview.delivery.zone_name == "Dubai"


async def test_an_unserviceable_pin_is_reported_rather_than_refused():
    """
    `create_order` raises for this pin; a preview must not. A checkout with no
    total on it cannot explain itself, and the refusal already lives on the pay
    button — next to the two things that resolve it, move the pin or collect.
    """
    with patch(
        "app.services.delivery_service.delivery_zone_service.find_zone",
        new=AsyncMock(return_value=None),
    ):
        preview = await _preview(_cart())

    assert preview.delivery.serviceable is False
    assert preview.delivery_fee is None, "no fee we are willing to name"
    # The goods and the surcharge are still real; only delivery is unknown.
    assert Decimal(str(preview.total)) == Decimal("39.00")
