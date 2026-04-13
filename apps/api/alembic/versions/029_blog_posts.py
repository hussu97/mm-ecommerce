"""Blog posts table with seed data

Revision ID: 029_blog_posts
Revises: 028_cms_about_contact_labels
Create Date: 2026-04-14 00:00:00.000000

"""

import uuid
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "029_blog_posts"
down_revision: Union[str, None] = "028_cms_about_contact_labels"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ─── Seed content ─────────────────────────────────────────────────────────────

POST_1 = {
    "en": {
        "title": "The Art of the Perfect Brownie",
        "excerpt": "What makes a brownie truly perfect? Fatema shares the secrets behind Melting Moments' signature fudgy brownies — from chocolate selection to the precise bake time that keeps them irresistibly soft.",
        "body": """Every baker has their obsession, and mine is the brownie.\n\nNot the cakey kind — the kind that sinks into itself, dense and glossy on top, with a pull that makes you close your eyes for a second. Getting there took years of testing, failing, and eating a lot of mediocre brownies.\n\n## The Chocolate Question\n\nIt starts with the chocolate. I use a blend of dark and semi-sweet — dark for the depth, semi-sweet to bring back just enough sweetness without tipping into candy territory. The ratio matters enormously. Too much dark and it becomes bitter; too much semi-sweet and you lose that sophisticated edge.\n\nButter is melted directly with the chocolate, not separately. This keeps the mixture homogeneous and contributes to that shiny crust that every good brownie deserves.\n\n## Sugar and Structure\n\nTwo sugars: caster and brown. The caster dissolves cleanly; the brown adds moisture and a faint caramel undertone that you might not notice consciously, but you would definitely notice its absence.\n\n## The Bake\n\nUnderbaking is not a mistake — it is the goal. The brownie should wobble slightly in the centre when you pull the tray. It firms up as it cools, and that residual heat carries the bake home. If it looks done in the oven, it is overdone on the plate.\n\nEvery batch at Melting Moments goes through this process. No shortcuts, no mixes. Just the real thing.""",
        "cover_image": "",
        "meta_description": "Fatema Abbasi shares the secrets behind Melting Moments' fudgy brownies — chocolate selection, sugar ratios, and the bake time that makes all the difference.",
        "tags": ["brownies", "baking", "recipes", "chocolate"],
    },
    "ar": {
        "title": "فن البراوني المثالي",
        "excerpt": "ما الذي يجعل البراوني مثالياً حقاً؟ تشارك فاطمة أسرار براونيز ملتنج مومنتس — من اختيار الشوكولاتة إلى وقت الخبز الدقيق.",
        "body": "كل خبّازة لها هوسها، وهوسي هو البراوني.\n\nليس النوع الكيكي — بل النوع الكثيف واللامع من الأعلى، الذي يذوب في الفم. وصلت إلى هذه النتيجة بعد سنوات من التجارب والفشل وتناول كميات كبيرة من البراونيز المتوسطة.\n\nكل دفعة في ملتنج مومنتس تمر بهذه العملية. لا اختصارات، لا خلطات جاهزة. فقط الشيء الحقيقي.",
        "cover_image": "",
        "meta_description": "تشارك فاطمة عباسي أسرار براونيز ملتنج مومنتس — اختيار الشوكولاتة ونسب السكر ووقت الخبز.",
        "tags": ["براوني", "خبز", "وصفات", "شوكولاتة"],
    },
}

POST_2 = {
    "en": {
        "title": "Why We Use Premium Ingredients",
        "excerpt": "In a world of shortcuts, Melting Moments chooses the long way. Here is why premium butter, real vanilla, and single-origin chocolate are non-negotiable in every batch.",
        "body": """There is a temptation, when running a home bakery, to optimise for cost. Cheaper butter, compound chocolate, artificial vanilla — the savings add up quickly and most customers, you tell yourself, won't notice.\n\nI noticed. Every time.\n\n## Real Butter\n\nThe difference between good butter and cheap margarine in baked goods is not subtle. Butter contributes flavour, texture, and that particular richness that makes a cookie melt on your tongue rather than crumble dryly. I use full-fat, unsalted butter in everything, added at the right temperature for each recipe.\n\n## Real Vanilla\n\nVanilla extract — not essence, not flavouring. Real vanilla has a complexity that the synthetic version simply cannot replicate. It rounds flavours, adds warmth, and disappears into the background in the best way possible. You do not taste vanilla in a good baked good; you taste everything else more fully.\n\n## Chocolate That Means Something\n\nCompound chocolate is made with vegetable fat instead of cocoa butter. It is cheaper, more stable, and noticeably inferior. The mouthfeel is waxier, the flavour thinner. For a bakery that leads with chocolate, this is not an acceptable trade-off.\n\nPremium ingredients cost more. That cost is reflected in our prices, and we think it is worth every fils.""",
        "cover_image": "",
        "meta_description": "Melting Moments explains why premium butter, real vanilla, and quality chocolate are the foundation of every batch — and why it matters to the customer.",
        "tags": ["ingredients", "quality", "baking", "artisanal"],
    },
    "ar": {
        "title": "لماذا نستخدم مكونات عالية الجودة",
        "excerpt": "في عالم مليء بالاختصارات، تختار ملتنج مومنتس الطريق الأطول. إليك السبب وراء اختيار الزبدة الفاخرة والفانيلا الحقيقية والشوكولاتة الأصلية في كل دفعة.",
        "body": "هناك إغراء، عند إدارة مخبز منزلي، للتحسين من أجل التكلفة. زبدة أرخص، شوكولاتة مركبة، فانيلا صناعية — المدخرات تتراكم بسرعة.\n\nلكنني لاحظت الفرق. في كل مرة.\n\nالمكونات الفاخرة تكلف أكثر. تنعكس هذه التكلفة في أسعارنا، ونعتقد أنها تستحق كل فلس.",
        "cover_image": "",
        "meta_description": "ملتنج مومنتس تشرح لماذا الزبدة الفاخرة والفانيلا الحقيقية والشوكولاتة عالية الجودة هي أساس كل دفعة.",
        "tags": ["مكونات", "جودة", "خبز", "حرفي"],
    },
}

