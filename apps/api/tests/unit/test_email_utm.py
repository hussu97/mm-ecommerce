"""
Links back to the storefront in customer emails carry UTM parameters.

So a visit that starts in an inbox is attributed to the email that sent it — the
storefront's analytics proxy forwards `utm_*` to Umami. Staff emails are left
alone: their readers are the shop, not traffic.
"""

from __future__ import annotations

from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app.services import email_service

SITE = "https://meltingmomentscakes.com"

#: Every template that is *not* a customer email. Listed so a new template has
#: to be classified one way or the other — an unlisted one fails below.
STAFF_TEMPLATES = {
    "auto_availability_change.html",
    "counter_pricing_mismatch.html",
    "custom_order_enquiry.html",
    "inventory_report_submitted.html",
    "owner_order_notification.html",
    "purchase_order_receiving_variance.html",
    "transfer_sending_variance.html",
}


@pytest.fixture(autouse=True)
def _site(monkeypatch):
    monkeypatch.setattr(email_service.settings, "WEB_URL", SITE)


def _raw_href(html: str) -> str:
    """The attribute exactly as written, entities and all."""
    return html.split('href="', 1)[1].split('"', 1)[0]


def _href(html: str) -> str:
    """The URL a mail client would follow."""
    return unescape(_raw_href(html))


def test_a_storefront_link_is_tagged_with_the_template_as_campaign():
    html = email_service.tag_links(f'<a href="{SITE}/en/contact">x</a>', "welcome")

    query = parse_qs(urlsplit(_href(html)).query)
    assert query == {
        "utm_source": ["email"],
        "utm_medium": ["email"],
        "utm_campaign": ["welcome"],
    }


def test_an_existing_query_string_survives_its_html_escaping():
    """Jinja renders `&` in an attribute as `&amp;`; the tracking link's own
    parameters must come out intact, and the attribute must stay escaped."""
    html = email_service.tag_links(
        f'<a href="{SITE}/en/track?order=MM-1&amp;email=a%40b.com">x</a>',
        "order_packed",
    )

    raw = _raw_href(html)
    assert "&amp;" in raw and "&" not in raw.replace("&amp;", "")
    query = parse_qs(urlsplit(_href(html)).query)
    assert query["order"] == ["MM-1"]
    assert query["email"] == ["a@b.com"]
    assert query["utm_campaign"] == ["order_packed"]


def test_www_is_the_same_site():
    html = email_service.tag_links(
        '<a href="https://www.meltingmomentscakes.com/ar">x</a>', "welcome"
    )
    assert "utm_source=email" in _href(html)


@pytest.mark.parametrize(
    "href",
    [
        "https://checkout.stripe.com/c/pay/cs_live_x",
        "https://maps.google.com/?q=25.3,55.4",
        "https://admin.meltingmomentscakes.com/orders/MM-1",
        "mailto:hello@meltingmomentscakes.com",
        "tel:+971500000000",
    ],
)
def test_links_that_are_not_the_storefront_are_untouched(href):
    source = f'<a href="{href}">x</a>'
    assert email_service.tag_links(source, "order_confirmation") == source


def test_a_link_that_names_its_own_campaign_keeps_it():
    html = email_service.tag_links(
        f'<a href="{SITE}/en?utm_campaign=eid">x</a>', "welcome"
    )

    query = parse_qs(urlsplit(_href(html)).query)
    assert query["utm_campaign"] == ["eid"]
    assert query["utm_source"] == ["email"]


def test_a_customer_email_is_tagged_end_to_end():
    """Rendered through `_render`, footer and body links alike."""
    html = email_service._render(
        "abandoned_basket.html",
        "buyer@example.com",
        items=[],
        subtotal="0.00",
        checkout_url=f"{SITE}/en/checkout",
    )

    storefront = [
        link
        for link in (part.split('"', 1)[0] for part in html.split('href="')[1:])
        if link.startswith(SITE)
    ]
    assert storefront, "expected storefront links in the email"
    assert all("utm_campaign=abandoned_basket" in link for link in storefront)


@pytest.mark.parametrize(
    "template, tagged",
    [("order_confirmation.html", True), ("owner_order_notification.html", False)],
)
def test_render_tags_customer_templates_only(monkeypatch, template, tagged):
    """The allow-list is what `_render` reads. A stub template stands in for
    the real one, whose full context is beside the point here."""
    from jinja2 import Template

    stub = Template('<a href="{{ web_url }}/en">shop</a>')
    monkeypatch.setattr(email_service._jinja_env, "get_template", lambda _n: stub)

    html = email_service._render(template, "someone@example.com")

    assert ("utm_source=email" in html) is tagged


def test_every_template_is_classified():
    """A new template must be declared customer-facing (tagged) or staff."""
    folder = Path(email_service.TEMPLATES_DIR)
    templates = {
        path.name
        for path in folder.glob("*.html")
        if not path.name.startswith("_") and path.name != "base.html"
    }
    unclassified = templates - email_service.CUSTOMER_TEMPLATES - STAFF_TEMPLATES
    assert not unclassified, f"classify these templates: {sorted(unclassified)}"
    assert email_service.CUSTOMER_TEMPLATES <= templates
