"""Rewrite the customer-facing copy so it reads like a person wrote it.

The site's words were the weakest part of its SEO. "Artisanal" was in the page
title, the manifest, llms.txt, the OpenSearch description and the image alt
text; "handcrafted", "bespoke", "indulge" and "the finest ingredients" carried
most of the rest. None of it said the words people actually search for —
*brownie delivery Dubai*, *same day*, *eggless*, *birthday cake*, *corporate
gifting*, *halal* — and the About page never mentioned a city.

Worse, the facts disagreed with themselves. llms.txt, ai-plugin.json and the
home-page Bakery schema all advertised cash on delivery; the FAQ said cash on
delivery was unavailable; the checkout only offers cash on *pickup*. An answer
engine reading this site would state the wrong thing with confidence.

So this migration replaces the marketing copy on the home, about, FAQ and
contact pages in both locales, grows the FAQ from 8 questions to 16 (the eight
new ones are the questions people actually type), rewrites the three blog posts
and adds four more aimed at real queries.

This *overwrites* stored copy rather than merging underneath it, which is the
point — the stored copy is what is being replaced. The previous content of
every row it touches is copied into `seo_content_backup_054` first, and the
downgrade puts it back verbatim and drops the table.

Revision ID: 054_seo_content_refresh
Revises: 053_retire_regions
Create Date: 2026-08-03 00:00:00.000000

"""

from __future__ import annotations

import json
import uuid
from typing import Any, Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "054_seo_content_refresh"
down_revision: Union[str, None] = "053_retire_regions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BACKUP_TABLE = "seo_content_backup_054"

BANNERS = "/images/banners"
TILES = "/images/tiles"
PHOTOS = "/images/photos"


# ── Home ──────────────────────────────────────────────────────────────────────
# Only the parts that carry words. The hero slides and promo bands from 049
# already read like a human wrote them and are left alone.

HOME_PATCH: dict[str, Any] = {
    "en": {
        "usps": {
            "items": [
                {"icon": "local_shipping", "label": "Delivered to all 7 emirates"},
                {"icon": "schedule", "label": "Same-day slots when we have them"},
                {"icon": "favorite", "label": "Baked to order, never off a shelf"},
                {"icon": "spa", "label": "No preservatives"},
                {
                    "icon": "card_giftcard",
                    "label": "Gift boxes with a handwritten note",
                },
                {"icon": "place", "label": "Baked in Sharjah, delivered to Dubai"},
            ],
        },
        "categories": {
            "title": "Shop by craving",
            "subtitle": "Brownies, cookies, cookie melts, cakes, eggless. Pick your corner of the menu.",
        },
        "cater": {
            "title": "We cater to",
            "subtitle": "Birthdays, Eid, weddings, office orders. Boxes for the days that matter.",
        },
        "baker": {
            "gallery": [
                {
                    "image": f"{PHOTOS}/person_shot_2.png",
                    "alt": "A tray of fudgy chocolate brownies fresh out of the oven",
                },
                {
                    "image": f"{PHOTOS}/person_shot_3.png",
                    "alt": "Cookies cooling on a rack in the Melting Moments kitchen in Sharjah",
                },
                {
                    "image": f"{PHOTOS}/person_shot_4.jpg",
                    "alt": "Dessert boxes packed and ready for delivery across the UAE",
                },
            ],
        },
        "seo": {
            "title": "Melting Moments Cakes — Brownie & Dessert Delivery Across the UAE",
            "description": (
                "Fudgy brownies, gooey cookies, cookie melts and cakes, baked to order in a "
                "Sharjah home kitchen and delivered to Dubai, Sharjah, Ajman and every emirate. "
                "Free delivery over AED 200."
            ),
        },
    },
    "ar": {
        "usps": {
            "items": [
                {"icon": "local_shipping", "label": "توصيل لكل الإمارات السبع"},
                {"icon": "schedule", "label": "مواعيد في نفس اليوم عند توفرها"},
                {"icon": "favorite", "label": "تُخبز عند الطلب، لا من الرف"},
                {"icon": "spa", "label": "بلا مواد حافظة"},
                {"icon": "card_giftcard", "label": "علب هدايا مع بطاقة بخط اليد"},
                {"icon": "place", "label": "تُخبز في الشارقة، وتصل إلى دبي"},
            ],
        },
        "categories": {
            "title": "تسوّق حسب مزاجك",
            "subtitle": "براوني، كوكيز، كوكي ملت، كيك، وخيارات خالية من البيض. اختر ركنك من القائمة.",
        },
        "cater": {
            "title": "نلبّي مناسباتك",
            "subtitle": "أعياد ميلاد، عيد، أعراس، وطلبات المكاتب. علب للأيام التي تهمّ.",
        },
        "baker": {
            "gallery": [
                {
                    "image": f"{PHOTOS}/person_shot_2.png",
                    "alt": "صينية براوني شوكولاتة طرية خارجة للتو من الفرن",
                },
                {
                    "image": f"{PHOTOS}/person_shot_3.png",
                    "alt": "كوكيز يبرد على الشبكة في مطبخ ملتنج مومنتس بالشارقة",
                },
                {
                    "image": f"{PHOTOS}/person_shot_4.jpg",
                    "alt": "علب حلويات معبّأة وجاهزة للتوصيل في أنحاء الإمارات",
                },
            ],
        },
        "seo": {
            "title": "ملتنج مومنتس — توصيل براوني وحلويات في كل الإمارات",
            "description": (
                "براوني طري وكوكيز وكوكي ملت وكيك، تُخبز عند الطلب في مطبخ منزلي بالشارقة "
                "وتُوصَّل إلى دبي والشارقة وعجمان وكل الإمارات. توصيل مجاني للطلبات فوق 200 درهم."
            ),
        },
    },
}


# ── About ─────────────────────────────────────────────────────────────────────

