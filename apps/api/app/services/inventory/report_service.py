"""Branch-configured shift inventory reports and atomic reconciliation posting."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.money import money, quantity, unit_cost
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.inventory import (
    InventoryCategory,
    InventoryItem,
    InventoryItemIngredient,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    TransactionStatusEnum,
)
from app.models.inventory_v2 import (
    BranchInventorySettings,
    InventoryReportCadenceEnum,
    InventoryReportTemplate,
    InventoryReportTemplateItem,
    InventoryReportTypeEnum,
    ShiftInventoryReport,
    ShiftInventoryReportComment,
    ShiftInventoryReportLine,
    ShiftInventoryReportStatusEnum,
)
from app.models.till import Till, TillStatusEnum
from app.models.user import User
from app.services import email_service
from app.services.inventory import (
    inventory_service,
    recipe_service,
    report_columns,
    source_event_service,
    transfer_service,
)

# The columns that add to a stock level and the ones that take from it, so the
# closing figure is netted the same way in one place. Opening is the base, not a
# movement; sales/production-consumption come from the ledger, the rest the shop
# may type — but the arithmetic does not care who filled a column in.
_NET_IN_COLUMNS = (
    "purchasing_quantity",
    "transfer_in_quantity",
    "production_quantity",
    # Signed: the net of the movements no other column carries. Role IN so it adds
    # to the net exactly as its sign says (a net removal is negative here).
    "adjustment_quantity",
)
_NET_OUT_COLUMNS = (
    "sales_consumption_quantity",
    "production_consumption_quantity",
    "extra_production_consumption_quantity",
    "transfer_out_quantity",
    "waste_quantity",
    "internal_use_quantity",
)

# The transaction types that have a dedicated report column. Everything else that
# moved the item in the window nets into the signed "Adjustments" column, so no
# movement is invisible and Opening + Σcolumns ties to the closing. Cost
# adjustments carry no quantity (sign 0) so they never appear here anyway.
_COLUMNED_MOVEMENT_TYPES: frozenset[str] = frozenset(
    {
        InventoryTransactionTypeEnum.PURCHASING.value,
        InventoryTransactionTypeEnum.TRANSFER_RECEIVE.value,
        InventoryTransactionTypeEnum.PRODUCTION.value,
        InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value,
        InventoryTransactionTypeEnum.CONSUMPTION_FROM_PRODUCTION.value,
        InventoryTransactionTypeEnum.EXTRA_PRODUCTION_USE.value,
        InventoryTransactionTypeEnum.TRANSFER_SEND.value,
        InventoryTransactionTypeEnum.WASTE_FROM_ORDERS.value,
        InventoryTransactionTypeEnum.WASTE_FROM_PRODUCTION.value,
        InventoryTransactionTypeEnum.INTERNAL_USE.value,
    }
)

# The non-count per-item ``required_input`` values, each mapped to the transaction
# type it posts on submit. ``physical_count`` is added at post time because it maps
# to either an opening balance or a count. The full set of supported inputs
# (``supported_report_item_inputs()``) must equal the schema Literal on
# ReportTemplateItemInput — an input the schema accepts but this cannot post 500s
# the submit; test_report_inputs_all_have_a_posting_type guards the two together.
_ITEM_INPUT_TRANSACTION_TYPES = {
    "internal_use": InventoryTransactionTypeEnum.INTERNAL_USE.value,
    "waste": InventoryTransactionTypeEnum.WASTE_FROM_PRODUCTION.value,
    "receipt": InventoryTransactionTypeEnum.PURCHASING.value,
}


def supported_report_item_inputs() -> frozenset[str]:
    """Every ``required_input`` value ``post_report`` knows how to post."""
    return frozenset({"physical_count", *_ITEM_INPUT_TRANSACTION_TYPES})


def _net_quantity(line: ShiftInventoryReportLine) -> Decimal:
    """Closing = Opening + Σ(in) − Σ(out), derived from the row's columns."""
    total = Decimal(str(line.opening_quantity or 0))
    for column in _NET_IN_COLUMNS:
        total += Decimal(str(getattr(line, column) or 0))
    for column in _NET_OUT_COLUMNS:
        total -= Decimal(str(getattr(line, column) or 0))
    return quantity(total)


def latest_active_templates(
    templates: list[InventoryReportTemplate],
) -> list[InventoryReportTemplate]:
    """Return the one current template per branch/report type for POS task creation.

    Revision rows stay visible in the control centre, but an older active row
    must never silently return after an operator deactivates the newest one.
    Selecting the latest row *before* considering ``is_active`` gives that
    explicit deactivation its intended meaning.
    """
    newest: dict[tuple[uuid.UUID, str], InventoryReportTemplate] = {}
    for template in templates:
        key = (template.branch_id, template.report_type)
        current = newest.get(key)
        if current is None or (int(template.version_number or 0), str(template.id)) > (
            int(current.version_number or 0),
            str(current.id),
        ):
            newest[key] = template
    # Ordered the way the shop fills them at close, low first — the cascade
    # depends on it (production before raw materials). Name breaks a tie.
    return sorted(
        (template for template in newest.values() if template.is_active),
        key=lambda template: (
            int(template.display_order or 0),
            template.report_type,
            template.name,
        ),
    )


async def _next_template_version(
    db: AsyncSession, *, branch_id: uuid.UUID, report_type: str
) -> int:
    """Allocate the next revision under the branch inventory transaction lock."""
    current = await db.scalar(
        select(func.max(InventoryReportTemplate.version_number)).where(
            InventoryReportTemplate.branch_id == branch_id,
            InventoryReportTemplate.report_type == report_type,
        )
    )
    return int(current or 0) + 1


async def load_report(db: AsyncSession, report_id: uuid.UUID) -> ShiftInventoryReport:
    report = (
        (
            await db.execute(
                select(ShiftInventoryReport)
                .where(ShiftInventoryReport.id == report_id)
                .options(selectinload(ShiftInventoryReport.lines))
            )
        )
        .scalars()
        .unique()
        .one_or_none()
    )
    if report is None:
        raise NotFoundError("Inventory report not found")
    return report


async def add_comment(
    db: AsyncSession,
    *,
    report: ShiftInventoryReport,
    user: User,
    body: str,
) -> ShiftInventoryReportComment:
    """Record a reviewer note on a report. The author's name is snapshotted so the
    note keeps its attribution if the user row is later removed."""
    cleaned = body.strip()
    if not cleaned:
        raise BadRequestError("A comment cannot be empty")
    comment = ShiftInventoryReportComment(
        report_id=report.id,
        author_id=user.id,
        author_name=user.display_name or user.email,
        body=cleaned,
    )
    db.add(comment)
    await db.flush()
    return comment


