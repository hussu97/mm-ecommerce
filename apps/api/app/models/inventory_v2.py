"""Versioned recipes, deterministic inventory source events, and shift controls.

The original inventory tables remain the compatibility surface while this module
adds the audit boundaries the domain was missing: recipes are versioned, accepted
order plans are frozen, and shift reconciliation is a first-class workflow.
"""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.associationproxy import AssociationProxy, association_proxy
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .inventory import InventoryItem


class InventoryItemKindEnum(str, enum.Enum):
    RAW_MATERIAL = "raw_material"
    PACKAGING = "packaging"
    SEMI_FINISHED = "semi_finished"
    PRODUCED_GOOD = "produced_good"
    RESALE_GOOD = "resale_good"


class InventoryTrackingModeEnum(str, enum.Enum):
    STOCKED = "stocked"
    PHANTOM = "phantom"


class RecipeOwnerKindEnum(str, enum.Enum):
    PRODUCT = "product"
    MODIFIER_OPTION = "modifier_option"
    INVENTORY_ITEM = "inventory_item"


class RecipeVersionStatusEnum(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class RecipeBasisEnum(str, enum.Enum):
    """Whether a version's lines describe one owner unit or one batch.

    ``unit`` (the default and every pre-existing version): the lines are the
    ingredients for a single owner unit. ``batch``: the lines are the
    ingredients for one batch, and ``RecipeVersion.batch_yield`` says how many
    owner units that batch makes — so consuming N units draws ``N / batch_yield``
    of the lines. Set per version, so a recipe can move between the two across
    versions.
    """

    UNIT = "unit"
    BATCH = "batch"


class RecipeCatalogState(Base):
    """Singleton generation clock for changes to the active recipe graph.

    Pending source events remember the generation they last tried.  The recovery
    loop can therefore keep its 30-second cadence without rebuilding the complete
    recipe catalog until an activation actually changes what expansion can do.
    """

    __tablename__ = "recipe_catalog_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_recipe_catalog_state_singleton"),
        CheckConstraint(
            "generation > 0", name="ck_recipe_catalog_state_positive_generation"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="1"
    )


