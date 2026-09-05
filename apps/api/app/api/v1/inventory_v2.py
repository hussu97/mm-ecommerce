"""Versioned recipe, immutable-ledger, and shift reconciliation APIs."""

from __future__ import annotations

import csv
import io
import uuid

import openpyxl
from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_db
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.core.permissions import require
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.inventory import InventoryItem, InventoryTransaction
from app.models.inventory_v2 import (
    BranchInventorySettings,
    InventoryReportTemplate,
    InventorySourceEvent,
    ShiftInventoryReport,
    ShiftInventoryReportStatusEnum,
)
from app.models.order import Order
from app.models.role import UserBranch
from app.models.till import Till
from app.models.user import User
from app.schemas.inventory import InventoryTransactionResponse
from app.schemas.inventory_v2 import (
    BranchInventorySettingsResponse,
    BranchInventorySettingsUpdate,
    OrderInventoryConsumptionResponse,
    OrderInventoryReturnRequest,
    ProjectionDriftResponse,
    RecipeDraftRequest,
    RecipeExpansionRequest,
    RecipeExpansionResponse,
    RecipeVersionResponse,
    ReportActionRequest,
    ReportSaveRequest,
    ReportTemplateResponse,
    ReportTemplateUpsert,
    ReverseTransactionRequest,
    ShiftReportResponse,
    StockAuditPreviewResponse,
    StockAuditRequest,
    StockAuditRowInput,
    VersionedRecipeResponse,
)
from app.services.inventory import (
    ledger_service,
    recipe_service,
    report_service,
    source_event_service,
)

control_router = APIRouter()
pos_inventory_router = APIRouter()
order_inventory_router = APIRouter()


async def _assert_branch_access(
    db: AsyncSession, user: User, branch_id: uuid.UUID
) -> None:
    """Apply the same explicit staff/branch boundary to admin and POS routes."""
    if user.is_admin or (user.role and user.role.is_super_admin):
        return
    allowed = await db.scalar(
        select(UserBranch.id).where(
            UserBranch.user_id == user.id,
            UserBranch.branch_id == branch_id,
        )
    )
    if allowed is None:
        raise ForbiddenError("You are not assigned to this branch")


def _branch_ids_for(user: User):
    return select(UserBranch.branch_id).where(UserBranch.user_id == user.id)


@control_router.get(
    "/recipes-v2/{owner_kind}/{owner_id}", response_model=VersionedRecipeResponse
)
async def get_versioned_recipe(
    owner_kind: str,
    owner_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("catalogue.recipes.read")),
):
    recipe = await recipe_service.get_recipe(db, owner_kind, owner_id)
    if recipe is None:
        raise NotFoundError("Recipe not found")
    recipe.versions.sort(key=lambda row: row.version_number, reverse=True)
    return recipe


@control_router.put(
    "/recipes-v2/{owner_kind}/{owner_id}/draft",
    response_model=RecipeVersionResponse,
)
async def put_recipe_draft(
    owner_kind: str,
    owner_id: uuid.UUID,
    data: RecipeDraftRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("catalogue.recipes.manage")),
):
    return await recipe_service.create_draft(
        db,
        kind=owner_kind,
        owner_id=owner_id,
        lines=[
            recipe_service.RecipeLineInput(**line.model_dump())
            for line in data.ingredients
        ],
        source=data.source,
        source_payload_hash=data.source_payload_hash,
        source_metadata=data.source_metadata,
    )


@control_router.post(
    "/recipes-v2/versions/{version_id}/activate",
    response_model=RecipeVersionResponse,
)
async def activate_recipe_version(
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("catalogue.recipes.manage")),
):
    return await recipe_service.activate(db, version_id=version_id, user_id=user.id)


