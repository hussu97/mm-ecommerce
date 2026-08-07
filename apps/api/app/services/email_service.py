from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import css_inline
import resend
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.models.delivery_batch import DELIVERY_TIMEZONE
from app.models.email_log import EmailLog
from app.models.pos_order import OrderSourceEnum
from app.services import address_format
from app.services.email_copy import DIRECTION, translator
from app.schemas.order import OrderResponse

__all__ = [
    "is_counter_sale",
    "locale_of",
    "notify_status_change",
    "send_order_cancelled",
    "send_order_confirmation",
    "send_order_delivered",
    "send_order_out_for_delivery",
    "send_order_packed",
    "send_order_undelivered",
    "maps_url",
    "send_owner_order_notification",
    "send_password_reset",
    "send_payment_failed",
    "send_refund_notification",
    "send_welcome",
]

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "emails"
OWNER_ORDER_RECIPIENTS = (
    "fatema_f@hotmail.co.uk",
    "fahimakhtarabbasi@gmail.com",
)

#: The clock every date in an email is printed on. A customer in Sharjah reading
#: "ready at 16:30" is standing on this one, and a UTC stamp would be four hours
#: wrong in the direction that makes people arrive to a closed counter.
TZ = ZoneInfo(DELIVERY_TIMEZONE)

_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)

#: Writes `base.html`'s stylesheet onto the elements themselves.
#:
#: A `<style>` block in `<head>` only survives in a client that renders the
#: whole document. A notification preview, an inbox snippet and Gmail's own web
#: client all render a stripped version that keeps the markup and throws the
#: head away — which is why the same message looked designed when opened and
#: naked in a notification. Inlining is the only styling every one of them
#: honours.
#:
#: `keep_at_rules` retains what cannot be inlined: an element has no `@media`,
#: so the phone breakpoint and the dark-mode block stay in a `<style>` tag and
#: keep working wherever one is honoured. Every rule in those blocks is already
#: flagged `!important`, so they still outrank the inline styles written here.
#:
#: `load_remote_stylesheets=False` matters more than it looks: `base.html` links
#: Google Fonts, and without this every send would make an outbound HTTP request
#: to fetch a stylesheet, on the send path, for no benefit. `keep_link_tags`
#: leaves the link itself alone so clients that load webfonts still do.
_inliner = css_inline.CSSInliner(
    keep_at_rules=True,
    keep_link_tags=True,
    load_remote_stylesheets=False,
)


def _inline(html: str) -> str:
    """
    The same email, styled in a way a preview pane cannot strip.

    Never raises. A malformed fragment is not a reason to drop an order
    confirmation — the uninlined HTML is exactly what we sent before this
    existed, so falling back to it is a cosmetic regression rather than a
    missing email.
    """
    try:
        return _inliner.inline(html)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("CSS inlining failed, sending unstyled HTML: %s", exc)
        return html


def _render(
    template_name: str, recipient_email: str, *, locale: str = "en", **context
) -> str:
    """
    Render one template in one language, styled for every client.

    `t`, `locale` and `direction` are injected here rather than passed by each
    caller, because every template and every macro needs all three and a
    template that renders without them renders in the wrong language silently.
    """
    template = _jinja_env.get_template(template_name)
    return _inline(
        template.render(
            web_url=settings.WEB_URL,
            shop_url=f"{settings.WEB_URL.rstrip('/')}/{locale}",
            now=datetime.now(timezone.utc),
            recipient_email=recipient_email,
            locale=locale,
            direction=DIRECTION.get(locale, "ltr"),
            t=translator(locale),
            **context,
        )
    )


def _order_tracking_url(order_number: str, email: str, locale: str = "en") -> str:
    """The tracking page, in the language the order was placed in.

    An Arabic email whose one button opens an English page has only moved the
    problem down a click.
    """
    query = urlencode({"order_number": order_number, "email": email})
    return f"{settings.WEB_URL.rstrip('/')}/{locale}/track?{query}"


def _account_order_url(order_number: str, locale: str = "en") -> str:
    return f"{settings.WEB_URL.rstrip('/')}/{locale}/account/orders/{order_number}"


def _admin_order_url(order_number: str) -> str:
    return f"{settings.ADMIN_URL.rstrip('/')}/orders/{order_number}"


# ─── Building the picture an order email paints ───────────────────────────────


