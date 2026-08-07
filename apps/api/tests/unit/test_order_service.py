from __future__ import annotations

import datetime
import uuid
from types import SimpleNamespace
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.delivery_settings import DeliverySettings
from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from app.schemas.address import AddressCreate
from app.schemas.order import OrderCreate
from app.schemas.promo_code import PromoCodeValidateResponse
from app.services import lalamove_service
from app.services.delivery_zone_service import Zone
from app.services.fulfilment_service import Fulfilment
from app.services.order_service import VALID_TRANSITIONS, create_order, update_status


DELIVERY_SETTINGS = DeliverySettings(
    free_delivery_threshold=Decimal("150.00"),
    pickup_fee=Decimal("0.00"),
    default_delivery_fee=Decimal("50.00"),
)


def _zone(fee: str, pricing_mode: str = "static", *, free: bool = True) -> Zone:
    return Zone(
        id=uuid.uuid4(),
        name="Test Zone",
        delivery_fee=Decimal(fee),
        fulfilment_provider="lalamove",
        min_lat=24.0,
        max_lat=26.0,
        min_lng=54.0,
        max_lng=57.0,
        rings=(),
        pricing_mode=pricing_mode,
        free_delivery_eligible=free,
    )


@pytest.fixture(autouse=True)
def delivery_pricing():
    """
    Price delivery without a database or a courier.

    The settings row, the zone lookup and the courier are patched — the pricing
    itself is not. Free delivery, the rounding and the refusal to price an
    unquotable pin all still run for real, which is the half of `create_order`
    these tests are actually about.

    Yields `(find_zone, estimate_for_point)` so a test can put a zone under the
    pin, or take the courier's answer away.
    """
    find_zone = AsyncMock(return_value=None)
    # A quotable pin by default: outside every drawn zone the fee *is* the
    # courier's number, so a courier that answers nothing is an unserviceable
    # address rather than a neutral starting point.
    estimate_for_point = AsyncMock(
        return_value=(
            lalamove_service.Estimate(
                cost=Decimal("40.00"),
                currency="AED",
                distance_m=18000,
                quotation_id="q_default",
            ),
            None,
        )
    )
    with (
        patch(
            "app.services.delivery_service.get_settings",
            new=AsyncMock(return_value=DELIVERY_SETTINGS),
        ),
        patch(
            "app.services.delivery_service.delivery_zone_service.find_zone",
            new=find_zone,
        ),
        patch(
            "app.services.delivery_service.lalamove_service.estimate_for_point",
            new=estimate_for_point,
        ),
        patch(
            "app.services.delivery_service.lalamove_service.is_enabled",
            return_value=True,
        ),
    ):
        yield find_zone, estimate_for_point


@pytest.fixture(autouse=True)
def mock_fulfilment():
    """
    Silence everything that talks to the zone map or a courier.

    These tests drive `create_order` and `update_status` against a mock session
    whose `execute` is a fixed script of results, so any extra query — resolving
    the zone, opening the delivery row, looking up a booking to cancel — runs
    the script off its end. What they are checking is arithmetic and status
    rules; dispatch has its own tests.
    """
    branch = SimpleNamespace(
        id=uuid.uuid4(),
        reference="K001",
        name="Melting Moments Cakes",
        is_active=True,
        deleted_at=None,
    )
    with (
        # Every order now names the kitchen making it, and the column is NOT
        # NULL, so this has to answer even for tests that are only about
        # arithmetic. Resolving it for real would mean three more queries on a
        # session whose results are a fixed script.
        patch(
            "app.services.order_service.resolve_branch",
            new_callable=AsyncMock,
            return_value=branch,
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
            "app.services.order_service.lalamove_service.get_delivery",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.services.order_service.lalamove_service.cancel_delivery",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.order_service.batching_service.assign_or_dispatch",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.order_service.batching_service.cancel_assignment",
            new_callable=AsyncMock,
        ),
        # Every customer-facing response now carries "when does this arrive",
        # which means a query for the delivery record and, for a collection
        # order, one for the branch. Both would run off the end of the scripted
        # session. `fulfilment_service` has its own tests.
        patch(
            "app.services.order_service.fulfilment_service.for_order",
            new_callable=AsyncMock,
            return_value=Fulfilment(
                method="delivery",
                stage="preparing",
                estimated_at=None,
                precision=None,
                tracking_url=None,
                tracking_by_sms=False,
                courier_managed=False,
                packed_at=None,
                picked_up_at=None,
                delivered_at=None,
                branch=None,
            ),
        ),
    ):
        yield


# ── Test helpers ─────────────────────────────────────────────────────────────


