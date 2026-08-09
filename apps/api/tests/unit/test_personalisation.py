"""
A message somebody types on a basket line, and the ways it can be lost.

The storefront advertised "gift boxes with a handwritten note" long before
anything could store one. Now that it can, the interesting failures are not
about storing it — they are about two lines quietly becoming one.

`cart_service._options_key` decides whether an add joins an existing line or
starts a new one, and it used to key on the product and its chosen options.
Two gift notes are the same product with the same (empty) options, so the
second one merged into the first as `quantity: 2` and **the second message was
gone** — no error, nothing in the order, and the customer finds out when the
wrong card arrives. Most of what follows pins that.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.exceptions import BadRequestError
from app.services import cart_service


def _product(
    *,
    personalisation_type: str | None = "handwritten_note",
    max_length: int = 100,
    name: str = "Handwritten note",
):
    return SimpleNamespace(
        id=uuid4(),
        name=name,
        sku="NOTE-01",
        translations={},
        base_price=Decimal("10.00"),
        personalisation_type=personalisation_type,
        personalisation_max_length=max_length,
    )


# ─── clean_note ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("\n\t ", None),
        ("  Happy birthday  ", "Happy birthday"),
    ],
)
def test_blank_in_every_spelling_becomes_none(raw, expected):
    """
    One representation for "they left it blank".

    Whitespace-only and the empty string and null all mean the same thing, and
    three spellings of it would make the checkout guard below depend on which
    client last wrote the row.
    """
    assert cart_service.clean_note(raw) == expected


def test_line_endings_are_normalised():
    """
    CRLF from a textarea and LF from anything else are the same message.

    They must normalise before the dedup key sees them, or the same words typed
    on Windows and on a phone are two lines.
    """
    assert cart_service.clean_note("Line one\r\nLine two") == "Line one\nLine two"
    assert cart_service.clean_note("Line one\rLine two") == "Line one\nLine two"


# ─── validate_note ────────────────────────────────────────────────────────────


def test_note_on_a_product_that_asks_for_nothing_is_refused():
    """A brownie has no card. Silently dropping the text would be worse."""
    with pytest.raises(BadRequestError, match="does not take a personalised message"):
        cart_service.validate_note(
            _product(personalisation_type=None, name="Ferrero Brownies"),
            "Happy birthday",
        )


def test_absent_note_on_a_plain_product_is_fine():
    assert cart_service.validate_note(_product(personalisation_type=None), None) is None
    assert cart_service.validate_note(_product(personalisation_type=None), "  ") is None


def test_empty_note_is_allowed_in_the_basket():
    """
    The field appears after the item is in the basket, so blank means
    "not finished yet". Checkout is where it becomes a refusal — see
    `test_checkout_refuses_a_line_with_no_message`.
    """
    assert cart_service.validate_note(_product(), "") is None
    assert cart_service.validate_note(_product(), None) is None


def test_note_at_the_limit_is_accepted_and_one_over_is_not():
    product = _product(max_length=10)
    assert cart_service.validate_note(product, "a" * 10) == "a" * 10
    with pytest.raises(BadRequestError, match="10 characters at most"):
        cart_service.validate_note(product, "a" * 11)


def test_the_limit_comes_from_the_product_not_a_constant():
    """A card is a physical thing; two products can be written on different ones."""
    assert cart_service.validate_note(_product(max_length=200), "a" * 150)
    with pytest.raises(BadRequestError):
        cart_service.validate_note(_product(max_length=20), "a" * 150)


def test_arabic_gets_the_same_allowance_as_english():
    """
    Counted in characters, not bytes.

    Arabic is multi-byte in UTF-8, so a byte-counted limit of 100 would give an
    Arabic message roughly half the card an English one gets — and the customer
    would have no way to tell why.
    """
    product = _product(max_length=10)
    arabic = "كل عام وأنت"  # 11 characters
    assert len(arabic) == 11
    with pytest.raises(BadRequestError):
        cart_service.validate_note(product, arabic)
    assert cart_service.validate_note(product, arabic[:10]) == arabic[:10]


def test_whitespace_is_trimmed_before_the_limit_is_applied():
    """Padding is not a message, so it must not consume the card."""
    product = _product(max_length=5)
    assert cart_service.validate_note(product, "   hello   ") == "hello"


# ─── the dedup key ────────────────────────────────────────────────────────────


def _key(product_id, note):
    return cart_service._options_key(product_id, [], note)


def test_two_different_messages_are_two_lines():
    """The bug this whole feature had to be built around."""
    product_id = uuid4()
    assert _key(product_id, "For Sara") != _key(product_id, "For Ahmed")


def test_the_same_message_is_one_line():
    """
    Tapping add twice on an identical card is one line of two, which is what
    somebody sending the same note to two people actually wants.
    """
    product_id = uuid4()
    assert _key(product_id, "For Sara") == _key(product_id, "For Sara")


def test_the_same_message_typed_differently_is_still_one_line():
    """Trailing space and CRLF must not split a line the customer sees as one."""
    product_id = uuid4()
    assert _key(product_id, "For Sara ") == _key(product_id, "For Sara")
    assert _key(product_id, "A\r\nB") == _key(product_id, "A\nB")


def test_a_message_and_no_message_are_two_lines():
    product_id = uuid4()
    assert _key(product_id, "For Sara") != _key(product_id, None)
    assert _key(product_id, None) == _key(product_id, "   ")


def test_options_still_separate_lines_when_no_note_is_involved():
    """The behaviour that was already there has not moved."""
    product_id = uuid4()
    ferrero, fudge = str(uuid4()), str(uuid4())
    two_ferrero = [{"option_id": ferrero, "quantity": 2}]
    one_each = [
        {"option_id": ferrero, "quantity": 1},
        {"option_id": fudge, "quantity": 1},
    ]
    assert cart_service._options_key(product_id, two_ferrero, None) != (
        cart_service._options_key(product_id, one_each, None)
    )


def test_option_order_does_not_split_a_line():
    product_id = uuid4()
    a, b = str(uuid4()), str(uuid4())
    forwards = [{"option_id": a, "quantity": 1}, {"option_id": b, "quantity": 1}]
    backwards = list(reversed(forwards))
    assert cart_service._options_key(product_id, forwards, "hi") == (
        cart_service._options_key(product_id, backwards, "hi")
    )


# ─── the checkout guard ───────────────────────────────────────────────────────


def _cart_item(product, *, note=None, quantity=1):
    return SimpleNamespace(
        product=product,
        quantity=quantity,
        selected_options=[],
        personalisation_note=note,
    )


def _cart(*items):
    return SimpleNamespace(items=list(items))


def test_checkout_refuses_a_line_with_no_message(monkeypatch):
    """
    The basket tolerates a blank note; the order does not.

    A gift note is an item whose entire value is the text it carries. Bought
    empty it is a paid line that does nothing, and the customer has no way to
    add the words afterwards.
    """
    from app.services import order_service

    monkeypatch.setattr(order_service, "is_website_product_visible", lambda p: True)
    with pytest.raises(BadRequestError, match="Add your message"):
        order_service._compute_item_totals(_cart(_cart_item(_product(), note=None)))


def test_checkout_refuses_a_whitespace_only_message(monkeypatch):
    """Spaces are not a message. Blank in every spelling is blank."""
    from app.services import order_service

    monkeypatch.setattr(order_service, "is_website_product_visible", lambda p: True)
    with pytest.raises(BadRequestError, match="Add your message"):
        order_service._compute_item_totals(_cart(_cart_item(_product(), note="   ")))


def test_the_message_is_snapshotted_onto_the_order(monkeypatch):
    """
    Captured at order time like every other snapshot on the line.

    Reading it back through the product later would lose it the moment the
    product is edited or deleted — and this is the string that has to survive
    exactly.
    """
    from app.services import order_service

    monkeypatch.setattr(order_service, "is_website_product_visible", lambda p: True)
    items, subtotal = order_service._compute_item_totals(
        _cart(_cart_item(_product(), note="  Happy birthday, Sara  "))
    )
    assert items[0]["personalisation_note"] == "Happy birthday, Sara"
    assert subtotal == Decimal("10.00")


def test_an_ordinary_product_snapshots_no_message(monkeypatch):
    from app.services import order_service

    monkeypatch.setattr(order_service, "is_website_product_visible", lambda p: True)
    items, _ = order_service._compute_item_totals(
        _cart(_cart_item(_product(personalisation_type=None)))
    )
    assert items[0]["personalisation_note"] is None
