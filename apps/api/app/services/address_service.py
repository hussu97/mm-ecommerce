from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.address import Address
from app.schemas.address import AddressCreate, AddressResponse, AddressUpdate


async def get_all(db: AsyncSession, user_id: uuid.UUID) -> list[AddressResponse]:
    result = await db.execute(
        select(Address)
        .where(Address.user_id == user_id)
        .order_by(Address.is_default.desc(), Address.created_at.desc())
    )
    return [AddressResponse.model_validate(a) for a in result.scalars().all()]


async def get_by_id(
    db: AsyncSession, user_id: uuid.UUID, address_id: uuid.UUID
) -> AddressResponse:
    result = await db.execute(select(Address).where(Address.id == address_id))
    address = result.scalar_one_or_none()
    if not address:
        raise NotFoundError("Address not found")
    if address.user_id != user_id:
        raise ForbiddenError("Not your address")
    return AddressResponse.model_validate(address)


async def create(
    db: AsyncSession, user_id: uuid.UUID, data: AddressCreate
) -> AddressResponse:
    if data.is_default:
        await _unset_defaults(db, user_id)

    address = Address(user_id=user_id, **data.model_dump())
    db.add(address)
    await db.flush()
    await db.refresh(address)
    return AddressResponse.model_validate(address)


async def update(
    db: AsyncSession, user_id: uuid.UUID, address_id: uuid.UUID, data: AddressUpdate
) -> AddressResponse:
    result = await db.execute(select(Address).where(Address.id == address_id))
    address = result.scalar_one_or_none()
    if not address:
        raise NotFoundError("Address not found")
    if address.user_id != user_id:
        raise ForbiddenError("Not your address")

    updates = data.model_dump(exclude_unset=True)
    if updates.get("is_default"):
        await _unset_defaults(db, user_id, exclude_id=address_id)

    for key, val in updates.items():
        setattr(address, key, val)

    await db.flush()
    await db.refresh(address)
    return AddressResponse.model_validate(address)


async def delete(db: AsyncSession, user_id: uuid.UUID, address_id: uuid.UUID) -> None:
    result = await db.execute(select(Address).where(Address.id == address_id))
    address = result.scalar_one_or_none()
    if not address:
        raise NotFoundError("Address not found")
    if address.user_id != user_id:
        raise ForbiddenError("Not your address")
    await db.delete(address)


async def set_default(
    db: AsyncSession, user_id: uuid.UUID, address_id: uuid.UUID
) -> AddressResponse:
    result = await db.execute(select(Address).where(Address.id == address_id))
    address = result.scalar_one_or_none()
    if not address:
        raise NotFoundError("Address not found")
    if address.user_id != user_id:
        raise ForbiddenError("Not your address")

    await _unset_defaults(db, user_id, exclude_id=address_id)
    address.is_default = True
    await db.flush()
    await db.refresh(address)
    return AddressResponse.model_validate(address)


async def _unset_defaults(
    db: AsyncSession, user_id: uuid.UUID, exclude_id: uuid.UUID | None = None
) -> None:
    stmt = select(Address).where(Address.user_id == user_id, Address.is_default == True)  # noqa: E712
    if exclude_id:
        stmt = stmt.where(Address.id != exclude_id)
    result = await db.execute(stmt)
    for addr in result.scalars().all():
        addr.is_default = False