def _product(
    base_price: str = "100.00",
    is_active: bool = True,
    is_stock_product: bool = False,
    stock_quantity: int = 0,
) -> MagicMock:
    p = MagicMock()
    p.id = uuid.uuid4()
    p.base_price = Decimal(base_price)
    p.is_active = is_active
    p.is_stock_product = is_stock_product
    p.stock_quantity = stock_quantity
    p.name = "Test Cake"
    p.sku = "CAKE-001"
    p.translations = {}
    p.sales_channels = ["web"]
    p.category_id = None
    p.category = None
    return p


def _cart_item(
    product: MagicMock, quantity: int = 1, selected_options: list | None = None
) -> MagicMock:
    ci = MagicMock()
    ci.product = product
    ci.quantity = quantity
    ci.selected_options = selected_options or []
    return ci


def _cart(items: list | None = None, session_id: str = "sess_test") -> MagicMock:
    c = MagicMock()
    c.id = uuid.uuid4()
    c.session_id = session_id
    c.items = items if items is not None else []
    return c


def _order_mock(
    order_number: str = "MM-20260101-001",
    delivery_method: DeliveryMethodEnum = DeliveryMethodEnum.PICKUP,
    status: OrderStatusEnum = OrderStatusEnum.CREATED,
    subtotal: Decimal = Decimal("100.00"),
    discount: Decimal = Decimal("0.00"),
    delivery_fee: Decimal = Decimal("0.00"),
    total: Decimal = Decimal("100.00"),
    vat_amount: Decimal = Decimal("4.76"),
    total_excl_vat: Decimal = Decimal("95.24"),
) -> MagicMock:
    now = datetime.datetime.now(datetime.timezone.utc)
    o = MagicMock()
    o.id = uuid.uuid4()
    o.order_number = order_number
    o.user_id = None
    o.email = "test@example.com"
    o.delivery_method = delivery_method
    o.delivery_fee = delivery_fee
    o.subtotal = subtotal
    o.discount_amount = discount
    o.total = total
    o.status = status
    o.promo_code_used = None
    o.shipping_address_snapshot = None
    o.payment_method = "stripe"
    o.payment_provider = None
    o.payment_id = None
    o.vat_rate = Decimal("0.0500")
    o.vat_amount = vat_amount
    o.total_excl_vat = total_excl_vat
    o.notes = None
    o.admin_notes = None
    o.created_at = now
    o.updated_at = now
    o.items = []
    # Response-only fields, set explicitly rather than left to the mock.
    # Pydantic coerces a bare MagicMock to `True` for a bool, so leaving
    # `email_has_account` unset quietly asserted the opposite of the default;
    # `fulfilment` is a typed model and fails outright, which is the honest
    # version of the same problem.
    o.email_has_account = False
    o.fulfilment = None
    o.source = "online"
    o.locale = "en"
    return o


def _result(
    scalar_one_or_none=None, scalar_one=None, scalars_all: list | None = None
) -> MagicMock:
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar_one_or_none
    r.scalar_one.return_value = scalar_one
    scalars = MagicMock()
    scalars.all.return_value = scalars_all or []
    r.scalars.return_value = scalars
    return r


def _db_for_create(
    cart,
    final_order,
    *,
    cart_items: list | None = None,
    last_order_seq: int | None = None,
    extra_results: list | None = None,
) -> AsyncMock:
    """
    Mock DB wired for create_order (no promo — patch promo service separately).
    Execute call order:
      1. cart lookup
      2. [extra_results — e.g. one stock decrement per stock-tracked product]
      3. _generate_order_number (numeric max of today's sequence)
      4. select CartItems for deletion
      5. final Order reload
    """
    db = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    db.begin_nested = MagicMock()
    db.execute = AsyncMock(
        side_effect=_sequenced(
            [
                _result(scalar_one_or_none=cart),
                *(extra_results or []),
                _result(scalar_one_or_none=last_order_seq),
                _result(scalars_all=cart_items or []),
                _result(scalar_one=final_order),
            ]
        )
    )
    return db


def _sequenced(results):
    """
    Answer these queries in order, and keep answering with the last one.

    A bare `side_effect` list asserts the exact number of queries the service
    makes, which is not what any of these tests are about: adding one lookup
    anywhere in `create_order` failed twenty-three of them at once with a
    `StopAsyncIteration` that named nothing and pointed at mock internals.

    The last entry is always the final reload, so holding it rather than
    exhausting the list means a new query in the middle shifts nothing — the
    reload still gets its order. Order still matters for everything before it.
    """
    remaining = list(results)

    async def answer(*_args, **_kwargs):
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return answer


