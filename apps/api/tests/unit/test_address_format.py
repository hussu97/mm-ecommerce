"""
One address, written the same way everywhere.

The emails, the register's ticket and both couriers each used to flatten a
shipping snapshot themselves, and the differences were accidents rather than
decisions. These assert the shape they now all share.
"""

from __future__ import annotations

from app.services import address_format


class TestOneLine:
    def test_the_unit_leads(self):
        """
        A formatted Google address gets a rider to the building; the flat number
        is the only part that finishes the delivery, so it goes first rather
        than trailing behind a landmark.
        """
        line = address_format.one_line(
            {
                "unit_number": "Flat 1204",
                "address_line_1": "Azizi Riviera 5, Nad Al Sheba 1, Dubai",
            }
        )

        assert line == "Flat 1204, Azizi Riviera 5, Nad Al Sheba 1, Dubai"

    def test_every_field_the_checkout_writes_appears(self):
        """
        The bug this exists for: the email card rendered `address_line_1`,
        `address_line_2` and a `city` that the checkout does not write, and
        never rendered `unit_number` at all — so MM-20260805-008 reached the
        customer with the flat number missing.
        """
        line = address_format.one_line(
            {
                "unit_number": "Flat 1204",
                "address_line_1": "Azizi Riviera 5",
                "address_line_2": "Building 12",
                "city": "Dubai",
            }
        )

        assert line == "Flat 1204, Azizi Riviera 5, Building 12, Dubai"

    def test_a_missing_part_leaves_no_stray_comma(self):
        line = address_format.one_line({"address_line_1": "Villa 1", "city": ""})

        assert line == "Villa 1"

    def test_nothing_to_show_is_none_rather_than_empty(self):
        """
        Every caller renders this conditionally, and they all already read `if
        address` — which is false for None and for "" alike, but only None says
        "there was nothing here" to a reader of the code.
        """
        assert address_format.one_line(None) is None
        assert address_format.one_line({}) is None
        assert address_format.one_line({"address_line_1": "   "}) is None


class TestRecipientName:
    def test_both_names(self):
        assert (
            address_format.recipient_name(
                {"first_name": "Hussain", "last_name": "Abbasi"}
            )
            == "Hussain Abbasi"
        )

    def test_one_name(self):
        assert address_format.recipient_name({"first_name": "Hussain"}) == "Hussain"

    def test_neither_is_none_not_a_lone_space(self):
        """`" ".join` over two empty fields used to render a single space, which
        a template then drew as an empty bold line above the address."""
        assert (
            address_format.recipient_name({"first_name": "", "last_name": ""}) is None
        )
        assert address_format.recipient_name(None) is None
