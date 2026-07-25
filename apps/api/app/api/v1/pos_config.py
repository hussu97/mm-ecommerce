"""
Admin CRUD for the small POS configuration entities:
taxes, tax groups, payment methods, charges, reasons, tags and kitchen flows.

These all share the same shape, so a router factory builds the five standard
endpoints and only the genuinely bespoke bits (tax-group membership, kitchen-flow
category routing) are written out.
"""

# NOTE: deliberately no `from __future__ import annotations` here.
# `build_crud_router` puts the schema classes it is given directly into endpoint
# signatures (`data: create_schema`). With PEP 563 those annotations become the
# string "create_schema", which FastAPI then fails to resolve against module
# globals because it is a factory local. Eager evaluation keeps them real classes.

import uuid
from decimal import Decimal
from typing import Any, Sequence

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_admin_user, get_current_active_user, get_db
from app.core.exceptions import BadRequestError, ConflictError
from app.models import (
    Charge,
    KitchenFlow,
    KitchenFlowCategory,
    PaymentMethod,
    Reason,
    Course,
    Tag,
    TaggedEntity,
    Tax,
    TaxGroup,
    TaxGroupTax,
)
from app.models.base import Base
from app.models.user import User
from app.schemas.pos import (
    ChargeCreate,
    ChargeResponse,
    ChargeUpdate,
    KitchenFlowCreate,
    KitchenFlowResponse,
    KitchenFlowUpdate,
    PaymentMethodCreate,
    PaymentMethodResponse,
    PaymentMethodUpdate,
    ReasonCreate,
    ReasonResponse,
    ReasonUpdate,
    CourseCreate,
    CourseResponse,
    CourseUpdate,
    TagAssignment,
    TagCreate,
    TagResponse,
    TagUpdate,
    TaxCreate,
    TaxGroupCreate,
    TaxGroupResponse,
    TaxGroupUpdate,
    TaxResponse,
    TaxUpdate,
)
from app.services import audit_service, crud_service


def build_crud_router(
    *,
    model: type[Base],
    create_schema: type[BaseModel],
    update_schema: type[BaseModel],
    response_schema: type[BaseModel],
    entity_type: str,
    label_field: str = "name",
    filter_builder: Any = None,
) -> APIRouter:
    """Five standard endpoints (list, create, read, update, delete) for `model`."""

    router = APIRouter()

    def _label(entity: Any) -> str:
        return str(getattr(entity, label_field, entity.id))

    # Reads are open to any signed-in user: a POS terminal has to load payment
    # methods, taxes, charges and reasons at launch, and a cashier is staff with
    # a role rather than a console admin. Writes stay admin-only below.
    @router.get("", response_model=list[response_schema])  # type: ignore[valid-type]
    async def list_items(
        include_deleted: bool = False,
        include_inactive: bool = True,
        type: str | None = Query(None, description="Filter by type where supported"),
        db: AsyncSession = Depends(get_db),
        _: User = Depends(get_current_active_user),
    ):
        filters: list[Any] = []
        if type is not None and hasattr(model, "type"):
            filters.append(model.type == type)  # type: ignore[attr-defined]
        if filter_builder is not None:
            filters.extend(filter_builder())
        return await crud_service.list_all(
            db,
            model,
            include_deleted=include_deleted,
            include_inactive=include_inactive,
            filters=filters,
        )

    @router.post(
        "", response_model=response_schema, status_code=status.HTTP_201_CREATED
    )  # type: ignore[valid-type]
    async def create_item(
        request: Request,
        data: create_schema,  # type: ignore[valid-type]
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_admin_user),
    ):
        entity = await crud_service.create(db, model, data)
        await audit_service.log_action(
            db,
            action="CREATE",
            entity_type=entity_type,
            entity_id=str(entity.id),
            entity_label=_label(entity),
            admin=admin,
            changes={"created": data.model_dump(mode="json")},
            request=request,
        )
        return entity

    @router.get("/{item_id}", response_model=response_schema)  # type: ignore[valid-type]
    async def get_item(
        item_id: uuid.UUID,
        db: AsyncSession = Depends(get_db),
        _: User = Depends(get_current_active_user),
    ):
        return await crud_service.get_or_404(db, model, item_id, include_deleted=True)

    @router.put("/{item_id}", response_model=response_schema)  # type: ignore[valid-type]
    async def update_item(
        request: Request,
        item_id: uuid.UUID,
        data: update_schema,  # type: ignore[valid-type]
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_admin_user),
    ):
        entity = await crud_service.get_or_404(db, model, item_id)
        entity = await crud_service.update(db, entity, data)
        await audit_service.log_action(
            db,
            action="UPDATE",
            entity_type=entity_type,
            entity_id=str(item_id),
            entity_label=_label(entity),
            admin=admin,
            changes={"data": data.model_dump(mode="json", exclude_unset=True)},
            request=request,
        )
        return entity

    @router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_item(
        request: Request,
        item_id: uuid.UUID,
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_admin_user),
    ):
        entity = await crud_service.get_or_404(db, model, item_id)
        label = _label(entity)
        await crud_service.soft_delete(db, entity)
        await audit_service.log_action(
            db,
            action="DELETE",
            entity_type=entity_type,
            entity_id=str(item_id),
            entity_label=label,
            admin=admin,
            changes={"deleted_id": str(item_id)},
            request=request,
        )

    @router.post("/{item_id}/restore", response_model=response_schema)  # type: ignore[valid-type]
    async def restore_item(
        request: Request,
        item_id: uuid.UUID,
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_admin_user),
    ):
        entity = await crud_service.get_or_404(db, model, item_id, include_deleted=True)
        entity = await crud_service.restore(db, entity)
        await audit_service.log_action(
            db,
            action="UPDATE",
            entity_type=entity_type,
            entity_id=str(item_id),
            entity_label=_label(entity),
            admin=admin,
            changes={"restored": True},
            request=request,
        )
        return entity

    return router


