"""Seed the "We cater to" gallery and enquiry-form copy into the home CMS row.

The section now shows real custom cakes (an auto-scrolling, click-to-zoom gallery)
and carries an inline enquiry form instead of a button to the contact page. The
images and the form's copy are CMS content so the shop can edit them in the admin
Content tab — this migration seeds the first set (images already uploaded to the
`cater/` folder of the public image bucket) and the bilingual form copy.

Guarded per the content-migration rule: it only *adds* the `gallery` and `form`
keys where they are absent, and only swaps the old subtitle where it still holds
the exact 049 default — so once a human edits any of it in the console (or on a
restored dump), this migration matches nothing and does nothing.

Revision ID: 263_cater_gallery_form
Revises: 262_custom_order_enquiries
Create Date: 2026-09-19
"""

from __future__ import annotations

import json
from typing import Any, Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "263_cater_gallery_form"
down_revision: Union[str, None] = "262_custom_order_enquiries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_BASE = "https://storage.googleapis.com/mm-product-images/cater"

# Interleaved so the marquee shows a mix of occasions rather than runs of one.
_GALLERY_EN = [
    {"image": f"{_BASE}/wedding-1.jpg", "alt": "Weddings"},
    {"image": f"{_BASE}/birthday-1.jpg", "alt": "Birthdays"},
    {"image": f"{_BASE}/baby-1.jpg", "alt": "Baby showers"},
    {"image": f"{_BASE}/kids-1.jpg", "alt": "Kids’ themes"},
    {"image": f"{_BASE}/engagement-1.jpg", "alt": "Engagements"},
    {"image": f"{_BASE}/firstbday-1.jpg", "alt": "First birthdays"},
    {"image": f"{_BASE}/wedding-2.jpg", "alt": "Weddings"},
    {"image": f"{_BASE}/birthday-2.jpg", "alt": "Birthdays"},
    {"image": f"{_BASE}/kids-2.jpg", "alt": "Kids’ themes"},
    {"image": f"{_BASE}/celebration-1.jpg", "alt": "Celebrations"},
    {"image": f"{_BASE}/baby-2.jpg", "alt": "Baby showers"},
    {"image": f"{_BASE}/wedding-3.jpg", "alt": "Weddings"},
    {"image": f"{_BASE}/birthday-3.jpg", "alt": "Birthdays"},
    {"image": f"{_BASE}/kids-3.jpg", "alt": "Kids’ themes"},
]

# Same photos, Arabic occasion captions.
_AR_ALT = {
    "Weddings": "أعراس",
    "Birthdays": "أعياد ميلاد",
    "Baby showers": "حفلات استقبال المولود",
    "Kids’ themes": "حفلات الأطفال",
    "Engagements": "خطوبة",
    "First birthdays": "عيد الميلاد الأول",
    "Celebrations": "مناسبات",
}
_GALLERY_AR = [{"image": g["image"], "alt": _AR_ALT[g["alt"]]} for g in _GALLERY_EN]

_FORM_EN = {
    "heading": "Request a custom order",
    "intro": (
        "Tell us what you have in mind and we’ll get back to you to plan the "
        "details. This is an enquiry, not an order — nothing is charged yet."
    ),
    "name_label": "Your name",
    "phone_label": "Phone number",
    "description_label": "What would you like?",
    "description_placeholder": (
        "Describe the cake or dessert — flavours, colours, the occasion…"
    ),
    "kg_label": "Approx. weight (kg) — optional",
    "images_label": "Inspiration photos — optional",
    "images_hint": "Up to 4 photos",
    "delivery_label": "Delivery by",
    "delivery_note": (
        "The delivery date will be confirmed only after we review your request."
    ),
    "submit_label": "Send request",
    "success_title": "Request received!",
    "success_body": (
        "Thank you — we’ll review your request and get back to you soon to "
        "confirm the details and delivery date."
    ),
}