def is_counter_sale(order: OrderResponse) -> bool:
    """
    Whether this order was rung up at a till rather than placed on the website.

    A counter sale gets none of these emails. The customer is standing at the
    counter holding the box and a printed receipt: "your order is confirmed"
    tells them something they watched happen, and "ready to collect" is a
    notification about a cake already in their hands. The shop does not want the
    owner notification for every till transaction either.

    Keyed on `source`, which is the column that exists for exactly this
    distinction and the one `/orders/admin/all` already filters its channel tab
    on. **Not** on `is_pos`: that is true for website orders too — a storefront
    order lands on a branch's register and is a POS order in every operational
    sense — so gating on it would silence every customer email the shop sends.
    """
    return order.source == OrderSourceEnum.CASHIER.value


def _money(value: Any) -> str:
    return f"{Decimal(str(value or 0)):.2f}"


def _customer_name(order: OrderResponse) -> str:
    snapshot = order.shipping_address_snapshot or {}
    name = str(snapshot.get("first_name") or "").strip()
    return name or "there"


def _item_rows(order: OrderResponse) -> list[dict[str, Any]]:
    """
    The order's lines, flattened for a template.

    The option list becomes one string here rather than in Jinja, because
    joining names out of a list of snapshot dicts is logic and a template that
    does logic is a template nobody can read.
    """
    rows: list[dict[str, Any]] = []
    for item in order.items:
        options = ", ".join(
            str(option.get("option_name"))
            for option in (item.selected_options_snapshot or [])
            if isinstance(option, dict) and option.get("option_name")
        )
        rows.append(
            {
                "name": item.product_name,
                "options": options,
                "quantity": item.quantity,
                "unit_price": _money(item.unit_price),
                "total_price": _money(item.total_price),
            }
        )
    return rows


def _totals(order: OrderResponse, *, is_pickup: bool, t) -> dict[str, Any]:
    fee = Decimal(str(order.delivery_fee or 0))
    low_order = Decimal(str(getattr(order, "low_order_fee", 0) or 0))
    return {
        "subtotal": _money(order.subtotal),
        "discount": _money(order.discount_amount) if order.discount_amount else None,
        "promo_code": order.promo_code_used,
        "fee_label": t("totals.collection") if is_pickup else t("totals.delivery"),
        # Zero reads better as the word than as 0.00 — it is the difference
        # between "we charged you nothing" and "there is a line here".
        "delivery_fee": t("totals.free") if fee == 0 else f"AED {_money(fee)}",
        # Null rather than "AED 0.00" when it does not apply, so the template can
        # drop the row entirely. A zero line invites the question it exists to
        # answer.
        "low_order_fee": (f"AED {_money(low_order)}" if low_order > 0 else None),
        "low_order_label": t("totals.low_order_fee"),
        "total": _money(order.total),
        "vat": _money(order.vat_amount),
    }


#: Arabic weekday and month names, indexed the way `datetime` counts them —
#: `weekday()` is Monday-first, `month` is 1-based. Written out rather than
#: taken from `locale`/`babel`: the C library's Arabic locale is not installed
#: in the container, and adding a dependency to print twelve words would be a
#: poor trade.
_AR_WEEKDAYS = ("الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد")
_AR_MONTHS = (
    "يناير",
    "فبراير",
    "مارس",
    "أبريل",
    "مايو",
    "يونيو",
    "يوليو",
    "أغسطس",
    "سبتمبر",
    "أكتوبر",
    "نوفمبر",
    "ديسمبر",
)
#: Arabic marks the halves of the day with single letters — صباحاً / مساءً,
#: abbreviated. "AM" in an Arabic sentence reads as a foreign object.
_AR_MERIDIEM = {"AM": "ص", "PM": "م"}


