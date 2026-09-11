"""Drop the vestigial POS Tags feature.

Tags (`tags` + `tagged_entities`) were a Foodics-legacy labelling system that
nothing ever assigned: the catalogue importer created tag *definitions* but no
code path linked a tag to a product, order or table, so the join table and the
two `*_tag_id(s)` columns were always empty and the tag-based Sales breakdowns
rendered nothing. Product/inventory categories cover the grouping need. This
retires the feature end to end — including the one concept with no category
equivalent, `tables.revenue_center_tag_id` (revenue-centre attribution), which
was likewise never populated.

Drop order matters: the columns that reference `tags` go first (the table FK,
then the array column), then `tagged_entities` (its `tag_id` FK cascades to
`tags`), then `tags` itself.

Revision ID: 229_drop_pos_tags
Revises: 228_order_delivery_courier_eta
Create Date: 2026-09-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "229_drop_pos_tags"
down_revision: Union[str, None] = "228_order_delivery_courier_eta"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    # Columns that reference tags.id first (drop_column cascades their FKs).
    op.drop_column("tables", "revenue_center_tag_id")
    op.drop_column("promotions", "customer_tag_ids")
    # Child table before parent (tagged_entities.tag_id FK → tags.id).
    op.drop_table("tagged_entities")
    op.drop_table("tags")


def downgrade() -> None:
    op.create_table(
        "tags",
        sa.Column("id", UUID, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("name_localized", sa.String(100), nullable=True),
        sa.Column("translations", JSONB, nullable=False, server_default="{}"),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("color", sa.String(9), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("type", "name", name="uq_tag_type_name"),
    )
    op.create_index("ix_tags_type", "tags", ["type"])

    op.create_table(
        "tagged_entities",
        sa.Column("id", UUID, nullable=False),
        sa.Column("tag_id", UUID, nullable=False),
        sa.Column("entity_type", sa.String(30), nullable=False),
        sa.Column("entity_id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tag_id", "entity_type", "entity_id", name="uq_tagged_entity"
        ),
    )
    op.create_index("ix_tagged_entities_tag_id", "tagged_entities", ["tag_id"])
    op.create_index(
        "ix_tagged_entities_entity_type", "tagged_entities", ["entity_type"]
    )
    op.create_index("ix_tagged_entities_entity_id", "tagged_entities", ["entity_id"])

    op.add_column(
        "tables",
        sa.Column(
            "revenue_center_tag_id",
            UUID,
            sa.ForeignKey("tags.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "promotions",
        sa.Column(
            "customer_tag_ids",
            postgresql.ARRAY(UUID),
            nullable=False,
            server_default="{}",
        ),
    )