ABOUT_PATCH: dict[str, Any] = {
    "en": {
        "hero": {
            "label": "Our Story",
            "title": "It started in a home kitchen in Sharjah",
            "subtitle": "Same kitchen. Same baker. The orders just travel a lot further now.",
        },
        "story_1": {
            "label": "The Beginning",
            "title": "It started because my family kept asking",
            "body": (
                "I never set out to run a bakery. I baked for my family, then for their "
                "friends, and at some point people I had never met were messaging me on "
                "Instagram asking whether I delivered to Dubai.\n\n"
                "The brownie took the longest. I lost count somewhere around the fortieth "
                "tray — too cakey, too sweet, edges set before the middle had a chance. What "
                "finally worked was melting dark and semi-sweet chocolate into the butter "
                "instead of folding it in afterwards, and pulling the tray while the centre "
                "still moves. It finishes setting on the counter. If it looks done in the "
                "oven, it already went too far.\n\n"
                "I'm Fatema Abbasi. I still do the baking myself, in a home kitchen in "
                "Sharjah, in small batches, when the order comes in. There is no shelf that "
                "any of this comes off."
            ),
            "image_url": f"{PHOTOS}/person_shot_2.png",
        },
        "story_2": {
            "label": "How it works",
            "title": "Baked the morning it goes out",
            "body": (
                "Nothing is made in advance. Your order comes in, the oven goes on — which is "
                "why the brownies are still warm when they're boxed, the cookies are soft in "
                "the middle, and the cookie melts haven't had time to set.\n\n"
                "We deliver to all seven emirates. Dubai, Sharjah and Ajman are the daily "
                "runs. Abu Dhabi, Al Ain, Ras Al Khaimah, Fujairah and Umm Al Quwain we plan "
                "around the drive, so nothing spends an afternoon in a hot car. Boxes are "
                "packed in layers so the top one doesn't land on the bottom one.\n\n"
                "The big orders are the ones I enjoy most — birthday boxes, dessert tables, "
                "Eid and Ramadan gifting, wedding favours, fifty boxes for an office. Those "
                "need about a week. Message me and we'll work it out."
            ),
            "image_url": f"{PHOTOS}/person_shot_3.png",
            "cta_text": "Get in Touch",
        },
        "values_section": {
            "label": "How we work",
            "title": "Four things that don't change",
        },
        "values": [
            {
                "icon": "favorite",
                "title": "Baked to order",
                "description": "The oven goes on when your order comes in. Nothing is baked ahead and stored.",
            },
            {
                "icon": "eco",
                "title": "Real ingredients",
                "description": "Butter, not margarine. Real vanilla. Chocolate with cocoa butter in it. It costs more and it tastes like it.",
            },
            {
                "icon": "diversity_3",
                "title": "Birthdays to boardrooms",
                "description": "Birthday boxes, Eid gifting, wedding favours, corporate orders. About a week's notice for the big ones.",
            },
            {
                "icon": "local_shipping",
                "title": "All seven emirates",
                "description": "Dubai, Sharjah, Ajman, Abu Dhabi, Al Ain, RAK, Fujairah, Umm Al Quwain. Free over AED 200.",
            },
        ],
        "cta": {
            "title": "Hungry yet?",
            "subtitle": "Brownies, cookies, cookie melts and cakes — baked to order, delivered across the UAE.",
            "button_text": "Shop Now",
            "button_link": "/all-products",
        },
        "seo": {
            "title": "About Fatema — the baker behind Melting Moments, Sharjah",
            "description": (
                "Melting Moments is a home bakery in Sharjah run by Fatema Abbasi. Brownies, "
                "cookies and desserts baked to order and delivered across Dubai, Sharjah and "
                "the rest of the UAE."
            ),
        },
    },
    "ar": {
        "hero": {
            "label": "قصتنا",
            "title": "بدأت في مطبخ منزلي بالشارقة",
            "subtitle": "المطبخ نفسه، والخبّازة نفسها. الطلبات فقط صارت تسافر أبعد.",
        },
        "story_1": {
            "label": "البداية",
            "title": "بدأت لأن عائلتي ظلّت تطلب المزيد",
            "body": (
                "لم أخطط يوماً لفتح مخبز. كنت أخبز لعائلتي، ثم لأصدقائهم، وفي لحظة ما صار "
                "أشخاص لم ألتقِ بهم يراسلونني على إنستغرام يسألون إن كنت أوصّل إلى دبي.\n\n"
                "البراوني أخذ أطول وقت. فقدت العدّ عند الصينية الأربعين تقريباً — إما أقرب "
                "إلى الكيك، أو حلو أكثر من اللازم، أو الأطراف تنضج قبل الوسط. ما نجح أخيراً "
                "هو إذابة الشوكولاتة الداكنة وشبه المحلاة في الزبدة بدل إضافتها لاحقاً، ثم "
                "إخراج الصينية والوسط ما زال يتحرك. تكتمل على الرخامة. إن بدت جاهزة داخل "
                "الفرن، فقد تجاوزت الحد.\n\n"
                "أنا فاطمة عباسي. ما زلت أخبز بنفسي، في مطبخ منزلي بالشارقة، بدفعات صغيرة، "
                "عند وصول الطلب. لا شيء هنا يُؤخذ من رف جاهز."
            ),
            "image_url": f"{PHOTOS}/person_shot_2.png",
        },
        "story_2": {
            "label": "كيف نعمل",
            "title": "يُخبز في صباح يوم التسليم",
            "body": (
                "لا شيء يُحضَّر مسبقاً. يصل طلبك فيشتغل الفرن — ولهذا يكون البراوني ما زال "
                "دافئاً وقت التعبئة، والكوكيز طرياً من الوسط، والكوكي ملت لم يتماسك بعد.\n\n"
                "نوصّل إلى الإمارات السبع جميعها. دبي والشارقة وعجمان يومياً. أبوظبي والعين "
                "ورأس الخيمة والفجيرة وأم القيوين ننظّمها حسب خط السير، حتى لا يبقى شيء في "
                "سيارة حارة طوال العصر. وتُعبَّأ العلب بطبقات كي لا تنزل العليا على السفلى.\n\n"
                "الطلبات الكبيرة هي المفضّلة عندي — علب أعياد الميلاد، وطاولات الحلويات، "
                "وهدايا العيد ورمضان، وتوزيعات الأعراس، وخمسون علبة لمكتب. هذه تحتاج نحو "
                "أسبوع. راسلني ونرتّبها معاً."
            ),
            "image_url": f"{PHOTOS}/person_shot_3.png",
            "cta_text": "تواصل معنا",
        },
        "values_section": {"label": "كيف نعمل", "title": "أربعة أشياء لا تتغيّر"},
        "values": [
            {
                "icon": "favorite",
                "title": "تُخبز عند الطلب",
                "description": "الفرن يشتغل حين يصل طلبك. لا شيء يُخبز مسبقاً ويُخزَّن.",
            },
            {
                "icon": "eco",
                "title": "مكونات حقيقية",
                "description": "زبدة لا مارغرين. فانيلا حقيقية. شوكولاتة فيها زبدة كاكاو. تكلف أكثر، ويظهر ذلك في المذاق.",
            },
            {
                "icon": "diversity_3",
                "title": "من أعياد الميلاد إلى المكاتب",
                "description": "علب أعياد الميلاد وهدايا العيد وتوزيعات الأعراس والطلبات المؤسسية. أسبوع تقريباً للطلبات الكبيرة.",
            },
            {
                "icon": "local_shipping",
                "title": "كل الإمارات السبع",
                "description": "دبي، الشارقة، عجمان، أبوظبي، العين، رأس الخيمة، الفجيرة، أم القيوين. مجاناً فوق 200 درهم.",
            },
        ],
        "cta": {
            "title": "جعت؟",
            "subtitle": "براوني وكوكيز وكوكي ملت وكيك — تُخبز عند الطلب وتُوصَّل في أنحاء الإمارات.",
            "button_text": "تسوّق الآن",
            "button_link": "/all-products",
        },
        "seo": {
            "title": "عن فاطمة — الخبّازة خلف ملتنج مومنتس، الشارقة",
            "description": (
                "ملتنج مومنتس مخبز منزلي في الشارقة تديره فاطمة عباسي. براوني وكوكيز وحلويات "
                "تُخبز عند الطلب وتُوصَّل إلى دبي والشارقة وبقية الإمارات."
            ),
        },
    },
}


# ── FAQ ───────────────────────────────────────────────────────────────────────
# 8 questions became 16. The new ones are the queries that actually get typed:
# same-day, how much is delivery, birthday cakes, corporate, eggless, halal,
# "what is a cookie melt", and where the bakery physically is.

FAQ_EN_ITEMS = [
    {
        "question": "Where do you deliver?",
        "answer": (
            "All seven emirates — Dubai, Sharjah, Ajman, Abu Dhabi, Al Ain, Ras Al Khaimah, "
            "Fujairah and Umm Al Quwain. The delivery fee is worked out from your address at "
            "checkout, and it's free on orders over AED 200. Pickup from Sharjah is free."
        ),
    },
    {
        "question": "Do you deliver to Dubai?",
        "answer": (
            "Yes — Dubai, Sharjah and Ajman are the daily runs. Abu Dhabi, Al Ain, Ras Al "
            "Khaimah, Fujairah and Umm Al Quwain go out on planned runs, and the checkout "
            "shows you which days are open for your address."
        ),
    },
    {
        "question": "Do you deliver same day?",
        "answer": (
            "Sometimes. Everything is baked to order, so it depends what's already in the "
            "oven that day. Put your address in at checkout and you'll see the slots that are "
            "genuinely open. If none of them work, message us on WhatsApp on +971 50 368 7757 "
            "and we'll tell you straight away what's possible."
        ),
    },
    {
        "question": "How much is delivery?",
        "answer": (
            "It depends where you are — the checkout quotes it from your pin, so you see the "
            "real number before you pay. Orders over AED 200 deliver free anywhere we go, and "
            "collecting from Sharjah costs nothing."
        ),
    },
    {
        "question": "How far in advance do I need to order?",
        "answer": (
            "A day or two is comfortable for a normal order. For birthdays, dessert tables, "
            "Eid boxes, wedding favours or anything going to an office, give us 5–7 days so "
            "we can plan the bake around it."
        ),
    },
    {
        "question": "Can I pick up my order?",
        "answer": (
            "Yes, and it's free. Once you've ordered we'll confirm the time and the Sharjah "
            "address on WhatsApp. Orders placed before noon are usually ready the same day."
        ),
    },
    {
        "question": "Do you make birthday cakes and custom orders?",
        "answer": (
            "Yes. Birthday boxes, celebration cakes, Eid and Ramadan gifting, wedding "
            "favours, dessert tables. Send us what you have in mind on WhatsApp or through "
            "the contact form — about a week's notice for the bigger ones."
        ),
    },
    {
        "question": "Do you take corporate and bulk orders?",
        "answer": (
            "Yes — office deliveries, client gifting, event boxes, Ramadan and Eid hampers. "
            "Tell us how many boxes, the date and roughly the budget, and we'll come back "
            "with options. A week's notice, and longer if it's over a hundred boxes."
        ),
    },
    {
        "question": "Do you have eggless options?",
        "answer": (
            "Yes, there's an eggless range on the menu. If you want something from another "
            "category made eggless, ask before you order — some recipes take the swap well "
            "and some don't, and we'll tell you honestly which is which."
        ),
    },
    {
        "question": "What payment methods do you accept?",
        "answer": (
            "Card online — Visa, Mastercard and Apple Pay, through Stripe. Cash is accepted "
            "on pickup orders only; there is no cash on delivery. Tabby and Tamara aren't "
            "live yet."
        ),
    },
    {
        "question": "Are your products halal?",
        "answer": (
            "Yes, all of it. Halal-certified ingredients, and no alcohol-based flavourings — "
            "no rum in the tiramisu, and no vanilla essence cut with alcohol."
        ),
    },
    {
        "question": "What is a cookie melt?",
        "answer": (
            "A thick cookie, served warm, deliberately underbaked through the middle so the "
            "centre stays molten. If it's cooled down by the time you get to it, ten seconds "
            "in the microwave brings it right back."
        ),
    },
    {
        "question": "How long do the products stay fresh?",
        "answer": (
            "Brownies and cookies keep for 3–5 days in an airtight container somewhere cool. "
            "Cookie melts are best in the first 2–3 days. There are no preservatives in any "
            "of it, so the first day is always the best one."
        ),
    },
    {
        "question": "What if I have an allergy or a dietary requirement?",
        "answer": (
            "Everything is baked in a home kitchen that also handles nuts, dairy, eggs and "
            "gluten, so we can't promise anything is free of them. If you have an allergy, "
            "message us before ordering and we'll tell you honestly whether we can work "
            "around it."
        ),
    },
    {
        "question": "Where are you based?",
        "answer": (
            "Sharjah. It's a home kitchen rather than a shop, so there's no counter to walk "
            "into — but pickup is free, and we send you the address once the order is placed."
        ),
    },
    {
        "question": "Can I send an order as a gift?",
        "answer": (
            "Yes. Add a note at checkout or tell us on WhatsApp, and we'll write it out by "
            "hand and pack the box for gifting. No extra charge, and the price doesn't go in "
            "the box."
        ),
    },
]