def _when(
    moment: datetime, *, precision: str, now: datetime, t, locale: str = "en"
) -> str:
    """
    A stamp as a person would say it, in their language.

    "Today" and "tomorrow" rather than a date whenever they apply, because those
    are the two answers a customer is actually looking for and a date makes them
    do the arithmetic. Beyond that it is a weekday and a date, which is
    unambiguous without being a timestamp.

    Digits stay Western in both languages. That is how prices and dates are
    written on the storefront, and Eastern Arabic numerals in a time somebody
    has to match against their phone's clock help nobody.
    """
    local = moment.astimezone(TZ)
    today = now.astimezone(TZ).date()
    delta = (local.date() - today).days

    if delta == 0:
        day = t("date.today")
    elif delta == 1:
        day = t("date.tomorrow")
    elif delta == -1:
        day = t("date.yesterday")
    elif locale == "ar":
        day = (
            f"{_AR_WEEKDAYS[local.weekday()]} {local.day} {_AR_MONTHS[local.month - 1]}"
        )
    else:
        day = local.strftime("%a %-d %b")

    if precision == "day":
        return day
    if precision == "day_by":
        # Somebody else's van: a date, bounded by an hour, and clearly a bound
        # rather than an appointment. "Wed 12 Aug before 10:00 PM" says what we
        # can actually stand behind; naming a time would borrow a precision from
        # a schedule that is not ours.
        clock = local.strftime("%-I:%M")
        meridiem = local.strftime("%p")
        if locale == "ar":
            meridiem = _AR_MERIDIEM.get(meridiem, meridiem)
        return t("date.by_time", day=day, time=f"{clock} {meridiem}")

    clock = local.strftime("%-I:%M")
    meridiem = local.strftime("%p")
    if locale == "ar":
        meridiem = _AR_MERIDIEM.get(meridiem, meridiem)
    return (
        f"{day}، {clock} {meridiem}" if locale == "ar" else f"{day}, {clock} {meridiem}"
    )


def _estimate_block(
    order: OrderResponse, *, is_pickup: bool, now: datetime, t, locale: str
) -> dict[str, str] | None:
    """
    The "when" panel, or nothing.

    Nothing is a real answer here and appears in three cases: a settled order, a
    failed handover, and a delivery on somebody else's van that has not moved
    yet. In all three the honest thing is an email with no promise on it rather
    than a panel containing a guess.
    """
    fulfilment = order.fulfilment
    if fulfilment is None or fulfilment.estimated_at is None:
        return None

    stage = fulfilment.stage
    value = _when(
        fulfilment.estimated_at,
        precision=fulfilment.precision or "day",
        now=now,
        t=t,
        locale=locale,
    )

    if stage == "collected":
        return {"label": t("estimate.collected"), "value": value, "note": ""}
    if stage == "delivered":
        return {"label": t("estimate.delivered"), "value": value, "note": ""}
    if stage == "ready" and is_pickup:
        return {
            "label": t("estimate.ready_since"),
            "value": value,
            "note": t("estimate.note_counter"),
        }
    if stage == "on_the_way":
        return {
            "label": t("estimate.arriving"),
            "value": value,
            "note": t("estimate.note_rider"),
        }

    note = ""
    if fulfilment.precision == "day":
        # An area a third party covers. Their van, their schedule; naming an
        # hour would be borrowing a precision that is not ours.
        note = t("estimate.note_day")
    return {
        "label": t("estimate.ready") if is_pickup else t("estimate.delivery"),
        "value": value,
        "note": note,
    }


def _timeline(
    order: OrderResponse, *, is_pickup: bool, now: datetime, t, locale: str
) -> list[dict]:
    """
    The journey this order is actually on.

    Built per order rather than from one fixed list, because the two journeys
    genuinely differ: a collection order never goes out for delivery, and greying
    out a step nobody is waiting for reads as a step that was skipped.
    """
    fulfilment = order.fulfilment
    if fulfilment is None:
        return []
    stage = fulfilment.stage
    if stage == "settled":
        return []

    if is_pickup:
        order_of = ["preparing", "ready", "collected"]
        labels = {
            "preparing": t("step.preparing"),
            "ready": t("step.pickup_ready"),
            "collected": t("step.pickup_collected"),
        }
        stamps = {"collected": fulfilment.delivered_at}
    elif fulfilment.courier_managed:
        order_of = ["preparing", "ready", "on_the_way", "delivered"]
        labels = {
            "preparing": t("step.preparing"),
            "ready": t("step.ready"),
            "on_the_way": t("step.on_the_way"),
            "delivered": t("step.delivered"),
        }
        stamps = {
            "ready": fulfilment.packed_at,
            "on_the_way": fulfilment.picked_up_at,
            "delivered": fulfilment.delivered_at,
        }
    else:
        # No courier reporting back, so there is no honest middle step: the box
        # is packed, and the next thing anybody knows is that it arrived.
        order_of = ["preparing", "ready", "delivered"]
        labels = {
            "preparing": t("step.preparing"),
            "ready": t("step.ready_handover"),
            "delivered": t("step.delivered"),
        }
        stamps = {"ready": fulfilment.packed_at, "delivered": fulfilment.delivered_at}

    reached = order_of.index(stage) if stage in order_of else 0
    return [
        {
            "label": labels[key],
            "done": index <= reached,
            "when": (
                _when(stamps[key], precision="time", now=now, t=t, locale=locale)
                if index <= reached and stamps.get(key) is not None
                else ""
            ),
        }
        for index, key in enumerate(order_of)
    ]