# ─── Simple entities ──────────────────────────────────────────────────────────

taxes_router = build_crud_router(
    model=Tax,
    create_schema=TaxCreate,
    update_schema=TaxUpdate,
    response_schema=TaxResponse,
    entity_type="tax",
)

payment_methods_router = build_crud_router(
    model=PaymentMethod,
    create_schema=PaymentMethodCreate,
    update_schema=PaymentMethodUpdate,
    response_schema=PaymentMethodResponse,
    entity_type="payment_method",
)

charges_router = build_crud_router(
    model=Charge,
    create_schema=ChargeCreate,
    update_schema=ChargeUpdate,
    response_schema=ChargeResponse,
    entity_type="charge",
)

reasons_router = build_crud_router(
    model=Reason,
    create_schema=ReasonCreate,
    update_schema=ReasonUpdate,
    response_schema=ReasonResponse,
    entity_type="reason",
)

courses_router = build_crud_router(
    model=Course,
    create_schema=CourseCreate,
    update_schema=CourseUpdate,
    response_schema=CourseResponse,
    entity_type="course",
)

tags_router = build_crud_router(
    model=Tag,
    create_schema=TagCreate,
    update_schema=TagUpdate,
    response_schema=TagResponse,
    entity_type="tag",
)


# ─── Tag assignment ───────────────────────────────────────────────────────────


async def _entity_tags(
    db: AsyncSession, entity_type: str, entity_id: uuid.UUID
) -> list[Tag]:
    stmt = (
        select(Tag)
        .join(TaggedEntity, TaggedEntity.tag_id == Tag.id)
        .where(
            TaggedEntity.entity_type == entity_type,
            TaggedEntity.entity_id == entity_id,
            Tag.deleted_at.is_(None),
        )
        .order_by(Tag.name)
    )
    return list((await db.execute(stmt)).scalars().all())