FAQ_AR_ITEMS = [
    {
        "question": "إلى أين تُوصّلون؟",
        "answer": (
            "إلى الإمارات السبع جميعها — دبي والشارقة وعجمان وأبوظبي والعين ورأس الخيمة "
            "والفجيرة وأم القيوين. تُحتسب رسوم التوصيل من عنوانك عند إتمام الطلب، وهي مجانية "
            "للطلبات فوق 200 درهم. والاستلام من الشارقة مجاني."
        ),
    },
    {
        "question": "هل توصّلون إلى دبي؟",
        "answer": (
            "نعم — دبي والشارقة وعجمان يومياً. أما أبوظبي والعين ورأس الخيمة والفجيرة وأم "
            "القيوين فتخرج ضمن جولات مجدولة، وصفحة الدفع تعرض الأيام المتاحة لعنوانك."
        ),
    },
    {
        "question": "هل يوجد توصيل في نفس اليوم؟",
        "answer": (
            "أحياناً. كل شيء يُخبز عند الطلب، فالأمر يعتمد على ما هو داخل الفرن ذلك اليوم. "
            "أدخل عنوانك عند الدفع لترى المواعيد المتاحة فعلاً. وإن لم يناسبك شيء، راسلنا على "
            "واتساب 7757 368 50 971+ ونخبرك فوراً بما يمكن عمله."
        ),
    },
    {
        "question": "كم تبلغ رسوم التوصيل؟",
        "answer": (
            "تعتمد على موقعك — تُحتسب من موقعك على الخريطة عند الدفع، فترى الرقم الحقيقي قبل "
            "السداد. الطلبات فوق 200 درهم تُوصَّل مجاناً إلى أي مكان نصل إليه، والاستلام من "
            "الشارقة بلا رسوم."
        ),
    },
    {
        "question": "كم مدة الإشعار المسبق اللازمة للطلب؟",
        "answer": (
            "يوم أو يومان يكفيان لطلب عادي. أما أعياد الميلاد وطاولات الحلويات وعلب العيد "
            "وتوزيعات الأعراس وأي طلب لمكتب، فامنحنا من 5 إلى 7 أيام لننظّم الخبز حولها."
        ),
    },
    {
        "question": "هل يمكنني استلام طلبي بنفسي؟",
        "answer": (
            "نعم، ومجاناً. بعد تقديم الطلب نؤكد لك الموعد وعنوان الاستلام في الشارقة عبر "
            "واتساب. الطلبات قبل الظهر تكون جاهزة في نفس اليوم عادةً."
        ),
    },
    {
        "question": "هل تصنعون كيك أعياد الميلاد والطلبات المخصصة؟",
        "answer": (
            "نعم. علب أعياد الميلاد وكيك الاحتفالات وهدايا العيد ورمضان وتوزيعات الأعراس "
            "وطاولات الحلويات. أرسل لنا فكرتك على واتساب أو عبر نموذج التواصل — ونحتاج نحو "
            "أسبوع للطلبات الكبيرة."
        ),
    },
    {
        "question": "هل تقبلون الطلبات المؤسسية والكميات الكبيرة؟",
        "answer": (
            "نعم — توصيل للمكاتب، وهدايا العملاء، وعلب الفعاليات، وسلال رمضان والعيد. أخبرنا "
            "بعدد العلب والتاريخ والميزانية تقريباً، ونعود إليك بخيارات. أسبوع من الإشعار، "
            "وأكثر إن تجاوز الطلب مئة علبة."
        ),
    },
    {
        "question": "هل لديكم خيارات خالية من البيض؟",
        "answer": (
            "نعم، هناك تشكيلة خالية من البيض في القائمة. وإن أردت صنفاً من فئة أخرى بلا بيض، "
            "اسألنا قبل الطلب — بعض الوصفات تقبل التبديل وبعضها لا، وسنخبرك بصراحة."
        ),
    },
    {
        "question": "ما طرق الدفع المتاحة؟",
        "answer": (
            "الدفع بالبطاقة عبر الإنترنت — فيزا وماستركارد وآبل باي من خلال سترايب. النقد "
            "مقبول لطلبات الاستلام فقط، ولا يوجد دفع عند التوصيل. وتابي وتمارا غير مفعّلتين "
            "بعد."
        ),
    },
    {
        "question": "هل منتجاتكم حلال؟",
        "answer": (
            "نعم، جميعها. مكونات معتمدة حلال، وبلا أي نكهات تحتوي كحولاً — لا رَم في "
            "التيراميسو، ولا خلاصة فانيلا مذابة بالكحول."
        ),
    },
    {
        "question": "ما هو الكوكي ملت؟",
        "answer": (
            "كوكي سميك يُقدَّم دافئاً، ناقص النضج من الوسط عن قصد ليبقى قلبه سائلاً. وإن برد "
            "قبل أن تصل إليه، عشر ثوانٍ في الميكروويف تعيده كما كان."
        ),
    },
    {
        "question": "كم تدوم طزاجة المنتجات؟",
        "answer": (
            "يبقى البراوني والكوكيز طازجين من 3 إلى 5 أيام في علبة محكمة بمكان بارد. والكوكي "
            "ملت أفضل خلال أول يومين أو ثلاثة. لا مواد حافظة في أي منها، لذا يبقى اليوم الأول "
            "هو الأفضل دائماً."
        ),
    },
    {
        "question": "ماذا لو كان لديّ حساسية أو متطلب غذائي؟",
        "answer": (
            "كل شيء يُخبز في مطبخ منزلي يتعامل أيضاً مع المكسرات والألبان والبيض والجلوتين، "
            "فلا نستطيع ضمان خلوّ أي صنف منها. إن كانت لديك حساسية، راسلنا قبل الطلب ونخبرك "
            "بصراحة إن كان بالإمكان تدبّر الأمر."
        ),
    },
    {
        "question": "أين مقرّكم؟",
        "answer": (
            "الشارقة. وهو مطبخ منزلي لا محل، فلا يوجد مكان تدخله وتشتري منه — لكن الاستلام "
            "مجاني، ونرسل لك العنوان بعد تقديم الطلب."
        ),
    },
    {
        "question": "هل يمكنني إرسال الطلب كهدية؟",
        "answer": (
            "نعم. أضف ملاحظة عند الدفع أو أخبرنا على واتساب، فنكتبها بخط اليد ونغلّف العلبة "
            "للإهداء. بلا رسوم إضافية، ولا يوضع السعر داخل العلبة."
        ),
    },
]

FAQ_PATCH: dict[str, Any] = {
    "en": {
        "header": {
            "title": "Frequently Asked Questions",
            "subtitle": "Delivery, timings, payment and allergies — the things worth knowing before you order.",
        },
        "items": FAQ_EN_ITEMS,
        "cta": {
            "title": "Ask us anything",
            "subtitle": "If it isn't here, message us on WhatsApp. You'll usually have an answer within the hour.",
            "whatsapp_text": "WhatsApp Us",
            "contact_text": "Contact Form",
        },
        "seo": {
            "title": "FAQ — delivery, ordering and payment",
            "description": (
                "How delivery works across Dubai, Sharjah and the rest of the UAE, how much "
                "notice we need, what we take as payment, and what's in the kitchen. Melting "
                "Moments Cakes, Sharjah."
            ),
        },
    },
    "ar": {
        "header": {
            "title": "الأسئلة الشائعة",
            "subtitle": "التوصيل والمواعيد والدفع والحساسية — ما يستحق معرفته قبل الطلب.",
        },
        "items": FAQ_AR_ITEMS,
        "cta": {
            "title": "اسألنا عن أي شيء",
            "subtitle": "إن لم تجد سؤالك هنا، راسلنا على واتساب. تصلك الإجابة عادةً خلال ساعة.",
            "whatsapp_text": "راسلنا على واتساب",
            "contact_text": "نموذج التواصل",
        },
        "seo": {
            "title": "الأسئلة الشائعة — التوصيل والطلب والدفع",
            "description": (
                "كيف يعمل التوصيل في دبي والشارقة وبقية الإمارات، وكم من الوقت نحتاج، وما "
                "طرق الدفع المقبولة. ملتنج مومنتس، الشارقة."
            ),
        },
    },
}