async def _lock_report(db: AsyncSession, report_id: uuid.UUID) -> ShiftInventoryReport:
    """Reload and lock a report so two devices cannot advance it together."""
    report = (
        (
            await db.execute(
                select(ShiftInventoryReport)
                .where(ShiftInventoryReport.id == report_id)
                .options(selectinload(ShiftInventoryReport.lines))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .unique()
        .one_or_none()
    )
    if report is None:
        raise NotFoundError("Inventory report not found")
    return report


async def current_sequence(db: AsyncSession, branch_id: uuid.UUID) -> int | None:
    return (
        await db.execute(
            select(func.max(InventoryTransaction.posting_sequence)).where(
                InventoryTransaction.branch_id == branch_id,
                InventoryTransaction.status == TransactionStatusEnum.CLOSED.value,
            )
        )
    ).scalar_one()


#: Movements a shift/count report never posts against, so their landing between a
#: refresh and a submit must not block the submit or invalidate a confirmed line.
#:
#: A physical count SETS the on-hand to the counted figure — `post_transaction`
#: re-reads the current level under the branch lock and posts `counted − current`,
#: so a sale that consumed stock in the meantime is already in that current level
#: and the count still lands on what was counted. A movement column posts its
#: `entered − prefilled` delta against a *non-sales* prefill (produce, waste,
#: transfer, internal use), which sale consumption never touches. So routine sale
#: consumption is orthogonal to everything the report posts — and it never stops
#: while a branch trades, which is why gating on it made the close count
#: unsubmittable at a working kitchen (Sharjah, 2026-09-09). Any *other* movement
#: (a manual adjustment, a transfer, a sibling production posting) does move a
#: baseline the report posts against and still blocks.
_NON_BLOCKING_MOVEMENT_TYPES = frozenset(
    {InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value}
)


async def _competing_movement_since(
    db: AsyncSession, branch_id: uuid.UUID, base_sequence: int | None
) -> bool:
    """Whether a movement that would invalidate the report's posting has landed
    since it was refreshed — everything except routine sale consumption."""
    return (
        await db.execute(
            select(func.count())
            .select_from(InventoryTransaction)
            .where(
                InventoryTransaction.branch_id == branch_id,
                InventoryTransaction.status == TransactionStatusEnum.CLOSED.value,
                InventoryTransaction.posting_sequence > (base_sequence or 0),
                InventoryTransaction.type.notin_(_NON_BLOCKING_MOVEMENT_TYPES),
            )
        )
    ).scalar_one() > 0


async def upsert_template(
    db: AsyncSession,
    *,
    template: InventoryReportTemplate | None,
    data,
) -> InventoryReportTemplate:
    if await db.get(Branch, data.branch_id) is None:
        raise NotFoundError("Branch not found")
    if template is not None:
        if template.branch_id != data.branch_id:
            raise ConflictError("A report template cannot move between branches")
        if template.report_type != data.report_type:
            raise ConflictError(
                "Create a new template instead of changing its report type"
            )
    # This also serializes adjacent template revisions.  The unique constraint
    # remains the database backstop for imports or future writers that do not
    # use this service.
    await source_event_service.lock_branch_inventory(db, data.branch_id)
    next_version = await _next_template_version(
        db, branch_id=data.branch_id, report_type=data.report_type
    )

    # PostgreSQL validates NOT NULL columns on ``flush()``, not when attributes
    # are subsequently assigned below.  A new template needs its complete
    # persisted shape before the flush that obtains its ID for template lines.
    # Keep this list shared by both the creation and update paths so an API
    # field cannot silently be initialised differently from later edits.
    template_values = {
        field: getattr(data, field)
        for field in (
            "name",
            "report_type",
            "cadence",
            "is_required",
            "is_active",
            "display_order",
            "configuration",
            "approval_cost_threshold",
            "approval_variance_percent",
        )
    }
    # Templates are append-only revisions. Existing issued reports keep their
    # original template FK and snapshot, while the POS resolver selects this
    # newest version for subsequent checklist creation.
    template = InventoryReportTemplate(
        branch_id=data.branch_id,
        version_number=next_version,
        **template_values,
    )
    db.add(template)
    await db.flush()
    seen: set[uuid.UUID] = set()
    for index, item_data in enumerate(data.items):
        if item_data.item_id in seen:
            raise BadRequestError(
                f"Inventory item {item_data.item_id} appears more than once"
            )
        seen.add(item_data.item_id)
        if await db.get(InventoryItem, item_data.item_id) is None:
            raise BadRequestError(f"Inventory item {item_data.item_id} not found")
        db.add(
            InventoryReportTemplateItem(
                template_id=template.id,
                item_id=item_data.item_id,
                display_order=item_data.display_order or index,
                required_input=item_data.required_input,
            )
        )
    await db.flush()
    return (
        (
            await db.execute(
                select(InventoryReportTemplate)
                .where(InventoryReportTemplate.id == template.id)
                .options(selectinload(InventoryReportTemplate.items))
            )
        )
        .scalars()
        .unique()
        .one()
    )


async def deactivate_template(
    db: AsyncSession, *, template: InventoryReportTemplate
) -> InventoryReportTemplate:
    """Deactivate the current revision without allowing an older revision to revive."""
    await source_event_service.lock_branch_inventory(db, template.branch_id)
    templates = list(
        (
            await db.execute(
                select(InventoryReportTemplate)
                .where(
                    InventoryReportTemplate.branch_id == template.branch_id,
                    InventoryReportTemplate.report_type == template.report_type,
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    latest_revision = max(
        templates,
        key=lambda row: (int(row.version_number or 0), str(row.id)),
    )
    if latest_revision.id != template.id:
        raise ConflictError(
            "Only the latest report-template version can be deactivated"
        )
    if template.is_active:
        template.is_active = False
        await db.flush()
    return template


async def ensure_tasks_for_till(
    db: AsyncSession, *, till: Till
) -> list[ShiftInventoryReport]:
    # Also serializes the per-business-day unique task when two tills finish at
    # nearly the same time, and freezes every prefill at a stable ledger point.
    await source_event_service.lock_branch_inventory(db, till.branch_id)
    template_revisions = list(
        (
            await db.execute(
                select(InventoryReportTemplate)
                .where(
                    InventoryReportTemplate.branch_id == till.branch_id,
                    InventoryReportTemplate.cadence.in_(
                        [
                            InventoryReportCadenceEnum.PER_TILL.value,
                            InventoryReportCadenceEnum.PER_BUSINESS_DAY.value,
                        ]
                    ),
                )
                .options(selectinload(InventoryReportTemplate.items))
                .order_by(InventoryReportTemplate.name)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    templates = latest_active_templates(template_revisions)
    reports: list[ShiftInventoryReport] = []
    has_other_open_tills = bool(
        await db.scalar(
            select(func.count())
            .select_from(Till)
            .where(
                Till.branch_id == till.branch_id,
                Till.business_date == till.business_date,
                Till.status == TillStatusEnum.OPEN.value,
                Till.id != till.id,
            )
        )
    )
    for template in templates:
        if (
            template.cadence == InventoryReportCadenceEnum.PER_BUSINESS_DAY.value
            and has_other_open_tills
        ):
            continue
        till_id = (
            None
            if template.cadence == InventoryReportCadenceEnum.PER_BUSINESS_DAY.value
            else till.id
        )
        key = f"shift-inventory:{template.id}:{till.business_date}:{till_id or 'day'}"
        report = (
            (
                await db.execute(
                    select(ShiftInventoryReport)
                    .where(ShiftInventoryReport.idempotency_key == key)
                    .options(selectinload(ShiftInventoryReport.lines))
                )
            )
            .scalars()
            .unique()
            .one_or_none()
        )
        if report is None:
            report = await _create_report(
                db, template=template, till=till, idempotency_key=key
            )
        elif (
            report.status
            in {
                ShiftInventoryReportStatusEnum.OUTSTANDING.value,
                ShiftInventoryReportStatusEnum.DRAFT.value,
                ShiftInventoryReportStatusEnum.DEFERRED.value,
                ShiftInventoryReportStatusEnum.REJECTED.value,
            }
            and await current_sequence(db, report.branch_id)
            != report.base_posting_sequence
        ):
            report = await refresh_report(db, report)
        if report.status in {
            ShiftInventoryReportStatusEnum.OUTSTANDING.value,
            ShiftInventoryReportStatusEnum.DRAFT.value,
            ShiftInventoryReportStatusEnum.DEFERRED.value,
            ShiftInventoryReportStatusEnum.REJECTED.value,
        }:
            reports.append(report)
    return reports


async def _movement_totals(
    db: AsyncSession,
    report: ShiftInventoryReport,
    *,
    through_sequence: int | None,
) -> dict[uuid.UUID, dict[str, Decimal]]:
    """Aggregate the source columns for the report's immutable time scope."""
    stmt = (
        select(InventoryTransaction, InventoryTransactionItem)
        .join(
            InventoryTransactionItem,
            InventoryTransactionItem.transaction_id == InventoryTransaction.id,
        )
        .where(
            InventoryTransaction.branch_id == report.branch_id,
            InventoryTransaction.status == TransactionStatusEnum.CLOSED.value,
        )
    )
    warehouse_id = report.template_snapshot.get("warehouse_id")
    if warehouse_id:
        stmt = stmt.where(InventoryTransaction.warehouse_id == uuid.UUID(warehouse_id))
    if through_sequence is not None:
        stmt = stmt.where(InventoryTransaction.posting_sequence <= through_sequence)
    if (
        report.template_snapshot.get("cadence")
        == InventoryReportCadenceEnum.PER_BUSINESS_DAY.value
    ):
        stmt = stmt.where(InventoryTransaction.business_date == report.business_date)
    else:
        opened_at = report.template_snapshot.get("window_opened_at")
        closed_at = report.template_snapshot.get("window_closed_at")
        if opened_at:
            stmt = stmt.where(
                InventoryTransaction.posted_at >= datetime.fromisoformat(opened_at)
            )
        if closed_at:
            stmt = stmt.where(
                InventoryTransaction.posted_at <= datetime.fromisoformat(closed_at)
            )

    movements: dict[uuid.UUID, dict[str, Decimal]] = {}
    for transaction, transaction_line in (await db.execute(stmt)).all():
        bucket = movements.setdefault(transaction_line.item_id, {})
        bucket[transaction.type] = bucket.get(transaction.type, Decimal("0")) + Decimal(
            str(transaction_line.signed_quantity or 0)
        )
    return movements


def _apply_source_columns(
    line: ShiftInventoryReportLine,
    *,
    expected: Decimal,
    item_movements: dict[str, Decimal],
    through_sequence: int | None,
    proposed_production_consumption: Decimal = Decimal("0"),
) -> None:
    net_movement = sum(item_movements.values(), Decimal("0"))

    def moved(transaction_type: str, *, outward: bool = False) -> Decimal:
        value = item_movements.get(transaction_type, Decimal("0"))
        return quantity(-value if outward else value)

    line.opening_quantity = quantity(expected - net_movement)
    line.purchasing_quantity = moved(InventoryTransactionTypeEnum.PURCHASING.value)
    line.transfer_in_quantity = moved(
        InventoryTransactionTypeEnum.TRANSFER_RECEIVE.value
    )
    line.production_quantity = moved(InventoryTransactionTypeEnum.PRODUCTION.value)
    line.sales_consumption_quantity = moved(
        InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value, outward=True
    )
    # "Used in production" = what the ledger already recorded (posted production) plus
    # what a sibling production report that has NOT posted yet will draw down once it
    # is approved (its recipe exploded against the produced-goods entered). The two are
    # disjoint by posted-status — the moment the sibling posts, its consumption is on
    # the ledger and it drops out of the proposed figure — so nothing double-counts.
    # This is what makes the raw-material consumption visible at close instead of
    # appearing only after production is approved.
    line.production_consumption_quantity = quantity(
        moved(
            InventoryTransactionTypeEnum.CONSUMPTION_FROM_PRODUCTION.value, outward=True
        )
        + proposed_production_consumption
    )
    line.extra_production_consumption_quantity = moved(
        InventoryTransactionTypeEnum.EXTRA_PRODUCTION_USE.value, outward=True
    )
    line.transfer_out_quantity = moved(
        InventoryTransactionTypeEnum.TRANSFER_SEND.value, outward=True
    )
    line.waste_quantity = quantity(
        moved(InventoryTransactionTypeEnum.WASTE_FROM_ORDERS.value, outward=True)
        + moved(InventoryTransactionTypeEnum.WASTE_FROM_PRODUCTION.value, outward=True)
    )
    line.internal_use_quantity = moved(
        InventoryTransactionTypeEnum.INTERNAL_USE.value, outward=True
    )
    # Everything else that moved this item in the window and has no column of its
    # own — a customer restock, a manual adjustment, a return to supplier. Its
    # signed net keeps Opening + Σcolumns equal to the system closing, so the
    # count's variance is measured against the whole picture, not a subset.
    line.adjustment_quantity = quantity(
        net_movement
        - sum(
            (item_movements.get(t, Decimal("0")) for t in _COLUMNED_MOVEMENT_TYPES),
            Decimal("0"),
        )
    )
    # The proposed production drawdown is not on the ledger, so the current stock
    # level (``expected``) does not reflect it yet; the anticipated closing is the
    # level minus what production will consume. Seeding the stored closing this way
    # keeps it equal to the column-derived net the register and the count recompute
    # against (opening + Σin − Σout).
    line.expected_quantity = quantity(expected - proposed_production_consumption)
    line.source_summary = {
        **(line.source_summary or {}),
        "through_sequence": through_sequence,
        # What the ledger already holds for every movement column, captured at
        # prefill time. Submit posts only what the shop *added* on top of this
        # (entered − prefilled), so confirming a column the ledger already filled
        # posts nothing and editing it posts the delta, never the whole value.
        "prefilled": {
            column: str(quantity(getattr(line, column) or 0))
            for column in (*_NET_IN_COLUMNS, *_NET_OUT_COLUMNS)
        },
    }


# The report kinds that draw raw materials down, and the sibling report kinds whose
# produced goods do the drawing.
_CONSUMES_PRODUCTION = frozenset(
    {
        InventoryReportTypeEnum.RAW_MATERIALS.value,
        InventoryReportTypeEnum.PACKAGING.value,
    }
)
_PRODUCES_GOODS = frozenset(
    {
        InventoryReportTypeEnum.PRODUCTION.value,
        InventoryReportTypeEnum.FINISHED_GOODS.value,
    }
)
# A sibling production report still heading for the ledger (its consumption will post
# on approval) but not there yet. POSTED is already on the ledger; SKIPPED/REJECTED/
# DEFERRED are not going to post this close, so their produced goods are not
# anticipated.
_UNPOSTED_STATUSES = frozenset(
    {
        ShiftInventoryReportStatusEnum.OUTSTANDING.value,
        ShiftInventoryReportStatusEnum.DRAFT.value,
        ShiftInventoryReportStatusEnum.PENDING_APPROVAL.value,
        ShiftInventoryReportStatusEnum.APPROVED.value,
    }
)


async def _proposed_production_consumption(
    db: AsyncSession, report: ShiftInventoryReport
) -> dict[uuid.UUID, Decimal]:
    """Raw material a not-yet-posted sibling production report will consume.

    The raw-material report must show the production drawdown at close, but that
    consumption only reaches the ledger when the production report is approved. For
    every production/finished-goods report in the same branch and business day that
    has not posted yet, explode the produced goods it has entered through the recipe —
    the same bill of materials ``produce()`` posts — and return the per-ingredient
    total. Scoped to *unposted* siblings, so the instant production posts, its
    consumption is on the ledger and drops out of this figure: no double count.

    The delta over each produced line's prefilled amount mirrors what ``produce()``
    actually posts (only ``entered − prefilled`` is produced), and the net consumed
    (``gross − planned_waste``) mirrors what lands as CONSUMPTION_FROM_PRODUCTION —
    the ledger column this figure augments — with the planned yield loss posting
    separately as production waste.
    """
    report_type = (report.template_snapshot or {}).get("report_type")
    if report_type not in _CONSUMES_PRODUCTION:
        return {}
    siblings = (
        (
            await db.execute(
                select(ShiftInventoryReport)
                .options(selectinload(ShiftInventoryReport.lines))
                .where(
                    ShiftInventoryReport.branch_id == report.branch_id,
                    ShiftInventoryReport.business_date == report.business_date,
                    ShiftInventoryReport.id != report.id,
                    ShiftInventoryReport.status.in_(_UNPOSTED_STATUSES),
                )
            )
        )
        .scalars()
        .unique()
        .all()
    )
    producing = [
        sibling
        for sibling in siblings
        if (sibling.template_snapshot or {}).get("report_type") in _PRODUCES_GOODS
    ]
    if not producing:
        return {}
    catalog = await recipe_service.load_active_catalog(db)
    proposed: dict[uuid.UUID, Decimal] = {}

    def add(ingredient_id: uuid.UUID, consumed: Decimal) -> None:
        if consumed <= 0:
            return
        proposed[ingredient_id] = quantity(
            proposed.get(ingredient_id, Decimal("0")) + consumed
        )

    for sibling in producing:
        for line in sibling.lines:
            summary = line.source_summary or {}
            if "production_quantity" not in summary.get("entered_columns", []):
                continue
            entered = quantity(line.production_quantity or 0)
            prefilled = quantity(
                Decimal(
                    str((summary.get("prefilled") or {}).get("production_quantity", 0))
                )
            )
            delta = quantity(entered - prefilled)
            if delta <= 0:
                continue
            try:
                expanded, _ = await recipe_service.expand_owner(
                    db,
                    kind="inventory_item",
                    owner_id=line.item_id,
                    multiplier=delta,
                    catalog=catalog,
                )
            except NotFoundError:
                expanded = None
            if expanded is not None:
                for ingredient_id, exp in expanded.items():
                    add(ingredient_id, quantity(exp.quantity - exp.planned_waste))
            else:
                legacy = (
                    (
                        await db.execute(
                            select(InventoryItemIngredient).where(
                                InventoryItemIngredient.parent_item_id == line.item_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for ingredient in legacy:
                    add(
                        ingredient.item_id,
                        quantity(Decimal(str(ingredient.quantity)) * delta),
                    )
    return proposed


async def _category_map(
    db: AsyncSession, item_ids: list[uuid.UUID]
) -> dict[uuid.UUID, InventoryCategory]:
    """The categories of the given items, keyed by category id, in one query."""
    if not item_ids:
        return {}
    category_ids = (
        (
            await db.execute(
                select(InventoryItem.category_id)
                .where(
                    InventoryItem.id.in_(item_ids),
                    InventoryItem.category_id.is_not(None),
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    if not category_ids:
        return {}
    categories = (
        (
            await db.execute(
                select(InventoryCategory).where(InventoryCategory.id.in_(category_ids))
            )
        )
        .scalars()
        .all()
    )
    return {category.id: category for category in categories}


async def _create_report(
    db: AsyncSession,
    *,
    template: InventoryReportTemplate,
    till: Till,
    idempotency_key: str,
) -> ShiftInventoryReport:
    warehouse = await inventory_service.default_warehouse(db, till.branch_id)
    base_sequence = await current_sequence(db, till.branch_id)
    report = ShiftInventoryReport(
        template_id=template.id,
        branch_id=till.branch_id,
        till_id=(
            None
            if template.cadence == InventoryReportCadenceEnum.PER_BUSINESS_DAY.value
            else till.id
        ),
        business_date=till.business_date,
        idempotency_key=idempotency_key,
        base_posting_sequence=base_sequence,
        template_snapshot={
            "name": template.name,
            "report_type": template.report_type,
            "cadence": template.cadence,
            "is_required": template.is_required,
            "version_number": template.version_number,
            "configuration": template.configuration or {},
            "opening_count": bool((template.configuration or {}).get("opening_count")),
            "approval_cost_threshold": str(template.approval_cost_threshold)
            if template.approval_cost_threshold is not None
            else None,
            "approval_variance_percent": str(template.approval_variance_percent)
            if template.approval_variance_percent is not None
            else None,
            "window_opened_at": till.opened_at.isoformat(),
            "window_closed_at": (till.closed_at or utcnow()).isoformat(),
            "warehouse_id": str(warehouse.id),
            "item_inputs": {
                str(row.item_id): row.required_input for row in template.items
            },
        },
    )
    db.add(report)
    await db.flush()
    movements = await _movement_totals(db, report, through_sequence=base_sequence)
    proposed = await _proposed_production_consumption(db, report)
    # One lookup of the item categories the report touches, so each line carries
    # its category name and the category's display order. The register groups the
    # count by category and the admin review shows the same grouping — both read
    # these off the line rather than re-fetching the catalogue.
    categories = await _category_map(db, [row.item_id for row in template.items])
    for template_item in sorted(template.items, key=lambda row: row.display_order):
        item = await db.get(InventoryItem, template_item.item_id)
        if item is None:
            continue
        level = await inventory_service.level_for(db, item.id, warehouse.id)
        expected = quantity(level.quantity)
        category = categories.get(item.category_id) if item.category_id else None
        report_line = ShiftInventoryReportLine(
            # Set the FK directly and add the line on its own, rather than
            # appending to the report's unloaded lines collection. The report was
            # just added and flushed, so touching that collection emits a lazy
            # load, which raises MissingGreenlet under asyncio and 500s the whole
            # /pos/inventory/tasks request — so no report is ever created and the
            # till-close count never appears. (Same trap as
            # menu_group_service._set_products.) load_report re-reads with the
            # lines eager-loaded.
            report_id=report.id,
            item_id=item.id,
            unit=item.ingredient_unit,
            source_summary={
                "item_name": item.name,
                "item_sku": item.sku,
                "required_input": template_item.required_input,
                "category_name": category.name if category else None,
                "category_order": int(category.display_order or 0)
                if category
                else None,
            },
        )
        _apply_source_columns(
            report_line,
            expected=expected,
            item_movements=movements.get(item.id, {}),
            through_sequence=base_sequence,
            proposed_production_consumption=proposed.get(item.id, Decimal("0")),
        )
        db.add(report_line)
    await db.flush()
    return await load_report(db, report.id)


async def refresh_report(
    db: AsyncSession, report: ShiftInventoryReport
) -> ShiftInventoryReport:
    report = await _lock_report(db, report.id)
    if report.status not in {
        ShiftInventoryReportStatusEnum.OUTSTANDING.value,
        ShiftInventoryReportStatusEnum.DRAFT.value,
        ShiftInventoryReportStatusEnum.DEFERRED.value,
        ShiftInventoryReportStatusEnum.REJECTED.value,
    }:
        raise ConflictError("Only an editable report can be refreshed")
    warehouse = await inventory_service.default_warehouse(db, report.branch_id)
    latest = await current_sequence(db, report.branch_id)
    movements = await _movement_totals(db, report, through_sequence=latest)
    proposed = await _proposed_production_consumption(db, report)
    moved = set(
        (
            await db.execute(
                select(InventoryTransactionItem.item_id)
                .join(
                    InventoryTransaction,
                    InventoryTransaction.id == InventoryTransactionItem.transaction_id,
                )
                .where(
                    InventoryTransaction.branch_id == report.branch_id,
                    InventoryTransaction.warehouse_id == warehouse.id,
                    InventoryTransaction.status == TransactionStatusEnum.CLOSED.value,
                    InventoryTransaction.posting_sequence
                    > (report.base_posting_sequence or 0),
                    InventoryTransaction.posting_sequence <= latest,
                    # A sale does not invalidate a confirmed count — the count is
                    # absolute — so it must not un-confirm the line and force a
                    # reconfirm on every refresh while the shop trades. Only a
                    # movement the report posts against does.
                    InventoryTransaction.type.notin_(_NON_BLOCKING_MOVEMENT_TYPES),
                )
                .distinct()
            )
        )
        .scalars()
        .all()
        if latest is not None
        else []
    )
    for line in report.lines:
        level = await inventory_service.level_for(db, line.item_id, warehouse.id)
        expected = quantity(level.quantity)
        if line.item_id in moved:
            line.confirmed = False
        _apply_source_columns(
            line,
            expected=expected,
            item_movements=movements.get(line.item_id, {}),
            through_sequence=latest,
            proposed_production_consumption=proposed.get(line.item_id, Decimal("0")),
        )
        line.source_summary = {
            **line.source_summary,
            "moved_since_prefill": line.item_id in moved,
            # _apply_source_columns just rewrote every movement column back to the
            # ledger's value, discarding whatever the shop had typed. The typed
            # markers must go with them — otherwise submit would re-post the
            # ledger's own movement as if the shop had entered it. The shop
            # re-enters and re-confirms after a refresh.
            "entered_columns": [],
        }
    report.base_posting_sequence = latest
    await db.flush()
    return report


async def _write_line_edits(
    db: AsyncSession, *, report: ShiftInventoryReport, data
) -> None:
    """Apply entered counts and movement columns from ``data`` onto the report's
    lines, recomputing each line's derived closing and variance.

    Shared by the register's save and the admin's pre-approval edit: the
    arithmetic that decides what the ledger will post must be identical on both
    paths, so an admin who corrects a count before approving posts exactly what a
    cashier who had typed it would have.
    """
    updates = {line.item_id: line for line in data.lines}
    unknown = set(updates) - {line.item_id for line in report.lines}
    if unknown:
        raise BadRequestError("Report contains items outside its template")
    warehouse = await inventory_service.default_warehouse(db, report.branch_id)
    report_type = report.template_snapshot.get("report_type", "")
    editable_keys = {
        column.key for column in report_columns.editable_columns(report_type)
    }
    for line in report.lines:
        update = updates.get(line.item_id)
        if update is None:
            continue
        line.entered_quantity = update.entered_quantity
        line.confirmed = update.confirmed
        line.override_reason = update.override_reason
        # The shop types the movement columns this report kind marks editable; the
        # ledger-filled columns (sales, production-consumption) and the derived
        # ends are never accepted from the client.
        for key, value in (update.movements or {}).items():
            if key not in editable_keys:
                raise BadRequestError(
                    f"Column '{key}' is not editable on a {report_type} report"
                )
            setattr(line, key, quantity(Decimal(str(value))))
        # Remember which columns the shop actually typed, so submit posts only
        # those and never re-posts a value the ledger merely prefilled (a
        # production figure already on the ledger must not be produced twice).
        if update.movements:
            already = set((line.source_summary or {}).get("entered_columns", []))
            line.source_summary = {
                **(line.source_summary or {}),
                "entered_columns": sorted(already | set(update.movements.keys())),
            }
        # Closing is derived, not typed: recompute it from the row's columns so the
        # variance below is measured against the same net the grid shows.
        line.expected_quantity = _net_quantity(line)
        if update.entered_quantity is not None:
            required_input = (line.source_summary or {}).get(
                "required_input", "physical_count"
            )
            line.variance_quantity = (
                quantity(
                    Decimal(str(update.entered_quantity))
                    - Decimal(str(line.expected_quantity))
                )
                if required_input == "physical_count"
                else Decimal("0")
            )
            item = await db.get(InventoryItem, line.item_id)
            level = (
                await inventory_service.level_for(db, line.item_id, warehouse.id)
                if item
                else None
            )
            per_ingredient_cost = (
                unit_cost(level.average_cost)
                if level and Decimal(str(level.average_cost or 0)) > 0
                else inventory_service.inventory_item_cost_for_unit(item, "ingredient")
                if item
                else Decimal("0")
            )
            line.variance_cost = money(
                abs(Decimal(str(line.variance_quantity))) * per_ingredient_cost
            )


async def save_report(
    db: AsyncSession, *, report: ShiftInventoryReport, data
) -> ShiftInventoryReport:
    report = await _lock_report(db, report.id)
    if report.status in {
        ShiftInventoryReportStatusEnum.POSTED.value,
        ShiftInventoryReportStatusEnum.APPROVED.value,
        ShiftInventoryReportStatusEnum.PENDING_APPROVAL.value,
    }:
        raise ConflictError("This inventory report is no longer editable")
    payload_hash = hashlib.sha256(
        json.dumps(
            data.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    if report.last_save_idempotency_key == data.idempotency_key:
        if report.last_save_payload_hash != payload_hash:
            raise ConflictError(
                "This save key was already used for different report data"
            )
        return report
    if data.base_posting_sequence != report.base_posting_sequence:
        raise ConflictError("Inventory moved since this report was refreshed")
    await _write_line_edits(db, report=report, data=data)
    report.notes = data.notes
    report.status = ShiftInventoryReportStatusEnum.DRAFT.value
    report.last_save_idempotency_key = data.idempotency_key
    report.last_save_payload_hash = payload_hash
    await db.flush()
    return report


async def edit_pending_report(
    db: AsyncSession, *, report: ShiftInventoryReport, data, user: User
) -> ShiftInventoryReport:
    """An approver's correction to a report that is waiting for approval.

    The register's ``save_report`` refuses a submitted report on purpose — the
    shop cannot quietly rewrite a count it has handed in. But the approver may:
    they are the person deciding whether the ledger takes these figures, so they
    must be able to fix a fat-fingered count before approving it. The report stays
    ``pending_approval`` (this is not an approval), and because approval posts
    straight off the lines, the corrected figures are exactly what the ledger
    takes.
    """
    report = await _lock_report(db, report.id)
    if report.status != ShiftInventoryReportStatusEnum.PENDING_APPROVAL.value:
        raise ConflictError("Only a report awaiting approval can be edited here")
    await _write_line_edits(db, report=report, data=data)
    if data.notes is not None:
        report.notes = data.notes
    await db.flush()
    return report


async def submit_report(
    db: AsyncSession, *, report: ShiftInventoryReport, user: User
) -> ShiftInventoryReport:
    report = await _lock_report(db, report.id)
    if report.status in {
        ShiftInventoryReportStatusEnum.POSTED.value,
        ShiftInventoryReportStatusEnum.PENDING_APPROVAL.value,
    }:
        return report
    await source_event_service.lock_branch_inventory(db, report.branch_id)
    # Block only on a movement the report actually posts against — not on the
    # routine sale consumption that never stops while a branch trades. Gating on
    # any sequence change made the close count unsubmittable at a working kitchen:
    # a sale posted between every refresh and submit. See `_NON_BLOCKING_MOVEMENT_TYPES`.
    if await _competing_movement_since(
        db, report.branch_id, report.base_posting_sequence
    ):
        raise ConflictError(
            "Inventory moved since this report was refreshed; refresh and reconfirm"
        )
    if any(
        not line.confirmed or line.entered_quantity is None for line in report.lines
    ):
        raise BadRequestError("Every report line must be actively confirmed")

    # The reason/remark is an optional note, not a gate. Requiring one on every
    # variance made a 30-item count a wall of mandatory typing and stalled the
    # close; the variance and its cost are already captured on the line and drive
    # the approval thresholds below, which is where an unexplained swing is caught.

    settings = (
        await db.execute(
            select(BranchInventorySettings).where(
                BranchInventorySettings.branch_id == report.branch_id
            )
        )
    ).scalar_one_or_none()
    snapshot_cost_threshold = report.template_snapshot.get("approval_cost_threshold")
    snapshot_percent_threshold = report.template_snapshot.get(
        "approval_variance_percent"
    )
    cost_threshold = Decimal(
        str(
            snapshot_cost_threshold
            if snapshot_cost_threshold is not None
            else settings.approval_cost_threshold
            if settings
            else 100
        )
    )
    percent_threshold = Decimal(
        str(
            snapshot_percent_threshold
            if snapshot_percent_threshold is not None
            else settings.approval_variance_percent
            if settings
            else 10
        )
    )
    requires_approval = False
    for line in report.lines:
        if (line.source_summary or {}).get(
            "required_input", "physical_count"
        ) != "physical_count":
            continue
        variance = abs(Decimal(str(line.variance_quantity or 0)))
        expected = abs(Decimal(str(line.expected_quantity or 0)))
        percent = (
            Decimal("100")
            if expected == 0 and variance
            else variance / expected * 100
            if expected
            else 0
        )
        requires_approval |= Decimal(str(line.variance_cost or 0)) >= cost_threshold
        requires_approval |= percent >= percent_threshold and variance > 0
        requires_approval |= (
            expected == 0 and Decimal(str(line.entered_quantity or 0)) != 0
        )
    requires_approval |= report.template_snapshot.get(
        "report_type"
    ) != InventoryReportTypeEnum.SPOT_CHECK.value and report.template_snapshot.get(
        "opening_count", False
    )
    report.submitted_by = user.id
    report.submitted_at = utcnow()
    report.status = (
        ShiftInventoryReportStatusEnum.PENDING_APPROVAL.value
        if requires_approval
        else ShiftInventoryReportStatusEnum.APPROVED.value
    )
    await db.flush()
    if not requires_approval:
        await post_report(db, report=report, user=user)
    await _notify_report_submitted(
        db, report=report, submitter=user, requires_approval=requires_approval
    )
    return report


async def _notify_report_submitted(
    db: AsyncSession,
    *,
    report: ShiftInventoryReport,
    submitter: User,
    requires_approval: bool,
) -> None:
    """Email the office a link to the report the moment it is submitted — whether
    it auto-posted or is now waiting for approval. ``email_service`` never raises
    and journals every attempt, so a mail outage cannot fail a till close."""
    branch = await db.get(Branch, report.branch_id)
    variance_cost = sum(
        (Decimal(str(line.variance_cost or 0)) for line in report.lines),
        Decimal("0"),
    )
    await email_service.send_inventory_report_submitted(
        report_id=str(report.id),
        report_name=str(report.template_snapshot.get("name") or "Inventory report"),
        branch_name=branch.name if branch else "—",
        business_date=report.business_date,
        submitted_by=(submitter.display_name or submitter.email),
        status=report.status,
        requires_approval=requires_approval,
        variance_cost=variance_cost,
    )


async def approve_report(
    db: AsyncSession, *, report: ShiftInventoryReport, user: User
) -> ShiftInventoryReport:
    report = await _lock_report(db, report.id)
    if report.status == ShiftInventoryReportStatusEnum.POSTED.value:
        return report
    if report.status != ShiftInventoryReportStatusEnum.PENDING_APPROVAL.value:
        raise ConflictError("Only a pending report can be approved")
    report.approved_by = user.id
    report.approved_at = utcnow()
    report.status = ShiftInventoryReportStatusEnum.APPROVED.value
    await db.flush()
    return await post_report(db, report=report, user=user)


async def reject_report(
    db: AsyncSession,
    *,
    report: ShiftInventoryReport,
    reason: str,
) -> ShiftInventoryReport:
    report = await _lock_report(db, report.id)
    if report.status != ShiftInventoryReportStatusEnum.PENDING_APPROVAL.value:
        raise ConflictError("Only a pending report can be rejected")
    report.status = ShiftInventoryReportStatusEnum.REJECTED.value
    report.deferred_reason = reason
    await db.flush()
    return report


async def defer_report(
    db: AsyncSession,
    *,
    report: ShiftInventoryReport,
    reason: str,
) -> ShiftInventoryReport:
    report = await _lock_report(db, report.id)
    if report.status in {
        ShiftInventoryReportStatusEnum.POSTED.value,
        ShiftInventoryReportStatusEnum.PENDING_APPROVAL.value,
        ShiftInventoryReportStatusEnum.APPROVED.value,
        ShiftInventoryReportStatusEnum.SKIPPED.value,
    }:
        raise ConflictError("This report can no longer be deferred")
    report.status = ShiftInventoryReportStatusEnum.DEFERRED.value
    report.deferred_reason = reason
    await db.flush()
    return report


async def skip_report(
    db: AsyncSession,
    *,
    report: ShiftInventoryReport,
    reason: str,
    manager: User | None = None,
) -> ShiftInventoryReport:
    report = await _lock_report(db, report.id)
    if report.status in {
        ShiftInventoryReportStatusEnum.POSTED.value,
        ShiftInventoryReportStatusEnum.APPROVED.value,
        ShiftInventoryReportStatusEnum.PENDING_APPROVAL.value,
        ShiftInventoryReportStatusEnum.SKIPPED.value,
    }:
        raise ConflictError("This report can no longer be skipped")
    if report.template_snapshot.get("is_required") and manager is None:
        raise ConflictError("Required reports must be waived by a manager")
    if not reason.strip():
        raise BadRequestError(
            "A waiver reason is required" if manager else "A skip reason is required"
        )
    report.status = ShiftInventoryReportStatusEnum.SKIPPED.value
    report.deferred_reason = reason
    if manager is not None:
        report.approved_by = manager.id
        report.approved_at = utcnow()
    await db.flush()
    return report


async def post_report(
    db: AsyncSession, *, report: ShiftInventoryReport, user: User
) -> ShiftInventoryReport:
    if (
        report.template_snapshot.get("report_type")
        == InventoryReportTypeEnum.SPOT_CHECK.value
    ):
        report.status = ShiftInventoryReportStatusEnum.POSTED.value
        await db.flush()
        return report
    await source_event_service.lock_branch_inventory(db, report.branch_id)
    warehouse = await inventory_service.default_warehouse(db, report.branch_id)
    report_type = report.template_snapshot.get("report_type")
    # A `production` report is an alias of the finished-goods sheet (see
    # report_columns._COLUMNS): the entered "Produced" column posts through
    # produce() below, and the physical count then trues the row up. The legacy
    # early-return that produced the *physical closing count* instead of the
    # Produced column inflated stock by the whole count and never posted the
    # count as a reconciliation — the generic path is now the only production
    # posting route.
    first_transaction: InventoryTransaction | None = None

    # The entered movement columns post first — each changes the level the physical
    # count then trues up against. Production goes through produce() so it also
    # draws down the raw materials its recipe names (which is what makes the next
    # report's raw-material consumption appear); the rest are plain movements whose
    # type carries the sign, so a positive magnitude is all the shop ever types.
    branch: Branch | None = None
    for column in report_columns.editable_columns(report_type):
        # Only a column the shop actually typed posts, and only the amount it typed
        # *beyond* what the ledger had already prefilled for it — the prefilled part
        # is on the ledger and must not be posted again (a prefilled production
        # figure would otherwise be produced twice). A non-positive delta means the
        # shop confirmed the prefill unchanged (or lowered it), so nothing posts.
        valued = []
        for report_line in report.lines:
            summary = report_line.source_summary or {}
            if column.key not in summary.get("entered_columns", []):
                continue
            entered_value = quantity(getattr(report_line, column.key) or 0)
            prefilled_value = quantity(
                Decimal(str((summary.get("prefilled") or {}).get(column.key, 0)))
            )
            delta = quantity(entered_value - prefilled_value)
            if delta <= 0:
                continue
            valued.append((report_line, delta))
        if not valued:
            continue
        if column.posts == InventoryTransactionTypeEnum.PRODUCTION.value:
            branch = branch or await db.get(Branch, report.branch_id)
            if branch is None:
                raise NotFoundError("Branch not found")
            for report_line, value in valued:
                produced, _ = await transfer_service.produce(
                    db,
                    branch=branch,
                    user=user,
                    item_id=report_line.item_id,
                    quantity=value,
                    warehouse_id=warehouse.id,
                    notes=f"Shift report {report.id} · {column.key}",
                )
                first_transaction = first_transaction or produced
            continue
        transaction = InventoryTransaction(
            reference=await inventory_service.next_reference(db, column.posts),
            type=column.posts,
            status=TransactionStatusEnum.DRAFT.value,
            branch_id=report.branch_id,
            warehouse_id=warehouse.id,
            business_date=report.business_date,
            creator_id=user.id,
            source_type="shift_inventory_report",
            source_id=str(report.id),
            idempotency_key=f"shift-report:{report.id}:{column.key}",
            notes=report.notes,
            items=[],
        )
        db.add(transaction)
        await db.flush()
        for report_line, value in valued:
            item = await db.get(InventoryItem, report_line.item_id)
            if item is None:
                raise BadRequestError(f"Inventory item {report_line.item_id} not found")
            level = await inventory_service.level_for(db, item.id, warehouse.id)
            current_cost = unit_cost(level.average_cost)
            if current_cost == 0:
                current_cost = inventory_service.inventory_item_cost_for_unit(
                    item, "ingredient"
                )
            transaction.items.append(
                InventoryTransactionItem(
                    item_id=item.id,
                    quantity=value,
                    unit="ingredient",
                    conversion_factor=Decimal("1"),
                    unit_cost=current_cost,
                )
            )
        await db.flush()
        await inventory_service.post_transaction(db, transaction=transaction, user=user)
        first_transaction = first_transaction or transaction

    # Then the physical count trues each row up to what was counted, per the input
    # its template item declares (physical_count for a count; the movement inputs
    # remain for any legacy template that drove a single column through the count).
    inputs_to_type = {
        "physical_count": (
            InventoryTransactionTypeEnum.OPENING_BALANCE.value
            if report.template_snapshot.get("opening_count")
            else InventoryTransactionTypeEnum.INVENTORY_COUNT.value
        ),
        **_ITEM_INPUT_TRANSACTION_TYPES,
    }
    grouped: dict[str, list[ShiftInventoryReportLine]] = {}
    for report_line in report.lines:
        required_input = (report_line.source_summary or {}).get(
            "required_input", "physical_count"
        )
        grouped.setdefault(required_input, []).append(report_line)

    for required_input, report_lines in grouped.items():
        movement_type = inputs_to_type.get(required_input)
        if movement_type is None:
            raise BadRequestError(f"Unsupported report input '{required_input}'")
        transaction = InventoryTransaction(
            reference=await inventory_service.next_reference(db, movement_type),
            type=movement_type,
            status=TransactionStatusEnum.DRAFT.value,
            branch_id=report.branch_id,
            warehouse_id=warehouse.id,
            business_date=report.business_date,
            creator_id=user.id,
            source_type="shift_inventory_report",
            source_id=str(report.id),
            idempotency_key=f"shift-report:{report.id}:{required_input}",
            notes=report.notes,
            items=[],
        )
        db.add(transaction)
        await db.flush()
        for report_line in report_lines:
            item = await db.get(InventoryItem, report_line.item_id)
            if item is None:
                raise BadRequestError(f"Inventory item {report_line.item_id} not found")
            entered = quantity(report_line.entered_quantity or 0)
            level = await inventory_service.level_for(db, item.id, warehouse.id)
            current_cost = unit_cost(level.average_cost)
            if (
                required_input == "receipt"
                or report.template_snapshot.get("opening_count")
                or current_cost == 0
            ):
                current_cost = inventory_service.inventory_item_cost_for_unit(
                    item, "ingredient"
                )
            transaction.items.append(
                InventoryTransactionItem(
                    item_id=item.id,
                    quantity=entered,
                    unit="ingredient",
                    conversion_factor=Decimal("1"),
                    unit_cost=current_cost,
                    expected_quantity=(
                        report_line.expected_quantity
                        if required_input == "physical_count"
                        else None
                    ),
                )
            )
        await db.flush()
        await inventory_service.post_transaction(db, transaction=transaction, user=user)
        first_transaction = first_transaction or transaction
    report.transaction_id = first_transaction.id if first_transaction else None
    report.status = ShiftInventoryReportStatusEnum.POSTED.value
    report.approved_by = report.approved_by or user.id
    report.approved_at = report.approved_at or utcnow()
    if report.template_snapshot.get("opening_count"):
        settings = (
            await db.execute(
                select(BranchInventorySettings).where(
                    BranchInventorySettings.branch_id == report.branch_id
                )
            )
        ).scalar_one_or_none()
        if settings is None:
            settings = BranchInventorySettings(branch_id=report.branch_id)
            db.add(settings)
        settings.go_live_sequence = (
            first_transaction.posting_sequence if first_transaction else None
        )
        settings.go_live_at = utcnow()
    await db.flush()
    return report