def _db_for_update(order, updated_order=None) -> AsyncMock:
    """
    Mock DB wired for update_status.
    Execute call order: 1. Order lookup  2. final Order reload
    """
    db = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock(
        side_effect=_sequenced(
            [
                _result(scalar_one_or_none=order),
                _result(scalar_one=updated_order or order),
            ]
        )
    )
    return db


def _delivery_address() -> AddressCreate:
    return AddressCreate(
        first_name="Test",
        last_name="User",
        phone="+971500000000",
        address_line_1="123 Test St",
        latitude=Decimal("25.2048"),
        longitude=Decimal("55.2708"),
    )


def _pickup_data(promo_code: str | None = None) -> OrderCreate:
    return OrderCreate(
        email="test@example.com",
        delivery_method=DeliveryMethodEnum.PICKUP,
        payment_method="stripe",
        session_id="sess_test",
        promo_code=promo_code,
    )


def _delivery_data(promo_code: str | None = None) -> OrderCreate:
    return OrderCreate(
        email="test@example.com",
        delivery_method=DeliveryMethodEnum.DELIVERY,
        payment_method="stripe",
        session_id="sess_test",
        shipping_address=_delivery_address(),
        promo_code=promo_code,
    )


# ── Status transition logic ───────────────────────────────────────────────────


class TestStatusTransitionLogic:
    """Pure checks on VALID_TRANSITIONS — no I/O."""

    def test_created_can_go_to_confirmed(self):
        assert OrderStatusEnum.CONFIRMED in VALID_TRANSITIONS[OrderStatusEnum.CREATED]

    def test_created_can_go_to_cancelled(self):
        assert OrderStatusEnum.CANCELLED in VALID_TRANSITIONS[OrderStatusEnum.CREATED]

    def test_created_cannot_skip_to_packed(self):
        assert OrderStatusEnum.PACKED not in VALID_TRANSITIONS[OrderStatusEnum.CREATED]

    def test_confirmed_can_go_to_packed(self):
        assert OrderStatusEnum.PACKED in VALID_TRANSITIONS[OrderStatusEnum.CONFIRMED]

    def test_confirmed_can_go_to_cancelled(self):
        assert OrderStatusEnum.CANCELLED in VALID_TRANSITIONS[OrderStatusEnum.CONFIRMED]

    def test_confirmed_cannot_revert_to_created(self):
        assert (
            OrderStatusEnum.CREATED not in VALID_TRANSITIONS[OrderStatusEnum.CONFIRMED]
        )

    def test_packed_can_be_refunded_or_disputed(self):
        # Packed is no longer strictly terminal — a packed order can still be
        # refunded (e.g. customer refuses delivery) or disputed (chargeback)
        assert OrderStatusEnum.REFUNDED in VALID_TRANSITIONS[OrderStatusEnum.PACKED]
        assert OrderStatusEnum.DISPUTED in VALID_TRANSITIONS[OrderStatusEnum.PACKED]

    def test_packed_cannot_go_back(self):
        packed_allowed = VALID_TRANSITIONS[OrderStatusEnum.PACKED]
        for status in (
            OrderStatusEnum.CREATED,
            OrderStatusEnum.CONFIRMED,
            OrderStatusEnum.CANCELLED,
            OrderStatusEnum.PAYMENT_FAILED,
        ):
            assert status not in packed_allowed

    def test_cancelled_is_terminal(self):
        assert VALID_TRANSITIONS[OrderStatusEnum.CANCELLED] == set()

    def test_refunded_is_terminal(self):
        assert VALID_TRANSITIONS[OrderStatusEnum.REFUNDED] == set()

    def test_disputed_is_terminal(self):
        assert VALID_TRANSITIONS[OrderStatusEnum.DISPUTED] == set()

    def test_all_statuses_have_an_entry(self):
        for status in OrderStatusEnum:
            assert status in VALID_TRANSITIONS


# ── update_status ─────────────────────────────────────────────────────────────


