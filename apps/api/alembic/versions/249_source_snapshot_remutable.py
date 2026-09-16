"""Let a never-posted inventory source event be re-snapshotted.

The ``inventory_source_snapshot_immutable`` trigger (186) froze ``frozen_plan``
and ``recipe_version_ids`` on EVERY update to an ``inventory_source_events`` row.
But the recovery path is *built* to rewrite exactly those columns: a
``missing_recipe`` event freezes only the lines that expanded at acceptance, and
``source_event_service.retry_event`` / the pending sweeper re-run
``snapshot_order`` once a recipe is activated and replace the never-applied plan.
``retry_event`` is guarded to events that have **not** posted a movement
(``status != 'posted'`` and ``transaction_id IS NULL``), so re-freezing the plan
can never double-count consumed stock — the very safety the trigger was meant to
enforce. The two shipped together and contradicted: every sweep of a
``missing_recipe`` event raised "accepted inventory source snapshots are
immutable", the savepoint rolled back, and (because the ORM row stayed dirty) the
quarantine could not stick either — so the event re-poisoned its branch every
30s tick forever and its order never drew stock.

This narrows the freeze to what the code's own guard already treats as
untouchable: the plan is immutable only ONCE a movement has posted against it
(``transaction_id IS NOT NULL`` or ``status = 'posted'``). Identity and the
acceptance envelope stay immutable in every state, exactly as before. Downgrade
restores the original all-states freeze.

Revision ID: 249_source_snapshot_remutable
Revises: 248_recipe_live_unit
Create Date: 2026-09-16
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "249_source_snapshot_remutable"
down_revision: Union[str, None] = "248_recipe_live_unit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The identity + acceptance envelope: immutable in EVERY state (unchanged from 186).
_IDENTITY_GUARD = """
          IF NEW.id <> OLD.id
             OR NEW.created_at <> OLD.created_at
             OR NEW.accepted_sequence <> OLD.accepted_sequence
             OR NEW.branch_id <> OLD.branch_id
             OR NEW.source_type <> OLD.source_type
             OR NEW.source_id <> OLD.source_id
             OR NEW.source_revision <> OLD.source_revision
             OR NEW.idempotency_key <> OLD.idempotency_key
             OR NEW.occurred_at IS DISTINCT FROM OLD.occurred_at
             OR NEW.accepted_at <> OLD.accepted_at THEN
            RAISE EXCEPTION 'accepted inventory source snapshots are immutable';
          END IF;
"""


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_inventory_source_snapshot_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'inventory source events are append-only';
          END IF;
        """
        + _IDENTITY_GUARD
        + """
          -- The frozen plan is immutable ONLY once a movement has posted against
          -- it. A never-posted event (a missing_recipe PENDING/EXCEPTION awaiting
          -- recipe activation) is re-snapshotted by retry_event / the sweeper,
          -- which is safe precisely because no stock has moved — the same guard
          -- retry_event enforces in code (status != 'posted' and transaction_id
          -- IS NULL).
          IF (OLD.transaction_id IS NOT NULL OR OLD.status = 'posted') AND (
               NEW.frozen_plan <> OLD.frozen_plan
               OR NEW.recipe_version_ids <> OLD.recipe_version_ids) THEN
            RAISE EXCEPTION 'accepted inventory source snapshots are immutable';
          END IF;
          RETURN NEW;
        END $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_inventory_source_snapshot_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'inventory source events are append-only';
          END IF;
          IF NEW.id <> OLD.id
             OR NEW.created_at <> OLD.created_at
             OR NEW.accepted_sequence <> OLD.accepted_sequence
             OR NEW.branch_id <> OLD.branch_id
             OR NEW.source_type <> OLD.source_type
             OR NEW.source_id <> OLD.source_id
             OR NEW.source_revision <> OLD.source_revision
             OR NEW.idempotency_key <> OLD.idempotency_key
             OR NEW.occurred_at IS DISTINCT FROM OLD.occurred_at
             OR NEW.accepted_at <> OLD.accepted_at
             OR NEW.frozen_plan <> OLD.frozen_plan
             OR NEW.recipe_version_ids <> OLD.recipe_version_ids THEN
            RAISE EXCEPTION 'accepted inventory source snapshots are immutable';
          END IF;
          RETURN NEW;
        END $$
        """
    )
