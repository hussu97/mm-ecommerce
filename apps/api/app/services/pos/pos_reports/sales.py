"""Sales: the summary, and the same money broken down by any supported dimension."""

from __future__ import annotations

import uuid

from sqlalchemy import Numeric, func, select
from sqlalchemy import null as sa_null
from sqlalchemy import true as sa_true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money
from app.models.category import Category
from app.models.order import Order, OrderItem
from app.models.order_delivery import OrderDelivery
from app.models.pos_order import (
    OrderCharge,
    OrderDiscount,
    OrderTax,
)
from app.models.pos_table import PosTable, Section
from app.models.product import Product
from app.models.tag import Tag, TaggedEntity
from app.services.pos import business_day_service

from ._base import (
    _COMPLETED_SALE,
    _DISCOUNT_SOURCES,
    _ENTITY_TAG_DIMENSIONS,
    _LINE_DIMENSIONS,
    _ORDER_DIMENSIONS,
    _TABLE_DIMENSIONS,
    SUPPORTED_DIMENSIONS,
    VOID,
    ZERO,
    _channel_logo,
    _covering_till,
    _labels_for,
    _scope,
)

# ─── Sales ────────────────────────────────────────────────────────────────────


async def sales_summary(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """Headline trading figures for the window."""
    stmt = _scope(
        select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.subtotal), 0),
            func.coalesce(func.sum(Order.discount_amount), 0),
            func.coalesce(func.sum(Order.charges_amount), 0),
            func.coalesce(func.sum(Order.vat_amount), 0),
            func.coalesce(func.sum(Order.total_excl_vat), 0),
            func.coalesce(func.sum(Order.rounding_amount), 0),
            func.coalesce(func.sum(Order.tips_amount), 0),
            func.coalesce(func.sum(Order.total), 0),
        ),
        branch_id=branch_id,
        date_from=date_from,
        date_to=date_to,
    ).where(_COMPLETED_SALE)

    row = (await db.execute(stmt)).one()
    (
        orders,
        subtotal,
        discounts,
        charges,
        vat,
        net_excl,
        rounding,
        tips,
        total,
    ) = row

    voided = (
        await db.execute(
            _scope(
                select(func.count(Order.id), func.coalesce(func.sum(Order.total), 0)),
                branch_id=branch_id,
                date_from=date_from,
                date_to=date_to,
            ).where(Order.pos_status == VOID)
        )
    ).one()

    returns = (
        await db.execute(
            _scope(
                select(
                    func.coalesce(
                        func.sum(OrderItem.returned_quantity * OrderItem.unit_price), 0
                    )
                )
                # The select list only mentions OrderItem, so the left side of the
                # join has to be stated explicitly or SQLAlchemy cannot infer it.
                .select_from(Order)
                .join(OrderItem, OrderItem.order_id == Order.id),
                branch_id=branch_id,
                date_from=date_from,
                date_to=date_to,
            )
            # Scoped to closed orders like every other figure above. Without it,
            # returns booked against still-open checks and voided orders counted
            # towards a `gross_sales` that excludes them, so the funnel did not
            # reconcile.
            .where(_COMPLETED_SALE)
        )
    ).scalar_one()

    order_count = int(orders or 0)
    net_sales = money(total)
    return {
        "orders_count": order_count,
        "gross_sales": money(subtotal),
        "discounts": money(discounts),
        "charges": money(charges),
        "returns": money(returns),
        "taxes": money(vat),
        "net_sales_excl_tax": money(net_excl),
        "rounding": money(rounding),
        "tips": money(tips),
        "net_sales": net_sales,
        "average_order_value": money(net_sales / order_count) if order_count else ZERO,
        "voided_orders": int(voided[0] or 0),
        "voided_value": money(voided[1]),
    }


