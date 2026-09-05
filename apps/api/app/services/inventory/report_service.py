"""Branch-configured shift inventory reports and atomic reconciliation posting."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.money import money, quantity, unit_cost
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
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
    ShiftInventoryReportLine,
    ShiftInventoryReportStatusEnum,
)
from app.models.till import Till, TillStatusEnum
from app.models.user import User
from app.services.inventory import (
    inventory_service,
    source_event_service,
    transfer_service,
)


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


async def upsert_template(
    db: AsyncSession,
    *,
    template: InventoryReportTemplate | None,
    data,
) -> InventoryReportTemplate:
    is_new = template is None
    if await db.get(Branch, data.branch_id) is None:
        raise NotFoundError("Branch not found")
    if template is None:
        template = InventoryReportTemplate(branch_id=data.branch_id, name=data.name)
        db.add(template)
        await db.flush()
    elif template.branch_id != data.branch_id:
        raise ConflictError("A report template cannot move between branches")

    for field in (
        "name",
        "report_type",
        "cadence",
        "is_required",
        "is_active",
        "configuration",
        "approval_cost_threshold",
        "approval_variance_percent",
    ):
        setattr(template, field, getattr(data, field))
    template.version_number = 1 if is_new else int(template.version_number or 1) + 1
    if not is_new:
        await db.execute(
            delete(InventoryReportTemplateItem).where(
                InventoryReportTemplateItem.template_id == template.id
            )
        )
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


async def ensure_tasks_for_till(
    db: AsyncSession, *, till: Till
) -> list[ShiftInventoryReport]:
    # Also serializes the per-business-day unique task when two tills finish at
    # nearly the same time, and freezes every prefill at a stable ledger point.
    await source_event_service.lock_branch_inventory(db, till.branch_id)
    templates = list(
        (
            await db.execute(
                select(InventoryReportTemplate)
                .where(
                    InventoryReportTemplate.branch_id == till.branch_id,
                    InventoryReportTemplate.is_active.is_(True),
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
    line.production_consumption_quantity = moved(
        InventoryTransactionTypeEnum.CONSUMPTION_FROM_PRODUCTION.value, outward=True
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
    line.expected_quantity = expected
    line.source_summary = {
        **(line.source_summary or {}),
        "through_sequence": through_sequence,
    }


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
    for template_item in sorted(template.items, key=lambda row: row.display_order):
        item = await db.get(InventoryItem, template_item.item_id)
        if item is None:
            continue
        level = await inventory_service.level_for(db, item.id, warehouse.id)
        expected = quantity(level.quantity)
        report_line = ShiftInventoryReportLine(
            item_id=item.id,
            unit=item.ingredient_unit,
            source_summary={
                "item_name": item.name,
                "item_sku": item.sku,
                "required_input": template_item.required_input,
            },
        )
        _apply_source_columns(
            report_line,
            expected=expected,
            item_movements=movements.get(item.id, {}),
            through_sequence=base_sequence,
        )
        report.lines.append(report_line)
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
        )
        line.source_summary = {
            **line.source_summary,
            "moved_since_prefill": line.item_id in moved,
        }
    report.base_posting_sequence = latest
    await db.flush()
    return report


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
    updates = {line.item_id: line for line in data.lines}
    unknown = set(updates) - {line.item_id for line in report.lines}
    if unknown:
        raise BadRequestError("Report contains items outside its template")
    warehouse = await inventory_service.default_warehouse(db, report.branch_id)
    for line in report.lines:
        update = updates.get(line.item_id)
        if update is None:
            continue
        line.entered_quantity = update.entered_quantity
        line.confirmed = update.confirmed
        line.override_reason = update.override_reason
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
    report.notes = data.notes
    report.status = ShiftInventoryReportStatusEnum.DRAFT.value
    report.last_save_idempotency_key = data.idempotency_key
    report.last_save_payload_hash = payload_hash
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
    latest = await current_sequence(db, report.branch_id)
    if latest != report.base_posting_sequence:
        raise ConflictError(
            "Inventory moved since this report was refreshed; refresh and reconfirm"
        )
    if any(
        not line.confirmed or line.entered_quantity is None for line in report.lines
    ):
        raise BadRequestError("Every report line must be actively confirmed")

    for line in report.lines:
        required_input = (line.source_summary or {}).get(
            "required_input", "physical_count"
        )
        needs_reason = (
            required_input == "physical_count"
            and Decimal(str(line.variance_quantity or 0)) != 0
        ) or (
            required_input in {"internal_use", "waste"}
            and Decimal(str(line.entered_quantity or 0)) > 0
        )
        if needs_reason and not (line.override_reason or "").strip():
            raise BadRequestError(
                "A reason is required for variances, internal use and waste"
            )

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
    return report


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
    if report_type == InventoryReportTypeEnum.PRODUCTION.value:
        branch = await db.get(Branch, report.branch_id)
        if branch is None:
            raise NotFoundError("Branch not found")
        first_transaction_id = None
        for report_line in report.lines:
            entered = quantity(report_line.entered_quantity or 0)
            if entered == 0:
                continue
            production, _ = await transfer_service.produce(
                db,
                branch=branch,
                user=user,
                item_id=report_line.item_id,
                quantity=entered,
                warehouse_id=warehouse.id,
                notes=f"Production report {report.id}",
            )
            first_transaction_id = first_transaction_id or production.id
        report.transaction_id = first_transaction_id
        report.status = ShiftInventoryReportStatusEnum.POSTED.value
        report.approved_by = report.approved_by or user.id
        report.approved_at = report.approved_at or utcnow()
        await db.flush()
        return report
    inputs_to_type = {
        "physical_count": (
            InventoryTransactionTypeEnum.OPENING_BALANCE.value
            if report.template_snapshot.get("opening_count")
            else InventoryTransactionTypeEnum.INVENTORY_COUNT.value
        ),
        "internal_use": InventoryTransactionTypeEnum.INTERNAL_USE.value,
        "waste": InventoryTransactionTypeEnum.WASTE_FROM_PRODUCTION.value,
        "receipt": InventoryTransactionTypeEnum.PURCHASING.value,
    }
    grouped: dict[str, list[ShiftInventoryReportLine]] = {}
    for report_line in report.lines:
        required_input = (report_line.source_summary or {}).get(
            "required_input", "physical_count"
        )
        grouped.setdefault(required_input, []).append(report_line)

    first_transaction: InventoryTransaction | None = None
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
