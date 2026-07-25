"""Roles, the permission catalogue, staff accounts, and terminal PIN sign-in."""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_admin_user, get_current_active_user, get_db
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    UnauthorizedError,
)
from app.core.security import create_access_token, hash_password, verify_password
from app.models import (
    ALL_PERMISSIONS,
    PERMISSION_GROUPS,
    Branch,
    Device,
    Role,
    UserBranch,
)
from app.models.user import User
from app.schemas.pos import (
    PermissionCatalogue,
    PermissionEntry,
    PinLoginRequest,
    PinLoginResponse,
    RoleCreate,
    RoleResponse,
    RoleUpdate,
    StaffCreate,
    StaffResponse,
    StaffUpdate,
)
from app.services import audit_service, crud_service

router = APIRouter()

# PINs are only 4–8 digits, so they are always paired with a branch and are never
# usable outside the terminal flow. They are still hashed with the password hasher.
PIN_TOKEN_MINUTES = 12 * 60


# ─── Permission catalogue ─────────────────────────────────────────────────────


@router.get("/permissions", response_model=PermissionCatalogue)
async def list_permissions(_: User = Depends(get_admin_user)):
    """The full authority matrix, grouped for the role editor."""
    return PermissionCatalogue(
        groups={
            group: [
                PermissionEntry(slug=slug, description=description)
                for slug, description in entries
            ]
            for group, entries in PERMISSION_GROUPS.items()
        }
    )


# ─── Roles ────────────────────────────────────────────────────────────────────

roles_router = APIRouter()


def _validate_permissions(permissions: list[str]) -> None:
    unknown = sorted(set(permissions) - set(ALL_PERMISSIONS))
    if unknown:
        raise BadRequestError(f"Unknown permissions: {', '.join(unknown)}")


async def _role_response(db: AsyncSession, role: Role) -> RoleResponse:
    payload = RoleResponse.model_validate(role)
    payload.user_count = int(
        (
            await db.execute(
                select(func.count()).select_from(User).where(User.role_id == role.id)
            )
        ).scalar_one()
    )
    return payload