class TestUpdateStatus:
    async def test_created_to_confirmed_succeeds(self):
        order = _order_mock(status=OrderStatusEnum.CREATED)
        updated = _order_mock(status=OrderStatusEnum.CONFIRMED)
        db = _db_for_update(order, updated)

        result = await update_status(db, order.order_number, OrderStatusEnum.CONFIRMED)

        assert result.status == OrderStatusEnum.CONFIRMED

    async def test_confirmed_to_packed_succeeds(self):
        order = _order_mock(status=OrderStatusEnum.CONFIRMED)
        updated = _order_mock(status=OrderStatusEnum.PACKED)
        db = _db_for_update(order, updated)

        result = await update_status(db, order.order_number, OrderStatusEnum.PACKED)

        assert result.status == OrderStatusEnum.PACKED

    async def test_created_to_cancelled_succeeds(self):
        order = _order_mock(status=OrderStatusEnum.CREATED)
        updated = _order_mock(status=OrderStatusEnum.CANCELLED)
        db = _db_for_update(order, updated)

        result = await update_status(db, order.order_number, OrderStatusEnum.CANCELLED)

        assert result.status == OrderStatusEnum.CANCELLED

    async def test_confirmed_to_cancelled_succeeds(self):
        order = _order_mock(status=OrderStatusEnum.CONFIRMED)
        updated = _order_mock(status=OrderStatusEnum.CANCELLED)
        db = _db_for_update(order, updated)

        result = await update_status(db, order.order_number, OrderStatusEnum.CANCELLED)

        assert result.status == OrderStatusEnum.CANCELLED

    async def test_packed_to_confirmed_raises(self):
        order = _order_mock(status=OrderStatusEnum.PACKED)
        db = _db_for_update(order)

        with pytest.raises(BadRequestError):
            await update_status(db, order.order_number, OrderStatusEnum.CONFIRMED)

    async def test_packed_to_cancelled_raises(self):
        order = _order_mock(status=OrderStatusEnum.PACKED)
        db = _db_for_update(order)

        with pytest.raises(BadRequestError):
            await update_status(db, order.order_number, OrderStatusEnum.CANCELLED)

    async def test_cancelled_to_confirmed_raises(self):
        order = _order_mock(status=OrderStatusEnum.CANCELLED)
        db = _db_for_update(order)

        with pytest.raises(BadRequestError):
            await update_status(db, order.order_number, OrderStatusEnum.CONFIRMED)

    async def test_order_not_found_raises_not_found(self):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_result(scalar_one_or_none=None))

        with pytest.raises(NotFoundError):
            await update_status(db, "MM-DOESNOTEXIST", OrderStatusEnum.CONFIRMED)

    async def test_admin_notes_saved_on_order(self):
        order = _order_mock(status=OrderStatusEnum.CREATED)
        order.admin_notes = None
        db = _db_for_update(order)

        await update_status(
            db, order.order_number, OrderStatusEnum.CONFIRMED, admin_notes="Reviewed"
        )

        assert order.admin_notes == "Reviewed"

    async def test_none_admin_notes_leaves_existing_unchanged(self):
        order = _order_mock(status=OrderStatusEnum.CREATED)
        order.admin_notes = "existing note"
        db = _db_for_update(order)

        await update_status(
            db, order.order_number, OrderStatusEnum.CONFIRMED, admin_notes=None
        )

        # admin_notes=None means "don't touch it"
        assert order.admin_notes == "existing note"


# ── create_order error paths ──────────────────────────────────────────────────


class TestCreateOrderErrors:
    async def test_no_session_id_and_no_user_raises(self):
        db = AsyncMock()
        data = OrderCreate(
            email="test@example.com",
            delivery_method=DeliveryMethodEnum.PICKUP,
            payment_method="stripe",
        )
        with pytest.raises(BadRequestError, match="session_id"):
            await create_order(db, data, user_id=None)

    async def test_cart_not_found_raises(self):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_result(scalar_one_or_none=None))

        with pytest.raises(BadRequestError, match="[Cc]art"):
            await create_order(db, _pickup_data(), user_id=None)

    async def test_empty_cart_raises(self):
        cart = _cart(items=[])
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_result(scalar_one_or_none=cart))

        with pytest.raises(BadRequestError, match="[Ee]mpty"):
            await create_order(db, _pickup_data(), user_id=None)

    async def test_delivery_without_address_raises(self):
        cart = _cart(items=[_cart_item(_product())])
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_result(scalar_one_or_none=cart))
        data = OrderCreate(
            email="test@example.com",
            delivery_method=DeliveryMethodEnum.DELIVERY,
            payment_method="stripe",
            session_id="sess_test",
            shipping_address=None,
        )
        with pytest.raises(BadRequestError, match="[Ss]hipping address"):
            await create_order(db, data, user_id=None)

    async def test_inactive_product_in_cart_raises(self):
        cart = _cart(items=[_cart_item(_product(is_active=False))])
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_result(scalar_one_or_none=cart))

        with pytest.raises(BadRequestError, match="[Nn]o longer available"):
            await create_order(db, _pickup_data(), user_id=None)

    async def test_product_in_hidden_category_in_cart_raises(self):
        product = _product()
        product.category_id = uuid.uuid4()
        product.category = MagicMock(is_active=False)
        cart = _cart(items=[_cart_item(product)])
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_result(scalar_one_or_none=cart))

        with pytest.raises(BadRequestError, match="[Nn]o longer available"):
            await create_order(db, _pickup_data(), user_id=None)


