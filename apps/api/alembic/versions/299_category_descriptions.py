"""A description, in English and Arabic, for every category.

All 20 categories had neither. On the storefront that meant a category page
with nothing under its title, and a search description generated from a
template ("Order brownies online from Melting Moments Cakes…"). That generated
line is what a search engine showed, including as a sitelink snippet under a
brand search. The 11 counter-only categories (drinks, coffee, momos…) and
cupcakes are not on the website, but they are edited in the same console, and
empty fields there are just a gap waiting to be filled in badly later.

Each description names real items in the category, taken from the live
catalogue while this was written (bestsellers first where the shop flags them),
and says only what holds for the whole category. Website categories say "baked
to order in Sharjah" or "delivered across the UAE". Counter categories say "at
our counter". The Christmas category is empty today, so its text says that
seasonal items appear when the menu is live. Every one is 120–160 characters,
the band the console's length guide marks green.

**Guarded, per convention 7, one field at a time.** The English description is
written only where it is empty. The Arabic one is written only where
`translations.ar.description` is empty, and without touching any other key in
`translations` (the Arabic name included). Once someone writes their own text
in the console, this matches nothing. Downgrade clears only a value that is
still exactly what this wrote.

Revision ID: 299_category_descriptions
Revises: 298_arabic_seo_descriptions
Create Date: 2026-09-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "299_category_descriptions"
down_revision: Union[str, None] = "298_arabic_seo_descriptions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: (slug, English, Arabic).
DESCRIPTIONS: list[tuple[str, str, str]] = [
    (
        "brownies",
        "Fudgy brownies baked to order in Sharjah — Ferrero, Lindor, Pistachio Kunafa, Tiramisu, Snickers and Dark Chocolate & Walnut. Delivered across the UAE.",
        "براونيز فادج طري يُخبز عند الطلب في الشارقة — فيريرو وليندور والكنافة بالفستق وتيراميسو وسنيكرز، والكلاسيكي بالشوكولاتة الداكنة والجوز. توصيل لكل الإمارات.",
    ),
    (
        "cookies",
        "Brown butter cookies, chewy in the middle and filled — Nutella, Kinder, Lotus, Red Velvet & Nutella, Cookies & Cream and Brookies. Baked to order in Sharjah.",
        "كوكيز بالزبدة البنية، طري من الداخل ومحشو — نوتيلا وكيندر ولوتس وريد فيلفيت بالنوتيلا وكوكيز وكريمة وبروكييز. تُخبز عند الطلب في الشارقة.",
    ),
    (
        "cookie-melts",
        "Warm, gooey brown butter cookie melts to share — Nutella, Kinder, Lotus, Brookie and Pistachio Kunafa, in 250g and 500g sizes. Baked to order in Sharjah.",
        "كوكيز ميلت دافئ ولزج للمشاركة — نوتيلا وكيندر ولوتس وبروكي والكنافة بالفستق، بحجمي 250 و500 غرام. يُخبز عند الطلب في مطبخنا بالشارقة.",
    ),
    (
        "cakes",
        "Layered cake slices — Chocolate Matilda, Pistachio Kunafa Chocolate, Chocolate Fudge & Raspberry, Lemon Raspberry and more. Baked to order in Sharjah.",
        "شرائح كيك بطبقات — ماتيلدا بالشوكولاتة، والشوكولاتة بالكنافة والفستق، والشوكولاتة والفدج والتوت، والليمون والتوت والمزيد. تُخبز عند الطلب في الشارقة.",
    ),
    (
        "desserts",
        "Chilled desserts from our Sharjah kitchen — silky dark chocolate mousse, espresso-soaked tiramisu and a creamy Basque cheesecake. Delivered across the UAE.",
        "حلويات باردة من مطبخنا في الشارقة — موس الشوكولاتة الداكنة الناعم، وتيراميسو بالإسبريسو، وتشيز كيك الباسك الكريمي. توصيل إلى كل الإمارات.",
    ),
    (
        "mix-boxes",
        "Can't pick one? Boxes of 3, 6 or 9 mixing our cookies, our brownies, or both — an easy gift, baked to order in Sharjah and delivered across the UAE.",
        "محتار؟ صناديق من 3 أو 6 أو 9 قطع تجمع الكوكيز أو البراونيز أو كليهما — هدية سهلة تُخبز عند الطلب في الشارقة وتُوصَّل إلى كل الإمارات.",
    ),
    (
        "eggless",
        "Eggless without compromise: chewy dark chocolate fudge brownies made with no eggs and the same great taste. Baked to order in Sharjah, delivered UAE-wide.",
        "بدون بيض وبلا تنازل: براونيز فادج بالشوكولاتة الداكنة، طري ولذيذ بنفس الطعم الرائع ومن دون بيض. يُخبز عند الطلب في الشارقة ويُوصَّل لكل الإمارات.",
    ),
    (
        "extras",
        "Finishing touches for your order, like a handwritten gift note card for someone special. Add your message in the order notes when you check out.",
        "لمسات أخيرة لطلبك، مثل بطاقة هدية مكتوبة بخط اليد لشخص عزيز عليك. أضف رسالتك في ملاحظات الطلب عند إتمام الشراء، ونكتبها لك بعناية.",
    ),
    (
        "cupcakes",
        "Classic cupcakes, freshly baked with a soft vanilla sponge and a swirl of buttercream on top. A small treat, available at our Melting Moments counters.",
        "كب كيك كلاسيكي يُخبز طازجاً بإسفنج الفانيليا الطري ولمسة من كريمة الزبدة في الأعلى. حلوى صغيرة متوفرة في فروع ملتنج مومنتس.",
    ),
    (
        "cat-ramadan",
        "Ramadan gift boxes for iftar and gifting — our Ramadan Advent Gift Box in 12 and 30 pieces, filled with Melting Moments treats to count down the holy month.",
        "علب هدايا رمضان للإفطار والإهداء — علبة رمضان للعد التنازلي بحجمي 12 و30 قطعة، مليئة بحلويات ملتنج مومنتس لتعدّ بها أيام الشهر الفضيل.",
    ),
    (
        "cat-christmas",
        "Festive Melting Moments treats for the Christmas season, from gift boxes to holiday bakes. Seasonal items appear here whenever the festive menu is live.",
        "حلويات ملتنج مومنتس لموسم عيد الميلاد، من علب الهدايا إلى مخبوزات العطلة. تظهر المنتجات الموسمية هنا عندما تكون قائمة العيد متاحة.",
    ),
    (
        "cat-juices",
        "Fresh juices and mocktails at our counter — orange, mango, watermelon, apple & carrot, lemon mint ginger, plus Strawberry and Ocean Blue mojito mocktails.",
        "عصائر طازجة وموهيتو في فروعنا — برتقال ومانجو وبطيخ وتفاح وجزر وليمون بالنعناع والزنجبيل، بالإضافة إلى موهيتو الفراولة والأوشن بلو.",
    ),
    (
        "cat-smoothies",
        "Thick, fruity smoothies blended to order at our counter — Tropical Colada, Mango Paradise, Raspberry Lover and the Green Machine for something lighter.",
        "سموذي فواكه كثيف يُحضّر عند الطلب في فروعنا — تروبيكال كولادا ومانجو بارادايس ورازبيري لوفر، وغرين ماشين لمن يفضّل الخيار الأخف.",
    ),
    (
        "cat-tea",
        "Hot and iced teas at our counter — a proper Karak, black and green tea, Nescafe tea and a refreshing lemon iced tea to go with your brownie.",
        "شاي ساخن ومثلج في فروعنا — كرك أصيل وشاي أسود وأخضر وشاي نسكافيه وشاي ليمون مثلج منعش يرافق قطعة البراوني المفضلة لديك في أي وقت.",
    ),
    (
        "cat-cold-coffee",
        "Iced coffee at our counter — Iced Spanish Latte, Caramel Macchiato, Cafe Mocha, Americano and Latte, plus Iced Matcha Latte and Iced Chocolate.",
        "قهوة مثلجة في فروعنا — آيس سبانش لاتيه وكراميل ماكياتو وموكا وأمريكانو ولاتيه، بالإضافة إلى آيس ماتشا لاتيه وآيس شوكولاتة.",
    ),
    (
        "cat-hot-coffee",
        "Espresso-based coffee made at our counter — Spanish Latte, Pistachio Latte, Flat White, Cappuccino, Turkish Coffee and more, plus matcha and hot chocolate.",
        "قهوة إسبريسو تُحضّر في فروعنا — سبانش لاتيه وبستاشيو لاتيه وفلات وايت وكابتشينو وقهوة تركية والمزيد، بالإضافة إلى الماتشا والشوكولاتة الساخنة.",
    ),
    (
        "cat-snacks",
        "Quick grab-and-go snacks at our counter — chips, biscuits, chocolate bars and instant noodles for when you need a bite on the way.",
        "وجبات خفيفة سريعة في فروعنا — شيبس وبسكويت وألواح شوكولاتة ونودلز سريعة التحضير لمن يحتاج لقمة سريعة على الطريق في أي وقت من اليوم.",
    ),
    (
        "cat-momos",
        "Steamed momos made to order — classic chicken, soy coriander chicken, mushroom cheese and karak mix veg dumplings, served hot at our counter.",
        "مومو على البخار يُحضّر عند الطلب — دجاج كلاسيكي، ودجاج بالصويا والكزبرة، وفطر بالجبن، وخضار مشكلة بالكرك، يُقدَّم ساخناً في فروعنا.",
    ),
    (
        "cat-savouries",
        "Savoury bites at our counter — pocket pizza, chicken puff, beef bun kebab and fresh chicken, vegetable, and vegetable & egg sandwiches.",
        "مخبوزات مالحة في فروعنا — بيتزا جيب وبف دجاج وكباب لحم بالخبز، وساندويشات طازجة بالدجاج والخضار والخضار مع البيض لوجبة خفيفة ومشبعة.",
    ),
    (
        "cat-drinks",
        "Cold drinks from our counter fridge — Coca Cola, Pepsi, 7UP, Fanta, Mirinda and their diet versions, Red Bull, Perrier and bottled water.",
        "مشروبات باردة من ثلاجة فروعنا — كوكاكولا وبيبسي وسفن أب وفانتا وميرندا ونسخها الدايت، وريد بُل وبيرييه ومياه معبأة لترافق طلبك.",
    ),
]


def upgrade() -> None:
    conn = op.get_bind()
    for slug, en, ar in DESCRIPTIONS:
        conn.execute(
            sa.text(
                "UPDATE categories SET description = :en, updated_at = now() "
                "WHERE slug = :slug AND coalesce(description, '') = ''"
            ),
            {"slug": slug, "en": en},
        )
        # `||` merges one key into the Arabic object, so any Arabic name
        # already there survives. The outer `jsonb_set` creates `ar` if it is
        # missing.
        conn.execute(
            sa.text(
                "UPDATE categories SET translations = jsonb_set("
                "coalesce(translations, '{}'::jsonb), '{ar}', "
                "coalesce(translations -> 'ar', '{}'::jsonb) "
                "|| jsonb_build_object('description', CAST(:ar AS text))), "
                "updated_at = now() "
                "WHERE slug = :slug "
                "AND coalesce(translations -> 'ar' ->> 'description', '') = ''"
            ),
            {"slug": slug, "ar": ar},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for slug, en, ar in DESCRIPTIONS:
        conn.execute(
            sa.text(
                "UPDATE categories SET description = NULL "
                "WHERE slug = :slug AND description = :en"
            ),
            {"slug": slug, "en": en},
        )
        conn.execute(
            sa.text(
                "UPDATE categories SET translations = jsonb_set(translations, "
                "'{ar}', (translations -> 'ar') - 'description') "
                "WHERE slug = :slug AND translations -> 'ar' ->> 'description' = :ar"
            ),
            {"slug": slug, "ar": ar},
        )
