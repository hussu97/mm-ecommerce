"""FAQ: cancellations & returns, and the refund policy.

Two questions the FAQ did not answer, asked for on 2026-09-28, in both locales:

* **Cancel or return** — not once the order is confirmed, and why: it is baked
  to order, so confirming it commits the kitchen, and food that has been made
  (or has left us) cannot be resold for food-safety reasons.
* **Refunds** — none as a rule, but a spoilt order is something to raise with us
  through the contact page, linked from the answer with the `[label](/contact)`
  syntax the storefront's FAQ now renders as a link.

Inserted after "What payment methods do you accept?", beside the other
ordering-and-paying answers; appended if that question has been removed.

**Guarded, per convention 7.** A question already present under the same wording
is not added again, so a second run, an environment that already has it, or a
console edit that kept the wording leaves the page alone. The downgrade removes
only an item whose question *and* answer still read exactly as written here.

Revision ID: 303_faq_cancel_refund
Revises: 302_courier_delay_window
Create Date: 2026-09-28
"""

from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "303_faq_cancel_refund"
down_revision: Union[str, None] = "302_courier_delay_window"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The question the new ones follow, per locale.
_AFTER = {
    "en": "What payment methods do you accept?",
    "ar": "ما طرق الدفع المتاحة؟",
}

_ITEMS: dict[str, list[dict[str, str]]] = {
    "en": [
        {
            "question": "Can I cancel or return my order?",
            "answer": (
                "Once your order is confirmed, it can't be cancelled or returned. "
                "Everything is baked fresh to order, so the moment it's confirmed "
                "our kitchen starts on it — ingredients are set aside and your "
                "slot is taken. And for food-safety reasons, anything that has "
                "been made or has left our kitchen can't be taken back or sold "
                "again. Please check the items, date and address before you place "
                "the order."
            ),
        },
        {
            "question": "What is your refund policy?",
            "answer": (
                "Our products are non-refundable. If something arrives spoilt, "
                "though, please [contact us](/contact) with your order number and "
                "a photo, and we'll talk it through with you."
            ),
        },
    ],
    "ar": [
        {
            "question": "هل يمكنني إلغاء طلبي أو إرجاعه؟",
            "answer": (
                "بعد تأكيد الطلب، لا يمكن إلغاؤه أو إرجاعه. كل شيء يُخبز طازجاً "
                "عند الطلب، فبمجرد تأكيده يبدأ مطبخنا العمل عليه — تُخصَّص المكونات "
                "ويُحجز موعدك. ولأسباب تتعلق بسلامة الغذاء، لا يمكن استرجاع أي منتج "
                "بعد تحضيره أو خروجه من مطبخنا، ولا إعادة بيعه. يُرجى مراجعة "
                "المنتجات والموعد والعنوان قبل إتمام الطلب."
            ),
        },
        {
            "question": "ما هي سياسة الاسترداد لديكم؟",
            "answer": (
                "منتجاتنا غير قابلة للاسترداد. لكن إن وصلك منتج تالف، يُرجى "
                "[التواصل معنا](/contact) مع رقم الطلب وصورة، وسنتحدث معك بشأنه."
            ),
        },
    ],
}


def _load(conn) -> dict | None:
    row = conn.execute(
        sa.text("SELECT content::text FROM cms_pages WHERE slug = 'faq'")
    ).first()
    return None if row is None else json.loads(row[0])


def _save(conn, content: dict) -> None:
    conn.execute(
        sa.text(
            "UPDATE cms_pages SET content = CAST(:content AS jsonb) WHERE slug = 'faq'"
        ),
        {"content": json.dumps(content, ensure_ascii=False)},
    )


def upgrade() -> None:
    conn = op.get_bind()
    content = _load(conn)
    if content is None:
        return
    changed = False
    for locale, new_items in _ITEMS.items():
        page = content.get(locale)
        if not isinstance(page, dict) or not isinstance(page.get("items"), list):
            continue
        items = page["items"]
        asked = {item.get("question") for item in items if isinstance(item, dict)}
        fresh = [item for item in new_items if item["question"] not in asked]
        if not fresh:
            continue
        anchor = next(
            (
                i + 1
                for i, item in enumerate(items)
                if isinstance(item, dict) and item.get("question") == _AFTER[locale]
            ),
            len(items),
        )
        items[anchor:anchor] = fresh
        changed = True
    if changed:
        _save(conn, content)


def downgrade() -> None:
    conn = op.get_bind()
    content = _load(conn)
    if content is None:
        return
    for locale, new_items in _ITEMS.items():
        page = content.get(locale)
        if isinstance(page, dict) and isinstance(page.get("items"), list):
            page["items"] = [item for item in page["items"] if item not in new_items]
    _save(conn, content)