# ── Contact ───────────────────────────────────────────────────────────────────

CONTACT_PATCH: dict[str, Any] = {
    "en": {
        "header": {
            "label": "Reach Out",
            "title": "Get in Touch",
            "subtitle": "Custom orders, corporate boxes, a question about delivery — WhatsApp is quickest.",
        },
        "seo": {
            "title": "Contact — WhatsApp, phone and opening hours",
            "description": (
                "Message Melting Moments Cakes on WhatsApp +971 50 368 7757 for custom cakes, "
                "dessert boxes and corporate gifting. Based in Sharjah, delivering across the UAE."
            ),
        },
    },
    "ar": {
        "header": {
            "label": "تواصل بنا",
            "title": "تواصل معنا",
            "subtitle": "طلبات مخصصة، علب للمكاتب، أو سؤال عن التوصيل — واتساب أسرع طريقة.",
        },
        "seo": {
            "title": "تواصل معنا — واتساب وهاتف وأوقات العمل",
            "description": (
                "راسل ملتنج مومنتس على واتساب 7757 368 50 971+ لطلب الكيك المخصص وعلب الحلويات "
                "وهدايا الشركات. مقرّنا الشارقة، ونوصّل في أنحاء الإمارات."
            ),
        },
    },
}


# ── Blog ──────────────────────────────────────────────────────────────────────
# The three existing posts keep their slugs — they are the only URLs on this
# site with any age on them. Two of them were named after the problem this
# migration exists to fix ("The Art of the Perfect Brownie", "Why We Use
# Premium Ingredients"), so the words change and the address doesn't.

POST_BROWNIE = {
    "en": {
        "title": "What makes a brownie fudgy instead of cakey",
        "excerpt": (
            "The difference comes down to three things, and none of them is a secret "
            "ingredient. Here's what I changed over about forty trays to get there."
        ),
        "body": (
            "People ask what the trick is. There isn't one. There are three ordinary "
            "decisions, and getting all three right at once took me about forty trays.\n\n"
            "## Less flour than you think\n\n"
            "A cakey brownie is a brownie with too much flour in it, or with enough gluten "
            "worked into the flour that's there. Once the batter goes in, I stop as soon as "
            "the streaks disappear. Ten more turns of the spoon and you've made a chocolate "
            "cake.\n\n"
            "## Melt the chocolate into the butter\n\n"
            "Not separately, and not folded in at the end. Dark and semi-sweet together — "
            "dark on its own goes bitter, semi-sweet on its own tastes like a supermarket "
            "tray bake. Melting them together with the butter is also what gives you that "
            "thin shiny crust on top. That crust isn't decoration; it's the sugar dissolving "
            "properly, which only happens if the mixture is warm when the eggs go in.\n\n"
            "## Take it out early\n\n"
            "This is the one people get wrong. The tray should still move in the middle when "
            "you pull it. It carries on cooking on the counter for another ten minutes, and "
            "that's the bake you actually wanted. If it looks finished in the oven, it's "
            "overdone by the time it reaches a plate.\n\n"
            "Two sugars, while we're here: caster for structure, a spoon of brown for "
            "moisture. You won't taste the brown sugar. You'd notice if it wasn't there.\n\n"
            "Every tray that leaves this kitchen goes through exactly that. No mixes, no "
            "shortcuts, and nothing baked the day before."
        ),
        "cover_image": f"{BANNERS}/hero-brownies.jpg",
        "meta_description": (
            "Why brownies turn out cakey, and the three things that fix it — flour, melted "
            "chocolate and pulling the tray early. From the Melting Moments kitchen in Sharjah."
        ),
        "tags": ["brownies", "baking", "chocolate", "how-to"],
    },
    "ar": {
        "title": "ما الذي يجعل البراوني طرياً بدل أن يكون كيكاً",
        "excerpt": (
            "الفرق يعود إلى ثلاثة أمور، وليس بينها مكوّن سري. هذا ما غيّرته عبر أربعين صينية "
            "تقريباً حتى وصلت."
        ),
        "body": (
            "يسألني الناس عن الحيلة. لا توجد حيلة. توجد ثلاثة قرارات عادية، وضبطها كلها معاً "
            "أخذ مني نحو أربعين صينية.\n\n"
            "## دقيق أقل مما تظن\n\n"
            "البراوني الكيكي هو براوني فيه دقيق زائد، أو عُجن حتى تكوّن الجلوتين. بعد إضافة "
            "الدقيق أتوقف فور اختفاء الخطوط البيضاء. عشر حركات إضافية بالملعقة تحوّله إلى كيكة "
            "شوكولاتة.\n\n"
            "## أذيبي الشوكولاتة داخل الزبدة\n\n"
            "لا منفصلة، ولا مضافة في النهاية. داكنة وشبه محلاة معاً — الداكنة وحدها تميل إلى "
            "المرارة، وشبه المحلاة وحدها طعمها كصواني السوبرماركت. وإذابتهما مع الزبدة هي "
            "أيضاً سبب تلك القشرة اللامعة الرفيعة في الأعلى. تلك القشرة ليست زينة، بل دليل أن "
            "السكر ذاب كما ينبغي، وهذا لا يحدث إلا إذا كان الخليط دافئاً عند إضافة البيض.\n\n"
            "## أخرجيها مبكراً\n\n"
            "هنا يخطئ الجميع. يجب أن يظل الوسط متحركاً وقت إخراج الصينية. تكمل نضجها على "
            "الرخامة عشر دقائق أخرى، وتلك هي درجة النضج التي أردتها فعلاً. إن بدت جاهزة داخل "
            "الفرن، فهي زائدة النضج حين تصل إلى الطبق.\n\n"
            "ونوعان من السكر: الناعم للقوام، وملعقة من البني للرطوبة. لن تتذوّق البني، لكنك "
            "ستلاحظ غيابه.\n\n"
            "كل صينية تخرج من هذا المطبخ تمرّ بهذا بالضبط. لا خلطات جاهزة، ولا اختصارات، ولا "
            "شيء مخبوز من اليوم السابق."
        ),
        "cover_image": f"{BANNERS}/hero-brownies.jpg",
        "meta_description": (
            "لماذا يخرج البراوني كيكياً، والأمور الثلاثة التي تصلحه — الدقيق، والشوكولاتة "
            "المذابة، وإخراج الصينية مبكراً. من مطبخ ملتنج مومنتس في الشارقة."
        ),
        "tags": ["براوني", "خبز", "شوكولاتة", "طريقة"],
    },
}

POST_INGREDIENTS = {
    "en": {
        "title": "Butter, vanilla, chocolate: where the money goes",
        "excerpt": (
            "The cheaper versions of all three exist and plenty of kitchens use them. Here's "
            "what changes when you don't, and why it shows up in the price."
        ),
        "body": (
            "Running a small kitchen, there is constant pressure to shave the ingredient "
            "cost. Margarine instead of butter, compound instead of couverture, essence "
            "instead of extract. The maths is genuinely tempting — the savings are real and "
            "they compound over a month.\n\n"
            "I've tried all three swaps. Here's what actually happens.\n\n"
            "## Butter\n\n"
            "Margarine has more water in it and a higher melting point, which is why a cookie "
            "made with it holds its shape neatly and then sits on your tongue instead of "
            "melting. Butter is what makes a cookie edge crisp and its middle short. It also "
            "browns, and browning is most of what you taste in a good cookie.\n\n"
            "## Vanilla\n\n"
            "Extract, not essence. Real vanilla doesn't announce itself — you shouldn't taste "
            "vanilla in a brownie. What it does is round the edges off the chocolate so it "
            "reads as rich rather than sharp. Synthetic vanillin only does the announcing "
            "part.\n\n"
            "## Chocolate\n\n"
            "Compound chocolate replaces cocoa butter with vegetable fat. It's cheaper, it "
            "behaves better in the heat, and it feels waxy — it coats your mouth and stays "
            "there. For a kitchen whose two bestsellers are both chocolate, that's not a "
            "trade I can make.\n\n"
            "All of this is why a brownie here isn't the cheapest brownie in the UAE. It's "
            "roughly where the price comes from, and it's the part of the cost I'd rather "
            "explain than hide."
        ),
        "cover_image": f"{PHOTOS}/person_shot_2.png",
        "meta_description": (
            "Butter vs margarine, extract vs essence, couverture vs compound chocolate — what "
            "each swap does to a baked good, and why Melting Moments doesn't make them."
        ),
        "tags": ["ingredients", "baking", "chocolate", "behind the scenes"],
    },
    "ar": {
        "title": "الزبدة والفانيلا والشوكولاتة: أين تذهب التكلفة",
        "excerpt": (
            "البدائل الأرخص للثلاثة موجودة، وكثير من المطابخ تستخدمها. هذا ما يتغيّر حين لا "
            "نستخدمها، ولماذا يظهر ذلك في السعر."
        ),
        "body": (
            "في مطبخ صغير هناك ضغط دائم لتقليص تكلفة المكونات. مارغرين بدل الزبدة، وشوكولاتة "
            "مركّبة بدل الحقيقية، وخلاصة صناعية بدل الطبيعية. الحساب مغرٍ فعلاً — التوفير "
            "حقيقي ويتراكم خلال الشهر.\n\n"
            "جرّبت التبديلات الثلاثة. وهذا ما يحدث فعلاً.\n\n"
            "## الزبدة\n\n"
            "المارغرين فيه ماء أكثر ودرجة انصهار أعلى، ولهذا يحتفظ الكوكي المصنوع به بشكله "
            "بدقة، ثم يجلس على لسانك بدل أن يذوب. الزبدة هي ما يجعل حافة الكوكي مقرمشة ووسطه "
            "هشاً. وهي أيضاً تتحمّر، والتحمّر هو معظم ما تتذوّقه في كوكي جيد.\n\n"
            "## الفانيلا\n\n"
            "خلاصة طبيعية لا صناعية. الفانيلا الحقيقية لا تعلن عن نفسها — لا يفترض أن تتذوّق "
            "الفانيلا في البراوني. وظيفتها تلطيف حدّة الشوكولاتة لتصبح غنية لا حادّة. أما "
            "الفانيلين الصناعي فلا يفعل سوى الإعلان عن نفسه.\n\n"
            "## الشوكولاتة\n\n"
            "الشوكولاتة المركّبة تستبدل زبدة الكاكاو بدهون نباتية. أرخص، وأثبت في الحرارة، "
            "وملمسها شمعي — تغلّف الفم وتبقى. ولمطبخ أكثر صنفين مبيعاً فيه شوكولاتة، هذه "
            "مقايضة لا أستطيع عقدها.\n\n"
            "ولهذا كله، البراوني هنا ليس أرخص براوني في الإمارات. هذا تقريباً مصدر السعر، وهو "
            "الجزء الذي أفضّل شرحه على إخفائه."
        ),
        "cover_image": f"{PHOTOS}/person_shot_2.png",
        "meta_description": (
            "الزبدة مقابل المارغرين، والخلاصة الطبيعية مقابل الصناعية، والشوكولاتة الحقيقية "
            "مقابل المركّبة — ماذا يفعل كل تبديل، ولماذا لا نفعله."
        ),
        "tags": ["مكونات", "خبز", "شوكولاتة", "من الكواليس"],
    },
}