def locale_of(order: OrderResponse) -> str:
    """
    The language to write this order's emails in.

    The order's own, captured at checkout. Falls back to English for a response
    built before the column existed, or by hand in a test.
    """
    return order.locale if order.locale in DIRECTION else "en"


def _order_context(
    order: OrderResponse, *, locale: str | None = None
) -> dict[str, Any]:
    """Everything every order template reads, built once, in one language."""
    now = datetime.now(timezone.utc)
    locale = locale or locale_of(order)
    t = translator(locale)
    is_pickup = order.delivery_method.value == "pickup"
    fulfilment = order.fulfilment
    branch = fulfilment.branch if fulfilment else None
    ar = locale == "ar"

    return {
        "order": order,
        "name": _customer_name(order),
        "is_pickup": is_pickup,
        "items": _item_rows(order),
        "totals": _totals(order, is_pickup=is_pickup, t=t),
        "estimate": _estimate_block(
            order, is_pickup=is_pickup, now=now, t=t, locale=locale
        ),
        "steps": _timeline(order, is_pickup=is_pickup, now=now, t=t, locale=locale),
        "branch": branch,
        # Resolved here rather than in the template, so the branch card renders
        # one language instead of stacking both. The `*_ar` fields are null when
        # they would only repeat the English, which is why each one falls back.
        "branch_name": (
            (branch.name_ar or branch.name)
            if ar and branch
            else (branch.name if branch else None)
        ),
        "branch_address": (
            (branch.address_ar or branch.address)
            if ar and branch
            else (branch.address if branch else None)
        ),
        "branch_city": (
            (branch.city_ar or branch.city)
            if ar and branch
            else (branch.city if branch else None)
        ),
        "address": None if is_pickup else (order.shipping_address_snapshot or None),
        # Flattened here, by the same function the register's ticket and both
        # couriers use. The card used to lay the fields out itself and got two
        # of them wrong: `unit_number` — the flat or floor, and the only part of
        # an address a map pin cannot supply — was never rendered, and `city` is
        # not a field the checkout writes, so that line never appeared either.
        "address_line": address_format.one_line(order.shipping_address_snapshot),
        "recipient_name": address_format.recipient_name(
            order.shipping_address_snapshot
        ),
        "tracking_url": _order_tracking_url(order.order_number, order.email, locale),
        "live_tracking_url": fulfilment.tracking_url if fulfilment else None,
        # noon Send text the customer their own tracking link once a rider has
        # the box, and give us nothing to link to. Without this the email simply
        # showed no live link and said nothing about why.
        "tracking_by_sms": bool(fulfilment and fulfilment.tracking_by_sms),
        "retry_url": _account_order_url(order.order_number, locale),
    }


