"""Barsha Heights trades as "Melting Moments Cafe" — receipts, invoices, address.

The Barsha counter is booked under Najm AlShamal Coffee Shop LLC (the `najm`
legal entity, not VAT-registered). Its brand was "Attibassi Coffee" with the
Attibassi logo; the shop now trades as Melting Moments Cafe. The legal name,
VAT status and invoice title do not change — only what the customer sees:

* `legal_entities` (`najm`): brand "Melting Moments Cafe" and the café logo.
  Receipts (mm-pos reads the entity off the counter bundle / order), the custom
  order invoice, the printed menu and the admin pickers all read these two
  columns. No accent on "Cafe": the receipt is encoded to a thermal codepage.
* `branches` (Barsha): the pickup/courier address led with "Attibassi Coffee"
  (Arabic "قهوة عتيبة"); it now leads with the café's name.

Guarded so it cannot fight the admin: each update matches the exact old value,
so once anybody edits it by hand this matches nothing — on a restored dump too.

Revision ID: 322_mm_cafe_rebrand
Revises: 321_iced_coffee_rename
Create Date: 2026-11-08
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "322_mm_cafe_rebrand"
down_revision: Union[str, None] = "321_iced_coffee_rename"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LOGOS = "https://storage.googleapis.com/mm-product-images/logos"
OLD_BRAND, NEW_BRAND = "Attibassi Coffee", "Melting Moments Cafe"
OLD_LOGO, NEW_LOGO = f"{_LOGOS}/attibassi.jpg", f"{_LOGOS}/melting-moments-cafe.png"

OLD_ADDRESS = "Attibassi Coffee, Al Shafar Tower 1, Barsha Heights, Dubai"
NEW_ADDRESS = "Melting Moments Cafe, Al Shafar Tower 1, Barsha Heights, Dubai"
OLD_ADDRESS_AR = "قهوة عتيبة، برج الشعفار 1، برشا هايتس، دبي"
NEW_ADDRESS_AR = "ملتينج مومنتس كافيه، برج الشعفار 1، برشا هايتس، دبي"


def _swap(table: str, column: str, old: str, new: str, extra: str = "") -> None:
    op.get_bind().execute(
        sa.text(
            f"UPDATE {table} SET {column} = CAST(:new AS text) "
            f"WHERE {column} = CAST(:old AS text){extra}"
        ),
        {"old": old, "new": new},
    )


def _apply(forward: bool) -> None:
    def pick(old: str, new: str) -> tuple[str, str]:
        return (old, new) if forward else (new, old)

    najm = " AND reference = 'najm'"
    _swap("legal_entities", "brand_name", *pick(OLD_BRAND, NEW_BRAND), najm)
    _swap("legal_entities", "logo_url", *pick(OLD_LOGO, NEW_LOGO), najm)
    _swap("branches", "address", *pick(OLD_ADDRESS, NEW_ADDRESS))
    _swap("branches", "address_localized", *pick(OLD_ADDRESS_AR, NEW_ADDRESS_AR))


def upgrade() -> None:
    _apply(forward=True)


def downgrade() -> None:
    _apply(forward=False)