POST_DELIVERY = {
    "en": {
        "title": "Delivering dessert across the UAE when it's 42 degrees outside",
        "excerpt": (
            "Chocolate melts at body temperature and a parked car in Sharjah hits sixty. "
            "Here's how orders actually get from the kitchen to Dubai, Abu Dhabi and RAK intact."
        ),
        "body": (
            "The recipe is the easy half. The hard half is the drive.\n\n"
            "Chocolate starts softening at about 30°C. A car parked in the sun here gets past "
            "60°C inside. So the question isn't whether a box can survive the summer — it's "
            "how long it spends in transit and what it's sitting in while it does.\n\n"
            "## Packing\n\n"
            "Every product gets packed differently. Cookies go into rigid boxes with "
            "parchment between the layers, because a cookie that slides is a cookie that "
            "arrives in pieces. Brownies are wrapped individually before they're boxed — that "
            "wrapping is as much about keeping the top crust intact as it is about hygiene. "
            "Cookie melts are the fussiest thing we make, and they're packed so the coating "
            "doesn't touch the lid.\n\n"
            "## Timing over speed\n\n"
            "We'd rather send an order out at the right hour than the earliest one. Dubai, "
            "Sharjah and Ajman are close enough for daily runs. Abu Dhabi, Al Ain, Ras Al "
            "Khaimah, Fujairah and Umm Al Quwain go out on planned runs so a box isn't sitting "
            "in a hot vehicle while a driver works through a route. In July that decision "
            "matters more than the recipe does.\n\n"
            "## When it arrives\n\n"
            "Put it somewhere cool, not in the fridge, unless it's a chilled dessert like "
            "tiramisu or basque cheesecake — those go straight in. Brownies and cookies go "
            "hard in a fridge and never fully recover. An airtight container on the counter "
            "is better for the three days they'll last.\n\n"
            "## Where we go\n\n"
            "All seven emirates: Dubai, Abu Dhabi, Sharjah, Ajman, Ras Al Khaimah, Fujairah "
            "and Umm Al Quwain, with Al Ain covered under Abu Dhabi. The fee is quoted from "
            "your address at checkout, and it's free over AED 200. Pickup from Sharjah costs "
            "nothing.\n\n"
            "Sending it as a gift? Add a note at checkout and we'll write it by hand. The "
            "price doesn't go in the box.\n\n"
            "Order at meltingmomentscakes.com, or on WhatsApp at +971 50 368 7757."
        ),
        "cover_image": f"{BANNERS}/banner-desserts.jpg",
        "meta_description": (
            "How dessert delivery works across Dubai, Sharjah, Abu Dhabi and the rest of the "
            "UAE in summer heat — packing, run planning, delivery fees and how to store it "
            "when it lands."
        ),
        "tags": ["delivery", "UAE", "Dubai", "Sharjah", "gifting"],
    },
    "ar": {
        "title": "توصيل الحلويات في الإمارات والحرارة 42 درجة",
        "excerpt": (
            "الشوكولاتة تذوب عند حرارة الجسم، وسيارة واقفة في الشارقة تتجاوز الستين. هذا كيف "
            "تصل الطلبات سليمة من المطبخ إلى دبي وأبوظبي ورأس الخيمة."
        ),
        "body": (
            "الوصفة هي النصف السهل. النصف الصعب هو الطريق.\n\n"
            "تبدأ الشوكولاتة باللين عند 30 درجة تقريباً. وسيارة واقفة في الشمس هنا تتجاوز 60 "
            "درجة من الداخل. فالسؤال ليس إن كانت العلبة تنجو من الصيف، بل كم تقضي من الوقت في "
            "الطريق، وفي أي حرارة.\n\n"
            "## التغليف\n\n"
            "كل صنف يُغلَّف بطريقته. الكوكيز في علب صلبة مع ورق زبدة بين الطبقات، لأن الكوكي "
            "الذي ينزلق يصل مكسوراً. والبراوني يُلفّ قطعة قطعة قبل التعبئة، وهذا اللفّ يحمي "
            "القشرة العلوية بقدر ما يحفظ النظافة. أما الكوكي ملت فهو أكثر ما نصنعه حساسية، "
            "ويُعبَّأ بحيث لا تلامس طبقته الغطاء.\n\n"
            "## التوقيت قبل السرعة\n\n"
            "نفضّل إرسال الطلب في الساعة المناسبة لا في أبكر ساعة. دبي والشارقة وعجمان قريبة "
            "بما يكفي لجولات يومية. وأبوظبي والعين ورأس الخيمة والفجيرة وأم القيوين ضمن جولات "
            "مجدولة، حتى لا تبقى علبة في سيارة حارة بينما يكمل السائق خطّه. في يوليو، هذا "
            "القرار أهم من الوصفة.\n\n"
            "## عند الوصول\n\n"
            "ضعها في مكان بارد، لا في الثلاجة، إلا إن كانت حلوى مبرّدة كالتيراميسو أو تشيز "
            "كيك الباسك — فهذه تدخل الثلاجة مباشرة. البراوني والكوكيز يتصلّبان في الثلاجة ولا "
            "يعودان كما كانا. علبة محكمة على الرخامة أفضل للأيام الثلاثة التي سيعيشانها.\n\n"
            "## إلى أين نصل\n\n"
            "الإمارات السبع جميعها: دبي وأبوظبي والشارقة وعجمان ورأس الخيمة والفجيرة وأم "
            "القيوين، والعين ضمن أبوظبي. تُحتسب الرسوم من عنوانك عند الدفع، وهي مجانية فوق 200 "
            "درهم. والاستلام من الشارقة بلا رسوم.\n\n"
            "ترسلها هدية؟ أضف ملاحظة عند الدفع ونكتبها بخط اليد. ولا يوضع السعر في العلبة.\n\n"
            "اطلب من meltingmomentscakes.com أو على واتساب 7757 368 50 971+."
        ),
        "cover_image": f"{BANNERS}/banner-desserts.jpg",
        "meta_description": (
            "كيف يعمل توصيل الحلويات في دبي والشارقة وأبوظبي وبقية الإمارات في حرّ الصيف — "
            "التغليف وتنظيم الجولات ورسوم التوصيل وطريقة الحفظ بعد الوصول."
        ),
        "tags": ["توصيل", "الإمارات", "دبي", "الشارقة", "هدايا"],
    },
}

