"""Terminal registration, pairing, and printer configuration."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_admin_user, get_db
from app.core.exceptions import ConflictError, UnauthorizedError
from app.models import (
    Branch,
    Device,
    DeviceStatusEnum,
    Printer,
)
from app.models.base import utcnow
from app.models.user import User
from app.schemas.pos import (
    BranchResponse,
    DeviceCreate,
    DevicePairRequest,
    DevicePairResponse,
    DeviceResponse,
    DeviceUpdate,
    PrinterCreate,
    PrinterResponse,
    PrinterUpdate,
)
from app.services import audit_service, crud_service

router = APIRouter()

PAIRING_CODE_TTL = timedelta(minutes=15)
PAIRING_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1


def hash_device_token(token: str) -> str:
    """Device tokens are high-entropy, so a fast digest is the right primitive."""
    return hashlib.sha256(token.encode()).hexdigest()


def _generate_pairing_code() -> str:
    return "".join(secrets.choice(PAIRING_CODE_ALPHABET) for _ in range(8))


async def authenticate_device(db: AsyncSession, token: str | None) -> Device:
    """Resolve the `X-Device-Token` header to a paired device."""
    if not token:
        raise UnauthorizedError("Device token required")
    stmt = select(Device).where(
        Device.token_hash == hash_device_token(token),
        Device.deleted_at.is_(None),
    )
    device = (await db.execute(stmt)).scalar_one_or_none()
    if device is None:
        raise UnauthorizedError("Unrecognised device token")
    if device.status == DeviceStatusEnum.DISABLED.value:
        raise UnauthorizedError("This device has been disabled")
    return device


async def get_current_device(
    x_device_token: str | None = Header(None, alias="X-Device-Token"),
    db: AsyncSession = Depends(get_db),
) -> Device:
    return await authenticate_device(db, x_device_token)


# ─── Devices (admin) ──────────────────────────────────────────────────────────


@router.get("", response_model=list[DeviceResponse])
async def list_devices(
    branch_id: uuid.UUID | None = None,
    type: str | None = None,
    include_deleted: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    filters = []
    if branch_id:
        filters.append(Device.branch_id == branch_id)
    if type:
        filters.append(Device.type == type)
    return await crud_service.list_all(
        db, Device, include_deleted=include_deleted, filters=filters
    )


@router.post("", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED)
async def create_device(
    request: Request,
    data: DeviceCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    await crud_service.get_or_404(db, Branch, data.branch_id)
    if await crud_service.reference_taken(db, Device, "reference", data.reference):
        raise ConflictError(f"Device reference '{data.reference}' is already in use")
    device = await crud_service.create(db, Device, data)
    await audit_service.log_action(
        db,
        action="CREATE",
        entity_type="device",
        entity_id=str(device.id),
        entity_label=device.name,
        admin=admin,
        changes={"created": data.model_dump(mode="json")},
        request=request,
    )
    return device


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(
    device_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    return await crud_service.get_or_404(db, Device, device_id, include_deleted=True)


@router.put("/{device_id}", response_model=DeviceResponse)
async def update_device(
    request: Request,
    device_id: uuid.UUID,
    data: DeviceUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    device = await crud_service.get_or_404(db, Device, device_id)
    if data.reference and await crud_service.reference_taken(
        db, Device, "reference", data.reference, exclude_id=device_id
    ):
        raise ConflictError(f"Device reference '{data.reference}' is already in use")
    device = await crud_service.update(db, device, data)
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="device",
        entity_id=str(device_id),
        entity_label=device.name,
        admin=admin,
        changes={"data": data.model_dump(mode="json", exclude_unset=True)},
        request=request,
    )
    return device


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(
    request: Request,
    device_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    device = await crud_service.get_or_404(db, Device, device_id)
    label = device.name
    # Revoking the token immediately locks the physical terminal out.
    device.token_hash = None
    device.pairing_code = None
    await crud_service.soft_delete(db, device)
    await audit_service.log_action(
        db,
        action="DELETE",
        entity_type="device",
        entity_id=str(device_id),
        entity_label=label,
        admin=admin,
        changes={"deleted_id": str(device_id)},
        request=request,
    )


@router.post("/{device_id}/pairing-code", response_model=DeviceResponse)
async def issue_pairing_code(
    request: Request,
    device_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """
    Mint a short-lived code the iPad types once to claim this device slot.
    Issuing a new code invalidates any existing pairing.
    """
    device = await crud_service.get_or_404(db, Device, device_id)
    device.pairing_code = _generate_pairing_code()
    device.pairing_code_expires_at = utcnow() + PAIRING_CODE_TTL
    device.token_hash = None
    device.status = DeviceStatusEnum.AVAILABLE.value
    await db.flush()
    await db.refresh(device)
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="device",
        entity_id=str(device_id),
        entity_label=device.name,
        admin=admin,
        changes={"pairing_code_issued": True},
        request=request,
    )
    return device


@router.post("/{device_id}/unpair", response_model=DeviceResponse)
async def unpair_device(
    request: Request,
    device_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    device = await crud_service.get_or_404(db, Device, device_id)
    device.token_hash = None
    device.pairing_code = None
    device.pairing_code_expires_at = None
    device.status = DeviceStatusEnum.AVAILABLE.value
    await db.flush()
    await db.refresh(device)
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="device",
        entity_id=str(device_id),
        entity_label=device.name,
        admin=admin,
        changes={"unpaired": True},
        request=request,
    )
    return device


# ─── Pairing (called by the terminal, no admin session) ───────────────────────


@router.post("/pair", response_model=DevicePairResponse)
async def pair_device(
    data: DevicePairRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Exchange a one-time pairing code for a long-lived device token.

    The token is returned exactly once; only its digest is stored.
    """
    stmt = select(Device).where(
        Device.pairing_code == data.pairing_code.upper().strip(),
        Device.deleted_at.is_(None),
    )
    device = (await db.execute(stmt)).scalar_one_or_none()
    if device is None:
        raise UnauthorizedError("Invalid pairing code")
    if (
        device.pairing_code_expires_at is None
        or device.pairing_code_expires_at < utcnow()
    ):
        raise UnauthorizedError("This pairing code has expired")

    token = secrets.token_urlsafe(32)
    device.token_hash = hash_device_token(token)
    device.pairing_code = None
    device.pairing_code_expires_at = None
    device.status = DeviceStatusEnum.USED.value
    device.app_version = data.app_version
    device.os_version = data.os_version
    device.model_identifier = data.model_identifier
    device.last_seen_at = utcnow()
    await db.flush()
    await db.refresh(device)

    branch = await crud_service.get_or_404(db, Branch, device.branch_id)
    return DevicePairResponse(
        device=DeviceResponse.model_validate(device),
        device_token=token,
        branch=BranchResponse.model_validate(branch),
    )