def _send(to: str, subject: str, html: str) -> dict:
    """
    Send an email via Resend. Never raises — always returns a result dict:
      {"status": "sent"|"failed"|"skipped", "resend_id": str|None, "error": str|None}
    """
    # Nobody to write to. A counter sale has no customer email — the field is
    # nullable and for a walk-in it is simply empty — so "cancel this order"
    # reaches here with nothing to send to. That is an absence, not a failure:
    # before this guard it went to Resend, came back `Invalid \`to\` field`, and
    # was logged as an error, which put five paging alerts on the board for
    # five orders that never had a customer to notify.
    if not to or not str(to).strip():
        logger.info("No recipient address — skipping email: %s", subject)
        return {"status": "skipped", "resend_id": None, "error": "no recipient address"}

    to = str(to).strip()

    # Nor anything that cannot be an address. Resend will refuse it, and a 4xx
    # we could have predicted is noise on a board that should only carry
    # surprises.
    if "@" not in to or to.startswith("@") or to.endswith("@"):
        logger.warning("Not a usable address — skipping email to %r: %s", to, subject)
        return {"status": "skipped", "resend_id": None, "error": "malformed address"}

    # Guests who decline to give an email are stored under the generated
    # `…@guest.local` address the auth layer mints. It is a placeholder, not a
    # mailbox — writing to it would only earn us bounces.
    if to.lower().endswith("@guest.local"):
        logger.info("Placeholder guest address — skipping email: %s", subject)
        return {
            "status": "skipped",
            "resend_id": None,
            "error": "placeholder guest address",
        }

    if not settings.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — skipping email to %s: %s", to, subject)
        return {
            "status": "skipped",
            "resend_id": None,
            "error": "RESEND_API_KEY not configured",
        }

    try:
        resend.api_key = settings.RESEND_API_KEY
        params: resend.Emails.SendParams = {
            "from": f"Melting Moments <{settings.FROM_EMAIL}>",
            "to": to,
            "subject": subject,
            "html": html,
        }
        response = resend.Emails.send(params)
        resend_id = response.id if hasattr(response, "id") else response.get("id")
        logger.info("Email sent: id=%s to=%s subject=%s", resend_id, to, subject)
        return {"status": "sent", "resend_id": resend_id, "error": None}
    except Exception as exc:
        error_msg = str(exc)
        logger.error(
            "Email send failed to=%s subject=%s: %s",
            to,
            subject,
            error_msg,
            exc_info=True,
        )
        return {"status": "failed", "resend_id": None, "error": error_msg}


async def _log(
    template: str,
    recipient: str,
    subject: str,
    result: dict,
    order_number: str | None = None,
) -> None:
    """Persist an EmailLog row. Swallows all errors so logging never breaks email flow."""
    try:
        async with AsyncSessionFactory() as db:
            db.add(
                EmailLog(
                    template=template,
                    recipient=recipient,
                    subject=subject,
                    order_number=order_number,
                    status=result["status"],
                    resend_id=result.get("resend_id"),
                    error=result.get("error"),
                )
            )
            await db.commit()
    except Exception as exc:
        logger.error("EmailLog persist failed: %s", exc)


async def _send_order_email(
    order: OrderResponse,
    *,
    template: str,
    subject: str,
    **extra,
) -> None:
    """
    Render one order email, send it, log it, and never raise.

    Every order email funnels through here so the rendering failure mode is the
    same everywhere: a template that throws is logged as a failed email against
    the order rather than becoming an exception in whatever was updating the
    order's status. A cake that is baked and boxed must not be blocked by a
    typo in a Jinja file.

    It is also the one place a counter sale is turned away, for the same reason:
    the rule belongs where every caller passes rather than at each of them.
    """
    if is_counter_sale(order):
        logger.info(
            "Counter sale %s — no customer email (%s)", order.order_number, template
        )
        return

    locale = locale_of(order)
    try:
        html = _render(
            template,
            recipient_email=order.email,
            locale=locale,
            **_order_context(order, locale=locale),
            **extra,
        )
        result = await asyncio.to_thread(_send, order.email, subject, html)
    except Exception as exc:
        logger.error(
            "%s render/send failed for %s: %s",
            template,
            order.order_number,
            exc,
            exc_info=True,
        )
        result = {"status": "failed", "resend_id": None, "error": str(exc)}
    await _log(
        template.removesuffix(".html"), order.email, subject, result, order.order_number
    )


# ─── Order emails ─────────────────────────────────────────────────────────────


def _subject(key: str, order: OrderResponse) -> str:
    """
    The line the inbox shows, in the order's own language.

    The order number and the shop name stay Latin in both: the number is what a
    customer reads back over the phone and searches their inbox for, and it has
    to be the same string either way.
    """
    return (
        f"{translator(locale_of(order))(key)} — {order.order_number} | Melting Moments"
    )


async def send_order_confirmation(order: OrderResponse) -> None:
    await _send_order_email(
        order,
        template="order_confirmation.html",
        subject=_subject("confirmation.subject", order),
    )


def maps_url(snapshot: dict | None) -> str | None:
    """
    A Google Maps link to the pin the customer actually dropped.

    Coordinates, not the typed address. The pin is what the courier is sent to
    and what the zone was priced from, and a UAE address line is frequently not
    findable — "villa 12, behind the mosque" is a real address and a hopeless
    search query. `?q=lat,lng` is the documented form and opens the app on a
    phone rather than the web map.

    `None` when there is no pin, so the caller renders no button rather than a
    link to the middle of the sea.
    """
    if not snapshot:
        return None
    lat, lng = snapshot.get("latitude"), snapshot.get("longitude")
    if lat is None or lng is None:
        return None
    try:
        return (
            f"https://www.google.com/maps/search/?api=1&query={float(lat)},{float(lng)}"
        )
    except (TypeError, ValueError):
        return None