POST_DUBAI = {
    "en": {
        "title": "Ordering dessert in Dubai: what actually travels well",
        "excerpt": (
            "Not everything survives a drive across town. If you're ordering dessert delivery "
            "in Dubai, here's what holds up, what doesn't, and how far ahead to order it."
        ),
        "body": (
            "We deliver into Dubai every day, and after a few thousand boxes there's a clear "
            "pattern in what arrives looking the way it left.\n\n"
            "## What travels best\n\n"
            "Brownies. They're dense, they have no structure to collapse, and the individual "
            "wrap keeps the crust on top. If you're ordering for an office or sending "
            "something to a friend across town and you don't want to think about it, order "
            "brownies.\n\n"
            "Cookies are close behind, as long as they're packed flat. Mix boxes work well "
            "because the box itself does the holding — nine pieces braced against each other "
            "don't move.\n\n"
            "## What needs more care\n\n"
            "Cookie melts. They're deliberately underbaked in the middle, which is the whole "
            "point of them and also why they're the fussiest thing to move. They arrive fine, "
            "but eat them the day they land, and give them ten seconds in the microwave "
            "first.\n\n"
            "Chilled desserts — tiramisu, basque cheesecake, mousse — go straight into the "
            "fridge on arrival. Don't leave them on a counter in July.\n\n"
            "## How far ahead\n\n"
            "A day or two for a normal order. Everything is baked to order, so same-day is "
            "possible but depends on what's already in the oven — the checkout shows the slots "
            "that are genuinely open once you put your address in. For a birthday, a dessert "
            "table or an office of fifty, give us about a week.\n\n"
            "## The delivery bit\n\n"
            "The fee is calculated from your address at checkout, and it's free over AED 200. "
            "Payment is card online — cash only if you're collecting from Sharjah yourself.\n\n"
            "If you're not sure what to order for a particular occasion, message us on "
            "WhatsApp. We'd rather point you at the right box than sell you the wrong one."
        ),
        "cover_image": f"{BANNERS}/hero-mix-box.jpg",
        "meta_description": (
            "A guide to dessert delivery in Dubai — which brownies, cookies and desserts "
            "travel well, how much notice to give, and how delivery is priced. From Melting "
            "Moments Cakes in Sharjah."
        ),
        "tags": ["Dubai", "delivery", "dessert delivery", "ordering"],
    },
    "ar": {
        "title": "طلب الحلويات في دبي: ما الذي يتحمّل الطريق فعلاً",
        "excerpt": (
            "ليس كل شيء ينجو من رحلة عبر المدينة. إن كنت تطلب توصيل حلويات في دبي، هذا ما "
            "يتحمّل، وما يحتاج عناية، وكم من الوقت تحتاج قبل الموعد."
        ),
        "body": (
            "نوصّل إلى دبي يومياً، وبعد بضعة آلاف من العلب صار النمط واضحاً: ما الذي يصل كما "
            "خرج.\n\n"
            "## الأفضل في الطريق\n\n"
            "البراوني. كثيف، ولا بنية فيه لتنهار، واللفّ الفردي يحفظ القشرة العلوية. إن كنت "
            "تطلب لمكتب أو ترسل شيئاً لصديق في الطرف الآخر من المدينة ولا تريد التفكير، اطلب "
            "براوني.\n\n"
            "والكوكيز قريب منه، ما دام معبّأ مسطّحاً. والصناديق المشكّلة ممتازة لأن العلبة نفسها "
            "تمسك المحتوى — تسع قطع متساندة لا تتحرك.\n\n"
            "## ما يحتاج عناية\n\n"
            "الكوكي ملت. ناقص النضج من الوسط عن قصد، وهذا كل جماله وسبب صعوبة نقله. يصل "
            "سليماً، لكن تناوله في يوم وصوله، وامنحه عشر ثوانٍ في الميكروويف أولاً.\n\n"
            "والحلويات المبرّدة — التيراميسو وتشيز كيك الباسك والموس — تدخل الثلاجة فور "
            "الوصول. لا تتركها على الرخامة في يوليو.\n\n"
            "## كم من الوقت قبل الموعد\n\n"
            "يوم أو يومان لطلب عادي. كل شيء يُخبز عند الطلب، فالتوصيل في نفس اليوم ممكن لكنه "
            "يعتمد على ما في الفرن — وصفحة الدفع تعرض المواعيد المتاحة فعلاً بعد إدخال عنوانك. "
            "أما لعيد ميلاد أو طاولة حلويات أو مكتب من خمسين شخصاً، فامنحنا أسبوعاً.\n\n"
            "## أما التوصيل\n\n"
            "تُحتسب الرسوم من عنوانك عند الدفع، وهي مجانية فوق 200 درهم. والدفع بالبطاقة عبر "
            "الإنترنت — والنقد فقط إن كنت ستستلم من الشارقة بنفسك.\n\n"
            "وإن لم تكن متأكداً مما يناسب مناسبتك، راسلنا على واتساب. نفضّل أن ندلّك على العلبة "
            "الصحيحة بدل أن نبيعك الخطأ."
        ),
        "cover_image": f"{BANNERS}/hero-mix-box.jpg",
        "meta_description": (
            "دليل توصيل الحلويات في دبي — أي براوني وكوكيز وحلويات تتحمّل الطريق، وكم من "
            "الإشعار تحتاج، وكيف تُحتسب رسوم التوصيل."
        ),
        "tags": ["دبي", "توصيل", "حلويات", "الطلب"],
    },
}

POST_EID = {
    "en": {
        "title": "Eid and Ramadan boxes: what to order, and when to order it",
        "excerpt": (
            "The two busiest weeks of our year. Here's what people order for Ramadan and Eid, "
            "how far ahead to book, and what works for gifting a long list of people."
        ),
        "body": (
            "Ramadan and Eid are the only two periods where we close the order book early, "
            "and every year somebody messages on the day itself hoping. So — the honest "
            "version, ahead of time.\n\n"
            "## When to order\n\n"
            "For a family box, a week is comfortable. For gifting — twenty boxes, fifty, an "
            "office list — two weeks, and earlier is better. The last few days before Eid are "
            "usually closed to new orders because the kitchen is already committed. It's one "
            "oven and one baker.\n\n"
            "## What people actually order\n\n"
            "**For iftar and suhoor at home:** brownies and cookie melts, because they keep "
            "for a few days and they're easy to put on a table without plating anything.\n\n"
            "**For gifting:** mix boxes. Nine pieces, a variety, and the box looks like a gift "
            "without needing wrapping. This is what most of the Eid volume is.\n\n"
            "**For a majlis or a bigger gathering:** trays rather than boxes, and a mix of "
            "chilled desserts and baked ones so there's something for people who don't want "
            "chocolate at eleven at night.\n\n"
            "## Gifting a long list\n\n"
            "If you're sending to twenty different addresses, send us the list on WhatsApp "
            "rather than placing twenty orders. We'll sort the delivery runs by area — Dubai "
            "together, Sharjah and Ajman together, and the further emirates on planned days — "
            "which is cheaper for you and less time in a car for the boxes.\n\n"
            "Handwritten notes are included, no charge, and the price never goes in the box.\n\n"
            "## Everything is halal\n\n"
            "All of it, all year. Halal-certified ingredients and no alcohol-based "
            "flavourings — worth saying plainly during a month when people ask.\n\n"
            "Message us on WhatsApp at +971 50 368 7757 to plan an Eid or Ramadan order."
        ),
        "cover_image": f"{BANNERS}/banner-cookies.jpg",
        "meta_description": (
            "Planning Ramadan and Eid dessert boxes in the UAE — what to order for iftar, "
            "gifting and majlis, how far ahead to book, and how bulk gifting deliveries work."
        ),
        "tags": ["Eid", "Ramadan", "gifting", "corporate", "UAE"],
    },
    "ar": {
        "title": "علب العيد ورمضان: ماذا تطلب، ومتى تطلبه",
        "excerpt": (
            "أكثر أسبوعين ازدحاماً في سنتنا. هذا ما يطلبه الناس في رمضان والعيد، وكم من الوقت "
            "تحتاج قبل الموعد، وما يناسب إهداء قائمة طويلة."
        ),
        "body": (
            "رمضان والعيد هما الفترتان الوحيدتان اللتان نغلق فيهما باب الطلبات مبكراً، وفي كل "
            "سنة يراسلنا أحدهم في اليوم نفسه على أمل. فلنقلها بصراحة، ومبكراً.\n\n"
            "## متى تطلب\n\n"
            "لعلبة عائلية، أسبوع مريح. أما للإهداء — عشرون علبة أو خمسون أو قائمة مكتب — "
            "فأسبوعان، والأبكر أفضل. الأيام الأخيرة قبل العيد تكون مغلقة عادةً أمام الطلبات "
            "الجديدة لأن المطبخ محجوز بالكامل. فرن واحد وخبّازة واحدة.\n\n"
            "## ما يطلبه الناس فعلاً\n\n"
            "**للإفطار والسحور في البيت:** البراوني والكوكي ملت، لأنهما يبقيان أياماً ويسهل "
            "وضعهما على الطاولة بلا تقديم معقّد.\n\n"
            "**للإهداء:** الصناديق المشكّلة. تسع قطع متنوعة، والعلبة نفسها تبدو كهدية بلا "
            "تغليف إضافي. ومعظم طلبات العيد من هذا النوع.\n\n"
            "**للمجلس أو تجمّع أكبر:** صواني بدل العلب، ومزيج من الحلويات المبرّدة والمخبوزة "
            "ليجد من لا يريد شوكولاتة في الحادية عشرة ليلاً شيئاً يناسبه.\n\n"
            "## إهداء قائمة طويلة\n\n"
            "إن كنت سترسل إلى عشرين عنواناً، أرسل لنا القائمة على واتساب بدل تقديم عشرين "
            "طلباً. سنرتّب جولات التوصيل حسب المنطقة — دبي معاً، والشارقة وعجمان معاً، "
            "والإمارات الأبعد في أيام مجدولة — وهذا أوفر لك وأقل وقتاً للعلب في السيارة.\n\n"
            "والبطاقات المكتوبة بخط اليد مشمولة بلا رسوم، ولا يوضع السعر في العلبة أبداً.\n\n"
            "## كل شيء حلال\n\n"
            "كل شيء، طوال السنة. مكونات معتمدة حلال وبلا نكهات كحولية — ويستحق قولها بوضوح في "
            "شهر يسأل فيه الناس.\n\n"
            "راسلنا على واتساب 7757 368 50 971+ لترتيب طلب العيد أو رمضان."
        ),
        "cover_image": f"{BANNERS}/banner-cookies.jpg",
        "meta_description": (
            "تخطيط علب حلويات رمضان والعيد في الإمارات — ماذا تطلب للإفطار والإهداء والمجلس، "
            "وكم من الوقت تحتاج، وكيف يعمل التوصيل للطلبات الكبيرة."
        ),
        "tags": ["العيد", "رمضان", "هدايا", "مؤسسات", "الإمارات"],
    },
}

