"""CMS pages table with seed data

Revision ID: 008_cms_pages
Revises: 007_i18n_foundation
Create Date: 2026-03-06 00:00:00.000000

"""

import uuid
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "008_cms_pages"
down_revision: Union[str, None] = "007_i18n_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Seed content ──────────────────────────────────────────────────────────────

ABOUT_CONTENT = {
    "en": {
        "hero": {
            "title": "Made with 100% Love",
            "subtitle": "Every bite tells a story of passion, craft, and a deep love for bringing joy through food.",
        },
        "story_1": {
            "title": "A kitchen, a dream, and a lot of chocolate",
            "label": "The Beginning",
            "body": (
                "Melting Moments started as a simple love for baking — late nights experimenting with "
                "chocolate ratios, butter temperatures, and the perfect fudgy brownie texture. What "
                "began as gifts for family and friends quickly became something far greater.\n\n"
                "I'm Fatema Abbasi, and I founded Melting Moments from my home kitchen in the UAE "
                "with one mission: to create handcrafted desserts that make people smile. No factory "
                "lines. No bulk orders. Just me, my oven, and a genuine obsession with quality.\n\n"
                "Every batch is made fresh to order using the finest ingredients — Belgian chocolate, "
                "real butter, and carefully sourced produce. The result is something you can taste the "
                "difference in from the very first bite."
            ),
            "image_url": "/images/photos/person_shot_2.png",
        },
        "story_2": {
            "title": "Baked fresh, delivered with care",
            "label": "The Craft",
            "body": (
                "Everything at Melting Moments is made to order. That means when you place an order, "
                "we start baking — not pulling from a shelf. Your brownies are warm from the oven. "
                "Your cookies are soft and chewy. Your cookie melts are perfectly gooey.\n\n"
                "We deliver across the UAE — Dubai, Sharjah, Ajman, and more — with each order "
                "packaged carefully to arrive as beautiful as it left the kitchen.\n\n"
                "Beyond everyday treats, we love creating custom orders for special moments: birthday "
                "boxes, wedding favours, Eid gifting, corporate packages. If you can dream it, we "
                "can bake it."
            ),
            "image_url": "/images/photos/person_shot_3.png",
        },
        "values": [
            {
                "icon": "favorite",
                "title": "Made with Love",
                "description": "Every item is baked fresh to order. No preservatives, no shortcuts — just honest, heartfelt baking.",
            },
            {
                "icon": "eco",
                "title": "Quality Ingredients",
                "description": "We source the finest ingredients — premium chocolate, real butter, and fresh produce for every batch.",
            },
            {
                "icon": "diversity_3",
                "title": "For Every Occasion",
                "description": "Birthdays, weddings, Eid, corporate gifting — we craft something special for every moment worth celebrating.",
            },
            {
                "icon": "local_shipping",
                "title": "Delivered Across UAE",
                "description": "We deliver to Dubai, Sharjah, Ajman and beyond. Orders are packed carefully to arrive picture-perfect.",
            },
        ],
        "cta": {
            "title": "Ready to indulge?",
            "subtitle": "Browse our range of handcrafted brownies, cookies, and desserts — made fresh for you.",
            "button_text": "Shop Now",
            "button_link": "/brownies",
        },
        "seo": {
            "title": "About Me",
            "description": "Meet Fatema Abbasi — the baker behind Melting Moments. Handcrafted brownies, cookies and desserts made with love from her home kitchen in the UAE.",
        },
    }
}

