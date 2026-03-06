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
    "ar": {
        "hero": {
            "title": "صُنع بمئة بالمئة من الحب",
            "subtitle": "كل قضمة تحكي قصة شغف وإبداع وحب عميق في إدخال البهجة من خلال الطعام.",
        },
        "story_1": {
            "label": "البداية",
            "title": "مطبخ وحلم وكميات كبيرة من الشوكولاتة",
            "body": (
                "بدأت ملتينج مومنتس من حب بسيط للخبيز — ليالٍ متأخرة في تجربة نسب الشوكولاتة ودرجات حرارة الزبدة والقوام المثالي للبراوني. ما بدأ كهدايا للعائلة والأصدقاء تحوّل سريعاً إلى شيء أعظم بكثير.\n\n"
                "أنا فاطمة عباسي، أسّست ملتينج مومنتس من مطبخ منزلي في الإمارات بهدف واحد: صنع حلويات يدوية تُدخل البسمة على وجوه الناس. لا خطوط إنتاج، لا طلبات جملة — فقط أنا وفرني وهوس حقيقي بالجودة.\n\n"
                "كل دفعة تُصنع طازجة عند الطلب باستخدام أجود المكونات — شوكولاتة بلجيكية وزبدة طبيعية ومنتجات مختارة بعناية. النتيجة شيء تشعر بفرقه من أول قضمة."
            ),
            "image_url": "/images/photos/person_shot_2.png",
        },
        "story_2": {
            "label": "الحرفة",
            "title": "مخبوز طازج، يُسلَّم باهتمام",
            "body": (
                "كل شيء في ملتينج مومنتس يُصنع عند الطلب. هذا يعني أننا نبدأ الخبيز فور استلام طلبك، لا نسحب من رف. براونياتك دافئة من الفرن، كوكيزك طري ومتماسك، وكوكي ملتس رائع القوام.\n\n"
                "نوصّل في أنحاء الإمارات — دبي والشارقة وعجمان والمزيد — مع تغليف كل طلب بعناية ليصل بنفس جمال لحظة خروجه من المطبخ.\n\n"
                "وبعيداً عن الحلويات اليومية، نعشق تنفيذ الطلبات الخاصة للمناسبات: صناديق أعياد الميلاد وتوزيعات الأعراس وهدايا العيد والحزم المؤسسية. إذا كان بإمكانك تخيّله، يمكننا خبزه."
            ),
            "image_url": "/images/photos/person_shot_3.png",
        },
        "values": [
            {
                "icon": "favorite",
                "title": "صُنع بالحب",
                "description": "كل منتج يُخبز طازجاً عند الطلب. لا مواد حافظة، لا اختصارات — مجرد خبيز صادق من القلب.",
            },
            {
                "icon": "eco",
                "title": "مكونات عالية الجودة",
                "description": "نختار أجود المكونات — شوكولاتة فاخرة وزبدة طبيعية ومنتجات طازجة في كل دفعة.",
            },
            {
                "icon": "diversity_3",
                "title": "لكل مناسبة",
                "description": "أعياد الميلاد والأعراس والعيد والهدايا المؤسسية — نصنع شيئاً مميزاً لكل لحظة تستحق الاحتفال.",
            },
            {
                "icon": "local_shipping",
                "title": "توصيل في أنحاء الإمارات",
                "description": "نوصّل إلى دبي والشارقة وعجمان وما بعدها. تُعبَّأ الطلبات بعناية لتصل في أحسن حال.",
            },
        ],
        "cta": {
            "title": "هل أنت مستعد للمتعة؟",
            "subtitle": "تصفّح تشكيلتنا من البراوني والكوكيز والحلويات المصنوعة يدوياً — طازجة خصيصاً لك.",
            "button_text": "تسوّق الآن",
            "button_link": "/brownies",
        },
        "seo": {
            "title": "من نحن",
            "description": "تعرّف على فاطمة عباسي — الخبّازة خلف ملتينج مومنتس. براوني وكوكيز وحلويات يدوية الصنع بكل حب من مطبخها المنزلي في الإمارات.",
        },
    },
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
    },
}

