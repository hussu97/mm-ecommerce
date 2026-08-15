"""
Every word the shop emails, in both languages it writes.

**Why this is a Python module and not the `ui_translations` table.** That table
holds storefront copy the owner edits in the admin, cached for five minutes and
filled in by a seed. Email copy is different in three ways that matter: it is
welded to the structure of the templates beside it, a missing key renders a raw
`order.foo` string into a customer's inbox rather than onto a page somebody can
refresh, and the mailer often runs where a slow or failed lookup would be worse
than useless. Shipping the words with the templates means a key cannot be
missing at runtime without a test failing first — `test_email_locales` asserts
both dictionaries have exactly the same keys.

**Arabic is a translation, not a transliteration.** Where English is breezy
("Lovely to have you here") the Arabic is warm but plainer, because the English
register does not survive the crossing. Order numbers, prices and the AED label
stay in Western digits and Latin script in both: that is how prices are written
on the storefront, and an order number a customer has to read back over the
phone must be the same string in both languages.
"""

from __future__ import annotations

from typing import Callable

__all__ = ["DIRECTION", "STRINGS", "translator"]

#: Text direction per locale, for the `dir` attribute the whole email hangs off.
DIRECTION = {"en": "ltr", "ar": "rtl"}


STRINGS: dict[str, dict[str, str]] = {
    "en": {
        # ── chrome ────────────────────────────────────────────────────────────
        "brand.tagline": "Cakes & Bakes · United Arab Emirates",
        "footer.signoff": "Made with 100% love",
        "footer.shop": "Shop",
        "footer.about": "Our story",
        "footer.rights": "© {year} Melting Moments Cakes · United Arab Emirates",
        # The standard unmonitored-sender notice. `FROM_EMAIL` is a noreply
        # address, so nothing here may invite a reply or print a mailbox —
        # every email that needs an answer sends the customer to the contact
        # page instead, through `cta.contact_us`.
        "footer.sent_to": (
            "Sent to {email} from an address that is not monitored. "
            "Please do not reply to this message."
        ),
        # ── shared section headings ───────────────────────────────────────────
        "section.your_order": "Your order",
        #: Prefixes the message on a personalised line, in both the customer's
        #: confirmation and the owner's copy. Deliberately imperative — the
        #: owner's copy is the instruction to whoever picks up the pen.
        "item.note_label": "Handwritten note:",
        "section.collect_from": "Collect from",
        "section.delivering_to": "Delivering to",
        "section.progress": "Progress",
        "section.your_note": "Your note",
        "label.open": "Open",
        "label.phone": "Phone",
        "cta.open_in_maps": "Open in Google Maps",
        "cta.contact_us": "Get in touch",
        # ── totals ────────────────────────────────────────────────────────────
        "totals.subtotal": "Subtotal",
        "totals.discount": "Discount",
        "totals.delivery": "Delivery",
        "totals.collection": "Collection",
        "totals.total": "Total",
        "totals.vat": "Includes VAT",
        "totals.free": "Free",
        "totals.low_order_fee": "Small order fee",
        # ── the estimate panel ────────────────────────────────────────────────
        "estimate.delivery": "Estimated delivery",
        #: A date bounded by an hour, for a van that is not ours to schedule.
        "date.by_time": "{day} before {time}",
        "estimate.ready": "Ready to collect",
        "estimate.ready_since": "Ready since",
        "estimate.arriving": "Estimated delivery",
        "estimate.delivered": "Delivered",
        "estimate.collected": "Collected",
        "estimate.note_counter": "Waiting for you at the counter.",
        "estimate.note_rider": (
            "Now that a driver is carrying it, this is our best estimate."
        ),
        "estimate.note_day": "We'll confirm a time once it's collected.",
        #: For a `day_by` promise. Distinct from `note_day`, which promises a
        #: time we will never send for a partner delivery — they do not tell us
        #: their route, so the closing hour is the whole of what we can commit
        #: to and saying otherwise sets up a message that never arrives.
        "estimate.note_day_by": (
            "It's with our delivery partner, who finish their rounds by then."
        ),
        # ── timeline steps ────────────────────────────────────────────────────
        "step.preparing": "Baking your order",
        "step.ready": "Packed",
        "step.ready_handover": "Packed and handed over",
        "step.on_the_way": "Out for delivery",
        "step.delivered": "Delivered",
        "step.pickup_ready": "Ready to collect",
        "step.pickup_collected": "Collected",
        # ── dates ─────────────────────────────────────────────────────────────
        "date.today": "Today",
        "date.tomorrow": "Tomorrow",
        "date.yesterday": "Yesterday",
        # ── confirmation ──────────────────────────────────────────────────────
        "confirmation.subject": "Order confirmed",
        "confirmation.title": "Order confirmed",
        "confirmation.eyebrow": "Order confirmed",
        "confirmation.heading": "Thank you, {name}.",
        "confirmation.preheader": "We're baking {order_number} now.",
        "confirmation.lead": (
            "We've got your order and the kitchen is on it. Everything is baked "
            "to order, so it's made fresh rather than pulled off a shelf."
        ),
        "confirmation.cta": "Track your order",
        "confirmation.footnote_pickup": (
            "We'll write again the moment it's ready to collect. If anything "
            "here looks wrong, let us know and we'll sort it out."
        ),
        "confirmation.footnote_delivery": (
            "We'll write again the moment it's on its way. If anything here "
            "looks wrong, let us know and we'll sort it out."
        ),
        # ── packed ────────────────────────────────────────────────────────────
        "packed.subject_pickup": "Ready to collect",
        "packed.subject_delivery": "On its way",
        "packed.eyebrow_pickup": "Ready for collection",
        "packed.eyebrow_delivery": "Out of the kitchen",
        "packed.heading_pickup": "It's ready, {name}.",
        "packed.heading_delivery": "It's on its way, {name}.",
        "packed.preheader_pickup": (
            "{order_number} is boxed and waiting at the counter."
        ),
        "packed.preheader_delivery": "{order_number} has left the kitchen.",
        "packed.lead_pickup": (
            "Freshly boxed and waiting for you at the counter. Bring your order "
            "number — that's all we need."
        ),
        "packed.lead_delivery": (
            "Your order is packed and has been handed over for delivery. Keep "
            "your phone nearby so the driver can reach you."
        ),
        "packed.cta": "View order details",
        # ── out for delivery ──────────────────────────────────────────────────
        "otw.subject": "Out for delivery",
        "otw.eyebrow": "Out for delivery",
        "otw.heading": "On the road to you, {name}.",
        "otw.preheader": "Your order is with the driver.",
        "otw.lead": (
            "Your order has been collected and is on its way. Please keep your "
            "phone nearby — the driver may call when they arrive."
        ),
        "otw.cta_live": "Track live",
        "otw.cta_details": "Order details",
        # Shown instead of a live-tracking button when the courier sends the
        # customer their own link by text rather than giving us one to show.
        # Without it the email simply had no tracking at all and said nothing
        # about why, which reads as something missing.
        "otw.sms_note": (
            "Our delivery partner will text you a live tracking link — "
            "please check your messages."
        ),
        # ── delivered / collected ─────────────────────────────────────────────
        "delivered.subject_pickup": "Collected",
        "delivered.subject_delivery": "Delivered",
        "delivered.eyebrow_pickup": "Collected",
        "delivered.eyebrow_delivery": "Delivered",
        "delivered.heading": "Enjoy it, {name}.",
        "delivered.preheader": "{order_number} is with you. We hope it's good.",
        "delivered.lead_pickup": (
            "Thanks for coming by — your order is with you. Everything is best "
            "fresh, so don't leave it too long."
        ),
        "delivered.lead_delivery": (
            "Your order has been delivered. Everything is best fresh, so don't "
            "leave it too long."
        ),
        "delivered.cta": "Order again",
        "delivered.footnote": (
            "If anything wasn't right, tell us. We read every message, and we "
            "would much rather hear it from you than not at all."
        ),
        # ── undelivered ───────────────────────────────────────────────────────
        "undelivered.subject": "We couldn't deliver",
        "undelivered.eyebrow": "Delivery attempted",
        "undelivered.heading": "We couldn't hand it over.",
        "undelivered.preheader": (
            "The driver couldn't complete delivery for {order_number}. "
            "We'll be in touch."
        ),
        "undelivered.lead": (
            "{name}, our driver reached your address but wasn't able to complete "
            "the delivery. Nothing is lost — your order is safe with us."
        ),
        "undelivered.next_label": "What happens next",
        "undelivered.next_body": (
            "Someone from our team will contact you shortly to arrange another "
            "attempt. If it is easier, send us a better time or a different "
            "address and we will work around it."
        ),
        "undelivered.cta": "View order details",
        # ── cancelled ─────────────────────────────────────────────────────────
        "cancelled.subject": "Order cancelled",
        "cancelled.eyebrow": "Order cancelled",
        "cancelled.heading": "Your order has been cancelled.",
        "cancelled.preheader": (
            "{order_number} has been cancelled. Any payment taken is being returned."
        ),
        "cancelled.lead": (
            "{name}, order {order_number} has been cancelled and nothing more "
            "will happen with it."
        ),
        "cancelled.why_label": "Why",
        "cancelled.refund_label": "Your refund",
        "cancelled.refund_body": (
            "Any payment already taken is being returned to the card you paid "
            "with. Banks usually take five to ten working days to show it."
        ),
        # ── the refund, said inside the email that carries the bad news ───────
        #
        # There is no separate "your refund has been processed" email any more.
        # Two messages about one event is two chances to worry, and the second
        # one always arrives after the customer has already emailed to ask.
        #
        # `refund.issued` names the figure, because "any payment taken" is true
        # and useless — the customer wants to check it against their statement.
        "refund.label": "Your refund",
        "refund.issued": (
            "We've sent {amount} back to the card you paid with. Banks usually "
            "take five to ten working days to show it."
        ),
        # Said plainly rather than buried, because it is the line that stops a
        # "you refunded the wrong amount" email: the number on the statement is
        # deliberately not the order total.
        "refund.fees_kept": (
            "That is the value of the items. The delivery charge isn't included "
            "— the driver had already been booked."
        ),
        # When we could not reach the gateway. Promises the outcome without
        # naming a figure we have not actually sent.
        "refund.pending": (
            "We're returning your payment to the card you paid with. If it "
            "hasn't reached you within ten working days, tell us."
        ),
        "cancelled.cta": "Back to the shop",
        "cancelled.footnote": (
            "If this wasn't expected, tell us — we would rather hear about it "
            "than have you wonder."
        ),
        # ── refunded ──────────────────────────────────────────────────────────
        "refunded.subject": "Refund processed",
        "refunded.eyebrow": "Refund processed",
        "refunded.heading": "Your money is on its way back.",
        "refunded.preheader": "AED {total} is on its way back to your card.",
        "refunded.lead": (
            "{name}, we've refunded AED {total} for order {order_number} to the "
            "card you paid with."
        ),
        "refunded.when_label": "When you will see it",
        "refunded.when_body": (
            "Banks typically take five to ten working days to post a refund. It "
            "will appear on the same card and statement as the original payment."
        ),
        "refunded.cta": "Back to the shop",
        "refunded.footnote": (
            "If it hasn't landed after ten working days, send us your order "
            "number and we'll chase it with the bank."
        ),
        # ── payment failed ────────────────────────────────────────────────────
        "failed.subject": "Payment didn't go through",
        "failed.eyebrow": "Payment unsuccessful",
        "failed.heading": "We couldn't take the payment.",
        "failed.preheader": (
            "Your order is held. Try the payment again and we'll start baking."
        ),
        "failed.lead": (
            "{name}, your payment for order {order_number} didn't go through, so "
            "we haven't started baking yet. Nothing has been charged."
        ),
        "failed.held_label": "Your order is held",
        "failed.held_body": (
            "We are keeping it for you. Try the payment again below — most "
            "declines are a bank check that clears on a second attempt or with a "
            "different card."
        ),
        "failed.cta": "Try payment again",
        "failed.footnote": (
            "Still not working? Let us know and we'll take it from there — we "
            "can arrange payment another way."
        ),
        # ── welcome ───────────────────────────────────────────────────────────
        "welcome.subject": "Welcome to Melting Moments",
        "welcome.eyebrow": "Welcome",
        "welcome.heading": "Lovely to have you here.",
        "welcome.preheader": "Your account is ready. Everything is baked to order.",
        "welcome.lead": (
            "Your account is ready. Everything we make is baked to order in our "
            "own kitchen and delivered across all seven emirates — or boxed up "
            "for you to collect."
        ),
        "welcome.cta": "Start browsing",
        "welcome.perks_label": "With an account you can",
        "welcome.perk_track": "Track every order from the kitchen to your door",
        "welcome.perk_addresses": "Save addresses so checkout takes seconds",
        "welcome.perk_reorder": "Reorder anything you've had before",
        "welcome.footnote": (
            "Didn't sign up? You can ignore this email and nothing further will happen."
        ),
        # ── password reset ────────────────────────────────────────────────────
        "reset.subject": "Reset your password",
        "reset.eyebrow": "Password reset",
        "reset.heading": "Let's get you back in.",
        "reset.preheader": "A link to set a new password. It expires in an hour.",
        "reset.lead": (
            "Someone asked to reset the password for this address. Use the "
            "button below to set a new one."
        ),
        "reset.cta": "Set a new password",
        "reset.expiry_label": "This link expires in one hour",
        "reset.expiry_body": (
            "If it has already expired, request another from the sign-in page "
            "and we will send a fresh one."
        ),
        "reset.footnote": (
            "Didn't ask for this? Ignore this email — your password stays "
            "exactly as it is, and nobody can change it without this link."
        ),
        "reset.fallback": "If the button doesn't work, copy this into your browser:",
    },
    "ar": {
        # ── chrome ────────────────────────────────────────────────────────────
        "brand.tagline": "كيك ومخبوزات · الإمارات العربية المتحدة",
        "footer.signoff": "صُنع بكل حب",
        "footer.shop": "المتجر",
        "footer.about": "قصتنا",
        "footer.rights": "© {year} ملتينج مومنتس كيكس · الإمارات العربية المتحدة",
        "footer.sent_to": (
            "أُرسلت إلى {email} من عنوان غير مُراقَب. يرجى عدم الرد على هذه الرسالة."
        ),
        # ── shared section headings ───────────────────────────────────────────
        "section.your_order": "طلبك",
        "item.note_label": "بطاقة مكتوبة بخط اليد:",
        "section.collect_from": "الاستلام من",
        "section.delivering_to": "التوصيل إلى",
        "section.progress": "مراحل الطلب",
        "section.your_note": "ملاحظتك",
        "label.open": "أوقات العمل",
        "label.phone": "الهاتف",
        "cta.open_in_maps": "افتح في خرائط جوجل",
        "cta.contact_us": "تواصل معنا",
        # ── totals ────────────────────────────────────────────────────────────
        "totals.subtotal": "المجموع الفرعي",
        "totals.discount": "الخصم",
        "totals.delivery": "التوصيل",
        "totals.collection": "الاستلام",
        "totals.total": "الإجمالي",
        "totals.vat": "شامل ضريبة القيمة المضافة",
        "totals.free": "مجاني",
        "totals.low_order_fee": "رسوم الطلب الصغير",
        # ── the estimate panel ────────────────────────────────────────────────
        "estimate.delivery": "موعد التوصيل المتوقع",
        "date.by_time": "{day} قبل {time}",
        "estimate.ready": "جاهز للاستلام",
        "estimate.ready_since": "جاهز منذ",
        "estimate.arriving": "موعد التوصيل المتوقع",
        "estimate.delivered": "تم التوصيل",
        "estimate.collected": "تم الاستلام",
        "estimate.note_counter": "بانتظارك عند الكاشير.",
        "estimate.note_rider": "بعد استلام السائق للطلب، هذا هو أدق تقدير لدينا.",
        "estimate.note_day": "سنؤكد الوقت بمجرد استلام الطلب.",
        "estimate.note_day_by": "الطلب مع شريك التوصيل، وينهون جولاتهم قبل ذلك.",
        # ── timeline steps ────────────────────────────────────────────────────
        "step.preparing": "جارٍ تحضير طلبك",
        "step.ready": "تم التعبئة",
        "step.ready_handover": "تم التعبئة والتسليم",
        "step.on_the_way": "في الطريق إليك",
        "step.delivered": "تم التوصيل",
        "step.pickup_ready": "جاهز للاستلام",
        "step.pickup_collected": "تم الاستلام",
        # ── dates ─────────────────────────────────────────────────────────────
        "date.today": "اليوم",
        "date.tomorrow": "غداً",
        "date.yesterday": "أمس",
        # ── confirmation ──────────────────────────────────────────────────────
        "confirmation.subject": "تم تأكيد الطلب",
        "confirmation.title": "تم تأكيد الطلب",
        "confirmation.eyebrow": "تم تأكيد الطلب",
        "confirmation.heading": "شكراً لك، {name}.",
        "confirmation.preheader": "بدأنا بتحضير الطلب {order_number}.",
        "confirmation.lead": (
            "استلمنا طلبك وبدأ المطبخ بتحضيره. كل ما نصنعه يُخبز عند الطلب، "
            "فيصلك طازجاً لا من على الرف."
        ),
        "confirmation.cta": "تتبع طلبك",
        "confirmation.footnote_pickup": (
            "سنراسلك فور أن يصبح جاهزاً للاستلام. إن بدا أي شيء هنا غير صحيح، "
            "أخبرنا وسنتولى الأمر."
        ),
        "confirmation.footnote_delivery": (
            "سنراسلك فور أن يكون في طريقه إليك. إن بدا أي شيء هنا غير صحيح، "
            "أخبرنا وسنتولى الأمر."
        ),
        # ── packed ────────────────────────────────────────────────────────────
        "packed.subject_pickup": "جاهز للاستلام",
        "packed.subject_delivery": "في الطريق إليك",
        "packed.eyebrow_pickup": "جاهز للاستلام",
        "packed.eyebrow_delivery": "خرج من المطبخ",
        "packed.heading_pickup": "طلبك جاهز، {name}.",
        "packed.heading_delivery": "طلبك في الطريق، {name}.",
        "packed.preheader_pickup": "الطلب {order_number} مُعبأ وبانتظارك عند الكاشير.",
        "packed.preheader_delivery": "الطلب {order_number} غادر المطبخ.",
        "packed.lead_pickup": (
            "طلبك مُعبأ للتو وبانتظارك عند الكاشير. أحضر رقم الطلب فقط، "
            "فهذا كل ما نحتاجه."
        ),
        "packed.lead_delivery": (
            "تم تعبئة طلبك وتسليمه للتوصيل. أبقِ هاتفك قريباً منك ليتمكن "
            "السائق من الوصول إليك."
        ),
        "packed.cta": "عرض تفاصيل الطلب",
        # ── out for delivery ──────────────────────────────────────────────────
        "otw.subject": "في الطريق إليك",
        "otw.eyebrow": "في الطريق إليك",
        "otw.heading": "طلبك في الطريق إليك، {name}.",
        "otw.preheader": "طلبك مع السائق الآن.",
        "otw.lead": (
            "تم استلام طلبك وهو في طريقه إليك. أبقِ هاتفك قريباً منك — "
            "فقد يتصل بك السائق عند وصوله."
        ),
        "otw.cta_live": "تتبع مباشر",
        "otw.cta_details": "تفاصيل الطلب",
        "otw.sms_note": (
            "سيرسل لك شريك التوصيل رابط تتبع مباشر عبر رسالة نصية — يرجى مراجعة رسائلك."
        ),
        # ── delivered / collected ─────────────────────────────────────────────
        "delivered.subject_pickup": "تم الاستلام",
        "delivered.subject_delivery": "تم التوصيل",
        "delivered.eyebrow_pickup": "تم الاستلام",
        "delivered.eyebrow_delivery": "تم التوصيل",
        "delivered.heading": "بالهناء والشفاء، {name}.",
        "delivered.preheader": "وصلك الطلب {order_number}. نتمنى أن ينال إعجابك.",
        "delivered.lead_pickup": (
            "شكراً لمرورك — طلبك بين يديك الآن. كل شيء ألذّ وهو طازج، فلا تتركه طويلاً."
        ),
        "delivered.lead_delivery": (
            "تم توصيل طلبك. كل شيء ألذّ وهو طازج، فلا تتركه طويلاً."
        ),
        "delivered.cta": "اطلب مرة أخرى",
        "delivered.footnote": (
            "إن لم يكن شيء على ما يرام، أخبرنا. نقرأ كل رسالة، ونفضّل أن نسمع "
            "منك على ألا نسمع شيئاً."
        ),
        # ── undelivered ───────────────────────────────────────────────────────
        "undelivered.subject": "تعذّر توصيل طلبك",
        "undelivered.eyebrow": "محاولة توصيل",
        "undelivered.heading": "لم نتمكن من تسليم الطلب.",
        "undelivered.preheader": (
            "لم يتمكن السائق من إتمام توصيل الطلب {order_number}. سنتواصل معك."
        ),
        "undelivered.lead": (
            "{name}، وصل السائق إلى عنوانك لكنه لم يتمكن من إتمام التوصيل. "
            "لم يضع شيء — طلبك محفوظ لدينا."
        ),
        "undelivered.next_label": "ما الخطوة التالية",
        "undelivered.next_body": (
            "سيتواصل معك أحد أفراد فريقنا قريباً لترتيب محاولة أخرى. وإن كان "
            "أسهل لك، أرسل لنا وقتاً أنسب أو عنواناً مختلفاً وسنرتب الأمر."
        ),
        "undelivered.cta": "عرض تفاصيل الطلب",
        # ── cancelled ─────────────────────────────────────────────────────────
        "cancelled.subject": "تم إلغاء الطلب",
        "cancelled.eyebrow": "تم إلغاء الطلب",
        "cancelled.heading": "تم إلغاء طلبك.",
        "cancelled.preheader": (
            "تم إلغاء الطلب {order_number}. وأي مبلغ تم دفعه سيُعاد إليك."
        ),
        "cancelled.lead": (
            "{name}، تم إلغاء الطلب {order_number} ولن يتم اتخاذ أي إجراء آخر بشأنه."
        ),
        "cancelled.why_label": "السبب",
        "cancelled.refund_label": "المبلغ المُعاد",
        "cancelled.refund_body": (
            "أي مبلغ تم دفعه سيُعاد إلى البطاقة التي دفعت بها. تستغرق البنوك "
            "عادةً من خمسة إلى عشرة أيام عمل حتى يظهر المبلغ."
        ),
        "refund.label": "المبلغ المُعاد",
        "refund.issued": (
            "أعدنا {amount} إلى البطاقة التي دفعت بها. تستغرق البنوك عادةً من "
            "خمسة إلى عشرة أيام عمل حتى يظهر المبلغ."
        ),
        "refund.fees_kept": (
            "هذه قيمة المنتجات. رسوم التوصيل غير مشمولة — فقد تم حجز السائق بالفعل."
        ),
        "refund.pending": (
            "نعيد مبلغ الدفع إلى البطاقة التي دفعت بها. إن لم يصلك خلال عشرة "
            "أيام عمل، أخبرنا."
        ),
        "cancelled.cta": "العودة إلى المتجر",
        "cancelled.footnote": (
            "إن لم يكن هذا متوقعاً، أخبرنا — نفضّل أن نسمع منك على أن تبقى في حيرة."
        ),
        # ── refunded ──────────────────────────────────────────────────────────
        "refunded.subject": "تمت معالجة المبلغ المُعاد",
        "refunded.eyebrow": "تمت معالجة المبلغ المُعاد",
        "refunded.heading": "مبلغك في طريقه إليك.",
        "refunded.preheader": "مبلغ {total} درهم في طريقه إلى بطاقتك.",
        "refunded.lead": (
            "{name}، أعدنا مبلغ AED {total} عن الطلب {order_number} إلى البطاقة "
            "التي دفعت بها."
        ),
        "refunded.when_label": "متى سيصلك",
        "refunded.when_body": (
            "تستغرق البنوك عادةً من خمسة إلى عشرة أيام عمل لقيد المبلغ. وسيظهر "
            "على البطاقة وكشف الحساب نفسيهما اللذين تم الدفع منهما."
        ),
        "refunded.cta": "العودة إلى المتجر",
        "refunded.footnote": (
            "إن لم يصلك المبلغ بعد عشرة أيام عمل، أرسل لنا رقم طلبك وسنتابع "
            "الأمر مع البنك."
        ),
        # ── payment failed ────────────────────────────────────────────────────
        "failed.subject": "لم تتم عملية الدفع",
        "failed.eyebrow": "لم تنجح عملية الدفع",
        "failed.heading": "لم نتمكن من إتمام الدفع.",
        "failed.preheader": "طلبك محجوز لك. أعد المحاولة وسنبدأ التحضير.",
        "failed.lead": (
            "{name}، لم تتم عملية الدفع للطلب {order_number}، لذلك لم نبدأ "
            "التحضير بعد. ولم يُخصم أي مبلغ."
        ),
        "failed.held_label": "طلبك محجوز لك",
        "failed.held_body": (
            "نحتفظ به لك. أعد محاولة الدفع من الزر أدناه — معظم حالات الرفض "
            "هي تحقق من البنك ينجح في المحاولة الثانية أو ببطاقة أخرى."
        ),
        "failed.cta": "إعادة محاولة الدفع",
        "failed.footnote": (
            "ما زالت المحاولة لا تنجح؟ أخبرنا وسنتولى الأمر — يمكننا ترتيب "
            "الدفع بطريقة أخرى."
        ),
        # ── welcome ───────────────────────────────────────────────────────────
        "welcome.subject": "أهلاً بك في ملتينج مومنتس",
        "welcome.eyebrow": "أهلاً بك",
        "welcome.heading": "سعداء بانضمامك إلينا.",
        "welcome.preheader": "حسابك جاهز. وكل ما نصنعه يُخبز عند الطلب.",
        "welcome.lead": (
            "حسابك جاهز. كل ما نصنعه يُخبز عند الطلب في مطبخنا ويصل إلى "
            "الإمارات السبع جميعها — أو نعبّئه لك لتستلمه بنفسك."
        ),
        "welcome.cta": "ابدأ التصفح",
        "welcome.perks_label": "مع حسابك يمكنك",
        "welcome.perk_track": "تتبع كل طلب من المطبخ حتى بابك",
        "welcome.perk_addresses": "حفظ عناوينك ليصبح إتمام الطلب في ثوانٍ",
        "welcome.perk_reorder": "إعادة طلب أي شيء سبق أن جربته",
        "welcome.footnote": (
            "لم تُنشئ حساباً؟ يمكنك تجاهل هذه الرسالة ولن يحدث أي شيء بعدها."
        ),
        # ── password reset ────────────────────────────────────────────────────
        "reset.subject": "إعادة تعيين كلمة المرور",
        "reset.eyebrow": "إعادة تعيين كلمة المرور",
        "reset.heading": "لنعد بك إلى حسابك.",
        "reset.preheader": "رابط لتعيين كلمة مرور جديدة. ينتهي خلال ساعة.",
        "reset.lead": (
            "طلب أحدهم إعادة تعيين كلمة المرور لهذا العنوان. استخدم الزر أدناه "
            "لتعيين كلمة مرور جديدة."
        ),
        "reset.cta": "تعيين كلمة مرور جديدة",
        "reset.expiry_label": "ينتهي هذا الرابط خلال ساعة واحدة",
        "reset.expiry_body": (
            "إن كان قد انتهى بالفعل، اطلب رابطاً آخر من صفحة تسجيل الدخول "
            "وسنرسل لك رابطاً جديداً."
        ),
        "reset.footnote": (
            "لم تطلب ذلك؟ تجاهل هذه الرسالة — كلمة مرورك تبقى كما هي، ولا يمكن "
            "لأحد تغييرها دون هذا الرابط."
        ),
        "reset.fallback": "إن لم يعمل الزر، انسخ هذا الرابط إلى متصفحك:",
    },
}


def translator(locale: str) -> Callable[..., str]:
    """
    The `t` a template calls.

    Falls back to English per key rather than per locale, so a key added to
    English and not yet translated renders real words instead of `refunded.cta`
    in somebody's inbox. `test_email_locales` makes that a belt rather than the
    only thing holding the trousers up.

    Placeholders are passed as keyword arguments and applied with `str.format`.
    A placeholder the caller forgot leaves the raw `{name}` visible rather than
    raising — an email that reads slightly wrong still arrives, and one that
    raises does not.
    """
    table = STRINGS.get(locale) or STRINGS["en"]
    fallback = STRINGS["en"]

    def t(key: str, **params) -> str:
        text = table.get(key) or fallback.get(key) or key
        if not params:
            return text
        try:
            return text.format(**params)
        except (KeyError, IndexError, ValueError):
            return text

    return t