class InventorySourceEventStatusEnum(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    POSTED = "posted"
    CANCELLED = "cancelled"
    EXCEPTION = "exception"


class InventoryReportTypeEnum(str, enum.Enum):
    PRODUCTION = "production"
    FINISHED_GOODS = "finished_goods"
    RAW_MATERIALS = "raw_materials"
    PACKAGING = "packaging"
    SPOT_CHECK = "spot_check"


class InventoryReportCadenceEnum(str, enum.Enum):
    PER_TILL = "per_till"
    PER_BUSINESS_DAY = "per_business_day"
    AD_HOC = "ad_hoc"


class ShiftInventoryReportStatusEnum(str, enum.Enum):
    OUTSTANDING = "outstanding"
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    POSTED = "posted"
    DEFERRED = "deferred"
    SKIPPED = "skipped"
    REJECTED = "rejected"


class BranchInventorySettings(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "branch_inventory_settings"

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    inventory_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    production_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    sales_consumption_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    validation_mode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    allow_negative_stock: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    approval_cost_threshold: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="100"
    )
    approval_variance_percent: Mapped[Any] = mapped_column(
        Numeric(8, 4), nullable=False, server_default="10"
    )
    go_live_sequence: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    go_live_at: Mapped[Any | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class InventoryLot(Base, UUIDMixin, TimestampMixin):
    """Optional lot identity. Allocation/expiry workflows intentionally follow later."""

    __tablename__ = "inventory_lots"
    __table_args__ = (
        UniqueConstraint(
            "warehouse_id", "item_id", "lot_reference", name="uq_inventory_lot"
        ),
    )

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    lot_reference: Mapped[str] = mapped_column(String(120), nullable=False)
    manufactured_at: Mapped[Any | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[Any | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )


class Recipe(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "recipes"
    __table_args__ = (
        CheckConstraint(
            "((product_id IS NOT NULL)::int + "
            "(modifier_option_id IS NOT NULL)::int + "
            "(inventory_item_id IS NOT NULL)::int) = 1",
            name="ck_recipe_one_owner",
        ),
        CheckConstraint(
            "(owner_kind = 'product' AND product_id IS NOT NULL) OR "
            "(owner_kind = 'modifier_option' AND modifier_option_id IS NOT NULL) OR "
            "(owner_kind = 'inventory_item' AND inventory_item_id IS NOT NULL)",
            name="ck_recipe_owner_kind",
        ),
        UniqueConstraint("product_id", name="uq_recipe_product"),
        UniqueConstraint("modifier_option_id", name="uq_recipe_modifier_option"),
        UniqueConstraint("inventory_item_id", name="uq_recipe_inventory_item"),
    )

    owner_kind: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=True
    )
    modifier_option_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("modifier_options.id", ondelete="CASCADE"),
        nullable=True,
    )
    inventory_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="CASCADE"),
        nullable=True,
    )
    versions: Mapped[list[RecipeVersion]] = relationship(
        "RecipeVersion",
        back_populates="recipe",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class RecipeVersion(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "recipe_versions"
    __table_args__ = (
        UniqueConstraint(
            "recipe_id", "version_number", name="uq_recipe_version_number"
        ),
        Index(
            "uq_recipe_one_active_version",
            "recipe_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "uq_recipe_one_draft_version",
            "recipe_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'retired')",
            name="ck_recipe_version_status",
        ),
        CheckConstraint(
            "basis IN ('unit', 'batch')",
            name="ck_recipe_version_basis",
        ),
        CheckConstraint(
            # NULL > 0 is NULL and a CHECK passes on NULL, so batch must assert
            # the yield IS NOT NULL explicitly, or a batch row with no yield slips
            # through.
            "(basis = 'unit' AND batch_yield IS NULL) "
            "OR (basis = 'batch' AND batch_yield IS NOT NULL AND batch_yield > 0)",
            name="ck_recipe_version_batch_yield",
        ),
    )

    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recipes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="draft", index=True
    )
    #: 'unit' — lines are per one owner unit; 'batch' — lines are per one batch
    #: that yields ``batch_yield`` owner units. See RecipeBasisEnum.
    basis: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="unit"
    )
    #: Owner units one batch makes; set (and > 0) iff basis == 'batch'.
    batch_yield: Mapped[Any | None] = mapped_column(Numeric(20, 8), nullable=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False, server_default="mm")
    source_payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_metadata: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    activated_at: Mapped[Any | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    activated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    retired_at: Mapped[Any | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recipe: Mapped[Recipe] = relationship("Recipe", back_populates="versions")
    lines: Mapped[list[RecipeLine]] = relationship(
        "RecipeLine",
        back_populates="version",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class RecipeLine(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "recipe_lines"
    __table_args__ = (
        UniqueConstraint("recipe_version_id", "item_id", name="uq_recipe_version_item"),
        CheckConstraint("quantity > 0", name="ck_recipe_line_positive_quantity"),
        CheckConstraint(
            "yield_percentage > 0 AND yield_percentage <= 1",
            name="ck_recipe_line_valid_yield",
        ),
    )

    recipe_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recipe_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    quantity: Mapped[Any] = mapped_column(Numeric(20, 8), nullable=False)
    yield_percentage: Mapped[Any] = mapped_column(
        Numeric(8, 6), nullable=False, server_default="1"
    )
    inactive_in_order_types: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    source_metadata: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    version: Mapped[RecipeVersion] = relationship(
        "RecipeVersion", back_populates="lines"
    )
    #: The inventory item this line consumes. Eager-loaded so the derived
    #: ``ingredient_unit`` below resolves without a lazy load under async.
    item: Mapped["InventoryItem"] = relationship(
        "InventoryItem", lazy="selectin", viewonly=True
    )
    #: The recipe is authored in the item's *ingredient* unit, but that unit is
    #: not frozen onto the line — it is read live from the item so that changing
    #: an item's ingredient unit (and its storage→ingredient factor, which cost
    #: and consumption already read live) is reflected by every recipe at once.
    ingredient_unit: AssociationProxy[str] = association_proxy(
        "item", "ingredient_unit"
    )


class InventorySourceEvent(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "inventory_source_events"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_inventory_source_event_idempotency"
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'posted', 'cancelled', 'exception')",
            name="ck_inventory_source_event_status",
        ),
    )

    accepted_sequence: Mapped[int] = mapped_column(
        BigInteger, Identity(), nullable=False, unique=True, index=True
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="pending", index=True
    )
    occurred_at: Mapped[Any | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    accepted_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    frozen_plan: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="{}")
    recipe_version_ids: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    #: Active-recipe generation used by the most recent snapshot attempt.  A
    #: pending missing-recipe event is eligible for recovery only after the
    #: singleton catalog generation advances beyond this value.
    recipe_catalog_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_transactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[Any | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class InventoryReportTemplate(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "inventory_report_templates"
    __table_args__ = (
        UniqueConstraint(
            "branch_id",
            "report_type",
            "version_number",
            name="uq_inventory_report_template_revision",
        ),
        CheckConstraint(
            "report_type IN ('production', 'finished_goods', 'raw_materials', "
            "'packaging', 'spot_check')",
            name="ck_inventory_report_template_type",
        ),
        CheckConstraint(
            "cadence IN ('per_till', 'per_business_day', 'ad_hoc')",
            name="ck_inventory_report_template_cadence",
        ),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    report_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    cadence: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="per_till"
    )
    is_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    #: The order the shop fills the reports at close, low first. It is not
    #: cosmetic: the reports cascade through the ledger, so Production & Finished
    #: Goods must post before Raw Materials for the raw-material consumption to
    #: appear. The register presents outstanding reports in this order.
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    version_number: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    configuration: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    approval_cost_threshold: Mapped[Any | None] = mapped_column(
        Numeric(16, 4), nullable=True
    )
    approval_variance_percent: Mapped[Any | None] = mapped_column(
        Numeric(8, 4), nullable=True
    )
    items: Mapped[list[InventoryReportTemplateItem]] = relationship(
        "InventoryReportTemplateItem",
        back_populates="template",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class InventoryReportTemplateItem(Base, UUIDMixin):
    __tablename__ = "inventory_report_template_items"
    __table_args__ = (
        UniqueConstraint(
            "template_id", "item_id", name="uq_inventory_report_template_item"
        ),
        CheckConstraint(
            "required_input IN ('physical_count', 'internal_use', 'waste', 'receipt')",
            name="ck_inventory_report_template_item_input",
        ),
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_report_templates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    required_input: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="physical_count"
    )
    template: Mapped[InventoryReportTemplate] = relationship(
        "InventoryReportTemplate", back_populates="items"
    )


class ShiftInventoryReport(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "shift_inventory_reports"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_shift_inventory_report_idempotency"
        ),
        CheckConstraint(
            "status IN ('outstanding', 'draft', 'pending_approval', 'approved', "
            "'posted', 'deferred', 'skipped', 'rejected')",
            name="ck_shift_inventory_report_status",
        ),
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_report_templates.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    till_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tills.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    business_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="outstanding"
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    last_save_idempotency_key: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    last_save_payload_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    template_snapshot: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    #: The provisional movement cutoff frozen at prefill/refresh — what the
    #: register edits and submits against (see ``save_report``'s optimistic check).
    base_posting_sequence: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: The AUTHORITATIVE closing boundary, stamped at post: the branch's
    #: posting-sequence high-water immediately BEFORE this report posted its own
    #: reconciliation transactions. A report's movement window is
    #: ``(previous report's posting_cutoff_sequence, this posting_cutoff_sequence]``,
    #: so every closed movement lands in exactly one report and a report's own
    #: postings (which sit above this cutoff) land in none. NULL until posted.
    posting_cutoff_sequence: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    deferred_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitted_at: Mapped[Any | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_at: Mapped[Any | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_transactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    lines: Mapped[list[ShiftInventoryReportLine]] = relationship(
        "ShiftInventoryReportLine",
        back_populates="report",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    #: Reviewer comments, oldest first — how an approver tells the shop why a
    #: report was rejected or what to check, read back on the till. Selectin so a
    #: report always carries its thread without a per-report follow-up query; a
    #: report has a handful of comments at most.
    comments: Mapped[list[ShiftInventoryReportComment]] = relationship(
        "ShiftInventoryReportComment",
        back_populates="report",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ShiftInventoryReportComment.created_at",
    )


class ShiftInventoryReportLine(Base, UUIDMixin):
    __tablename__ = "shift_inventory_report_lines"
    __table_args__ = (
        UniqueConstraint("report_id", "item_id", name="uq_shift_inventory_report_line"),
    )

    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shift_inventory_reports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    unit: Mapped[str] = mapped_column(String(30), nullable=False)
    opening_quantity: Mapped[Any] = mapped_column(
        Numeric(20, 6), nullable=False, server_default="0"
    )
    purchasing_quantity: Mapped[Any] = mapped_column(
        Numeric(20, 6), nullable=False, server_default="0"
    )
    transfer_in_quantity: Mapped[Any] = mapped_column(
        Numeric(20, 6), nullable=False, server_default="0"
    )
    production_quantity: Mapped[Any] = mapped_column(
        Numeric(20, 6), nullable=False, server_default="0"
    )
    sales_consumption_quantity: Mapped[Any] = mapped_column(
        Numeric(20, 6), nullable=False, server_default="0"
    )
    production_consumption_quantity: Mapped[Any] = mapped_column(
        Numeric(20, 6), nullable=False, server_default="0"
    )
    transfer_out_quantity: Mapped[Any] = mapped_column(
        Numeric(20, 6), nullable=False, server_default="0"
    )
    waste_quantity: Mapped[Any] = mapped_column(
        Numeric(20, 6), nullable=False, server_default="0"
    )
    internal_use_quantity: Mapped[Any] = mapped_column(
        Numeric(20, 6), nullable=False, server_default="0"
    )
    #: The net of every ledger movement in the window that has no column of its
    #: own — a customer restock, a manual adjustment, a return to supplier. Signed
    #: (may be negative), ledger-filled and read-only, so Opening + Σcolumns ties
    #: to the system closing and the count's variance is measured against it.
    adjustment_quantity: Mapped[Any] = mapped_column(
        Numeric(20, 6), nullable=False, server_default="0"
    )
    #: Raw material consumed in production beyond the recipe (off-recipe use, or
    #: producing a good not set up in the system). Shop-entered on the
    #: raw-materials/packaging report; posts an EXTRA_PRODUCTION_USE movement. It
    #: is an additional deduction, independent of the recipe-derived
    #: ``production_consumption_quantity``.
    extra_production_consumption_quantity: Mapped[Any] = mapped_column(
        Numeric(20, 6), nullable=False, server_default="0"
    )
    expected_quantity: Mapped[Any] = mapped_column(
        Numeric(20, 6), nullable=False, server_default="0"
    )
    #: The on-hand level stamped at post, so the NEXT report's Opening is this
    #: stored Closing rather than a re-derivation of ``expected − net_movement``.
    #: NULL until the report posts (and on legacy pre-migration rows).
    closing_quantity: Mapped[Any | None] = mapped_column(Numeric(20, 6), nullable=True)
    entered_quantity: Mapped[Any | None] = mapped_column(Numeric(20, 6), nullable=True)
    confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    variance_quantity: Mapped[Any | None] = mapped_column(Numeric(20, 6), nullable=True)
    variance_cost: Mapped[Any | None] = mapped_column(Numeric(20, 4), nullable=True)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_summary: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    report: Mapped[ShiftInventoryReport] = relationship(
        "ShiftInventoryReport", back_populates="lines"
    )


class ShiftInventoryReportComment(Base, UUIDMixin, TimestampMixin):
    """One reviewer note on a submitted report, shown back to the shop on the till.

    The report's own free-text ``notes``/``deferred_reason`` are the shop's and
    the rejection's single fields; this is the conversation on top — why a count
    was queried, what to recheck. ``author_name`` is snapshotted at write time so
    the note keeps its attribution even after the user row is removed
    (``author_id`` then goes null via SET NULL).
    """

    __tablename__ = "shift_inventory_report_comments"

    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shift_inventory_reports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    report: Mapped[ShiftInventoryReport] = relationship(
        "ShiftInventoryReport", back_populates="comments"
    )