FAQ_CONTENT = {
    "en": {
        "header": {
            "title": "Frequently Asked Questions",
            "subtitle": "Everything you need to know about ordering, delivery, and our products.",
        },
        "items": [
            {
                "question": "Where do you deliver?",
                "answer": "We deliver across the UAE — including Dubai, Sharjah, Ajman, Abu Dhabi, Ras Al Khaimah, Fujairah, and Umm Al Quwain. Delivery to Dubai, Sharjah, and Ajman is AED 35. All other emirates are AED 50. Orders above AED 200 qualify for free delivery.",
            },
            {
                "question": "How far in advance do I need to order?",
                "answer": "We recommend placing orders at least 24–48 hours in advance to ensure freshness and availability. For large or custom orders (events, corporate gifting, weddings), please reach out at least 5–7 days ahead so we can plan accordingly.",
            },
            {
                "question": "Can I pick up my order?",
                "answer": "Yes! Store pickup is available and completely free of charge. Once your order is placed, we'll confirm the pickup time and location via WhatsApp. Orders placed before 12 PM are typically ready for same-day pickup.",
            },
            {
                "question": "Do you take custom orders?",
                "answer": "Absolutely. We love creating bespoke treats for birthdays, Eid, weddings, corporate events, and more. Get in touch via WhatsApp or the contact form with your requirements and we'll put together something special just for you.",
            },
            {
                "question": "What payment methods do you accept?",
                "answer": "We accept card payments online (Visa, Mastercard, and Apple Pay via Stripe). Buy-now-pay-later options through Tabby and Tamara are coming soon. Cash on delivery is not currently available.",
            },
            {
                "question": "Are your products halal?",
                "answer": "Yes, all our products are 100% halal. We use halal-certified ingredients and maintain strict hygiene standards throughout our baking process. We do not use any alcohol-based flavourings.",
            },
            {
                "question": "How long do the products stay fresh?",
                "answer": "Our brownies and cookies stay fresh for 3–5 days when stored in a cool, dry place. For best results, keep them in an airtight container. Cookie melts are best enjoyed within 2–3 days. We don't use preservatives, so freshness is best enjoyed soon after delivery!",
            },
            {
                "question": "What if I have an allergy or dietary requirement?",
                "answer": "Our products are made in a home kitchen that handles nuts, dairy, eggs, gluten, and other common allergens. We cannot guarantee an allergen-free environment. If you have a specific requirement, please contact us before ordering so we can advise you appropriately.",
            },
        ],
        "cta": {
            "title": "We're here to help",
            "subtitle": "Can't find the answer you're looking for? Reach out and we'll get back to you as soon as possible.",
            "whatsapp_text": "WhatsApp Us",
            "contact_text": "Contact Form",
        },
        "seo": {
            "title": "FAQ",
            "description": "Frequently asked questions about ordering, delivery, payments, and products at Melting Moments Cakes in the UAE.",
        },
    }
}

CONTACT_CONTENT = {
    "en": {
        "header": {
            "title": "Get in Touch",
            "subtitle": "Custom orders, delivery questions, event catering — we're always happy to help.",
        },
        "info": {
            "phone": "+971 56 352 6578",
            "whatsapp": "https://wa.me/971563526578",
            "email": "hello@meltingmomentscakes.com",
            "location": "Dubai, UAE",
            "location_detail": "Delivering across the UAE",
            "hours": "Mon – Sat: 9 AM – 9 PM",
            "hours_detail": "Orders before 12 PM for same-day",
        },
        "seo": {
            "title": "Contact",
            "description": "Get in touch with Melting Moments Cakes. Order custom treats, ask about delivery, or just say hello — we're always happy to hear from you.",
        },
    }
}

PAGES = [
    ("about", ABOUT_CONTENT),
    ("faq", FAQ_CONTENT),
    ("contact", CONTACT_CONTENT),
]


def upgrade() -> None:
    op.create_table(
        "cms_pages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("slug", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("is_active", sa.Boolean, nullable=False, default=True),
        sa.Column("content", JSONB, nullable=False, default=dict),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Seed
    cms_pages = sa.table(
        "cms_pages",
        sa.column("id", UUID),
        sa.column("slug", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("content", JSONB),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )

    now = datetime.utcnow()
    op.bulk_insert(
        cms_pages,
        [
            {
                "id": uuid.uuid4(),
                "slug": slug,
                "is_active": True,
                "content": content,
                "created_at": now,
                "updated_at": now,
            }
            for slug, content in PAGES
        ],
    )


def downgrade() -> None:
    op.drop_table("cms_pages")