POST_EGGLESS = {
    "en": {
        "title": "Eggless baking: what changes, and what doesn't",
        "excerpt": (
            "Eggless doesn't have to mean second-best. Here's what eggs are actually doing in "
            "a brownie, what replaces them, and which of our recipes take the swap well."
        ),
        "body": (
            "We get asked for eggless every week — for kids, for allergies, for people who "
            "just don't eat them. So it's worth explaining what actually changes, because "
            '"eggless" gets treated as a compromise and it doesn\'t have to be one.\n\n'
            "## What eggs are doing\n\n"
            "Three jobs, and they matter differently depending on what you're baking. They "
            "bind, so the thing holds together. They add water, which becomes steam, which is "
            "lift. And the yolk adds fat and emulsifies, which is most of what makes a "
            "brownie feel rich rather than greasy.\n\n"
            "Which job matters most tells you how hard the recipe is to convert.\n\n"
            "## What converts easily\n\n"
            "Brownies, and it surprises people. A brownie doesn't want lift — it wants to be "
            "dense. Take the eggs out and replace the binding and the fat, and you're most of "
            "the way there. A well-made eggless brownie is honestly hard to pick out in a "
            "blind taste.\n\n"
            "Thick cookies convert well for the same reason. They're not relying on rise.\n\n"
            "## What's harder\n\n"
            "Anything light. Sponge, mousse, anything where the structure *is* whipped egg. "
            "You can get there, but it's a different recipe rather than a substitution, and "
            "pretending otherwise is how you end up with something dense and apologetic.\n\n"
            "## Ordering eggless\n\n"
            "There's an eggless category on the menu — those are the recipes we've worked "
            "through properly and are happy to put our name on. If you want something from "
            "another category made eggless, message us before ordering. Sometimes the answer "
            "is yes with a couple of days' notice, and sometimes it's an honest no.\n\n"
            "One thing we can't change: it's a home kitchen that also handles eggs, so an "
            "eggless recipe isn't an allergen-free environment. If it's an allergy rather than "
            "a preference, tell us and we'll be straight with you about what we can promise."
        ),
        "cover_image": f"{TILES}/tile-eggless.jpg",
        "meta_description": (
            "What eggs do in baking, what replaces them, and which recipes convert well — plus "
            "how to order eggless brownies and cookies from Melting Moments in the UAE."
        ),
        "tags": ["eggless", "baking", "dietary", "brownies"],
    },
    "ar": {
        "title": "الخبز بلا بيض: ما الذي يتغيّر، وما الذي لا يتغيّر",
        "excerpt": (
            "«خالٍ من البيض» لا يعني بالضرورة أقل جودة. هذا ما يفعله البيض فعلاً في البراوني، "
            "وما الذي يحل محله، وأي وصفاتنا تقبل التبديل."
        ),
        "body": (
            "يُطلب منا الخالي من البيض كل أسبوع — للأطفال، أو بسبب حساسية، أو لأن أحدهم لا "
            "يأكله ببساطة. لذا يستحق الأمر شرحاً، لأن «بلا بيض» يُعامَل كتنازل، وهو ليس كذلك "
            "بالضرورة.\n\n"
            "## ماذا يفعل البيض\n\n"
            "ثلاث وظائف، وأهميتها تختلف حسب ما تخبزه. يربط، فيتماسك الخليط. ويضيف ماءً يتحوّل "
            "بخاراً، والبخار هو الارتفاع. وصفار البيض يضيف دهناً ويستحلب، وهذا معظم ما يجعل "
            "البراوني غنياً لا دهنياً.\n\n"
            "وأي وظيفة هي الأهم يحدّد صعوبة تحويل الوصفة.\n\n"
            "## ما يتحوّل بسهولة\n\n"
            "البراوني، وهذا يفاجئ الناس. البراوني لا يريد ارتفاعاً — يريد أن يكون كثيفاً. "
            "أخرِج البيض وعوّض الربط والدهن، وتكون قطعت معظم الطريق. البراوني الخالي من البيض "
            "المصنوع جيداً يصعب تمييزه في تذوّق أعمى.\n\n"
            "والكوكيز السميك يتحوّل جيداً للسبب نفسه. فهو لا يعتمد على الانتفاخ.\n\n"
            "## ما هو أصعب\n\n"
            "كل ما هو خفيف. الإسفنجي، والموس، وكل ما تكون بنيته بيضاً مخفوقاً. يمكن الوصول، "
            "لكنها وصفة مختلفة لا مجرد استبدال، والتظاهر بغير ذلك هو ما ينتهي بشيء ثقيل "
            "معتذر.\n\n"
            "## كيف تطلب الخالي من البيض\n\n"
            "توجد فئة خالية من البيض في القائمة — وهي الوصفات التي عملنا عليها كما ينبغي "
            "ونضع اسمنا عليها. وإن أردت صنفاً من فئة أخرى بلا بيض، راسلنا قبل الطلب. أحياناً "
            "الجواب نعم بإشعار يومين، وأحياناً لا بصراحة.\n\n"
            "وأمر واحد لا نستطيع تغييره: هذا مطبخ منزلي يتعامل أيضاً مع البيض، فالوصفة الخالية "
            "منه ليست بيئة خالية من مسبّبات الحساسية. إن كان الأمر حساسية لا تفضيلاً، أخبرنا "
            "ونكون صريحين معك في ما نستطيع ضمانه."
        ),
        "cover_image": f"{TILES}/tile-eggless.jpg",
        "meta_description": (
            "ماذا يفعل البيض في الخبز، وما الذي يحل محله، وأي الوصفات تتحوّل جيداً — وكيف تطلب "
            "براوني وكوكيز خالياً من البيض في الإمارات."
        ),
        "tags": ["خالي من البيض", "خبز", "غذائي", "براوني"],
    },
}