@roles_router.get("", response_model=list[RoleResponse])
async def list_roles(
    include_deleted: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    roles = await crud_service.list_all(db, Role, include_deleted=include_deleted)
    return [await _role_response(db, r) for r in roles]


@roles_router.post("", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
async def create_role(
    request: Request,
    data: RoleCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    _validate_permissions(data.permissions)
    role = await crud_service.create(db, Role, data)
    await audit_service.log_action(
        db,
        action="CREATE",
        entity_type="role",
        entity_id=str(role.id),
        entity_label=role.name,
        admin=admin,
        changes={"created": data.model_dump(mode="json")},
        request=request,
    )
    return await _role_response(db, role)


@roles_router.get("/{role_id}", response_model=RoleResponse)
async def get_role(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    role = await crud_service.get_or_404(db, Role, role_id, include_deleted=True)
    return await _role_response(db, role)


@roles_router.put("/{role_id}", response_model=RoleResponse)
async def update_role(
    request: Request,
    role_id: uuid.UUID,
    data: RoleUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    if data.permissions is not None:
        _validate_permissions(data.permissions)
    role = await crud_service.get_or_404(db, Role, role_id)
    role = await crud_service.update(db, role, data)
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="role",
        entity_id=str(role_id),
        entity_label=role.name,
        admin=admin,
        changes={"data": data.model_dump(mode="json", exclude_unset=True)},
        request=request,
    )
    return await _role_response(db, role)


@roles_router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    request: Request,
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    role = await crud_service.get_or_404(db, Role, role_id)
    assigned = int(
        (
            await db.execute(
                select(func.count()).select_from(User).where(User.role_id == role_id)
            )
        ).scalar_one()
    )
    if assigned:
        raise ConflictError(
            f"{assigned} user(s) still have this role. Reassign them first."
        )
    label = role.name
    await crud_service.soft_delete(db, role)
    await audit_service.log_action(
        db,
        action="DELETE",
        entity_type="role",
        entity_id=str(role_id),
        entity_label=label,
        admin=admin,
        changes={"deleted_id": str(role_id)},
        request=request,
    )


# ─── Staff ────────────────────────────────────────────────────────────────────


async def _branch_ids(db: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    rows = (
        (
            await db.execute(
                select(UserBranch.branch_id).where(UserBranch.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _staff_response(db: AsyncSession, user: User) -> StaffResponse:
    payload = StaffResponse.model_validate(user)
    payload.branch_ids = await _branch_ids(db, user.id)
    payload.has_pin = bool(user.pin_hash)
    payload.role_name = user.role.name if user.role else None
    return payload


async def _sync_branches(
    db: AsyncSession, user: User, branch_ids: list[uuid.UUID]
) -> None:
    await db.execute(delete(UserBranch).where(UserBranch.user_id == user.id))
    for branch_id in dict.fromkeys(branch_ids):
        branch = await db.get(Branch, branch_id)
        if branch is None or branch.deleted_at is not None:
            raise BadRequestError(f"Branch {branch_id} does not exist")
        db.add(UserBranch(user_id=user.id, branch_id=branch_id))
    await db.flush()


@router.get("", response_model=list[StaffResponse])
async def list_staff(
    branch_id: uuid.UUID | None = None,
    include_inactive: bool = True,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    stmt = select(User).where(User.is_staff.is_(True))
    if not include_inactive:
        stmt = stmt.where(User.is_active.is_(True))
    if branch_id:
        stmt = stmt.join(UserBranch, UserBranch.user_id == User.id).where(
            UserBranch.branch_id == branch_id
        )
    stmt = stmt.order_by(User.display_name, User.email)
    staff = list((await db.execute(stmt)).scalars().unique().all())
    return [await _staff_response(db, s) for s in staff]


@router.post("", response_model=StaffResponse, status_code=status.HTTP_201_CREATED)
async def create_staff(
    request: Request,
    data: StaffCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    email = data.email.strip().lower()
    existing = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(f"A user with email {email} already exists")

    if data.staff_number and await crud_service.reference_taken(
        db, User, "staff_number", data.staff_number
    ):
        raise ConflictError(f"Staff number '{data.staff_number}' is already in use")

    if data.role_id is not None:
        await crud_service.get_or_404(db, Role, data.role_id)

    user = User(
        email=email,
        display_name=data.display_name,
        staff_number=data.staff_number,
        phone=data.phone,
        hashed_password=hash_password(data.password) if data.password else None,
        pin_hash=hash_password(data.pin) if data.pin else None,
        role_id=data.role_id,
        is_active=data.is_active,
        is_admin=data.is_admin,
        is_staff=True,
        is_driver=data.is_driver,
    )
    db.add(user)
    await db.flush()
    await _sync_branches(db, user, data.branch_ids)
    await db.refresh(user)

    await audit_service.log_action(
        db,
        action="CREATE",
        entity_type="staff",
        entity_id=str(user.id),
        entity_label=user.display_name or user.email,
        admin=admin,
        changes={"created": data.model_dump(mode="json", exclude={"password", "pin"})},
        request=request,
    )
    return await _staff_response(db, user)


@router.get("/for-device", response_model=list[StaffResponse])
async def staff_for_device(
    device_reference: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """
    Staff assigned to the branch a given device belongs to.

    Declared before `/{user_id}` — FastAPI matches in declaration order, and a
    literal segment placed after a same-shape path parameter is unreachable.
    """
    device = (
        await db.execute(
            select(Device).where(
                Device.reference == device_reference, Device.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if device is None:
        raise BadRequestError(f"No device with reference '{device_reference}'")
    return await list_staff(device.branch_id, True, db, admin)


@router.get("/{user_id}", response_model=StaffResponse)
async def get_staff(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    user = await crud_service.get_or_404(db, User, user_id)
    return await _staff_response(db, user)


@router.put("/{user_id}", response_model=StaffResponse)
async def update_staff(
    request: Request,
    user_id: uuid.UUID,
    data: StaffUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    user = await crud_service.get_or_404(db, User, user_id)

    if data.staff_number and await crud_service.reference_taken(
        db, User, "staff_number", data.staff_number, exclude_id=user_id
    ):
        raise ConflictError(f"Staff number '{data.staff_number}' is already in use")
    if data.role_id is not None:
        await crud_service.get_or_404(db, Role, data.role_id)

    payload = data.model_dump(
        exclude={"password", "pin", "branch_ids"}, exclude_unset=True
    )
    if "email" in payload and payload["email"]:
        payload["email"] = payload["email"].strip().lower()
    if data.password:
        payload["hashed_password"] = hash_password(data.password)
    if data.pin:
        payload["pin_hash"] = hash_password(data.pin)

    user = await crud_service.update(db, user, payload)
    if data.branch_ids is not None:
        await _sync_branches(db, user, data.branch_ids)

    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="staff",
        entity_id=str(user_id),
        entity_label=user.display_name or user.email,
        admin=admin,
        changes={
            "data": data.model_dump(
                mode="json", exclude_unset=True, exclude={"password", "pin"}
            ),
            "password_changed": bool(data.password),
            "pin_changed": bool(data.pin),
        },
        request=request,
    )
    return await _staff_response(db, user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_staff(
    request: Request,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """
    Staff are deactivated rather than deleted — their name must stay resolvable on
    historical orders, tills and audit entries.
    """
    if user_id == admin.id:
        raise ConflictError("You cannot deactivate your own account")
    user = await crud_service.get_or_404(db, User, user_id)
    user.is_active = False
    user.pin_hash = None
    await db.flush()
    await audit_service.log_action(
        db,
        action="DELETE",
        entity_type="staff",
        entity_id=str(user_id),
        entity_label=user.display_name or user.email,
        admin=admin,
        changes={"deactivated": True},
        request=request,
    )


# ─── Terminal PIN sign-in ─────────────────────────────────────────────────────


@router.post("/pin-login", response_model=PinLoginResponse)
async def pin_login(
    data: PinLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Sign a cashier in on a shared terminal.

    A PIN alone is weak, so it is only ever accepted scoped to one branch, and
    only for staff explicitly assigned to that branch. The candidate set is small
    (one shop's staff), and every stored PIN is bcrypt-hashed.
    """
    branch = await crud_service.get_or_404(db, Branch, data.branch_id)

    stmt = (
        select(User)
        .join(UserBranch, UserBranch.user_id == User.id)
        .where(
            UserBranch.branch_id == branch.id,
            User.is_staff.is_(True),
            User.is_active.is_(True),
            User.pin_hash.isnot(None),
        )
    )
    candidates = list((await db.execute(stmt)).scalars().unique().all())

    matched: User | None = None
    for candidate in candidates:
        if candidate.pin_hash and verify_password(data.pin, candidate.pin_hash):
            matched = candidate
            break

    if matched is None:
        raise UnauthorizedError("Incorrect PIN for this branch")

    if not matched.can("pos.register.access"):
        raise ForbiddenError("This user is not allowed to use the cash register")

    token = create_access_token(
        user_id=str(matched.id),
        email=matched.email,
        is_admin=matched.is_admin,
        expires_delta=timedelta(minutes=PIN_TOKEN_MINUTES),
    )
    permissions = (
        ALL_PERMISSIONS
        if (matched.is_admin or (matched.role and matched.role.is_super_admin))
        else list(matched.role.permissions if matched.role else [])
    )
    return PinLoginResponse(
        access_token=token,
        staff=await _staff_response(db, matched),
        permissions=permissions,
    )


@router.get("/me/permissions", response_model=list[str])
async def my_permissions(user: User = Depends(get_current_active_user)):
    if user.is_admin or (user.role and user.role.is_super_admin):
        return ALL_PERMISSIONS
    return list(user.role.permissions if user.role else [])


@router.get("/me/branches", response_model=list[uuid.UUID])
async def my_branches(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    return await _branch_ids(db, user.id)