POST_3 = {
    "en": {
        "title": "Dessert Delivery Across the UAE — What You Need to Know",
        "excerpt": "Getting fresh baked goods across the UAE requires more than a good recipe. Here is how Melting Moments handles delivery to ensure your order arrives exactly as it left the kitchen.",
        "body": """Delivering baked goods across the UAE presents a unique challenge: the climate.\n\nAverage summer temperatures exceed 40°C, and even in winter the days are warm. This means that what leaves the kitchen in perfect condition can arrive melted, collapsed, or stale if the logistics are not right.\n\n## How We Package\n\nEvery order is packaged specifically for the item type. Cookies go into sturdy boxes with parchment separating each piece. Brownies are individually wrapped before boxing. Cookie melts — our most temperature-sensitive product — are packed with care to maintain their shape and coating.\n\n## Delivery Timing\n\nWe schedule deliveries to minimise time in transit. Orders are dispatched fresh, and we coordinate pickup and drop-off windows to avoid extended periods in hot vehicles.\n\n## Coverage\n\nWe deliver to all seven UAE Emirates: Dubai, Abu Dhabi, Sharjah, Ajman, Ras Al Khaimah, Fujairah, and Umm Al Quwain. Al Ain is covered as part of the Abu Dhabi emirate.\n\n## Custom and Gifting Orders\n\nIf you are sending Melting Moments as a gift, we can include a handwritten note and package the order for gifting at no extra charge. Just add a note at checkout or mention it on WhatsApp.\n\nOrders can be placed at meltingmomentscakes.com or via WhatsApp at +971 50 368 7757.""",
        "cover_image": "",
        "meta_description": "How Melting Moments delivers fresh baked goods across all UAE Emirates — packaging, timing, and gifting options explained.",
        "tags": ["delivery", "UAE", "Dubai", "Sharjah", "gifting"],
    },
    "ar": {
        "title": "توصيل الحلويات في جميع أنحاء الإمارات — ما تحتاج معرفته",
        "excerpt": "الحصول على مخبوزات طازجة في جميع أنحاء الإمارات يتطلب أكثر من وصفة جيدة. إليك كيفية تعامل ملتنج مومنتس مع التوصيل.",
        "body": "توصيل المخبوزات عبر الإمارات يمثل تحدياً فريداً: المناخ.\n\nنوصل إلى جميع إمارات الإمارات السبع: دبي، أبوظبي، الشارقة، عجمان، رأس الخيمة، الفجيرة، وأم القيوين.\n\nيمكن تقديم الطلبات على meltingmomentscakes.com أو عبر واتساب على الرقم +971503687757.",
        "cover_image": "",
        "meta_description": "كيف تُوصّل ملتنج مومنتس المخبوزات الطازجة إلى جميع إمارات الدولة — التغليف والتوقيت وخيارات الهدايا.",
        "tags": ["توصيل", "الإمارات", "دبي", "الشارقة", "هدايا"],
    },
}

POSTS = [
    ("the-art-of-the-perfect-brownie", POST_1),
    ("why-we-use-premium-ingredients", POST_2),
    ("dessert-delivery-across-the-uae", POST_3),
]


def upgrade() -> None:
    op.create_table(
        "blog_posts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("slug", sa.String(150), unique=True, nullable=False, index=True),
        sa.Column("is_active", sa.Boolean, nullable=False, default=False),
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

    blog_posts = sa.table(
        "blog_posts",
        sa.column("id", UUID),
        sa.column("slug", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("content", JSONB),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )

    now = datetime.utcnow()
    op.bulk_insert(
        blog_posts,
        [
            {
                "id": uuid.uuid4(),
                "slug": slug,
                "is_active": True,
                "content": content,
                "created_at": now,
                "updated_at": now,
            }
            for slug, content in POSTS
        ],
    )


def downgrade() -> None:
    op.drop_table("blog_posts")