@control_router.post(
    "/recipes-v2/{owner_kind}/{owner_id}/expand",
    response_model=RecipeExpansionResponse,
)
async def expand_recipe(
    owner_kind: str,
    owner_id: uuid.UUID,
    data: RecipeExpansionRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("catalogue.recipes.read")),
):
    expanded, version_ids = await recipe_service.expand_owner(
        db,
        kind=owner_kind,
        owner_id=owner_id,
        multiplier=data.multiplier,
        order_type=data.order_type,
    )
    return {
        "lines": [expanded[key].as_snapshot() for key in sorted(expanded, key=str)],
        "recipe_version_ids": sorted(version_ids, key=str),
    }


@control_router.get(
    "/branch-settings/{branch_id}", response_model=BranchInventorySettingsResponse
)
async def get_branch_inventory_settings(
    branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.read")),
):
    await _assert_branch_access(db, user, branch_id)
    settings = (
        await db.execute(
            select(BranchInventorySettings).where(
                BranchInventorySettings.branch_id == branch_id
            )
        )
    ).scalar_one_or_none()
    if settings is None:
        if await db.get(Branch, branch_id) is None:
            raise NotFoundError("Branch not found")
        settings = BranchInventorySettings(branch_id=branch_id)
        db.add(settings)
        await db.flush()
    return settings


@control_router.patch(
    "/branch-settings/{branch_id}", response_model=BranchInventorySettingsResponse
)
async def update_branch_inventory_settings(
    branch_id: uuid.UUID,
    data: BranchInventorySettingsUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.manage")),
):
    await _assert_branch_access(db, user, branch_id)
    settings = (
        await db.execute(
            select(BranchInventorySettings).where(
                BranchInventorySettings.branch_id == branch_id
            )
        )
    ).scalar_one_or_none()
    if settings is None:
        settings = BranchInventorySettings(branch_id=branch_id)
        db.add(settings)
        await db.flush()
    if (
        data.inventory_enabled is True or data.sales_consumption_enabled is True
    ) and settings.go_live_at is None:
        raise ConflictError(
            "A manager-approved opening count is required before inventory can be enabled"
        )
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(settings, key, value)
    await db.flush()
    return settings


@control_router.post(
    "/transactions/{transaction_id}/reverse",
    response_model=InventoryTransactionResponse,
)
async def reverse_inventory_transaction(
    transaction_id: uuid.UUID,
    data: ReverseTransactionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.adjustments.manage")),
):
    return await ledger_service.reverse_transaction(
        db, transaction_id=transaction_id, user=user, reason=data.reason
    )


@control_router.get("/projection-drift", response_model=list[ProjectionDriftResponse])
async def preview_projection_drift(
    branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.ledger.read")),
):
    await _assert_branch_access(db, user, branch_id)
    return [
        row.as_dict()
        for row in await ledger_service.reconcile_levels(db, branch_id=branch_id)
    ]


@control_router.post(
    "/projection-rebuild", response_model=list[ProjectionDriftResponse]
)
async def rebuild_projection(
    branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.projection.rebuild")),
):
    await _assert_branch_access(db, user, branch_id)
    return [
        row.as_dict()
        for row in await ledger_service.reconcile_levels(
            db, branch_id=branch_id, apply=True
        )
    ]


@control_router.post("/stock-audits/preview", response_model=StockAuditPreviewResponse)
async def preview_stock_audit(
    data: StockAuditRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.ledger.read")),
):
    await _assert_branch_access(db, user, data.branch_id)
    return await ledger_service.preview_stock_audit(
        db, branch_id=data.branch_id, rows=data.rows
    )


