"""
The one place a purchase order's status is allowed to change.

It used to be four: `submit`, `approve`, `decline` and `receive` each opened
with their own `if status != …` and then assigned the column and stamped an
actor by hand, inline in `api/v1/inventory.py` — while the order's state
machine was a service and the transfer's was another. Three homes for one
pattern is how they drift, and `order_lifecycle` exists because the order one
already had: five sets of rules, one of which silently skipped the refund.

These tests pin the extracted machine: which moves are legal, that the illegal
ones are refused in the words the endpoints used, that the consequences fire
whoever makes the move, and that receiving still cannot post stock against an
order it is not allowed to receive.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import ConflictError, ForbiddenError
from app.models.inventory import (
    PurchaseOrder,
    PurchaseOrderItem,
)
from app.models.inventory import (
    PurchaseOrderStatusEnum as PO,
)
from app.services.inventory import inventory_service


def _user(user_id=None, *, is_admin=False):
    return SimpleNamespace(id=user_id or uuid.uuid4(), is_admin=is_admin)


def _po(status: PO, *, submitter_id=None) -> PurchaseOrder:
    order = PurchaseOrder(
        reference="PO-000001",
        status=status.value,
        supplier_id=uuid.uuid4(),
        branch_id=uuid.uuid4(),
        business_date="2026-08-15",
        submitter_id=submitter_id,
    )
    return order


class TestTheMap:
    def test_a_draft_can_only_be_submitted(self):
        assert inventory_service.allowed_purchase_order_transitions(PO.DRAFT.value) == {
            PO.PENDING
        }

    def test_a_submitted_order_can_be_approved_or_declined(self):
        assert inventory_service.allowed_purchase_order_transitions(
            PO.PENDING.value
        ) == {PO.APPROVED, PO.DECLINED}

    def test_an_approved_order_can_only_be_received(self):
        """Receiving reaches one of two states from here; which one is decided
        by what actually arrived."""
        assert inventory_service.allowed_purchase_order_transitions(
            PO.APPROVED.value
        ) == {PO.PARTIALLY_RECEIVED, PO.CLOSED}

    def test_a_partial_receipt_can_be_topped_up_or_closed(self):
        assert inventory_service.allowed_purchase_order_transitions(
            PO.PARTIALLY_RECEIVED.value
        ) == {PO.PARTIALLY_RECEIVED, PO.CLOSED}

    @pytest.mark.parametrize("terminal", [PO.DECLINED, PO.CLOSED])
    def test_the_endings_are_endings(self, terminal):
        """
        No route from `declined` back to `draft`: the shop's answer to a
        rejected order is a new one, not a quietly re-edited copy of the one
        somebody already refused.
        """
        assert inventory_service.allowed_purchase_order_transitions(terminal.value) == (
            set()
        )

    def test_an_unrecognised_status_leads_nowhere_rather_than_everywhere(self):
        """A column holding something the enum does not know is a data problem;
        it must not read as "any move is fine"."""
        assert inventory_service.allowed_purchase_order_transitions("nonsense") == set()


class TestLegalMoves:
    async def test_submitting_stamps_who_and_when(self):
        order, user = _po(PO.DRAFT), _user()

        moved = await inventory_service.transition_purchase_order(
            AsyncMock(), order, PO.PENDING, user=user
        )

        assert moved is True
        assert order.status == PO.PENDING.value
        assert order.submitter_id == user.id
        assert order.submitted_at is not None

    async def test_approving_stamps_who_and_when(self):
        order = _po(PO.PENDING, submitter_id=uuid.uuid4())
        approver = _user()

        await inventory_service.transition_purchase_order(
            AsyncMock(), order, PO.APPROVED, user=approver
        )

        assert order.status == PO.APPROVED.value
        assert order.approver_id == approver.id
        assert order.approved_at is not None

    async def test_declining_stamps_the_approver_and_no_timestamp(self):
        """
        The asymmetry is deliberate and preserved: `purchase_orders` has
        `approved_at` and no `declined_at`, and inventing one is a migration
        rather than part of an extraction that promises identical behaviour.
        """
        order = _po(PO.PENDING, submitter_id=uuid.uuid4())
        approver = _user()

        await inventory_service.transition_purchase_order(
            AsyncMock(), order, PO.DECLINED, user=approver
        )

        assert order.status == PO.DECLINED.value
        assert order.approver_id == approver.id
        assert order.approved_at is None

    async def test_moving_to_where_it_already_is_changes_nothing(self):
        """The ordinary answer for a second partial receipt, and not an error."""
        order = _po(PO.PARTIALLY_RECEIVED)

        moved = await inventory_service.transition_purchase_order(
            AsyncMock(), order, PO.PARTIALLY_RECEIVED, user=_user()
        )

        assert moved is False
        assert order.approver_id is None


class TestIllegalMoves:
    async def test_only_a_draft_can_be_submitted_and_the_message_says_which(self):
        order = _po(PO.APPROVED)

        with pytest.raises(ConflictError) as raised:
            await inventory_service.transition_purchase_order(
                AsyncMock(), order, PO.PENDING, user=_user()
            )

        # The endpoint's own wording, moved rather than rewritten.
        assert raised.value.detail == (
            "Only draft orders can be submitted (this is approved)"
        )
        assert order.status == PO.APPROVED.value

    async def test_only_a_submitted_order_can_be_approved(self):
        with pytest.raises(ConflictError) as raised:
            await inventory_service.transition_purchase_order(
                AsyncMock(), _po(PO.DRAFT), PO.APPROVED, user=_user()
            )
        assert raised.value.detail == "Only submitted orders can be approved"

    async def test_only_a_submitted_order_can_be_declined(self):
        with pytest.raises(ConflictError) as raised:
            await inventory_service.transition_purchase_order(
                AsyncMock(), _po(PO.CLOSED), PO.DECLINED, user=_user()
            )
        assert raised.value.detail == "Only submitted orders can be declined"

    async def test_a_declined_order_cannot_be_revived(self):
        for target in (PO.PENDING, PO.APPROVED, PO.CLOSED):
            with pytest.raises(ConflictError):
                await inventory_service.transition_purchase_order(
                    AsyncMock(), _po(PO.DECLINED), target, user=_user()
                )

    async def test_a_closed_order_cannot_be_received_again(self):
        """
        Asserted through the guard rather than through `transition`: moving an
        order to where it already is answers `False` rather than raising, which
        is what makes a second partial receipt ordinary. A *closed* order asking
        to be received is the case that must be refused, and receiving asks the
        guard up front for exactly that reason.
        """
        with pytest.raises(ConflictError) as raised:
            inventory_service.assert_can_transition_purchase_order(
                _po(PO.CLOSED), PO.PARTIALLY_RECEIVED
            )
        assert "only approved orders can be received" in raised.value.detail


class TestSeparationOfDuties:
    """
    Whoever raised the order cannot wave it through.

    A rule about the *transition*, which is what it always was — and why, living
    in one endpoint, it ran on exactly one of the ways in.
    """

    async def test_the_submitter_cannot_approve_their_own_order(self):
        submitter = _user()
        order = _po(PO.PENDING, submitter_id=submitter.id)

        with pytest.raises(ForbiddenError) as raised:
            await inventory_service.transition_purchase_order(
                AsyncMock(), order, PO.APPROVED, user=submitter
            )

        assert (
            raised.value.detail == "A purchase order must be approved by someone else"
        )
        assert order.status == PO.PENDING.value

    async def test_an_admin_may_approve_their_own(self):
        """A one-person shop still has to be able to buy flour."""
        admin = _user(is_admin=True)
        order = _po(PO.PENDING, submitter_id=admin.id)

        await inventory_service.transition_purchase_order(
            AsyncMock(), order, PO.APPROVED, user=admin
        )

        assert order.status == PO.APPROVED.value

    async def test_the_rule_does_not_apply_to_declining(self):
        """Refusing your own order is not a conflict of interest."""
        submitter = _user()
        order = _po(PO.PENDING, submitter_id=submitter.id)

        await inventory_service.transition_purchase_order(
            AsyncMock(), order, PO.DECLINED, user=submitter
        )

        assert order.status == PO.DECLINED.value


class TestReceivingGoesThroughTheSameMap:
    """
    Receiving is the one move whose consequence — stock arriving — runs before
    the assignment, because the state it lands in is computed from what turned
    up. So it asks the map up front, against the same rules and the same words.
    """

    async def test_an_order_that_cannot_be_received_never_reaches_the_stock_post(self):
        db = AsyncMock()
        order = _po(PO.PENDING)

        with pytest.raises(ConflictError) as raised:
            await inventory_service.receive_purchase_order(
                db, purchase_order=order, user=_user(), received={}
            )

        assert raised.value.detail == (
            "Purchase order is pending; only approved orders can be received"
        )
        # Nothing was written: no branch lookup, no transaction, no posting.
        db.add.assert_not_called()

    @pytest.mark.parametrize(
        "fully_received,expected",
        [(True, PO.CLOSED), (False, PO.PARTIALLY_RECEIVED)],
    )
    async def test_a_short_delivery_leaves_it_open_and_a_full_one_closes_it(
        self, fully_received, expected
    ):
        order = _po(PO.APPROVED)
        line = PurchaseOrderItem(
            id=uuid.uuid4(),
            item_id=uuid.uuid4(),
            unit="ingredient",
            quantity=Decimal("10"),
            received_quantity=Decimal("0"),
            conversion_factor=Decimal("1"),
            unit_cost=Decimal("2"),
        )
        order.items = [line]

        db = AsyncMock()
        db.add = MagicMock()
        db.get = AsyncMock(return_value=SimpleNamespace(id=order.branch_id))

        with (
            patch.object(
                inventory_service,
                "next_reference",
                new=AsyncMock(return_value="PUR-000001"),
            ),
            patch.object(
                inventory_service.business_day_service,
                "current_business_date",
                new=AsyncMock(return_value="2026-08-15"),
            ),
            patch.object(
                inventory_service, "post_transaction", new=AsyncMock(return_value=None)
            ),
            patch.object(
                type(order),
                "is_fully_received",
                property(lambda _self: fully_received),
            ),
        ):
            await inventory_service.receive_purchase_order(
                db,
                purchase_order=order,
                user=_user(),
                received={line.id: Decimal("4")},
            )

        assert order.status == expected.value
