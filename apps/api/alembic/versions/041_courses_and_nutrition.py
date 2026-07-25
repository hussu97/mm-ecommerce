"""Courses for staged firing, and full nutrition facts on a product.

Revision ID: 041_courses_and_nutrition
Revises: 040_product_web_visibility
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "041_courses_and_nutrition"
down_revision = "040_product_web_visibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "courses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("name_localized", sa.String(100), nullable=True),
        sa.Column(
            "translations",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )

    # Which course a line belongs to. Nullable: most orders are not coursed,
    # and a takeaway never is.
    op.add_column(
        "order_items",
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_order_items_course_id",
        "order_items",
        "courses",
        ["course_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_order_items_course_id", "order_items", ["course_id"])

    # Free-form so a new field (allergens, sugar) does not need a migration;
    # the shape is whatever the label on the packet states.
    op.add_column("products", sa.Column("nutrition", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "nutrition")
    op.drop_index("ix_order_items_course_id", table_name="order_items")
    op.drop_constraint("fk_order_items_course_id", "order_items", type_="foreignkey")
    op.drop_column("order_items", "course_id")
    op.drop_table("courses")