@control_router.post(
    "/stock-audits/file-preview", response_model=StockAuditPreviewResponse
)
async def preview_stock_audit_file(
    branch_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.ledger.read")),
):
    """Normalize a CSV/XLSX count sheet before it can create ledger deltas."""
    await _assert_branch_access(db, user, branch_id)
    content = await file.read()
    if len(content) > 5_000_000:
        raise BadRequestError("Count sheet is larger than 5 MB")
    name = (file.filename or "").lower()
    if name.endswith(".xlsx"):
        workbook = openpyxl.load_workbook(
            io.BytesIO(content), read_only=True, data_only=True
        )
        sheet = workbook.active
        values = list(sheet.iter_rows(values_only=True))
        if not values:
            raise BadRequestError("Count sheet is empty")
        headers = [
            str(value or "").strip().lower().replace(" ", "_") for value in values[0]
        ]
        raw_rows = [dict(zip(headers, row, strict=False)) for row in values[1:]]
    elif name.endswith(".csv") or file.content_type in {"text/csv", "application/csv"}:
        try:
            decoded = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise BadRequestError("CSV must be UTF-8 encoded") from exc
        raw_rows = list(csv.DictReader(io.StringIO(decoded)))
        raw_rows = [
            {
                str(key).strip().lower().replace(" ", "_"): value
                for key, value in row.items()
            }
            for row in raw_rows
        ]
    else:
        raise BadRequestError("Upload a .csv or .xlsx count sheet")
    if len(raw_rows) > 5_000:
        raise BadRequestError("Count sheets may contain at most 5,000 rows")
    rows: list[StockAuditRowInput] = []
    for index, row in enumerate(raw_rows, start=2):
        sku = str(row.get("sku") or "").strip()
        counted = row.get("counted_quantity")
        if not sku and (counted is None or str(counted).strip() == ""):
            continue
        try:
            rows.append(
                StockAuditRowInput(
                    sku=sku,
                    counted_quantity=counted,
                    unit=str(row.get("unit") or "ingredient").strip().lower(),
                    remark=str(row.get("remark") or "").strip() or None,
                )
            )
        except ValueError as exc:
            raise BadRequestError(f"Invalid count sheet row {index}: {exc}") from exc
    if not rows:
        raise BadRequestError("Count sheet contains no count rows")
    return await ledger_service.preview_stock_audit(db, branch_id=branch_id, rows=rows)


@control_router.post("/stock-audits/apply", response_model=StockAuditPreviewResponse)
async def apply_stock_audit(
    data: StockAuditRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.counts.approve")),
):
    await _assert_branch_access(db, user, data.branch_id)
    preview, _ = await ledger_service.apply_stock_audit(db, data=data, user=user)
    return preview


@control_router.get("/report-templates", response_model=list[ReportTemplateResponse])
async def list_report_templates(
    branch_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.read")),
):
    stmt = select(InventoryReportTemplate).options(
        selectinload(InventoryReportTemplate.items)
    )
    if branch_id:
        await _assert_branch_access(db, user, branch_id)
        stmt = stmt.where(InventoryReportTemplate.branch_id == branch_id)
    elif not (user.is_admin or (user.role and user.role.is_super_admin)):
        stmt = stmt.where(InventoryReportTemplate.branch_id.in_(_branch_ids_for(user)))
    return list(
        (await db.execute(stmt.order_by(InventoryReportTemplate.name)))
        .scalars()
        .unique()
    )


@control_router.post(
    "/report-templates",
    response_model=ReportTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_report_template(
    data: ReportTemplateUpsert,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.manage")),
):
    await _assert_branch_access(db, user, data.branch_id)
    return await report_service.upsert_template(db, template=None, data=data)


@control_router.put(
    "/report-templates/{template_id}", response_model=ReportTemplateResponse
)
async def update_report_template(
    template_id: uuid.UUID,
    data: ReportTemplateUpsert,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.manage")),
):
    template = await db.get(InventoryReportTemplate, template_id)
    if template is None:
        raise NotFoundError("Inventory report template not found")
    await _assert_branch_access(db, user, template.branch_id)
    await _assert_branch_access(db, user, data.branch_id)
    return await report_service.upsert_template(db, template=template, data=data)


@control_router.get("/shift-reports", response_model=list[ShiftReportResponse])
async def list_shift_reports(
    branch_id: uuid.UUID | None = None,
    report_status: str | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("reports.inventory")),
):
    stmt = select(ShiftInventoryReport).options(
        selectinload(ShiftInventoryReport.lines)
    )
    if branch_id:
        await _assert_branch_access(db, user, branch_id)
        stmt = stmt.where(ShiftInventoryReport.branch_id == branch_id)
    elif not (user.is_admin or (user.role and user.role.is_super_admin)):
        stmt = stmt.where(ShiftInventoryReport.branch_id.in_(_branch_ids_for(user)))
    if report_status:
        stmt = stmt.where(ShiftInventoryReport.status == report_status)
    return list(
        (await db.execute(stmt.order_by(ShiftInventoryReport.created_at.desc())))
        .scalars()
        .unique()
    )


