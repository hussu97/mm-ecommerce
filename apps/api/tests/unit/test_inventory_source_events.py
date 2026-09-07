import os
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.exceptions import ConflictError
from app.models.inventory_v2 import (
    InventorySourceEvent,
    InventorySourceEventStatusEnum,
)
from app.services.inventory import source_event_service


class _Savepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, _type, _value, _traceback):
        return False


def _db():
    return SimpleNamespace(
        begin_nested=lambda: _Savepoint(),
        refresh=AsyncMock(),
        flush=AsyncMock(),
    )


def _event() -> InventorySourceEvent:
    return InventorySourceEvent(
        id=uuid4(),
        branch_id=uuid4(),
        source_type="order",
        source_id=str(uuid4()),
        idempotency_key=f"order:{uuid4()}:1",
        status=InventorySourceEventStatusEnum.PENDING.value,
        accepted_at=None,
    )


@pytest.mark.asyncio
async def test_domain_posting_failure_becomes_a_no_movement_exception(monkeypatch):
    db = _db()
    event = _event()
    monkeypatch.setattr(
        source_event_service,
        "post_event",
        AsyncMock(side_effect=ConflictError("Insufficient stock")),
    )

    result = await source_event_service._post_or_record_exception(
        db,
        event=event,
        order=SimpleNamespace(),
        user=None,
        already_locked=True,
    )

    assert result is None
    assert event.status == InventorySourceEventStatusEnum.EXCEPTION.value
    assert event.error_code == "inventory_posting_failed"
    assert event.error_detail == "Insufficient stock"
    db.refresh.assert_awaited_once_with(event)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_unexpected_posting_failure_still_rolls_back_the_order(monkeypatch):
    db = _db()
    event = _event()
    monkeypatch.setattr(
        source_event_service,
        "post_event",
        AsyncMock(side_effect=RuntimeError("database connection lost")),
    )

    with pytest.raises(RuntimeError, match="database connection lost"):
        await source_event_service._post_or_record_exception(
            db,
            event=event,
            order=SimpleNamespace(),
            user=None,
            already_locked=True,
        )

    db.refresh.assert_not_awaited()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_refuses_an_event_that_already_moved_stock():
    """Re-snapshotting a posted event would double-count what it already consumed."""
    posted = _event()
    posted.status = InventorySourceEventStatusEnum.POSTED.value
    with pytest.raises(ConflictError):
        await source_event_service.retry_event(
            _db(), event=posted, order=SimpleNamespace(), user=None, already_locked=True
        )

    with_txn = _event()
    with_txn.transaction_id = uuid4()
    with pytest.raises(ConflictError):
        await source_event_service.retry_event(
            _db(),
            event=with_txn,
            order=SimpleNamespace(),
            user=None,
            already_locked=True,
        )


# ── F-INV-3: a missing recipe on one line must not suppress the whole order ─────

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
MARKER = "pytest-src-event"
BUSINESS_DATE = "2026-09-07"


