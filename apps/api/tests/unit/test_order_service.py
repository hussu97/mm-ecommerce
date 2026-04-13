from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.address import RegionEnum
from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from app.schemas.address import AddressCreate
from app.schemas.order import OrderCreate
from app.schemas.promo_code import PromoCodeValidateResponse
from app.services.order_service import VALID_TRANSITIONS, create_order, update_status


@pytest.fixture(autouse=True)
def mock_calculate_fee():
    """Patch delivery_service.calculate_fee to avoid DB calls in order_service tests."""
    with patch(
        "app.services.order_service.delivery_service.calculate_fee",
        new_callable=AsyncMock,
    ) as m:
        m.return_value = Decimal("0.00")
        yield m


# ── Test helpers ─────────────────────────────────────────────────────────────


def _product(base_price: str = "100.00", is_active: bool = True) -> MagicMock:
    p = MagicMock()
    p.id = uuid.uuid4()
    p.base_price = Decimal(base_price)
    p.is_active = is_active
    p.name = "Test Cake"
    p.sku = "CAKE-001"
    p.translations = {}
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
    last_order_num: str | None = None,
) -> AsyncMock:
    """
    Mock DB wired for create_order (no promo — patch promo service separately).
    Execute call order:
      1. cart lookup
      2. _generate_order_number
      3. select CartItems for deletion
      4. final Order reload
    """
    db = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _result(scalar_one_or_none=cart),
            _result(scalar_one_or_none=last_order_num),
            _result(scalars_all=cart_items or []),
            _result(scalar_one=final_order),
        ]
    )
    return db


def _db_for_update(order, updated_order=None) -> AsyncMock:
    """
    Mock DB wired for update_status.
    Execute call order: 1. Order lookup  2. final Order reload
    """
    db = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _result(scalar_one_or_none=order),
            _result(scalar_one=updated_order or order),
        ]
    )
    return db


def _delivery_address(region: RegionEnum = RegionEnum.DUBAI) -> AddressCreate:
    return AddressCreate(
        first_name="Test",
        last_name="User",
        phone="+971500000000",
        address_line_1="123 Test St",
        region=region,
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


def _delivery_data(
    region: RegionEnum = RegionEnum.DUBAI, promo_code: str | None = None
) -> OrderCreate:
    return OrderCreate(
        email="test@example.com",
        delivery_method=DeliveryMethodEnum.DELIVERY,
        payment_method="stripe",
        session_id="sess_test",
        shipping_address=_delivery_address(region),
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

    async def test_standard_zone_delivery_fee_is_35(self, mock_calculate_fee):
        mock_calculate_fee.return_value = Decimal("35.00")
        cart = _cart(items=[_cart_item(_product("100.00"))])
        db = _db_for_create(cart, _order_mock(delivery_fee=Decimal("35.00")))

        await create_order(db, _delivery_data(RegionEnum.DUBAI), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.delivery_fee == Decimal("35.00")

    async def test_remote_zone_delivery_fee_is_50(self, mock_calculate_fee):
        mock_calculate_fee.return_value = Decimal("50.00")
        cart = _cart(items=[_cart_item(_product("100.00"))])
        db = _db_for_create(cart, _order_mock(delivery_fee=Decimal("50.00")))

        await create_order(db, _delivery_data(RegionEnum.ABU_DHABI), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.delivery_fee == Decimal("50.00")

    async def test_free_delivery_at_200_threshold(self):
        cart = _cart(items=[_cart_item(_product("200.00"))])
        db = _db_for_create(cart, _order_mock(delivery_fee=Decimal("0.00")))

        await create_order(db, _delivery_data(RegionEnum.DUBAI), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.delivery_fee == Decimal("0.00")

    async def test_free_delivery_above_threshold(self):
        cart = _cart(items=[_cart_item(_product("250.00"))])
        db = _db_for_create(cart, _order_mock(delivery_fee=Decimal("0.00")))

        await create_order(db, _delivery_data(RegionEnum.ABU_DHABI), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.delivery_fee == Decimal("0.00")

    async def test_order_number_starts_at_001_for_first_order(self):
        cart = _cart(items=[_cart_item(_product())])
        db = _db_for_create(cart, _order_mock(), last_order_num=None)

        await create_order(db, _pickup_data(), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.order_number.endswith("-001")

    async def test_order_number_increments_from_last(self):
        cart = _cart(items=[_cart_item(_product())])
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
        db = _db_for_create(cart, _order_mock(), last_order_num=f"MM-{today}-005")

        await create_order(db, _pickup_data(), user_id=None)

        order_arg = db.add.call_args_list[0][0][0]
        assert order_arg.order_number.endswith("-006")

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
        db.execute = AsyncMock(
            side_effect=[
                _result(scalar_one_or_none=cart),  # cart lookup
                _result(scalar_one_or_none=None),  # order number
                _result(),  # promo uses UPDATE
                _result(scalars_all=[]),  # cart item deletion
                _result(scalar_one=final_order),  # final reload
            ]
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
        mock_get_promo.return_value = MagicMock(max_uses=None, id=uuid.uuid4())

        cart = _cart(items=[_cart_item(_product("100.00"))])
        db = self._promo_db(cart, _order_mock())

        await create_order(db, _pickup_data(promo_code="SAVE10"), user_id=None)

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
        self, mock_get_promo, mock_validate
    ):
        """
        Discount reduces the subtotal to exactly FREE_THRESHOLD (200 AED),
        so delivery fee should become zero even for a remote zone.
        subtotal=250, discount=50 → discounted_subtotal=200 → free delivery.
        """
        mock_validate.return_value = PromoCodeValidateResponse(
            valid=True, discount_amount=Decimal("50.00")
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

        await create_order(
            db, _delivery_data(RegionEnum.ABU_DHABI, promo_code="DISC50"), user_id=None
        )

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
