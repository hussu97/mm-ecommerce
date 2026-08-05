"""Terminal registration, pairing, and printer configuration."""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_admin_user, get_current_active_user, get_db
from app.core.exceptions import ConflictError, UnauthorizedError
from app.models import (
    Branch,
    Device,
    DeviceStatusEnum,
    Printer,
)
from app.models.base import utcnow
from app.models.device_push_token import DevicePushToken
from app.models.user import User
from app.schemas.pos import (
    BranchResponse,
    DeviceCreate,
    DevicePairRequest,
    DevicePairResponse,
    DeviceResponse,
    DeviceSessionResponse,
    DeviceUpdate,
    PrinterCreate,
    PrinterResponse,
    PrinterUpdate,
)
from app.services import audit_service, crud_service, push_service

logger = logging.getLogger("mm.pos.devices")

router = APIRouter()

PAIRING_CODE_TTL = timedelta(minutes=15)
PAIRING_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1


def hash_device_token(token: str) -> str:
    """Device tokens are high-entropy, so a fast digest is the right primitive."""
    return hashlib.sha256(token.encode()).hexdigest()


def _generate_pairing_code() -> str:
    return "".join(secrets.choice(PAIRING_CODE_ALPHABET) for _ in range(8))


#: How stale `last_seen_at` may get before an authenticated request refreshes it.
#: Comfortably under the five minutes the dashboards treat as offline, so a
#: terminal that is talking to us is never reported as down; long enough that a
#: busy till ringing up a check does not write a row per request.
SEEN_REFRESH_SECONDS = 60


async def authenticate_device(db: AsyncSession, token: str | None) -> Device:
    """
    Resolve the `X-Device-Token` header to a paired device, and record that we
    heard from it.

    `last_seen_at` used to be written only by pairing and by the explicit
    heartbeat, which a terminal sends once at launch. Five minutes later every
    dashboard reported it offline — including tills that had been ringing up
    sales all afternoon, which made "offline devices" a number nobody could act
    on. Seen means last spoke to us, so it is stamped here, at the single choke
    point every device-authenticated request passes through.

    Throttled to one write a minute per device. Without that a till mid-service
    would write a row per tap.
    """
    if not token:
        raise UnauthorizedError("Device token required")
    stmt = select(Device).where(
        Device.token_hash == hash_device_token(token),
        Device.deleted_at.is_(None),
    )
    device = (await db.execute(stmt)).scalar_one_or_none()
    if device is None:
        # The only 401 here that means "this terminal is no longer paired": the
        # token matches no device, so it is worthless and the app is right to
        # throw it away and ask for a new pairing code.
        #
        # Coded, because the register cannot tell that from the wording. It used
        # to treat *every* 401 from the heartbeat as an unpairing — including a
        # missing header and the wrong-host refusal below — and discard a
        # working device token over a configuration mistake. That is why a
        # terminal asked for a pairing code every morning.
        raise UnauthorizedError("Unrecognised device token", code="device_revoked")
    if device.status == DeviceStatusEnum.DISABLED.value:
        # Deliberately *not* coded. The token is still good and the device
        # record still exists — an admin flips the status back and the terminal
        # carries on. Clearing the pairing here would turn a two-second fix in
        # the console into a trip to the shop with a pairing code.
        raise UnauthorizedError("This device has been disabled")

    now = utcnow()
    if (
        device.last_seen_at is None
        or (now - device.last_seen_at).total_seconds() >= SEEN_REFRESH_SECONDS
    ):
        device.last_seen_at = now
        await db.commit()

    return device


async def get_current_device(
    request: Request,
    x_device_token: str | None = Header(None, alias="X-Device-Token"),
    db: AsyncSession = Depends(get_db),
) -> Device:
    """
    Resolve a paired device, and only on the register API.

    A device token is a long-lived credential sitting on a shop counter, and
    the register has its own application and hostname precisely so that
    credential has one place it works. Honouring it on the public storefront
    host as well would give that boundary away for nothing — the terminal
    never calls there, so refusing costs no real client anything.
    """
    on_pos_app = getattr(request.app.state, "is_pos_app", False)
    if not on_pos_app:
        if settings.POS_REQUIRE_POS_HOST:
            raise UnauthorizedError(
                "Device tokens are only accepted by the register API"
            )
        logger.warning(
            "Device token used against the storefront API — point this terminal "
            "at the register host, then set POS_REQUIRE_POS_HOST=true"
        )
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


@router.post("/heartbeat", response_model=DeviceSessionResponse)
async def device_heartbeat(
    device: Device = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
):
    """
    Terminals ping this so the console can show which are online.

    It doubles as the terminal's cold-start call: it returns the branch too, so
    a paired-but-signed-out terminal can render its own name without a user
    token it does not yet have.
    """
    device.last_seen_at = utcnow()
    await db.flush()
    await db.refresh(device)
    branch = await crud_service.get_or_404(db, Branch, device.branch_id)
    return DeviceSessionResponse(
        device=DeviceResponse.model_validate(device),
        branch=BranchResponse.model_validate(branch),
    )


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


# ─── Push registration ────────────────────────────────────────────────────────


class PushTokenRegisterRequest(BaseModel):
    """What a register tells us so it can be woken."""

    #: Apple's device token, hex.
    token: str = Field(min_length=32, max_length=200)
    #: The app that owns it — `com.meltingmoments.pos` for the iPad register,
    #: `com.meltingmoments.posmanager` for the phone. It is the APNs topic, and
    #: a push to the wrong one is silently dropped by Apple.
    bundle_id: str = Field(min_length=1, max_length=100)
    #: The counter this device stands on. Until it is set the device receives
    #: nothing — a push is addressed to a kitchen, not to a business.
    branch_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    #: True for a TestFlight or debug build. Sandbox tokens are not valid on the
    #: production APNs host and vice versa, and the failure names nothing useful,
    #: so the app has to tell us which it is.
    is_sandbox: bool = False


class PushTokenResponse(BaseModel):
    id: str
    branch_id: str | None
    bundle_id: str
    is_sandbox: bool
    push_enabled: bool


@router.post("/push-token", response_model=PushTokenResponse)
async def register_push_token(
    data: PushTokenRegisterRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """
    Register this device for order notifications, or update its registration.

    Called on every launch, so it upserts on the token: a row per launch would
    mean the same iPad buzzing five times for one order.

    `push_enabled` in the response says whether the server can actually send —
    false means no APNs key is configured, and the app should keep polling
    rather than assume silence means no orders.
    """
    row = await push_service.register_token(
        db,
        token=data.token.strip(),
        bundle_id=data.bundle_id.strip(),
        branch_id=data.branch_id,
        device_id=data.device_id,
        user_id=user.id,
        is_sandbox=data.is_sandbox,
    )
    return PushTokenResponse(
        id=str(row.id),
        branch_id=str(row.branch_id) if row.branch_id else None,
        bundle_id=row.bundle_id,
        is_sandbox=row.is_sandbox,
        push_enabled=push_service.is_enabled(),
    )


@router.delete("/push-token/{token}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_push_token(
    token: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Stop sending to this device — a sign-out, or notifications turned off."""
    row = (
        (
            await db.execute(
                select(DevicePushToken).where(DevicePushToken.token == token)
            )
        )
        .scalars()
        .first()
    )
    if row is not None and row.revoked_at is None:
        row.revoked_at = utcnow()
        row.revoked_reason = "revoked by device"
