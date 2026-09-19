"""Rename the enquiry form heading to "Customized cake order".

Guarded content edit: only swaps the heading where it is still the exact value
263 seeded, so an admin edit in the Content tab (or a restored dump) wins.

Revision ID: 264_cater_form_heading
Revises: 263_cater_gallery_form
Create Date: 2026-09-19
"""

from __future__ import annotations

import json
from typing import Any, Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "264_cater_form_heading"
down_revision: Union[str, None] = "263_cater_gallery_form"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = {"en": "Request a custom order", "ar": "اطلب طلباً خاصاً"}
_NEW = {"en": "Customized cake order", "ar": "طلب كيكة مخصّصة"}


def _swap(mapping_from: dict, mapping_to: dict) -> None:
    conn = op.get_bind()
    row = conn.execute(
        sa.text("SELECT content FROM cms_pages WHERE slug = 'home'")
    ).fetchone()
    if row is None:
        return
    content: dict[str, Any] = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    changed = False
    for locale in mapping_from:
        form = ((content.get(locale) or {}).get("cater") or {}).get("form")
        if isinstance(form, dict) and form.get("heading") == mapping_from[locale]:
            form["heading"] = mapping_to[locale]
            changed = True
    if changed:
        conn.execute(
            sa.text(
                "UPDATE cms_pages SET content = :content, updated_at = NOW() "
                "WHERE slug = 'home'"
            ),
            {"content": json.dumps(content)},
        )


def upgrade() -> None:
    _swap(_OLD, _NEW)


def downgrade() -> None:
    _swap(_NEW, _OLD)
