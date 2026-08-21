"""A terminal records which build it is running, and on what.

`devices.app_version` has existed since `034` and the console shows it as
"App" — but it is written once, by the pairing request, and never again. For an
iPad paired months and thirty builds ago that column is not stale so much as
answering a different question than the one it appears to: *what was installed
the day somebody first set this up*. `build-number.json` is at 36 while the
console will happily still say 1.0.

Two columns, because the marketing version alone does not identify a build.
Support asking "which build is that till on" needs the number that goes up every
time CI archives, and a future minimum-version gate needs something monotonic to
compare — which is the reason for capturing it now rather than when the gate is
written.

**`build_number` is text, not an integer.** Android's `versionCode` is a
monotonic int and iOS's `CFBundleVersion` usually is, but it is allowed to be
`36.1`, and CI does produce those. The column's job is to report faithfully what
the device said, not to do arithmetic on it — a comparison that needs ordering
can parse it at the point it needs ordering, where it also knows which platform's
rules apply.

**`platform` is `String` + CHECK**, per convention 6. Nullable, like
`build_number`, because every row in this table predates the header that fills
them and a terminal that has not been updated yet must keep working untouched.

Revision ID: 124_device_build_platform
Revises: 123_delete_promo_banner_row
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "124_device_build_platform"
down_revision: Union[str, None] = "123_delete_promo_banner_row"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Mirrored in `Device.__table_args__`. Spelled out rather than derived from an
#: enum: these are the two app stores, not a lifecycle, and adding a third is a
#: deliberate act that should show up as a migration.
ALLOWED_PLATFORMS = ("ios", "android")


def upgrade() -> None:
    op.add_column(
        "devices", sa.Column("build_number", sa.String(length=20), nullable=True)
    )
    op.add_column("devices", sa.Column("platform", sa.String(length=10), nullable=True))
    op.create_check_constraint(
        "ck_devices_platform_allowed",
        "devices",
        "platform IS NULL OR platform IN ('ios', 'android')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_devices_platform_allowed", "devices", type_="check")
    op.drop_column("devices", "platform")
    op.drop_column("devices", "build_number")