async def send_owner_order_notification(order: OrderResponse) -> None:
    # Always English: the two people who receive it run the shop and the admin
    # it links to is English-only, so translating it would make the operational
    # email harder to read for the only people who read it. The branch card
    # inside is still the customer's language — that is the address they saw.
    #
    # The counter does not need telling about the counter. Whoever rang it up is
    # standing at the register that printed it.
    if is_counter_sale(order):
        logger.info("Counter sale %s — no owner notification", order.order_number)
        return

    subject = f"New order — {order.order_number} | Melting Moments"
    context = _order_context(order)
    snapshot = order.shipping_address_snapshot or {}
    for recipient in OWNER_ORDER_RECIPIENTS:
        try:
            html = _render(
                "owner_order_notification.html",
                recipient_email=recipient,
                locale="en",
                admin_order_url=_admin_order_url(order.order_number),
                customer_name=address_format.recipient_name(snapshot) or "—",
                customer_phone=snapshot.get("phone"),
                maps_url=maps_url(snapshot),
                **context,
            )
            result = await asyncio.to_thread(_send, recipient, subject, html)
        except Exception as exc:
            logger.error(
                "owner_order_notification render/send failed for %s to %s: %s",
                order.order_number,
                recipient,
                exc,
                exc_info=True,
            )
            result = {"status": "failed", "resend_id": None, "error": str(exc)}
        await _log(
            "owner_order_notification",
            recipient,
            subject,
            result,
            order.order_number,
        )


async def send_order_packed(order: OrderResponse) -> None:
    """
    The box is finished.

    Worth an email for a collection order — it is the "come and get it" — and
    for a delivery in an area a third party covers, where "packed" is the last
    thing anybody will tell us and so the last email the customer would ever
    get. Not worth one for a delivery we book ourselves: there, packed means the
    box is on a shelf waiting for a rider, and the news the customer wants is
    the next event, when somebody is actually carrying it. Sending both turns
    the useful email into the second of two.

    The decision is `should_send_packed`, so it is one function to test rather
    than an `if` at each of the two call sites.
    """
    is_pickup = order.delivery_method.value == "pickup"
    await _send_order_email(
        order,
        template="order_packed.html",
        subject=_subject(
            "packed.subject_pickup" if is_pickup else "packed.subject_delivery",
            order,
        ),
    )


def should_send_packed(order: OrderResponse) -> bool:
    """Whether `packed` is news for this order. See `send_order_packed`."""
    if order.delivery_method.value == "pickup":
        return True
    return not (order.fulfilment is not None and order.fulfilment.courier_managed)


async def send_order_out_for_delivery(order: OrderResponse) -> None:
    await _send_order_email(
        order,
        template="order_out_for_delivery.html",
        subject=_subject("otw.subject", order),
    )


async def send_order_delivered(order: OrderResponse) -> None:
    is_pickup = order.delivery_method.value == "pickup"
    await _send_order_email(
        order,
        template="order_delivered.html",
        subject=_subject(
            "delivered.subject_pickup" if is_pickup else "delivered.subject_delivery",
            order,
        ),
    )


async def send_order_undelivered(order: OrderResponse) -> None:
    await _send_order_email(
        order,
        template="order_undelivered.html",
        subject=_subject("undelivered.subject", order),
    )


async def send_payment_failed(order: OrderResponse) -> None:
    await _send_order_email(
        order,
        template="payment_failed.html",
        subject=_subject("failed.subject", order),
    )


async def send_refund_notification(order: OrderResponse) -> None:
    await _send_order_email(
        order,
        template="order_refunded.html",
        subject=_subject("refunded.subject", order),
    )


async def send_order_cancelled(order: OrderResponse) -> None:
    await _send_order_email(
        order,
        template="order_cancelled.html",
        subject=_subject("cancelled.subject", order),
        # A card order that reached `confirmed` was paid for; one cancelled out
        # of `created` or `payment_failed` never was. Telling somebody a refund
        # is coming when no money was taken is the kind of thing that generates
        # a support email a week later.
        was_paid=bool(order.payment_id) and order.payment_method != "cod",
    )