@pos_inventory_router.get("/tasks", response_model=list[ShiftReportResponse])
async def inventory_tasks_for_till(
    till_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.read")),
):
    till = await db.get(Till, till_id)
    if till is None:
        raise NotFoundError("Till not found")
    await _assert_branch_access(db, user, till.branch_id)
    return await report_service.ensure_tasks_for_till(db, till=till)


@pos_inventory_router.post(
    "/reports/{report_id}/refresh", response_model=ShiftReportResponse
)
async def refresh_shift_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.read")),
):
    report = await report_service.load_report(db, report_id)
    await _assert_branch_access(db, user, report.branch_id)
    return await report_service.refresh_report(db, report)


@pos_inventory_router.put("/reports/{report_id}", response_model=ShiftReportResponse)
async def save_shift_report(
    report_id: uuid.UUID,
    data: ReportSaveRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.reports.submit")),
):
    report = await report_service.load_report(db, report_id)
    await _assert_branch_access(db, user, report.branch_id)
    return await report_service.save_report(db, report=report, data=data)


@pos_inventory_router.post(
    "/reports/{report_id}/submit", response_model=ShiftReportResponse
)
async def submit_shift_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.reports.submit")),
):
    report = await report_service.load_report(db, report_id)
    await _assert_branch_access(db, user, report.branch_id)
    return await report_service.submit_report(db, report=report, user=user)


@pos_inventory_router.post(
    "/reports/{report_id}/approve", response_model=ShiftReportResponse
)
async def approve_shift_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.counts.approve")),
):
    report = await report_service.load_report(db, report_id)
    await _assert_branch_access(db, user, report.branch_id)
    return await report_service.approve_report(db, report=report, user=user)


@pos_inventory_router.post(
    "/reports/{report_id}/reject", response_model=ShiftReportResponse
)
async def reject_shift_report(
    report_id: uuid.UUID,
    data: ReportActionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.counts.approve")),
):
    report = await report_service.load_report(db, report_id)
    await _assert_branch_access(db, user, report.branch_id)
    if report.status != ShiftInventoryReportStatusEnum.PENDING_APPROVAL.value:
        raise ConflictError("Only a pending report can be rejected")
    report.status = ShiftInventoryReportStatusEnum.REJECTED.value
    report.deferred_reason = data.reason
    await db.flush()
    return report


@pos_inventory_router.post(
    "/reports/{report_id}/defer", response_model=ShiftReportResponse
)
async def defer_shift_report(
    report_id: uuid.UUID,
    data: ReportActionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.reports.submit")),
):
    report = await report_service.load_report(db, report_id)
    await _assert_branch_access(db, user, report.branch_id)
    if report.status in {"posted", "pending_approval", "approved"}:
        raise ConflictError("This report can no longer be deferred")
    report.status = ShiftInventoryReportStatusEnum.DEFERRED.value
    report.deferred_reason = data.reason
    await db.flush()
    return report


@pos_inventory_router.post(
    "/reports/{report_id}/skip", response_model=ShiftReportResponse
)
async def skip_shift_report(
    report_id: uuid.UUID,
    data: ReportActionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.reports.submit")),
):
    report = await report_service.load_report(db, report_id)
    await _assert_branch_access(db, user, report.branch_id)
    if report.template_snapshot.get("is_required"):
        raise ConflictError("Required reports must be waived by a manager")
    report.status = ShiftInventoryReportStatusEnum.SKIPPED.value
    report.deferred_reason = data.reason
    await db.flush()
    return report


