"""Repair Deliveroo's cancelled-order credit and re-promote rounding gaps.

1. Deliveroo paid +26.98 on cancelled order 7ebbf9a3 (AGG-20260916-071): a
   "Cancelled order value" row (+40.00) and a "Deliveroo commission on cancelled
   order" row (−12.40, VAT −0.62). The statement parser keyed each row's net
   payable on the order, so the second row's −13.02 overwrote the first's
   +40.00, and the P&L booked a 13.02 charge. The parser now sums an order's rows;
   this repairs the line, the marketplace row and the order already stored, each
   guarded on the exact value it replaces.

2. Settled orders whose fees miss their payout by a few fils (Talabat rounds the
   net it pays, not each fee's VAT: 175 orders in September, 27 in August; Careem
   15) are re-promoted, where `promote._commission_to_the_net` now books the
   commission that reconciles to the payout. Marking the row changed is how the
   hourly promotion revisits an order, so the logic stays in one place. A row the
   promotion has already reconciled no longer matches, so a replay does nothing.

Revision ID: 314_pnl_net_to_the_fil
Revises: 313_noon_droo_cancel_net
Create Date: 2026-10-10
"""

from typing import Sequence, Union

from alembic import op

revision: str = "314_pnl_net_to_the_fil"
down_revision: Union[str, None] = "313_noon_droo_cancel_net"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LINE = "80904499:7ebbf9a3-b935-44a2-ba31-09dfe5ab6061:net_payable"
_AGG = "78eb399e-c2cd-33dc-9b8e-f6f6f0179920"
_ORDER = "AGG-20260916-071"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE aggregator_statement_line SET amount = 26.98
         WHERE channel = 'deliveroo' AND source_key = '{_LINE}' AND amount = -13.02
        """
    )
    op.execute(
        f"""
        UPDATE aggregator_order SET net_payable = 26.98
         WHERE channel = 'deliveroo' AND external_order_id = '{_AGG}'
           AND net_payable = -13.02
        """
    )
    op.execute(
        f"""
        UPDATE orders SET marketplace_cancellation_net = 26.98
         WHERE order_number = '{_ORDER}' AND marketplace_cancellation_net = -13.02
        """
    )
    op.execute(
        """
        UPDATE aggregator_order SET updated_at = now()
         WHERE statement_id IS NOT NULL
           AND net_payable IS NOT NULL
           AND commission_amount IS NOT NULL
           AND promoted_at IS NOT NULL
           AND lower(coalesce(status, '')) NOT IN
               ('cancelled', 'canceled', '50', 'rejected', 'failed', 'declined')
           AND abs(round(
                 coalesce(gross_sales, 0)
               - least(coalesce(refund_amount, 0), coalesce(gross_sales, 0))
               - commission_amount
               - coalesce(payment_fee, 0)
               - coalesce(cancellation_fee, 0)
               - coalesce(marketing_fee, 0)
               - net_payable, 2)) BETWEEN 0.01 AND 0.05
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE orders SET marketplace_cancellation_net = -13.02
         WHERE order_number = '{_ORDER}' AND marketplace_cancellation_net = 26.98
        """
    )
    op.execute(
        f"""
        UPDATE aggregator_order SET net_payable = -13.02
         WHERE channel = 'deliveroo' AND external_order_id = '{_AGG}'
           AND net_payable = 26.98
        """
    )
    op.execute(
        f"""
        UPDATE aggregator_statement_line SET amount = -13.02
         WHERE channel = 'deliveroo' AND source_key = '{_LINE}' AND amount = 26.98
        """
    )
