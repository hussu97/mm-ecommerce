"""Let the ledger poster write line balances while closing a transaction.

The immutable-line trigger blocked every real posting: it rejected any update to a
line whose parent transaction is `closed`, with no exception for the one path that
legitimately does so. `post_transaction` sets `mm.inventory_posting = 'on'` for the
flush that closes a transaction and writes its lines' balances, and the *transaction*
trigger already honours that marker — the *line* trigger did not, so
`consumption_from_orders` (and every count) raised "closed inventory transaction
lines are immutable" and no stock ever moved. This teaches the line trigger the same
bypass. Idempotent CREATE OR REPLACE; the poster is the only writer that sets the
marker, and only for its own flush.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "194_fix_inv_line_trigger"
down_revision: Union[str, None] = "193_unseed_till_close_report"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_WITH_BYPASS = """
CREATE OR REPLACE FUNCTION public.prevent_closed_inventory_line_mutation()
RETURNS trigger LANGUAGE plpgsql AS $function$
    DECLARE parent_status text;
    BEGIN
      IF COALESCE(current_setting('mm.inventory_posting', true), '') = 'on' THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
      END IF;
      SELECT status INTO parent_status FROM inventory_transactions
      WHERE id = CASE WHEN TG_OP = 'INSERT' THEN NEW.transaction_id ELSE OLD.transaction_id END;
      IF parent_status = 'closed' THEN
        RAISE EXCEPTION 'closed inventory transaction lines are immutable';
      END IF;
      IF TG_OP = 'UPDATE' AND NEW.transaction_id <> OLD.transaction_id THEN
        SELECT status INTO parent_status FROM inventory_transactions WHERE id = NEW.transaction_id;
        IF parent_status = 'closed' THEN
          RAISE EXCEPTION 'closed inventory transaction lines are immutable';
        END IF;
      END IF;
      RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END $function$;
"""

_WITHOUT_BYPASS = """
CREATE OR REPLACE FUNCTION public.prevent_closed_inventory_line_mutation()
RETURNS trigger LANGUAGE plpgsql AS $function$
    DECLARE parent_status text;
    BEGIN
      SELECT status INTO parent_status FROM inventory_transactions
      WHERE id = CASE WHEN TG_OP = 'INSERT' THEN NEW.transaction_id ELSE OLD.transaction_id END;
      IF parent_status = 'closed' THEN
        RAISE EXCEPTION 'closed inventory transaction lines are immutable';
      END IF;
      IF TG_OP = 'UPDATE' AND NEW.transaction_id <> OLD.transaction_id THEN
        SELECT status INTO parent_status FROM inventory_transactions WHERE id = NEW.transaction_id;
        IF parent_status = 'closed' THEN
          RAISE EXCEPTION 'closed inventory transaction lines are immutable';
        END IF;
      END IF;
      RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END $function$;
"""


def upgrade() -> None:
    op.execute(_WITH_BYPASS)


def downgrade() -> None:
    op.execute(_WITHOUT_BYPASS)