_FORM_AR = {
    "heading": "اطلب طلباً خاصاً",
    "intro": (
        "أخبرنا بما يدور في ذهنك وسنعاود التواصل معك لترتيب التفاصيل. هذا استفسار "
        "وليس طلباً — لن يتم فرض أي رسوم الآن."
    ),
    "name_label": "الاسم",
    "phone_label": "رقم الهاتف",
    "description_label": "ماذا تريد؟",
    "description_placeholder": "صف الكيكة أو الحلوى — النكهات، الألوان، المناسبة…",
    "kg_label": "الوزن التقريبي (كجم) — اختياري",
    "images_label": "صور للإلهام — اختياري",
    "images_hint": "حتى 4 صور",
    "delivery_label": "التسليم بحلول",
    "delivery_note": "موعد التسليم يُؤكَّد فقط بعد مراجعة طلبك.",
    "submit_label": "إرسال الطلب",
    "success_title": "تم استلام طلبك!",
    "success_body": (
        "شكراً لك. سنراجع طلبك ونتواصل معك قريباً لتأكيد التفاصيل وموعد التسليم."
    ),
}

# The subtitle 049 seeded. Only this exact string is replaced with the
# custom-order framing; anything a human has since written is left alone.
_OLD_SUBTITLE = {
    "en": "Boxes for the days that matter.",
    "ar": "علب للأيام التي تهمّ.",
}
_NEW_SUBTITLE = {
    "en": "Custom cakes and desserts for every occasion.",
    "ar": "كيكات وحلويات مصممة خصيصاً لكل مناسبة.",
}

_SEED = {
    "en": {"gallery": _GALLERY_EN, "form": _FORM_EN},
    "ar": {"gallery": _GALLERY_AR, "form": _FORM_AR},
}


def upgrade() -> None:
    conn = op.get_bind()
    row = conn.execute(
        sa.text("SELECT content FROM cms_pages WHERE slug = 'home'")
    ).fetchone()
    if row is None:
        return
    content: dict[str, Any] = row[0] if isinstance(row[0], dict) else json.loads(row[0])

    changed = False
    for locale, seed in _SEED.items():
        cater = (content.get(locale) or {}).get("cater")
        if not isinstance(cater, dict):
            # No cater block for this locale — 049 always makes one, so this only
            # happens on an unexpected shape; leave it rather than invent one.
            continue
        # Add-if-absent: never overwrite what a human has curated.
        for key, value in seed.items():
            if not cater.get(key):
                cater[key] = value
                changed = True
        # Swap the subtitle only where it is still the exact 049 default.
        if cater.get("subtitle") == _OLD_SUBTITLE[locale]:
            cater["subtitle"] = _NEW_SUBTITLE[locale]
            changed = True

    if changed:
        conn.execute(
            sa.text(
                "UPDATE cms_pages SET content = :content, updated_at = NOW() "
                "WHERE slug = 'home'"
            ),
            {"content": json.dumps(content)},
        )


def downgrade() -> None:
    """Remove the seeded gallery/form and restore the old subtitle where it is
    still exactly what this migration set."""
    conn = op.get_bind()
    row = conn.execute(
        sa.text("SELECT content FROM cms_pages WHERE slug = 'home'")
    ).fetchone()
    if row is None:
        return
    content: dict[str, Any] = row[0] if isinstance(row[0], dict) else json.loads(row[0])

    for locale in _SEED:
        cater = (content.get(locale) or {}).get("cater")
        if not isinstance(cater, dict):
            continue
        if cater.get("gallery") == _SEED[locale]["gallery"]:
            cater.pop("gallery", None)
        if cater.get("form") == _SEED[locale]["form"]:
            cater.pop("form", None)
        if cater.get("subtitle") == _NEW_SUBTITLE[locale]:
            cater["subtitle"] = _OLD_SUBTITLE[locale]

    conn.execute(
        sa.text(
            "UPDATE cms_pages SET content = :content, updated_at = NOW() "
            "WHERE slug = 'home'"
        ),
        {"content": json.dumps(content)},
    )