FAQ_CONTENT = {
    "ar": {
        "header": {
            "title": "الأسئلة الشائعة",
            "subtitle": "كل ما تحتاج معرفته عن الطلب والتوصيل ومنتجاتنا.",
        },
        "items": [
            {
                "question": "إلى أين تُوصّلون؟",
                "answer": "نوصّل في أنحاء الإمارات — دبي والشارقة وعجمان وأبوظبي ورأس الخيمة والفجيرة وأم القيوين. رسوم التوصيل إلى دبي والشارقة وعجمان 35 درهماً، وإلى باقي الإمارات 50 درهماً. الطلبات التي تتجاوز 200 درهم تحصل على توصيل مجاني.",
            },
            {
                "question": "كم مدة الإشعار المسبق اللازمة للطلب؟",
                "answer": "نوصي بتقديم الطلبات قبل 24 إلى 48 ساعة على الأقل لضمان الطزاجة والتوفر. أما الطلبات الكبيرة أو المخصصة (الفعاليات والهدايا المؤسسية والأعراس)، فيُرجى التواصل قبل 5 إلى 7 أيام على الأقل.",
            },
            {
                "question": "هل يمكنني استلام طلبي بنفسي؟",
                "answer": "نعم! الاستلام الذاتي متاح ومجاني تماماً. بعد تقديم طلبك، نؤكد لك موعد ومكان الاستلام عبر واتساب. الطلبات المقدمة قبل الساعة 12 ظهراً تكون جاهزة للاستلام في نفس اليوم عادةً.",
            },
            {
                "question": "هل تقبلون الطلبات المخصصة؟",
                "answer": "بالتأكيد. نعشق صنع حلويات مميزة لأعياد الميلاد والعيد والأعراس والفعاليات المؤسسية وغيرها. تواصل معنا عبر واتساب أو نموذج التواصل بمتطلباتك وسنُعدّ لك شيئاً استثنائياً.",
            },
            {
                "question": "ما طرق الدفع المتاحة؟",
                "answer": "نقبل الدفع بالبطاقات الإلكترونية (فيزا وماستركارد وآبل باي عبر سترايب). خيارات الشراء الآن والدفع لاحقاً من تابي وتمارا قريباً. الدفع عند الاستلام غير متاح حالياً.",
            },
            {
                "question": "هل منتجاتكم حلال؟",
                "answer": "نعم، جميع منتجاتنا حلال 100%. نستخدم مكونات معتمدة حلال ونحافظ على أعلى معايير النظافة طوال عملية الخبيز، ولا نستخدم أي نكهات تحتوي على كحول.",
            },
            {
                "question": "كم تدوم طزاجة المنتجات؟",
                "answer": "تحتفظ البراوني والكوكيز بطزاجتها من 3 إلى 5 أيام في مكان بارد وجاف. للحصول على أفضل نتيجة، احفظها في علبة محكمة الإغلاق. يُفضَّل تناول كوكي ملتس خلال 2 إلى 3 أيام. لا نستخدم مواد حافظة، لذا الطزاجة في أفضل حالاتها فور التسليم!",
            },
            {
                "question": "ماذا لو كان لديّ حساسية أو متطلبات غذائية خاصة؟",
                "answer": "تُصنع منتجاتنا في مطبخ منزلي يتعامل مع المكسرات ومنتجات الألبان والبيض والجلوتين وغيرها من مسببات الحساسية الشائعة. لا نستطيع ضمان بيئة خالية من مسببات الحساسية. إذا كانت لديك متطلبات خاصة، يُرجى التواصل معنا قبل الطلب.",
            },
        ],
        "cta": {
            "title": "نحن هنا لمساعدتك",
            "subtitle": "لم تجد إجابة على سؤالك؟ تواصل معنا وسنردّ عليك في أقرب وقت.",
            "whatsapp_text": "راسلنا على واتساب",
            "contact_text": "نموذج التواصل",
        },
        "seo": {
            "title": "الأسئلة الشائعة",
            "description": "أسئلة شائعة حول الطلب والتوصيل والدفع ومنتجات ملتينج مومنتس في الإمارات.",
        },
    },
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
    },
}

CONTACT_CONTENT = {
    "ar": {
        "header": {
            "title": "تواصل معنا",
            "subtitle": "طلبات مخصصة، استفسارات توصيل، تقديم طعام للفعاليات — يسعدنا دائماً مساعدتك.",
        },
        "info": {
            "phone": "+971 50 368 7757",
            "whatsapp": "https://wa.me/971503687757",
            "email": "fatema_f@hotmail.co.uk",
            "location": "الشارقة، الإمارات",
            "location_detail": "نوصّل في جميع أنحاء الإمارات",
            "hours": "الأحد: 3 م – 11:30 م",
            "hours_detail": "الإثنين – السبت: 8 ص – 11:30 م",
        },
        "seo": {
            "title": "اتصل بنا",
            "description": "تواصل مع ملتينج مومنتس. اطلب حلويات مخصصة أو استفسر عن التوصيل أو فقط قل مرحباً — يسعدنا دائماً سماعك.",
        },
    },
    "en": {
        "header": {
            "title": "Get in Touch",
            "subtitle": "Custom orders, delivery questions, event catering — we're always happy to help.",
        },
        "info": {
            "phone": "+971 50 368 7757",
            "whatsapp": "https://wa.me/971503687757",
            "email": "fatema_f@hotmail.co.uk",
            "location": "Sharjah, UAE",
            "location_detail": "Delivering across the UAE",
            "hours": "Sun: 3 PM – 11:30 PM",
            "hours_detail": "Mon – Sat: 8 AM – 11:30 PM",
        },
        "seo": {
            "title": "Contact",
            "description": "Get in touch with Melting Moments Cakes. Order custom treats, ask about delivery, or just say hello — we're always happy to hear from you.",
        },
    },
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
