from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models.inventory_v2 import InventoryReportTemplate
from app.schemas.inventory_v2 import ReportTemplateUpsert
from app.services.inventory import report_service


class _TemplateResult:
    """Small SQLAlchemy-result stand-in for the response reload."""

    def __init__(self, template: InventoryReportTemplate):
        self.template = template

    def scalars(self):
        return self

    def unique(self):
        return self

    def one(self):
        return self.template


@pytest.mark.asyncio
async def test_new_report_template_has_required_columns_before_first_flush():
    """The flush that allocates the template ID must be insertable on PostgreSQL."""
    branch_id = uuid4()
    item_id = uuid4()
    added: list[object] = []
    flush_snapshots: list[dict[str, object]] = []
    db = SimpleNamespace(
        get=AsyncMock(
            side_effect=[SimpleNamespace(id=branch_id), SimpleNamespace(id=item_id)]
        ),
        add=MagicMock(side_effect=added.append),
        flush=AsyncMock(),
        execute=AsyncMock(),
    )
    data = ReportTemplateUpsert(
        branch_id=branch_id,
        name="Sharjah Kitchen — Packaging & Dispatch",
        report_type="packaging",
        cadence="per_business_day",
        is_required=True,
        is_active=True,
        configuration={"visible_columns": ["opening", "expected", "physical"]},
        approval_cost_threshold="100",
        approval_variance_percent="10",
        items=[
            {"item_id": item_id, "display_order": 0, "required_input": "physical_count"}
        ],
    )

    async def flush_assigns_id():
        # The database normally evaluates UUIDMixin's default during INSERT.
        template = next(
            value for value in added if isinstance(value, InventoryReportTemplate)
        )
        flush_snapshots.append(
            {
                "report_type": template.report_type,
                "cadence": template.cadence,
                "configuration": template.configuration,
                "approval_cost_threshold": template.approval_cost_threshold,
                "approval_variance_percent": template.approval_variance_percent,
            }
        )
        template.id = uuid4()

    db.flush.side_effect = flush_assigns_id

    async def execute(_statement):
        template = next(
            value for value in added if isinstance(value, InventoryReportTemplate)
        )
        return _TemplateResult(template)

    db.execute.side_effect = execute

    result = await report_service.upsert_template(db, template=None, data=data)

    assert result.report_type == "packaging"
    assert result.cadence == "per_business_day"
    assert result.configuration == {
        "visible_columns": ["opening", "expected", "physical"]
    }
    assert flush_snapshots[0] == {
        "report_type": "packaging",
        "cadence": "per_business_day",
        "configuration": {"visible_columns": ["opening", "expected", "physical"]},
        "approval_cost_threshold": Decimal("100"),
        "approval_variance_percent": Decimal("10"),
    }
    db.flush.assert_awaited()
