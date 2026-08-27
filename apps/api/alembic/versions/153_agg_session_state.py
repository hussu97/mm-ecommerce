"""Persist the Playwright storage_state so a worker restart is not a re-login.

The httpx ingest already replays the name→value cookie map and tokens. That is
not enough to *reopen a browser*: Playwright needs domain/path/expiry/httpOnly
cookies plus localStorage, and Keeta keeps shop ids in sessionStorage. Those
live in `aggregator_session.storage_state_encrypted`. A new container with an
empty `/data` volume hydrates from this column instead of asking the operator
for OTP again.

Revision ID: 153_agg_session_state
Revises: 152_aggregator_branch_map_seed
Create Date: 2026-08-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "153_agg_session_state"
down_revision: Union[str, None] = "152_aggregator_branch_map_seed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "aggregator_session",
        sa.Column("storage_state_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("aggregator_session", "storage_state_encrypted")