async def sales_by_dimension(
    db: AsyncSession,
    *,
    dimension: str,
    branch_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """
    Sales grouped by any of the dimensions a GM actually asks about.

    One function rather than a route per dimension: the query shape is
    identical and only the grouping column changes. Foodics ships this as
    twenty-odd separate `sales-by-*` screens; the same answers come out of
    one endpoint with a `dimension` parameter.
    """
    if dimension in {"product", "category"}:
        return await _sales_by_item(
            db,
            group_by_category=(dimension == "category"),
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )

    if dimension == "modifier_option":
        return await _sales_by_modifier_option(
            db, branch_id=branch_id, date_from=date_from, date_to=date_to, limit=limit
        )

    if dimension in _TABLE_DIMENSIONS:
        return await _sales_by_seating(
            db,
            dimension=dimension,
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )

    if dimension == "delivery_zone":
        return await _sales_by_delivery_zone(
            db, branch_id=branch_id, date_from=date_from, date_to=date_to, limit=limit
        )

    if dimension in _ENTITY_TAG_DIMENSIONS:
        return await _sales_by_tag(
            db,
            dimension=dimension,
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )

    if dimension in _DISCOUNT_SOURCES or dimension in _LINE_DIMENSIONS:
        return await _sales_by_related(
            db,
            dimension=dimension,
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )

    column = _ORDER_DIMENSIONS.get(dimension)
    if column is None:
        raise ValueError(
            f"Unsupported dimension '{dimension}'. "
            f"Try one of: {', '.join(sorted(SUPPORTED_DIMENSIONS))}"
        )

    if dimension == "hour":
        # `closed_at` is stored in UTC; the hour has to be read in the business's
        # local timezone or a 6pm rush reports as 2pm.
        tz = await business_day_service.resolve_timezone(db)
        column = func.to_char(
            func.timezone(tz.key, func.coalesce(Order.closed_at, Order.created_at)),
            "HH24",
        )

    # Aggregator and website orders carry no cashier or terminal of their own, so
    # grouping on the raw column drops every one of them into a single "Unknown"
    # row. Read "who was online at the POS" off the till that was open when the
    # order arrived instead; the order's own value still wins when it has one, so
    # a counter sale is unaffected and only the un-rung orders borrow the till's.
    till_lateral = None
    if dimension in {"cashier", "staff", "device"}:
        till_lateral = _covering_till()
        if dimension == "device":
            column = func.coalesce(Order.device_id, till_lateral.c.device_id)
        else:
            column = func.coalesce(Order.closer_id, till_lateral.c.user_id)

    # What the group cost us, beside what it took. Every dimension here groups
    # whole orders, so the two stamped fee columns sum cleanly against the same
    # rows — which is the entire point of `order_fees` storing them: a manager
    # can now see that a channel took the most and kept the least without
    # anybody exporting a spreadsheet.
    #
    # `sum` over a nullable column skips the nulls, so a channel whose rate is
    # not configured contributes its orders to `net_sales` and nothing to
    # `fees`. That understates the cost rather than inventing one, and
    # `fees_known` below is what lets the client say so instead of implying the
    # margin is real.
    fees = func.coalesce(
        func.sum(func.coalesce(Order.aggregator_fee, 0)), 0
    ) + func.coalesce(func.sum(func.coalesce(Order.payment_fee, 0)), 0)
    # How many of the orders in this group actually carry a costed fee. Equal to
    # the order count when every one is priced; lower when some are not.
    fees_known = func.count(Order.payment_fee)

    selectable = select(
        column.label("key"),
        func.count(Order.id),
        func.coalesce(func.sum(Order.total), 0),
        func.coalesce(func.sum(Order.discount_amount), 0),
        fees,
        fees_known,
    )
    if till_lateral is not None:
        # Correlated to Order, so it is a LEFT JOIN LATERAL and never multiplies
        # the row: at most one till answers, and orders that carry their own
        # cashier/device never consult it.
        selectable = selectable.select_from(Order).outerjoin(till_lateral, sa_true())

    stmt = (
        _scope(
            selectable,
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
        )
        .where(_COMPLETED_SALE)
        .group_by(column)
        .order_by(func.coalesce(func.sum(Order.total), 0).desc())
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()
    labels = await _labels_for(db, dimension, rows)
    return [
        {
            "key": str(key) if key is not None else "unknown",
            "label": labels.get(str(key), str(key) if key is not None else "Unknown"),
            "orders": int(count or 0),
            "net_sales": money(total),
            "discounts": money(discount),
            "fees": money(fee_total),
            "net_after_fees": money(total - fee_total),
            # False when any order in the group has no costed fee, so the client
            # can mark the figure as partial rather than quoting a margin that
            # is missing a quarter of its costs.
            "fees_complete": int(known or 0) >= int(count or 0),
            # The marketplace's badge, on the channel breakdown only. A list of
            # channels is scanned, not read, and the logo is what the eye finds
            # — the same badge the order list, the register board and the
            # kitchen ticket already carry, from the same table.
            "image_url": _channel_logo(key) if dimension == "channel" else None,
        }
        for key, count, total, discount, fee_total, known in rows
    ]


async def _sales_by_related(
    db: AsyncSession,
    *,
    dimension: str,
    branch_id: uuid.UUID | None,
    date_from: str | None,
    date_to: str | None,
    limit: int,
) -> list[dict]:
    """
    Group by something attached to the order rather than on it.

    The amount summed is the child's own — the discount given, the charge
    levied — not the order total, so two discounts on one check do not each
    claim the whole check's value.
    """
    model, amount = {
        "discount": (OrderDiscount, OrderDiscount.amount),
        "charge": (OrderCharge, OrderCharge.amount),
        "tax": (OrderTax, OrderTax.amount),
    }.get(dimension, (OrderDiscount, OrderDiscount.amount))

    stmt = (
        _scope(
            select(
                model.name.label("key"),
                func.count(func.distinct(Order.id)),
                func.coalesce(func.sum(amount), 0),
            ),
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
        )
        .select_from(Order)
        .join(model, model.order_id == Order.id)
        .where(_COMPLETED_SALE)
        .where(
            OrderDiscount.source == _DISCOUNT_SOURCES[dimension]
            if dimension in _DISCOUNT_SOURCES
            else sa_true()
        )
        .group_by(model.name)
        .order_by(func.coalesce(func.sum(amount), 0).desc())
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()
    return [
        {
            "key": name or "unknown",
            "label": name or "Unknown",
            "orders": int(count or 0),
            # For these dimensions the money *is* the discount or charge, so
            # it is reported under both keys rather than inventing a new one
            # the admin table would have to special-case.
            "net_sales": money(total),
            "discounts": money(total) if dimension == "discount" else money(0),
        }
        for name, count, total in rows
    ]


async def _sales_by_item(
    db: AsyncSession,
    *,
    group_by_category: bool,
    branch_id: uuid.UUID | None,
    date_from: str | None,
    date_to: str | None,
    limit: int,
) -> list[dict]:
    quantity = func.sum(OrderItem.quantity - OrderItem.returned_quantity)
    revenue = func.sum(OrderItem.total_price)

    # The picture, so a best-sellers list can be scanned rather than read. Taken
    # from the catalogue in the same query rather than fetched per row: the
    # phone renders a hundred of these and a round trip each would make the
    # thumbnails cost more than the report.
    #
    # `image_urls` is a JSON array on the product; the first entry is the one
    # every other screen shows, so this is the same picture the products list
    # and the storefront use rather than a second opinion about which is the
    # main one.
    # `image_urls` is a Postgres text array, not JSON: PG arrays are 1-based so
    # `[0]` is always NULL, and `.astext` is a JSON accessor this column does not
    # have — together they built invalid SQL that 500'd product *and* category
    # (this line runs before the branch split) and never yielded a thumbnail.
    thumbnail = Product.image_urls[1]

    if group_by_category:
        key, label = Category.id, Category.name
        # A category has no picture of its own. Borrowing one product's would be
        # picking a favourite; the row shows its name and its share instead.
        stmt = (
            select(
                key,
                label,
                quantity,
                revenue,
                func.sum(OrderItem.discount_amount),
                sa_null().label("image_url"),
            )
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .join(Product, Product.id == OrderItem.product_id)
            .join(Category, Category.id == Product.category_id)
        )
        group_columns = (key, label)
    else:
        key, label = OrderItem.product_id, OrderItem.product_name
        stmt = (
            select(
                key,
                label,
                quantity,
                revenue,
                func.sum(OrderItem.discount_amount),
                func.min(thumbnail).label("image_url"),
            )
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            # Outer, so a line whose product has since been deleted still
            # reports its sales. It loses its thumbnail, which is the right
            # trade: a missing picture is a gap, a missing row is a wrong total.
            .outerjoin(Product, Product.id == OrderItem.product_id)
        )
        group_columns = (key, label)

    stmt = _scope(stmt, branch_id=branch_id, date_from=date_from, date_to=date_to)
    stmt = (
        stmt.where(_COMPLETED_SALE, OrderItem.status != "void")
        .group_by(*group_columns)
        .order_by(revenue.desc())
        .limit(limit)
    )

    return [
        {
            "key": str(k) if k is not None else "unknown",
            "label": name or "Unknown",
            "quantity": int(qty or 0),
            "net_sales": money(total),
            "discounts": money(discount),
            "image_url": image_url,
        }
        for k, name, qty, total, discount, image_url in (await db.execute(stmt)).all()
    ]


async def _sales_by_modifier_option(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None,
    date_from: str | None,
    date_to: str | None,
    limit: int,
) -> list[dict]:
    """
    Which modifier options actually sell — oat milk versus full fat.

    The chosen options are a JSON snapshot on the line rather than rows, so
    they are expanded here. The snapshot is deliberate: it records what the
    customer was charged at the time, and must not change when someone later
    edits the modifier's price.
    """
    option = func.jsonb_array_elements(
        func.cast(OrderItem.selected_options_snapshot, JSONB)
    ).alias("option")
    name = func.coalesce(option.column.op("->>")("name"), "Unknown")
    price = func.coalesce(
        func.cast(func.nullif(option.column.op("->>")("price"), ""), Numeric), 0
    )
    quantity = OrderItem.quantity - OrderItem.returned_quantity

    stmt = (
        _scope(
            select(
                name.label("key"),
                func.sum(quantity),
                func.coalesce(func.sum(price * quantity), 0),
            ),
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
        )
        .select_from(Order)
        .join(OrderItem, OrderItem.order_id == Order.id)
        .join(option, sa_true())
        .where(_COMPLETED_SALE)
        .group_by(name)
        .order_by(func.sum(quantity).desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "key": key,
            "label": key,
            "quantity": int(qty or 0),
            "orders": int(qty or 0),
            "net_sales": money(total),
            "discounts": money(0),
        }
        for key, qty, total in rows
    ]


async def _sales_by_seating(
    db: AsyncSession,
    *,
    dimension: str,
    branch_id: uuid.UUID | None,
    date_from: str | None,
    date_to: str | None,
    limit: int,
) -> list[dict]:
    """Sales by the section, or revenue centre, an order was seated in."""
    if dimension == "section":
        key, label = Section.id, Section.name
        joined = (
            select()
            .join(PosTable, PosTable.id == Order.table_id)
            .join(Section, Section.id == PosTable.section_id)
        )
    else:
        # Foodics models a revenue centre as a tag on the table, and so do we.
        key, label = Tag.id, Tag.name
        joined = (
            select()
            .join(PosTable, PosTable.id == Order.table_id)
            .join(Tag, Tag.id == PosTable.revenue_center_tag_id)
        )

    stmt = (
        _scope(
            select(
                label.label("key"),
                func.count(func.distinct(Order.id)),
                func.coalesce(func.sum(Order.total), 0),
                func.coalesce(func.sum(Order.discount_amount), 0),
            ),
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
        )
        .select_from(Order)
        .join(PosTable, PosTable.id == Order.table_id)
        .join(
            Section if dimension == "section" else Tag,
            (Section.id == PosTable.section_id)
            if dimension == "section"
            else (Tag.id == PosTable.revenue_center_tag_id),
        )
        .where(_COMPLETED_SALE)
        .group_by(label)
        .order_by(func.coalesce(func.sum(Order.total), 0).desc())
        .limit(limit)
    )
    del key, joined
    rows = (await db.execute(stmt)).all()
    return [
        {
            "key": k or "unknown",
            "label": k or "Unknown",
            "orders": int(c or 0),
            "net_sales": money(t),
            "discounts": money(d),
        }
        for k, c, t, d in rows
    ]


async def _sales_by_tag(
    db: AsyncSession,
    *,
    dimension: str,
    branch_id: uuid.UUID | None,
    date_from: str | None,
    date_to: str | None,
    limit: int,
) -> list[dict]:
    """
    Sales grouped by a tag on the product, or on the order itself.

    Product tags sum the lines carrying them; order tags sum whole orders. A
    product tag must not claim the whole check — a "vegan" tag on one slice
    says nothing about the coffee next to it.
    """
    if dimension == "branch_tag":
        stmt = (
            _scope(
                select(
                    Tag.name.label("key"),
                    func.count(func.distinct(Order.id)),
                    func.coalesce(func.sum(Order.total), 0),
                ),
                branch_id=branch_id,
                date_from=date_from,
                date_to=date_to,
            )
            .select_from(Order)
            .join(
                TaggedEntity,
                (TaggedEntity.entity_id == Order.branch_id)
                & (TaggedEntity.entity_type == "branch"),
            )
            .join(Tag, Tag.id == TaggedEntity.tag_id)
        )
    elif dimension == "product_tag":
        stmt = (
            _scope(
                select(
                    Tag.name.label("key"),
                    func.count(func.distinct(Order.id)),
                    func.coalesce(func.sum(OrderItem.total_price), 0),
                ),
                branch_id=branch_id,
                date_from=date_from,
                date_to=date_to,
            )
            .select_from(Order)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .join(
                TaggedEntity,
                (TaggedEntity.entity_id == OrderItem.product_id)
                & (TaggedEntity.entity_type == "product"),
            )
            .join(Tag, Tag.id == TaggedEntity.tag_id)
        )
    else:
        stmt = (
            _scope(
                select(
                    Tag.name.label("key"),
                    func.count(func.distinct(Order.id)),
                    func.coalesce(func.sum(Order.total), 0),
                ),
                branch_id=branch_id,
                date_from=date_from,
                date_to=date_to,
            )
            .select_from(Order)
            .join(
                TaggedEntity,
                (TaggedEntity.entity_id == Order.id)
                & (TaggedEntity.entity_type == "order"),
            )
            .join(Tag, Tag.id == TaggedEntity.tag_id)
        )

    stmt = (
        stmt.where(_COMPLETED_SALE)
        .group_by(Tag.name)
        .order_by(func.count(func.distinct(Order.id)).desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "key": k,
            "label": k,
            "orders": int(c or 0),
            "net_sales": money(t),
            "discounts": money(0),
        }
        for k, c, t in rows
    ]


async def _sales_by_delivery_zone(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID | None,
    date_from: str | None,
    date_to: str | None,
    limit: int,
) -> list[dict]:
    """
    Delivery sales grouped by zone, with the fees collected.

    Only delivery orders count. A takeaway has no zone, and including it
    would drop every counter sale into an "Unzoned" bucket that swamps the
    real ones.

    Grouped by the zone that actually priced the order, snapshotted on the
    delivery record. That used to be the customer's self-declared emirate,
    which made this report a summary of what people typed rather than of where
    the cakes went.
    """
    zone = func.coalesce(OrderDelivery.zone_name, "unzoned")
    stmt = (
        _scope(
            select(
                zone.label("key"),
                func.count(Order.id),
                func.coalesce(func.sum(Order.total), 0),
                func.coalesce(func.sum(Order.delivery_fee), 0),
            ),
            branch_id=branch_id,
            date_from=date_from,
            date_to=date_to,
        )
        .select_from(Order)
        .outerjoin(OrderDelivery, OrderDelivery.order_id == Order.id)
        .where(_COMPLETED_SALE, Order.order_type == "delivery")
        .group_by(zone)
        .order_by(func.coalesce(func.sum(Order.total), 0).desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "key": key,
            "label": key,
            "orders": int(count or 0),
            "net_sales": money(total),
            "discounts": money(0),
            "delivery_fees": money(fees),
        }
        for key, count, total, fees in rows
    ]