# ── create_order calculations ─────────────────────────────────────────────────


class TestCreateOrderCalculations:
    """
    Test the financial math inside create_order.
    Assertions are made against the Order instance passed to db.add().
    """

    async def test_pickup_delivery_fee_is_zero(self):
        cart = _cart(items=[_cart_item(_product("100.00"))])
        db = _db_for_create(cart, _order_mock())

        await create_order(db, _pickup_data(), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.delivery_fee == Decimal("0.00")

    async def test_single_item_subtotal(self):
        cart = _cart(items=[_cart_item(_product("150.00"), quantity=2)])
        db = _db_for_create(cart, _order_mock())

        await create_order(db, _pickup_data(), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.subtotal == Decimal("300.00")
        assert order_arg.total == Decimal("300.00")

    async def test_multiple_items_subtotal_sums(self):
        cart = _cart(
            items=[
                _cart_item(_product("40.00"), quantity=2),  # 80
                _cart_item(_product("60.00"), quantity=1),  # 60
            ]
        )
        db = _db_for_create(cart, _order_mock())

        await create_order(db, _pickup_data(), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.subtotal == Decimal("140.00")

    async def test_selected_options_add_to_unit_price(self):
        product = _product("80.00")
        options = [{"option_name": "extra tier", "option_price": 20}]
        cart = _cart(items=[_cart_item(product, quantity=1, selected_options=options)])
        db = _db_for_create(cart, _order_mock())

        await create_order(db, _pickup_data(), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        # unit_price = 80 + 20 = 100
        assert order_arg.subtotal == Decimal("100.00")

    async def test_vat_back_calculation(self):
        """VAT is extracted from a tax-inclusive price at 5% (UAE VAT)."""
        cart = _cart(items=[_cart_item(_product("100.00"))])
        db = _db_for_create(cart, _order_mock())

        await create_order(db, _pickup_data(), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        # 100 * 0.05 / 1.05 = 4.7619... → 4.76
        assert order_arg.vat_amount == Decimal("4.76")
        # 100 / 1.05 = 95.238... → 95.24
        assert order_arg.total_excl_vat == Decimal("95.24")

    async def test_fixed_fee_zone_charges_its_own_fee(self, delivery_pricing):
        find_zone, _ = delivery_pricing
        find_zone.return_value = _zone("35.00")
        cart = _cart(items=[_cart_item(_product("100.00"))])
        db = _db_for_create(cart, _order_mock(delivery_fee=Decimal("35.00")))

        await create_order(db, _delivery_data(), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.delivery_fee == Decimal("35.00")

    async def test_dynamic_zone_charges_the_courier_price_rounded_up(
        self, delivery_pricing
    ):
        """The order is written with the same rounded number the checkout showed."""
        find_zone, estimate_for_point = delivery_pricing
        find_zone.return_value = _zone("0.00", pricing_mode="dynamic")
        estimate_for_point.return_value = (
            lalamove_service.Estimate(
                cost=Decimal("31.05"),
                currency="AED",
                distance_m=22000,
                quotation_id="q_1",
            ),
            None,
        )
        cart = _cart(items=[_cart_item(_product("100.00"))])
        db = _db_for_create(cart, _order_mock(delivery_fee=Decimal("32.00")))

        await create_order(db, _delivery_data(), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.delivery_fee == Decimal("32.00")
        assert order_arg.total == Decimal("132.00")

    async def test_an_unquotable_dynamic_pin_refuses_the_order(self, delivery_pricing):
        """
        The point of the whole refusal: no price, no order. Taking the money and
        discovering at dispatch that nobody will carry it is the outcome this
        exists to prevent.
        """
        find_zone, estimate_for_point = delivery_pricing
        find_zone.return_value = _zone("0.00", pricing_mode="dynamic")
        estimate_for_point.return_value = (None, "Outside the service area")
        cart = _cart(items=[_cart_item(_product("100.00"))])
        db = _db_for_create(cart, _order_mock())

        with pytest.raises(BadRequestError):
            await create_order(db, _delivery_data(), user_id=None)

        assert db.add.call_args_list == [], "nothing was written"

    async def test_a_fixed_fee_zone_survives_a_courier_outage(self, delivery_pricing):
        """
        Its price never came from the courier, so a courier that will not answer
        changes nothing the customer can see.
        """
        find_zone, estimate_for_point = delivery_pricing
        find_zone.return_value = _zone("15.00")
        estimate_for_point.return_value = (None, "Courier quote failed")
        cart = _cart(items=[_cart_item(_product("100.00"))])
        db = _db_for_create(cart, _order_mock(delivery_fee=Decimal("15.00")))

        await create_order(db, _delivery_data(), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.delivery_fee == Decimal("15.00")

    async def test_free_delivery_at_the_threshold(self, delivery_pricing):
        find_zone, _ = delivery_pricing
        find_zone.return_value = _zone("25.00")
        cart = _cart(items=[_cart_item(_product("150.00"))])
        db = _db_for_create(cart, _order_mock(delivery_fee=Decimal("0.00")))

        await create_order(db, _delivery_data(), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.delivery_fee == Decimal("0.00")

    async def test_free_delivery_above_threshold(self, delivery_pricing):
        find_zone, _ = delivery_pricing
        find_zone.return_value = _zone("25.00")
        cart = _cart(items=[_cart_item(_product("250.00"))])
        db = _db_for_create(cart, _order_mock(delivery_fee=Decimal("0.00")))

        await create_order(db, _delivery_data(), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.delivery_fee == Decimal("0.00")

    async def test_a_courier_priced_zone_charges_however_big_the_basket(
        self, delivery_pricing
    ):
        """
        The fee out there is a bill someone sends us, not a margin we chose, and
        it is the same 40 dirhams whether the order is 50 or 500.
        """
        find_zone, estimate_for_point = delivery_pricing
        find_zone.return_value = _zone("0.00", pricing_mode="dynamic", free=False)
        cart = _cart(items=[_cart_item(_product("500.00"))])
        db = _db_for_create(cart, _order_mock(delivery_fee=Decimal("40.00")))

        await create_order(db, _delivery_data(), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.delivery_fee == Decimal("40.00")
        assert order_arg.total == Decimal("540.00")

    async def test_order_number_starts_at_001_for_first_order(self):
        cart = _cart(items=[_cart_item(_product())])
        db = _db_for_create(cart, _order_mock(), last_order_seq=None)

        await create_order(db, _pickup_data(), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.order_number.endswith("-001")

    async def test_order_number_increments_from_last(self):
        cart = _cart(items=[_cart_item(_product())])
        db = _db_for_create(cart, _order_mock(), last_order_seq=5)

        await create_order(db, _pickup_data(), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.order_number.endswith("-006")

    async def test_order_number_increments_past_four_digits(self):
        """Regression: a string max would sort '-999' above '-1000'."""
        cart = _cart(items=[_cart_item(_product())])
        db = _db_for_create(cart, _order_mock(), last_order_seq=1000)

        await create_order(db, _pickup_data(), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.order_number.endswith("-1001")

    async def test_cart_items_deleted_after_order_created(self):
        ci = _cart_item(_product())
        cart = _cart(items=[ci])
        db = _db_for_create(cart, _order_mock(), cart_items=[ci])

        await create_order(db, _pickup_data(), user_id=None)

        db.delete.assert_called()

    async def test_email_stored_on_order(self):
        cart = _cart(items=[_cart_item(_product())])
        db = _db_for_create(cart, _order_mock())
        data = OrderCreate(
            email="customer@example.com",
            delivery_method=DeliveryMethodEnum.PICKUP,
            payment_method="stripe",
            session_id="sess_test",
        )
        await create_order(db, data, user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.email == "customer@example.com"

    async def test_payment_method_stored_on_order(self):
        cart = _cart(items=[_cart_item(_product())])
        db = _db_for_create(cart, _order_mock())

        await create_order(db, _pickup_data(), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.payment_method == "stripe"

    async def test_initial_status_is_created(self):
        cart = _cart(items=[_cart_item(_product())])
        db = _db_for_create(cart, _order_mock())

        await create_order(db, _pickup_data(), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.status == OrderStatusEnum.CREATED

    async def test_a_delivery_records_what_the_checkout_promised(self):
        """
        The order carries the promise, so every email about it can repeat the
        number the customer was actually shown.

        Before this, each reader worked out its own: checkout read the batch
        window that was open at the time, and the confirmation email — sent
        before any batch exists — fell back to a generic prep-plus-drive sum.
        MM-20260805-008 was quoted 19:00 on the page and 17:25 in the inbox.
        """
        cart = _cart(items=[_cart_item(_product())])
        db = _db_for_create(cart, _order_mock())

        await create_order(db, _delivery_data(), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.promised_at is not None
        assert order_arg.promised_precision in {"time", "day"}

    async def test_a_collection_order_promises_nothing(self):
        """
        There is nothing for a stored copy to protect. A pickup estimate is
        `created_at + prep`, which is the same answer however often it is
        derived — unlike a delivery, whose window has closed by the time any
        email is sent.
        """
        cart = _cart(items=[_cart_item(_product())])
        db = _db_for_create(cart, _order_mock())

        await create_order(db, _pickup_data(), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.promised_at is None
        assert order_arg.promised_precision is None


# ── Stock-tracked products ────────────────────────────────────────────────────


class TestCreateOrderStock:
    async def test_stock_product_is_decremented_at_creation(self):
        product = _product(is_stock_product=True, stock_quantity=5)
        cart = _cart(items=[_cart_item(product, quantity=2)])
        db = _db_for_create(
            cart,
            _order_mock(),
            # Guarded UPDATE affected a row → stock was claimed.
            extra_results=[_result(scalar_one_or_none=product.id)],
        )

        await create_order(db, _pickup_data(), user_id=None)

        decrement = db.execute.call_args_list[1][0][0]
        assert "stock_quantity" in str(decrement)

    async def test_out_of_stock_product_fails_the_order(self):
        product = _product(is_stock_product=True, stock_quantity=1)
        cart = _cart(items=[_cart_item(product, quantity=2)])
        db = _db_for_create(
            cart,
            _order_mock(),
            # Guarded UPDATE affected no rows → not enough stock.
            extra_results=[_result(scalar_one_or_none=None)],
        )

        with pytest.raises(BadRequestError, match="[Oo]ut of stock"):
            await create_order(db, _pickup_data(), user_id=None)

    async def test_non_stock_product_is_not_decremented(self):
        cart = _cart(items=[_cart_item(_product(), quantity=2)])
        db = _db_for_create(cart, _order_mock())

        await create_order(db, _pickup_data(), user_id=None)

        # Second execute is the order-number query, not a stock UPDATE.
        second = db.execute.call_args_list[1][0][0]
        assert "stock_quantity" not in str(second)


# ── Promo code applied to order ───────────────────────────────────────────────


class TestCreateOrderWithPromo:
    """
    Tests for orders that include a promo code.
    promo_code_service is patched so tests focus on order_service math.
    """

    def _promo_db(self, cart, final_order) -> AsyncMock:
        """
        DB mock wired for create_order WITH a promo (max_uses=None branch).
        Execute call order:
          1. cart lookup
          2. _generate_order_number
          3. promo uses UPDATE (always runs even when max_uses is None)
          4. select CartItems for deletion
          5. final Order reload
        """
        db = AsyncMock()
        db.add = MagicMock()
        db.delete = AsyncMock()
        db.flush = AsyncMock()
        db.begin_nested = MagicMock()
        db.execute = AsyncMock(
            side_effect=_sequenced(
                [
                    _result(scalar_one_or_none=cart),  # cart lookup
                    _result(scalar_one_or_none=None),  # order number
                    _result(),  # promo uses UPDATE
                    _result(scalars_all=[]),  # cart item deletion
                    _result(scalar_one=final_order),  # final reload
                ]
            )
        )
        return db

    @patch("app.services.order_service.promo_code_service.validate")
    @patch("app.services.order_service.promo_code_service.get_promo")
    async def test_percentage_promo_reduces_total(self, mock_get_promo, mock_validate):
        """Regression: Decimal discount_amount must not cause TypeError."""
        mock_validate.return_value = PromoCodeValidateResponse(
            valid=True, discount_amount=Decimal("15.00")
        )
        mock_get_promo.return_value = MagicMock(max_uses=None, id=uuid.uuid4())

        cart = _cart(items=[_cart_item(_product("100.00"))])
        db = self._promo_db(
            cart, _order_mock(discount=Decimal("15.00"), total=Decimal("85.00"))
        )

        await create_order(db, _pickup_data(promo_code="MM15"), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.discount_amount == Decimal("15.00")
        assert order_arg.total == Decimal("85.00")

    @patch("app.services.order_service.promo_code_service.validate")
    @patch("app.services.order_service.promo_code_service.get_promo")
    async def test_promo_discount_no_type_error(self, mock_get_promo, mock_validate):
        """
        Explicit regression: Decimal subtotal - Decimal discount_amount must not raise.
        Was: TypeError: unsupported operand type(s) for -: 'decimal.Decimal' and 'float'
        """
        mock_validate.return_value = PromoCodeValidateResponse(
            valid=True, discount_amount=Decimal("15.00")
        )
        mock_get_promo.return_value = MagicMock(max_uses=None, id=uuid.uuid4())

        cart = _cart(items=[_cart_item(_product("100.00"))])
        db = self._promo_db(cart, _order_mock())

        # Must not raise TypeError
        await create_order(db, _pickup_data(promo_code="MM15"), user_id=None)

    @patch("app.services.order_service.promo_code_service.validate")
    @patch("app.services.order_service.promo_code_service.get_promo")
    async def test_promo_stored_on_order(self, mock_get_promo, mock_validate):
        mock_validate.return_value = PromoCodeValidateResponse(
            valid=True, discount_amount=Decimal("10.00")
        )
        mock_get_promo.return_value = MagicMock(
            max_uses=None, id=uuid.uuid4(), code="SAVE10"
        )

        cart = _cart(items=[_cart_item(_product("100.00"))])
        db = self._promo_db(cart, _order_mock())

        await create_order(db, _pickup_data(promo_code="SAVE10"), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.promo_code_used == "SAVE10"

    @patch("app.services.order_service.promo_code_service.validate")
    @patch("app.services.order_service.promo_code_service.get_promo")
    async def test_the_arabic_spelling_is_stored_as_the_english_one(
        self, mock_get_promo, mock_validate
    ):
        """
        One coupon, one value on the order.

        The two codes are names for the same row, so storing whichever was typed
        would leave `promo_code_used` holding either — and every count that
        reads it, including the per-customer ceiling, would have to know about
        both spellings to get the right answer.
        """
        mock_validate.return_value = PromoCodeValidateResponse(
            valid=True, discount_amount=Decimal("10.00")
        )
        mock_get_promo.return_value = MagicMock(
            max_uses=None, id=uuid.uuid4(), code="SAVE10"
        )

        cart = _cart(items=[_cart_item(_product("100.00"))])
        db = self._promo_db(cart, _order_mock())

        await create_order(db, _pickup_data(promo_code="خصم10"), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.promo_code_used == "SAVE10"

    @patch("app.services.order_service.promo_code_service.validate")
    @patch("app.services.order_service.promo_code_service.get_promo")
    async def test_invalid_promo_raises_bad_request(
        self, mock_get_promo, mock_validate
    ):
        mock_validate.return_value = PromoCodeValidateResponse(
            valid=False, message="Promo code not found"
        )

        cart = _cart(items=[_cart_item(_product("100.00"))])
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_result(scalar_one_or_none=cart))

        with pytest.raises(BadRequestError, match="[Pp]romo"):
            await create_order(db, _pickup_data(promo_code="BADCODE"), user_id=None)

    @patch("app.services.order_service.promo_code_service.validate")
    @patch("app.services.order_service.promo_code_service.get_promo")
    async def test_discount_brings_delivery_to_free_at_threshold(
        self, mock_get_promo, mock_validate, delivery_pricing
    ):
        """
        Discount reduces the subtotal to exactly the threshold (150 AED), so a
        fixed-fee zone delivers free: subtotal=250, discount=100 → 150.

        It is the discounted figure that is compared, not the basket — a
        customer who has paid 150 has bought a 150 dirham order whatever the
        sticker said.
        """
        find_zone, _ = delivery_pricing
        find_zone.return_value = _zone("15.00")
        mock_validate.return_value = PromoCodeValidateResponse(
            valid=True, discount_amount=Decimal("100.00")
        )
        mock_get_promo.return_value = MagicMock(max_uses=None, id=uuid.uuid4())

        cart = _cart(items=[_cart_item(_product("250.00"))])
        db = self._promo_db(
            cart,
            _order_mock(
                delivery_method=DeliveryMethodEnum.DELIVERY,
                delivery_fee=Decimal("0.00"),
            ),
        )

        await create_order(db, _delivery_data(promo_code="DISC50"), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.delivery_fee == Decimal("0.00")

    @patch("app.services.order_service.promo_code_service.validate")
    @patch("app.services.order_service.promo_code_service.get_promo")
    async def test_vat_calculated_on_discounted_amount(
        self, mock_get_promo, mock_validate
    ):
        """VAT base is (subtotal - discount), not the full subtotal."""
        # subtotal=100, discount=10 → taxable=90
        # vat = 90 * 0.05 / 1.05 = 4.2857... → 4.29
        mock_validate.return_value = PromoCodeValidateResponse(
            valid=True, discount_amount=Decimal("10.00")
        )
        mock_get_promo.return_value = MagicMock(max_uses=None, id=uuid.uuid4())

        cart = _cart(items=[_cart_item(_product("100.00"))])
        db = self._promo_db(cart, _order_mock())

        await create_order(db, _pickup_data(promo_code="SAVE10"), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.vat_amount == Decimal("4.29")
        assert order_arg.total_excl_vat == Decimal("85.71")