@tags_router.get(
    "/assigned/{entity_type}/{entity_id}", response_model=list[TagResponse]
)
async def list_entity_tags(
    entity_type: str,
    entity_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    """Tags currently attached to any taggable entity."""
    return await _entity_tags(db, entity_type, entity_id)


@tags_router.put(
    "/assigned/{entity_type}/{entity_id}", response_model=list[TagResponse]
)
async def set_entity_tags(
    entity_type: str,
    entity_id: uuid.UUID,
    data: TagAssignment,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    """Replace the full tag set for an entity."""
    await db.execute(
        delete(TaggedEntity).where(
            TaggedEntity.entity_type == entity_type,
            TaggedEntity.entity_id == entity_id,
        )
    )
    for tag_id in dict.fromkeys(data.tag_ids):
        tag = await db.get(Tag, tag_id)
        if tag is None or tag.deleted_at is not None:
            raise BadRequestError(f"Tag {tag_id} does not exist")
        db.add(
            TaggedEntity(tag_id=tag_id, entity_type=entity_type, entity_id=entity_id)
        )
    await db.flush()
    return await _entity_tags(db, entity_type, entity_id)


# ─── Tax groups (membership needs bespoke handling) ───────────────────────────

tax_groups_router = APIRouter()


def _tax_group_response(group: TaxGroup) -> TaxGroupResponse:
    payload = TaxGroupResponse.model_validate(group)
    payload.taxes = [
        TaxResponse.model_validate(link.tax) for link in group.taxes if link.tax
    ]
    payload.combined_rate = sum((t.rate for t in payload.taxes), start=Decimal("0"))
    return payload


async def _load_tax_group(db: AsyncSession, group_id: uuid.UUID) -> TaxGroup:
    return await crud_service.get_or_404(db, TaxGroup, group_id, include_deleted=True)


async def _sync_group_taxes(
    db: AsyncSession, group: TaxGroup, tax_ids: Sequence[uuid.UUID]
) -> None:
    await db.execute(delete(TaxGroupTax).where(TaxGroupTax.tax_group_id == group.id))
    for tax_id in dict.fromkeys(tax_ids):
        found = await db.get(Tax, tax_id)
        if found is None or found.deleted_at is not None:
            raise BadRequestError(f"Tax {tax_id} does not exist")
        db.add(TaxGroupTax(tax_group_id=group.id, tax_id=tax_id))
    await db.flush()


@tax_groups_router.get("", response_model=list[TaxGroupResponse])
async def list_tax_groups(
    include_deleted: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    groups = await crud_service.list_all(db, TaxGroup, include_deleted=include_deleted)
    return [_tax_group_response(g) for g in groups]


@tax_groups_router.post(
    "", response_model=TaxGroupResponse, status_code=status.HTTP_201_CREATED
)
async def create_tax_group(
    request: Request,
    data: TaxGroupCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    payload = data.model_dump(exclude={"tax_ids"}, exclude_unset=True)
    group = await crud_service.create(db, TaxGroup, payload)
    await _sync_group_taxes(db, group, data.tax_ids)
    if data.is_default:
        await _clear_other_defaults(db, group.id)
    await db.refresh(group)
    await audit_service.log_action(
        db,
        action="CREATE",
        entity_type="tax_group",
        entity_id=str(group.id),
        entity_label=group.name,
        admin=admin,
        changes={"created": data.model_dump(mode="json")},
        request=request,
    )
    return _tax_group_response(await _load_tax_group(db, group.id))


@tax_groups_router.get("/{group_id}", response_model=TaxGroupResponse)
async def get_tax_group(
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    return _tax_group_response(await _load_tax_group(db, group_id))


@tax_groups_router.put("/{group_id}", response_model=TaxGroupResponse)
async def update_tax_group(
    request: Request,
    group_id: uuid.UUID,
    data: TaxGroupUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    group = await crud_service.get_or_404(db, TaxGroup, group_id)
    await crud_service.update(
        db, group, data.model_dump(exclude={"tax_ids"}, exclude_unset=True)
    )
    if data.tax_ids is not None:
        await _sync_group_taxes(db, group, data.tax_ids)
    if data.is_default:
        await _clear_other_defaults(db, group.id)
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="tax_group",
        entity_id=str(group_id),
        entity_label=group.name,
        admin=admin,
        changes={"data": data.model_dump(mode="json", exclude_unset=True)},
        request=request,
    )
    return _tax_group_response(await _load_tax_group(db, group_id))


@tax_groups_router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tax_group(
    request: Request,
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    group = await crud_service.get_or_404(db, TaxGroup, group_id)
    if group.is_default:
        raise ConflictError("The default tax group cannot be deleted")
    label = group.name
    await crud_service.soft_delete(db, group)
    await audit_service.log_action(
        db,
        action="DELETE",
        entity_type="tax_group",
        entity_id=str(group_id),
        entity_label=label,
        admin=admin,
        changes={"deleted_id": str(group_id)},
        request=request,
    )


async def _clear_other_defaults(db: AsyncSession, keep_id: uuid.UUID) -> None:
    stmt = select(TaxGroup).where(TaxGroup.is_default.is_(True), TaxGroup.id != keep_id)
    for other in (await db.execute(stmt)).scalars().all():
        other.is_default = False
    await db.flush()


# ─── Kitchen flows (category routing needs bespoke handling) ──────────────────

kitchen_flows_router = APIRouter()


def _flow_response(flow: KitchenFlow) -> KitchenFlowResponse:
    payload = KitchenFlowResponse.model_validate(flow)
    payload.category_ids = [link.category_id for link in flow.categories]
    return payload


async def _sync_flow_categories(
    db: AsyncSession, flow: KitchenFlow, category_ids: Sequence[uuid.UUID]
) -> None:
    await db.execute(
        delete(KitchenFlowCategory).where(
            KitchenFlowCategory.kitchen_flow_id == flow.id
        )
    )
    for category_id in dict.fromkeys(category_ids):
        db.add(KitchenFlowCategory(kitchen_flow_id=flow.id, category_id=category_id))
    await db.flush()


@kitchen_flows_router.get("", response_model=list[KitchenFlowResponse])
async def list_kitchen_flows(
    branch_id: uuid.UUID | None = None,
    include_deleted: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    filters = [KitchenFlow.branch_id == branch_id] if branch_id else []
    flows = await crud_service.list_all(
        db, KitchenFlow, include_deleted=include_deleted, filters=filters
    )
    return [_flow_response(f) for f in flows]


@kitchen_flows_router.post(
    "", response_model=KitchenFlowResponse, status_code=status.HTTP_201_CREATED
)
async def create_kitchen_flow(
    request: Request,
    data: KitchenFlowCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    flow = await crud_service.create(
        db, KitchenFlow, data.model_dump(exclude={"category_ids"}, exclude_unset=True)
    )
    await _sync_flow_categories(db, flow, data.category_ids)
    if data.is_default:
        await _clear_other_default_flows(db, flow)
    await db.refresh(flow)
    await audit_service.log_action(
        db,
        action="CREATE",
        entity_type="kitchen_flow",
        entity_id=str(flow.id),
        entity_label=flow.name,
        admin=admin,
        changes={"created": data.model_dump(mode="json")},
        request=request,
    )
    return _flow_response(flow)


@kitchen_flows_router.get("/{flow_id}", response_model=KitchenFlowResponse)
async def get_kitchen_flow(
    flow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    return _flow_response(
        await crud_service.get_or_404(db, KitchenFlow, flow_id, include_deleted=True)
    )


@kitchen_flows_router.put("/{flow_id}", response_model=KitchenFlowResponse)
async def update_kitchen_flow(
    request: Request,
    flow_id: uuid.UUID,
    data: KitchenFlowUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    flow = await crud_service.get_or_404(db, KitchenFlow, flow_id)
    await crud_service.update(
        db, flow, data.model_dump(exclude={"category_ids"}, exclude_unset=True)
    )
    if data.category_ids is not None:
        await _sync_flow_categories(db, flow, data.category_ids)
    if data.is_default:
        await _clear_other_default_flows(db, flow)
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="kitchen_flow",
        entity_id=str(flow_id),
        entity_label=flow.name,
        admin=admin,
        changes={"data": data.model_dump(mode="json", exclude_unset=True)},
        request=request,
    )
    await db.refresh(flow)
    return _flow_response(flow)


@kitchen_flows_router.delete("/{flow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kitchen_flow(
    request: Request,
    flow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    flow = await crud_service.get_or_404(db, KitchenFlow, flow_id)
    label = flow.name
    await crud_service.soft_delete(db, flow)
    await audit_service.log_action(
        db,
        action="DELETE",
        entity_type="kitchen_flow",
        entity_id=str(flow_id),
        entity_label=label,
        admin=admin,
        changes={"deleted_id": str(flow_id)},
        request=request,
    )


async def _clear_other_default_flows(db: AsyncSession, flow: KitchenFlow) -> None:
    """Only one default flow may exist per branch."""
    stmt = select(KitchenFlow).where(
        KitchenFlow.branch_id == flow.branch_id,
        KitchenFlow.is_default.is_(True),
        KitchenFlow.id != flow.id,
    )
    for other in (await db.execute(stmt)).scalars().all():
        other.is_default = False
    await db.flush()


__all__ = [
    "charges_router",
    "kitchen_flows_router",
    "payment_methods_router",
    "reasons_router",
    "tags_router",
    "tax_groups_router",
    "taxes_router",
]