@pos_inventory_router.post(
    "/reports/{report_id}/waive", response_model=ShiftReportResponse
)
async def waive_shift_report(
    report_id: uuid.UUID,
    data: ReportActionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.counts.approve")),
):
    report = await report_service.load_report(db, report_id)
    await _assert_branch_access(db, user, report.branch_id)
    if report.status in {"posted", "approved"}:
        raise ConflictError("This report can no longer be waived")
    if not data.reason:
        raise BadRequestError("A waiver reason is required")
    report.status = ShiftInventoryReportStatusEnum.SKIPPED.value
    report.deferred_reason = data.reason
    report.approved_by = user.id
    report.approved_at = utcnow()
    await db.flush()
    return report


@order_inventory_router.get(
    "/{order_id}/inventory-consumption",
    response_model=OrderInventoryConsumptionResponse,
)
async def get_order_inventory_consumption(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.ledger.read")),
):
    order = await db.get(Order, order_id)
    if order is None:
        raise NotFoundError("Order not found")
    await _assert_branch_access(db, user, order.branch_id)
    events = list(
        (
            await db.execute(
                select(InventorySourceEvent)
                .where(
                    InventorySourceEvent.source_type.in_(["order", "order_return"]),
                    InventorySourceEvent.source_id == str(order.id),
                )
                .order_by(InventorySourceEvent.accepted_sequence)
            )
        )
        .scalars()
        .all()
    )
    transactions = list(
        (
            await db.execute(
                select(InventoryTransaction)
                .where(InventoryTransaction.order_id == order.id)
                .options(selectinload(InventoryTransaction.items))
                .order_by(InventoryTransaction.posting_sequence)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    item_ids = {line.item_id for row in transactions for line in row.items}
    inventory_items = {
        item.id: item
        for item in (
            (
                await db.execute(
                    select(InventoryItem).where(InventoryItem.id.in_(item_ids))
                )
            )
            .scalars()
            .all()
            if item_ids
            else []
        )
    }
    return {
        "order_id": order.id,
        "transactions": [
            {
                "id": str(row.id),
                "reference": row.reference,
                "type": row.type,
                "posting_sequence": row.posting_sequence,
                "posted_at": row.posted_at,
                "lines": [
                    {
                        "item_id": str(line.item_id),
                        "item_name": inventory_items[line.item_id].name,
                        "item_sku": inventory_items[line.item_id].sku,
                        "unit": inventory_items[line.item_id].ingredient_unit,
                        "signed_quantity": line.signed_quantity,
                        "balance_after_quantity": line.balance_after_quantity,
                        "recipe_version_id": str(line.recipe_version_id)
                        if line.recipe_version_id
                        else None,
                        "recipe_path": line.recipe_path or [],
                    }
                    for line in row.items
                ],
            }
            for row in transactions
        ],
        "source_events": [
            {
                "id": str(event.id),
                "accepted_sequence": event.accepted_sequence,
                "status": event.status,
                "error_code": event.error_code,
                "error_detail": event.error_detail,
            }
            for event in events
        ],
        "theoretical_plan": next(
            (
                event.frozen_plan
                for event in reversed(events)
                if event.source_type == "order"
            ),
            None,
        ),
        "warnings": [event.error_detail for event in events if event.error_detail],
    }


@order_inventory_router.post(
    "/{order_id}/inventory-return",
    response_model=list[InventoryTransactionResponse],
)
async def post_order_inventory_return(
    order_id: uuid.UUID,
    data: OrderInventoryReturnRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.adjustments.manage")),
):
    order = await db.get(Order, order_id)
    if order is None:
        raise NotFoundError("Order not found")
    await _assert_branch_access(db, user, order.branch_id)
    return await source_event_service.record_return(
        db,
        order=order,
        user=user,
        disposition=data.disposition,
        proportion=data.proportion,
        idempotency_key=data.idempotency_key,
        notes=data.notes,
    )
