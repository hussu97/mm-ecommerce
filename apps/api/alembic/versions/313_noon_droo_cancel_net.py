"""Book what noon and Deliveroo statements settled on cancelled orders.

`promote._cancellation_net` used to trust a cancelled order's settled figure
only on Talabat and Keeta, so on noon and Deliveroo it stayed NULL and the P&L
fell back to the order's fee columns — or dropped the order altogether:

* AGG-20260924-009 (noon FG9ONNBM32FWXVA): cancelled in noon's OMS, billed as a
  40.00 sale on its 30 Sep statement, 28.66 paid. Not a sale, not a charged
  cancellation, not a settled one — in no line of the P&L.
* AGG-20260930-054 (Deliveroo): charged 17.90 on a cancellation; booked at
  0.85, because `cancelled_order_charge` lands on no fee column.

The promotion now carries the statement's net once the order is on one. This
writes it onto the cancelled orders already promoted, which no re-promotion
will revisit (their `aggregator_order` rows will not change again).

Guarded: only a cancelled noon/Deliveroo order whose figure is still NULL and
whose marketplace row is on a statement, so it is a no-op once promotion has
written it, and on a database restored from an older dump it writes exactly
what promotion would. The downgrade clears what it wrote.

Revision ID: 313_noon_droo_cancel_net
Revises: 312_third_party_stale_fares
Create Date: 2026-10-10
"""

from typing import Sequence, Union

from alembic import op

revision: str = "313_noon_droo_cancel_net"
down_revision: Union[str, None] = "312_third_party_stale_fares"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MATCH = """
      FROM aggregator_order AS a
     WHERE a.mm_order_id = o.id
       AND a.channel IN ('noon', 'deliveroo')
       AND a.statement_id IS NOT NULL
       AND a.net_payable IS NOT NULL
       AND o.source = 'aggregator'
       AND o.status = 'cancelled'
"""


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE orders AS o
           SET marketplace_cancellation_net = round(a.net_payable, 2),
               marketplace_cancellation_provisional = false
        {_MATCH}
           AND o.marketplace_cancellation_net IS NULL
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE orders AS o
           SET marketplace_cancellation_net = NULL
        {_MATCH}
           AND o.marketplace_cancellation_net = round(a.net_payable, 2)
        """
    )
