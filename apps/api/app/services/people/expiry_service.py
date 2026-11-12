"""Which of the shop's paperwork lapses soon: the warning the console raises.

Two sources, one list:
- each **active** employee's *current* document of each `EXPIRY_TRACKED_TYPES`
  type — visa, Emirates ID, labour card, OHC, medical insurance, WPS card (the
  cycle expiring last for that type — an older cycle lapsing is history, not
  news);
- each **active** legal entity's current trade licence, which
  `legal_entity_documents.sync_current_license` mirrors onto
  `legal_entities.trade_license_expiry`.

A document already expired stays on the list (negative `days_left`) until a
renewal is entered — that is exactly when it most needs uploading. "Today" is
the Dubai calendar day, which is what the documents' dates mean.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import trading_hours
from app.models.employee import (
    EMPLOYEE_DOCUMENT_SPECS,
    EXPIRY_TRACKED_TYPES,
    Employee,
    EmployeeDocument,
)
from app.models.legal_entity import LegalEntity
from app.schemas.employee import ExpiringDocument, ExpiringDocumentsResponse

#: The owner's lead time: warn 15 days before a document lapses.
DEFAULT_WITHIN_DAYS = 15


def dubai_today() -> date:
    return datetime.now(trading_hours.TZ).date()


async def expiring_documents(
    db: AsyncSession,
    *,
    within_days: int = DEFAULT_WITHIN_DAYS,
    today: date | None = None,
) -> ExpiringDocumentsResponse:
    today = today or dubai_today()
    cutoff = today + timedelta(days=within_days)
    items: list[ExpiringDocument] = []

    # The current cycle per (employee, type): DISTINCT ON keeps the first row of
    # each group in ORDER BY, i.e. the one expiring last.
    current = (
        select(
            EmployeeDocument.employee_id,
            EmployeeDocument.doc_type,
            EmployeeDocument.document_number,
            EmployeeDocument.expiry_date,
        )
        .join(Employee, Employee.id == EmployeeDocument.employee_id)
        .where(
            Employee.status == "active",
            EmployeeDocument.doc_type.in_(EXPIRY_TRACKED_TYPES),
        )
        .distinct(EmployeeDocument.employee_id, EmployeeDocument.doc_type)
        .order_by(
            EmployeeDocument.employee_id,
            EmployeeDocument.doc_type,
            EmployeeDocument.expiry_date.desc(),
        )
        .subquery()
    )
    rows = await db.execute(
        select(current, Employee.full_name)
        .join(Employee, Employee.id == current.c.employee_id)
        .where(current.c.expiry_date <= cutoff)
    )
    for row in rows:
        items.append(
            ExpiringDocument(
                owner_kind="employee",
                owner_id=row.employee_id,
                owner_name=row.full_name,
                doc_type=row.doc_type,
                doc_label=EMPLOYEE_DOCUMENT_SPECS[row.doc_type]["label"],
                document_number=row.document_number,
                expiry_date=row.expiry_date,
                days_left=(row.expiry_date - today).days,
            )
        )

    entities = await db.execute(
        select(
            LegalEntity.id,
            LegalEntity.legal_name,
            LegalEntity.trade_license_number,
            LegalEntity.trade_license_expiry,
        ).where(
            LegalEntity.is_active.is_(True),
            LegalEntity.trade_license_expiry.is_not(None),
            LegalEntity.trade_license_expiry <= cutoff,
        )
    )
    for row in entities:
        items.append(
            ExpiringDocument(
                owner_kind="legal_entity",
                owner_id=row.id,
                owner_name=row.legal_name,
                doc_type="trade_license",
                doc_label="Trade licence",
                document_number=row.trade_license_number,
                expiry_date=row.trade_license_expiry,
                days_left=(row.trade_license_expiry - today).days,
            )
        )

    items.sort(key=lambda i: (i.expiry_date, i.owner_name, i.doc_type))
    return ExpiringDocumentsResponse(within_days=within_days, today=today, items=items)