@router.post("/heartbeat", response_model=DeviceResponse)
async def device_heartbeat(
    device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
):
    """Terminals ping this so the console can show which are online."""
    device.last_seen_at = utcnow()
    await db.flush()
    await db.refresh(device)
    return device


# ─── Printers ─────────────────────────────────────────────────────────────────

printers_router = APIRouter()


@printers_router.get("", response_model=list[PrinterResponse])
async def list_printers(
    branch_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    role: str | None = None,
    include_deleted: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    filters = []
    if branch_id:
        filters.append(Printer.branch_id == branch_id)
    if device_id:
        filters.append(Printer.device_id == device_id)
    if role:
        filters.append(Printer.role == role)
    return await crud_service.list_all(
        db, Printer, include_deleted=include_deleted, filters=filters
    )


@printers_router.post(
    "", response_model=PrinterResponse, status_code=status.HTTP_201_CREATED
)
async def create_printer(
    data: PrinterCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    await crud_service.get_or_404(db, Branch, data.branch_id)
    printer = await crud_service.create(db, Printer, data)
    if printer.is_default:
        await _clear_other_defaults(db, printer)
    return printer


@printers_router.get("/{printer_id}", response_model=PrinterResponse)
async def get_printer(
    printer_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    return await crud_service.get_or_404(db, Printer, printer_id, include_deleted=True)


@printers_router.put("/{printer_id}", response_model=PrinterResponse)
async def update_printer(
    printer_id: uuid.UUID,
    data: PrinterUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    printer = await crud_service.get_or_404(db, Printer, printer_id)
    printer = await crud_service.update(db, printer, data)
    if printer.is_default:
        await _clear_other_defaults(db, printer)
    return printer


@printers_router.delete("/{printer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_printer(
    printer_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    printer = await crud_service.get_or_404(db, Printer, printer_id)
    await crud_service.soft_delete(db, printer)


@printers_router.get("/for-device/me", response_model=list[PrinterResponse])
async def printers_for_current_device(
    device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
):
    """
    Printers the calling terminal should use: its own, plus any branch-wide
    printer not bound to a specific device.
    """
    stmt = (
        select(Printer)
        .where(
            Printer.branch_id == device.branch_id,
            Printer.deleted_at.is_(None),
            Printer.is_active.is_(True),
            (Printer.device_id == device.id) | (Printer.device_id.is_(None)),
        )
        .order_by(Printer.is_default.desc(), Printer.name)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _clear_other_defaults(db: AsyncSession, printer: Printer) -> None:
    """One default printer per (branch, role)."""
    stmt = select(Printer).where(
        Printer.branch_id == printer.branch_id,
        Printer.role == printer.role,
        Printer.is_default.is_(True),
        Printer.id != printer.id,
    )
    for other in (await db.execute(stmt)).scalars().all():
        other.is_default = False
    await db.flush()