#: Order status -> the email it earns, if any. Statuses absent from this map are
#: silent on purpose: `created` is answered by the checkout page itself, and
#: `disputed` is a conversation with a bank rather than with a customer.
_EMAIL_FOR_STATUS = {
    "confirmed": send_order_confirmation,
    "packed": send_order_packed,
    "out_for_delivery": send_order_out_for_delivery,
    "delivered": send_order_delivered,
    "undelivered": send_order_undelivered,
    "cancelled": send_order_cancelled,
    "payment_failed": send_payment_failed,
    "refunded": send_refund_notification,
}


async def notify_status_change(order: OrderResponse) -> str | None:
    """
    Send whichever email this order's current state earns.

    One entry point rather than a `match` at every call site. Three places move
    an order — the admin's status endpoint, Stripe's webhooks and the couriers'
    — and before this each of them decided independently which emails existed,
    which is why a courier marking an order delivered sent nothing at all.

    Returns the name of the event it acted on, or None when the state is
    deliberately silent. Never raises: `_send_order_email` swallows its own
    failures, so a caller can await this inside the transaction that moved the
    order.
    """
    # A counter sale earns nothing, whatever its status. Checked here as well as
    # in `_send_order_email` so the return value is the truth: reporting
    # "confirmed" for an email nobody sent would make this function's answer
    # useless to a caller trying to log what happened.
    if is_counter_sale(order):
        return None

    # A failed handover used to be checked here, ahead of the map, because it
    # was not a status — it lived on the courier record while the order still
    # read `out_for_delivery`. It is `OrderStatusEnum.UNDELIVERED` now, so it is
    # in the map with everything else and there is one path again. Two paths was
    # how the same order could earn two emails, or none.
    status = order.status.value
    handler = _EMAIL_FOR_STATUS.get(status)
    if handler is None:
        return None
    if handler is send_order_packed and not should_send_packed(order):
        logger.info(
            "Packed email skipped for %s — a rider will be the news", order.order_number
        )
        return None
    await handler(order)
    return status


async def notify_order(db, order) -> str | None:
    """
    `notify_status_change` for a caller holding the ORM row rather than the
    response model — which is both couriers, since a webhook arrives with a
    delivery record and works back to the order from it.

    The row is re-read with the mailer's own load options rather than used as
    handed over. A courier selects an order to move its status, not to write
    about it, and an order selected without `items` used to take the whole email
    down with a `MissingGreenlet` that nothing but this function's own `except`
    ever saw. The reload is one query and it removes the class of bug.

    The import is deferred because `order_service` imports this module's package
    at load time and a top-level import here would close the cycle. It is the
    only one in this file, and it is here rather than at each courier so there
    is one of it.
    """
    from app.services import order_service

    try:
        loaded = await order_service.get_for_notification(db, order.id) or order
        return await notify_status_change(await order_service.to_response(db, loaded))
    except Exception as exc:  # pragma: no cover — defensive
        # A courier push that moved an order must not be rejected because we
        # could not write about it. Lalamove would retry the whole event for a
        # day; noon Send would not retry at all.
        logger.error(
            "Could not notify %s about its new status: %s",
            getattr(order, "order_number", "?"),
            exc,
            exc_info=True,
        )
        return None


# ─── User emails ──────────────────────────────────────────────────────────────


async def send_welcome(email: str, locale: str = "en") -> None:
    """
    Welcome. In the language they signed up in.

    There is no order to read a locale off, so the caller passes the one the
    request arrived on. It defaults rather than being required, because a signup
    must not fail over a missing parameter.
    """
    t = translator(locale)
    subject = t("welcome.subject")
    html = _render("welcome.html", recipient_email=email, locale=locale)
    result = await asyncio.to_thread(_send, email, subject, html)
    await _log("welcome", email, subject, result)


async def send_password_reset(email: str, reset_token: str, locale: str = "en") -> None:
    t = translator(locale)
    reset_link = (
        f"{settings.WEB_URL.rstrip('/')}/{locale}/reset-password?token={reset_token}"
    )
    subject = f"{t('reset.subject')} — Melting Moments"
    html = _render(
        "password_reset.html",
        recipient_email=email,
        locale=locale,
        reset_link=reset_link,
    )
    result = await asyncio.to_thread(_send, email, subject, html)
    await _log("password_reset", email, subject, result)
