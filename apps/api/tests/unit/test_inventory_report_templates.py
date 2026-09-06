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


class _TemplateListResult:
    def __init__(self, templates: list[InventoryReportTemplate]):
        self.templates = templates

    def scalars(self):
        return self

    def all(self):
        return self.templates


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
        scalar=AsyncMock(return_value=0),
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
            (value for value in added if isinstance(value, InventoryReportTemplate)),
            None,
        )
        if template is None:
            return SimpleNamespace()
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
    assert result.version_number == 1
    db.flush.assert_awaited()


def test_pos_template_selection_never_falls_back_after_latest_is_deactivated():
    branch_id = uuid4()
    production_v1 = InventoryReportTemplate(
        id=uuid4(),
        branch_id=branch_id,
        name="Production Report - SHJ",
        report_type="production",
        version_number=1,
        is_active=True,
    )
    production_v2 = InventoryReportTemplate(
        id=uuid4(),
        branch_id=branch_id,
        name="Production Report - SHJ",
        report_type="production",
        version_number=2,
        is_active=False,
    )
    packaging_v1 = InventoryReportTemplate(
        id=uuid4(),
        branch_id=branch_id,
        name="Packaging Report - SHJ",
        report_type="packaging",
        version_number=1,
        is_active=True,
    )

    current = report_service.latest_active_templates(
        [production_v1, production_v2, packaging_v1]
    )

    assert [
        (template.report_type, template.version_number) for template in current
    ] == [("packaging", 1)]


@pytest.mark.asyncio
async def test_template_update_appends_a_same_name_revision():
    branch_id = uuid4()
    item_id = uuid4()
    existing = InventoryReportTemplate(
        id=uuid4(),
        branch_id=branch_id,
        name="Production Report - SHJ",
        report_type="production",
        cadence="per_business_day",
        version_number=1,
        is_active=True,
    )
    added: list[object] = []
    db = SimpleNamespace(
        get=AsyncMock(
            side_effect=[SimpleNamespace(id=branch_id), SimpleNamespace(id=item_id)]
        ),
        add=MagicMock(side_effect=added.append),
        flush=AsyncMock(),
        scalar=AsyncMock(return_value=1),
        execute=AsyncMock(),
    )
    data = ReportTemplateUpsert(
        branch_id=branch_id,
        name="Production Report - SHJ",
        report_type="production",
        cadence="per_till",
        is_required=True,
        is_active=True,
        configuration={},
        items=[{"item_id": item_id, "required_input": "production"}],
    )

    async def flush_assigns_id():
        created = next(
            value for value in added if isinstance(value, InventoryReportTemplate)
        )
        created.id = uuid4()

    async def execute(_statement):
        created = next(
            (value for value in added if isinstance(value, InventoryReportTemplate)),
            None,
        )
        return _TemplateResult(created) if created else SimpleNamespace()

    db.flush.side_effect = flush_assigns_id
    db.execute.side_effect = execute

    created = await report_service.upsert_template(db, template=existing, data=data)

    assert created.id != existing.id
    assert created.name == existing.name
    assert created.version_number == 2
    assert created.cadence == "per_till"
    assert existing.version_number == 1
    assert existing.cadence == "per_business_day"


@pytest.mark.asyncio
async def test_deactivating_latest_revision_does_not_mutate_prior_revision():
    branch_id = uuid4()
    prior = InventoryReportTemplate(
        id=uuid4(),
        branch_id=branch_id,
        name="Production Report - SHJ",
        report_type="production",
        version_number=1,
        is_active=True,
    )
    latest = InventoryReportTemplate(
        id=uuid4(),
        branch_id=branch_id,
        name="Production Report - SHJ",
        report_type="production",
        version_number=2,
        is_active=True,
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[SimpleNamespace(), _TemplateListResult([prior, latest])]
        ),
        flush=AsyncMock(),
    )

    result = await report_service.deactivate_template(db, template=latest)

    assert result is latest
    assert latest.is_active is False
    assert prior.is_active is True
    db.flush.assert_awaited_once()


def test_create_report_adds_lines_without_lazy_loading_the_collection():
    """`_create_report` must not touch `report.lines` on a freshly-flushed report.

    Appending to the relationship emits a lazy load of the unloaded collection,
    which raises `MissingGreenlet` under asyncio and 500s /pos/inventory/tasks —
    so no till-close report is ever created (the shift_inventory_reports table
    stayed empty in production). The fix sets the FK and adds each line on its
    own; this asserts the shape so the trap cannot creep back. (Same class of bug
    as menu_group_service._set_products.)
    """
    import inspect

    source = inspect.getsource(report_service._create_report)
    assert "report.lines.append" not in source, (
        "appending to report.lines lazy-loads the collection (MissingGreenlet)"
    )
    assert "report_id=report.id" in source, "each line must set its FK directly"
    assert "db.add(report_line)" in source, "each line is added on its own"