POST_OCCASIONS = {
    "en": {
        "title": "Ordering for a birthday or an office: how it actually works",
        "excerpt": (
            "Custom boxes, celebration cakes and corporate gifting — what we need from you, "
            "how far ahead, and roughly what it costs."
        ),
        "body": (
            "Most of what we make is someone ordering a box for themselves on a Tuesday. But "
            "the orders that take planning are the ones people are least sure how to ask "
            "for, so here's the whole process.\n\n"
            "## Birthdays\n\n"
            "Two shapes work. A celebration cake, or a box of the things the person actually "
            "likes — which, for a lot of people, beats a cake nobody finishes. Tell us the "
            "date, roughly how many people, and anything they don't eat. About a week is "
            "comfortable; less than three days and we're guessing at oven space.\n\n"
            "If you want a name or a message on it, say so when you ask, not after.\n\n"
            "## Dessert tables and gatherings\n\n"
            "For anything over about twenty people, trays work better than boxes and a mix of "
            "chilled and baked works better than all one thing. We'll suggest quantities if "
            "you tell us the headcount — it's usually less than people think, because nobody "
            "eats three desserts.\n\n"
            "## Corporate and client gifting\n\n"
            "Office deliveries, client boxes, event gifting, Ramadan and Eid hampers. What we "
            "need: how many boxes, the delivery date, whether it's one address or many, and "
            "roughly the budget per box. We'll come back with two or three options rather "
            "than a catalogue.\n\n"
            "A week's notice for a normal corporate order. Over a hundred boxes, talk to us "
            "two to three weeks out — that's a planning problem, not a baking one.\n\n"
            "Multiple addresses are fine and usually cheaper than they sound: we group the "
            "runs by area rather than sending a car per box.\n\n"
            "## Weddings\n\n"
            "Favours mostly — small boxes, one or two pieces, often with a printed or "
            "handwritten tag. These need the longest lead time because the quantities are "
            "large and the date doesn't move. Three weeks, and talk to us before you commit "
            "to a number.\n\n"
            "## How to start\n\n"
            "WhatsApp, on +971 50 368 7757, or the contact form. Give us the date first — "
            "everything else is easier to work out once we know whether the date is even "
            "open."
        ),
        "cover_image": f"{TILES}/tile-cakes.jpg",
        "meta_description": (
            "How to order custom birthday boxes, dessert tables, wedding favours and corporate "
            "gifting from Melting Moments Cakes — lead times, quantities and delivery across "
            "the UAE."
        ),
        "tags": ["birthday", "corporate", "custom orders", "weddings", "gifting"],
    },
    "ar": {
        "title": "الطلب لعيد ميلاد أو لمكتب: كيف يجري الأمر فعلاً",
        "excerpt": (
            "العلب المخصصة وكيك الاحتفالات وهدايا الشركات — ما نحتاجه منك، وكم من الوقت قبل "
            "الموعد، والتكلفة تقريباً."
        ),
        "body": (
            "معظم ما نصنعه هو علبة يطلبها أحدهم لنفسه يوم ثلاثاء. لكن الطلبات التي تحتاج "
            "تخطيطاً هي التي يحتار الناس كيف يطلبونها، فإليك العملية كاملة.\n\n"
            "## أعياد الميلاد\n\n"
            "يعمل شكلان. كيكة احتفال، أو علبة تضم ما يحبه الشخص فعلاً — وهذا عند كثيرين أفضل "
            "من كيكة لا يكملها أحد. أخبرنا بالتاريخ، وعدد الأشخاص تقريباً، وأي شيء لا "
            "يأكلونه. أسبوع مريح؛ وأقل من ثلاثة أيام يعني أننا نخمّن مساحة الفرن.\n\n"
            "وإن أردت اسماً أو رسالة عليها، قل ذلك عند الطلب لا بعده.\n\n"
            "## طاولات الحلويات والتجمّعات\n\n"
            "لأي عدد يتجاوز العشرين تقريباً، الصواني أفضل من العلب، ومزيج المبرّد والمخبوز أفضل "
            "من صنف واحد. سنقترح الكميات إن أخبرتنا بالعدد — وهي عادةً أقل مما يظن الناس، لأن "
            "لا أحد يأكل ثلاث حلويات.\n\n"
            "## هدايا الشركات والعملاء\n\n"
            "توصيل للمكاتب، وعلب العملاء، وهدايا الفعاليات، وسلال رمضان والعيد. ما نحتاجه: عدد "
            "العلب، وتاريخ التسليم، وهل هو عنوان واحد أم عدة عناوين، والميزانية التقريبية "
            "للعلبة. وسنعود إليك بخيارين أو ثلاثة بدل كتالوج.\n\n"
            "أسبوع من الإشعار لطلب مؤسسي عادي. وفوق مئة علبة، كلّمنا قبل أسبوعين إلى ثلاثة — "
            "فهذه مسألة تنظيم لا خبز.\n\n"
            "وتعدد العناوين ليس مشكلة، وغالباً أرخص مما يبدو: نجمّع الجولات حسب المنطقة بدل "
            "إرسال سيارة لكل علبة.\n\n"
            "## الأعراس\n\n"
            "التوزيعات غالباً — علب صغيرة بقطعة أو قطعتين، وغالباً ببطاقة مطبوعة أو مكتوبة "
            "بخط اليد. وهذه تحتاج أطول مهلة لأن الكميات كبيرة والتاريخ لا يتحرك. ثلاثة أسابيع، "
            "وكلّمنا قبل أن تلتزم برقم.\n\n"
            "## كيف تبدأ\n\n"
            "واتساب على 7757 368 50 971+، أو نموذج التواصل. أعطنا التاريخ أولاً — فكل ما عداه "
            "أسهل بعد أن نعرف إن كان التاريخ متاحاً أصلاً."
        ),
        "cover_image": f"{TILES}/tile-cakes.jpg",
        "meta_description": (
            "كيف تطلب علب أعياد الميلاد المخصصة وطاولات الحلويات وتوزيعات الأعراس وهدايا "
            "الشركات من ملتنج مومنتس — المهل والكميات والتوصيل في الإمارات."
        ),
        "tags": ["عيد ميلاد", "مؤسسات", "طلبات مخصصة", "أعراس", "هدايا"],
    },
}

# Existing slugs keep their URLs; only the words behind them change.
BLOG_REWRITES = [
    ("the-art-of-the-perfect-brownie", POST_BROWNIE),
    ("why-we-use-premium-ingredients", POST_INGREDIENTS),
    ("dessert-delivery-across-the-uae", POST_DELIVERY),
]

BLOG_NEW = [
    ("dessert-delivery-dubai-what-travels-well", POST_DUBAI),
    ("eid-and-ramadan-dessert-boxes", POST_EID),
    ("eggless-baking-what-changes", POST_EGGLESS),
    ("birthday-and-corporate-orders", POST_OCCASIONS),
]

CMS_PATCHES = [
    ("home", HOME_PATCH),
    ("about", ABOUT_PATCH),
    ("faq", FAQ_PATCH),
    ("contact", CONTACT_PATCH),
]


def _apply(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Patch over base, recursing into dicts and replacing everything else.

    Lists are replaced whole rather than merged: the FAQ's `items` and the
    About page's `values` are ordered sequences where a positional merge would
    produce a question with one page's wording and another page's answer.
    """
    result = dict(base)
    for key, value in patch.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = _apply(current, value)
        else:
            result[key] = value
    return result


def _backup_table() -> sa.Table:
    return sa.table(
        BACKUP_TABLE,
        sa.column("id", UUID),
        sa.column("kind", sa.String),
        sa.column("slug", sa.String),
        sa.column("content", JSONB),
    )


def upgrade() -> None:
    conn = op.get_bind()

    op.create_table(
        BACKUP_TABLE,
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("slug", sa.String(150), nullable=False),
        # NULL means "this row did not exist before the migration" — the
        # downgrade deletes those rather than restoring them.
        sa.Column("content", JSONB, nullable=True),
    )
    backup = _backup_table()

    # ── CMS pages ─────────────────────────────────────────────────────────────
    for slug, patch in CMS_PATCHES:
        row = conn.execute(
            sa.text("SELECT content FROM cms_pages WHERE slug = :s"), {"s": slug}
        ).fetchone()
        if not row:
            continue

        content: dict[str, Any] = row[0] or {}
        op.bulk_insert(
            backup,
            [{"id": uuid.uuid4(), "kind": "cms", "slug": slug, "content": content}],
        )

        updated = dict(content)
        for locale, locale_patch in patch.items():
            updated[locale] = _apply(updated.get(locale) or {}, locale_patch)

        conn.execute(
            sa.text(
                "UPDATE cms_pages SET content = CAST(:c AS jsonb), updated_at = now() "
                "WHERE slug = :s"
            ),
            {"c": json.dumps(updated), "s": slug},
        )

    # ── Blog: rewrite in place ────────────────────────────────────────────────
    for slug, content in BLOG_REWRITES:
        row = conn.execute(
            sa.text("SELECT content FROM blog_posts WHERE slug = :s"), {"s": slug}
        ).fetchone()
        if not row:
            continue

        op.bulk_insert(
            backup,
            [
                {
                    "id": uuid.uuid4(),
                    "kind": "blog",
                    "slug": slug,
                    "content": row[0] or {},
                }
            ],
        )
        conn.execute(
            sa.text(
                "UPDATE blog_posts SET content = CAST(:c AS jsonb), updated_at = now() "
                "WHERE slug = :s"
            ),
            {"c": json.dumps(content), "s": slug},
        )

    # ── Blog: four new posts ──────────────────────────────────────────────────
    for slug, content in BLOG_NEW:
        exists = conn.execute(
            sa.text("SELECT 1 FROM blog_posts WHERE slug = :s"), {"s": slug}
        ).fetchone()
        if exists:
            continue

        op.bulk_insert(
            backup,
            [{"id": uuid.uuid4(), "kind": "blog", "slug": slug, "content": None}],
        )
        conn.execute(
            sa.text(
                "INSERT INTO blog_posts (id, slug, is_active, content, created_at, updated_at) "
                "VALUES (:id, :s, true, CAST(:c AS jsonb), now(), now())"
            ),
            {"id": str(uuid.uuid4()), "s": slug, "c": json.dumps(content)},
        )


def downgrade() -> None:
    conn = op.get_bind()

    rows = conn.execute(
        sa.text(f"SELECT kind, slug, content FROM {BACKUP_TABLE}")  # noqa: S608
    ).fetchall()

    for kind, slug, content in rows:
        if kind == "cms":
            conn.execute(
                sa.text(
                    "UPDATE cms_pages SET content = CAST(:c AS jsonb), updated_at = now() "
                    "WHERE slug = :s"
                ),
                {"c": json.dumps(content), "s": slug},
            )
        elif content is None:
            conn.execute(sa.text("DELETE FROM blog_posts WHERE slug = :s"), {"s": slug})
        else:
            conn.execute(
                sa.text(
                    "UPDATE blog_posts SET content = CAST(:c AS jsonb), updated_at = now() "
                    "WHERE slug = :s"
                ),
                {"c": json.dumps(content), "s": slug},
            )

    op.drop_table(BACKUP_TABLE)