@pytest.fixture
async def db_engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.mark.skipif(not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL")
@pytest.mark.asyncio
async def test_a_missing_modifier_recipe_does_not_suppress_the_products_that_have_one(
    db_engine,
):
    """Product P has a recipe, modifier M does not — P must still be consumed."""
    from app.models.branch import Branch
    from app.models.inventory import (
        InventoryItem,
        InventoryLevel,
        InventoryTransaction,
        InventoryTransactionItem,
        InventoryTransactionTypeEnum,
        Warehouse,
    )
    from app.models.inventory_v2 import BranchInventorySettings, Recipe, RecipeVersion
    from app.models.modifier import Modifier, ModifierOption
    from app.models.order import DeliveryMethodEnum, Order, OrderItem
    from app.models.product import Product
    from app.models.user import User
    from app.services.inventory import recipe_service
    from app.services.inventory.recipe_service import RecipeLineInput

    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    created = {}
    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} branch", reference=f"{MARKER}-{uuid4().hex[:12]}"
        )
        db.add(branch)
        await db.flush()
        db.add(Warehouse(branch_id=branch.id, name="Default", is_default=True))
        db.add(
            BranchInventorySettings(
                branch_id=branch.id,
                inventory_enabled=True,
                sales_consumption_enabled=True,
                allow_negative_stock=True,
                go_live_at=source_event_service.utcnow(),
                go_live_sequence=0,
            )
        )
        user = User(email=f"{MARKER}-{uuid4().hex[:8]}@ex.com", hashed_password="x")
        db.add(user)
        ingredient = InventoryItem(
            sku=f"{MARKER}-{uuid4().hex[:10]}",
            name="Sugar",
            kind="raw_material",
            tracking_mode="stocked",
            storage_unit="g",
            ingredient_unit="g",
            storage_to_ingredient_factor=Decimal("1"),
            cost=Decimal("1"),
        )
        db.add(ingredient)
        product = Product(name="Cake", slug=f"cake-{uuid4().hex[:8]}")
        db.add(product)
        modifier = Modifier(name="Topping", reference=f"top-{uuid4().hex[:8]}")
        db.add(modifier)
        await db.flush()
        option = ModifierOption(
            modifier_id=modifier.id, name="Sprinkles", sku=f"spr-{uuid4().hex[:8]}"
        )
        db.add(option)
        await db.flush()

        # Product P gets an active recipe (2g sugar); modifier option M gets none.
        await recipe_service.draft_and_activate(
            db,
            kind="product",
            owner_id=product.id,
            lines=[RecipeLineInput(item_id=ingredient.id, quantity=Decimal("2"))],
            user_id=user.id,
        )

        order = Order(
            order_number=f"SE-{uuid4().hex[:10]}",
            email="pytest-src@example.com",
            source="online",
            branch_id=branch.id,
            business_date=BUSINESS_DATE,
            delivery_method=DeliveryMethodEnum.PICKUP,
            subtotal=Decimal("10.00"),
            total=Decimal("10.00"),
        )
        db.add(order)
        await db.flush()
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                product_name="Cake",
                product_sku="CAKE",
                quantity=1,
                base_price=Decimal("10"),
                unit_price=Decimal("10"),
                total_price=Decimal("10"),
                selected_options_snapshot=[
                    {"modifier_option_id": str(option.id), "quantity": 1}
                ],
            )
        )
        await db.commit()
        created = {
            "branch_id": branch.id,
            "order_id": order.id,
            "ingredient_id": ingredient.id,
            "user_id": user.id,
            "product_id": product.id,
            "modifier_id": modifier.id,
            "option_id": option.id,
        }

    try:
        async with Session() as db:
            order = await db.get(Order, created["order_id"])
            user = await db.get(User, created["user_id"])
            event = await source_event_service.accept_order(db, order=order, user=user)
            await db.commit()

            # The missing modifier recipe is flagged as a warning...
            assert event.error_code == "missing_recipe"
            assert "modifier option" in (event.error_detail or "")
            # ...but the product that DOES have a recipe was still posted.
            assert event.status == InventorySourceEventStatusEnum.POSTED.value
            assert event.transaction_id is not None

        async with Session() as db:
            line = (
                await db.execute(
                    select(InventoryTransactionItem)
                    .join(
                        InventoryTransaction,
                        InventoryTransaction.id
                        == InventoryTransactionItem.transaction_id,
                    )
                    .where(
                        InventoryTransaction.order_id == created["order_id"],
                        InventoryTransaction.type
                        == InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value,
                        InventoryTransactionItem.item_id == created["ingredient_id"],
                    )
                )
            ).scalar_one()
            assert Decimal(str(line.signed_quantity)) == Decimal("-2")
    finally:
        async with Session() as db:
            await db.execute(text("SET session_replication_role = 'replica'"))
            bid = created["branch_id"]
            await db.execute(
                InventoryTransactionItem.__table__.delete().where(
                    InventoryTransactionItem.transaction_id.in_(
                        select(InventoryTransaction.id).where(
                            InventoryTransaction.branch_id == bid
                        )
                    )
                )
            )
            await db.execute(
                InventoryTransaction.__table__.delete().where(
                    InventoryTransaction.branch_id == bid
                )
            )
            await db.execute(
                InventorySourceEvent.__table__.delete().where(
                    InventorySourceEvent.branch_id == bid
                )
            )
            await db.execute(
                OrderItem.__table__.delete().where(
                    OrderItem.order_id == created["order_id"]
                )
            )
            await db.execute(
                Order.__table__.delete().where(Order.id == created["order_id"])
            )
            # Recipes reference the product; drop version->recipe then items.
            await db.execute(
                RecipeVersion.__table__.delete().where(
                    RecipeVersion.recipe_id.in_(
                        select(Recipe.id).where(
                            Recipe.product_id == created["product_id"]
                        )
                    )
                )
            )
            await db.execute(
                Recipe.__table__.delete().where(
                    Recipe.product_id == created["product_id"]
                )
            )
            await db.execute(
                InventoryLevel.__table__.delete().where(
                    InventoryLevel.item_id == created["ingredient_id"]
                )
            )
            await db.execute(
                BranchInventorySettings.__table__.delete().where(
                    BranchInventorySettings.branch_id == bid
                )
            )
            await db.execute(
                InventoryItem.__table__.delete().where(
                    InventoryItem.id == created["ingredient_id"]
                )
            )
            await db.execute(
                ModifierOption.__table__.delete().where(
                    ModifierOption.id == created["option_id"]
                )
            )
            await db.execute(
                Modifier.__table__.delete().where(Modifier.id == created["modifier_id"])
            )
            await db.execute(
                Product.__table__.delete().where(Product.id == created["product_id"])
            )
            await db.execute(
                Warehouse.__table__.delete().where(Warehouse.branch_id == bid)
            )
            await db.execute(
                User.__table__.delete().where(User.id == created["user_id"])
            )
            await db.execute(Branch.__table__.delete().where(Branch.id == bid))
            await db.execute(text("SET session_replication_role = 'origin'"))
            await db.commit()
