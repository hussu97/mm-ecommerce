"""Enforce one monotonic report-template revision per branch and report type.

Revision ID: 190_report_template_revisions
Revises: 189_fix_inventory_classify
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "190_report_template_revisions"
down_revision: Union[str, None] = "189_fix_inventory_classify"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A name identifies a report family to staff, not one immutable database
    # row: an operator must be able to create v2 with the same familiar name.
    # The revision tuple is the stable identity instead.
    # `186_inventory_v2` named this constraint, but tolerate restored/staged
    # databases where a previous downgrade deliberately did not recreate it:
    # recreating name uniqueness after valid v2 rows exist could require
    # deleting history. PostgreSQL has no `DROP CONSTRAINT IF EXISTS` operation
    # in Alembic, so discover the old two-column unique constraint first.
    op.execute(
        """
        DO $$
        DECLARE old_constraint text;
        BEGIN
          SELECT conname INTO old_constraint
          FROM pg_constraint
          WHERE conrelid = 'inventory_report_templates'::regclass
            AND contype = 'u'
            AND pg_get_constraintdef(oid) = 'UNIQUE (branch_id, name)';
          IF old_constraint IS NOT NULL THEN
            EXECUTE format(
              'ALTER TABLE inventory_report_templates DROP CONSTRAINT %I',
              old_constraint
            );
          END IF;
        END $$;
        """
    )
    # Existing live templates are already unique at this grain. The service
    # serializes allocation; this constraint is the database backstop for an
    # importer or future writer bypassing that service.
    op.create_unique_constraint(
        "uq_inventory_report_template_revision",
        "inventory_report_templates",
        ["branch_id", "report_type", "version_number"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_inventory_report_template_revision",
        "inventory_report_templates",
        type_="unique",
    )
    # Do not restore the retired name uniqueness: a valid v2 may share its
    # staff-facing name with v1, and recreating it would make a rollback fail
    # or require deleting legitimate revision history.
