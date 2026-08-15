"""
The `UUID[]` columns that scope discounts, promotions, terminals and alerts.

Nine of them across five tables, and Postgres cannot put a foreign key on any of
them. So a promotion could scope itself to a branch that had been deleted, a
notification rule could name a member of staff who left, and a display could be
routed to a category that no longer existed — all accepted, none an error. The
scoping simply stopped meaning what it said, and the symptom of that on a
discount rule is money.

Join tables are the real fix and are still owed. This is the half that closes
the hole where it bites: an id is checked against a live row at the moment
somebody writes it.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import BadRequestError
from app.models import Branch, Device, Discount, NotificationRule, Promotion, TimedEvent
from app.services import reference_integrity


def _db(live: list[uuid.UUID]):
    """A session whose `select(target.id)` finds exactly *live*."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = live
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    return session


async def test_an_id_that_names_nothing_is_refused():
    missing = uuid.uuid4()

    with pytest.raises(BadRequestError) as raised:
        await reference_integrity.check(_db([]), Discount, {"branch_ids": [missing]})

    assert str(missing) in raised.value.detail
    assert "branch_ids" in raised.value.detail


async def test_the_message_lists_only_the_ids_that_are_wrong():
    """
    "One of these fourteen branch ids is wrong" is not something anybody can
    act on.
    """
    good, bad = uuid.uuid4(), uuid.uuid4()

    with pytest.raises(BadRequestError) as raised:
        await reference_integrity.check(
            _db([good]), Discount, {"branch_ids": [good, bad]}
        )

    assert str(bad) in raised.value.detail
    assert str(good) not in raised.value.detail


async def test_live_ids_pass():
    ids = [uuid.uuid4(), uuid.uuid4()]
    await reference_integrity.check(_db(ids), Discount, {"branch_ids": ids})


async def test_a_deleted_or_deactivated_target_counts_as_missing():
    """
    A promotion scoped to a switched-off branch is a promotion that does
    nothing, and the person saving it believes otherwise. The filter is in the
    query, so the test asserts on the SQL rather than on a second round trip.
    """
    db = _db([])
    with pytest.raises(BadRequestError):
        await reference_integrity.check(db, Discount, {"branch_ids": [uuid.uuid4()]})

    sql = str(db.execute.call_args.args[0])
    assert "deleted_at IS NULL" in sql
    assert "is_active" in sql


async def test_an_empty_list_means_all_of_them_and_asks_nothing():
    """Empty is meaningful for every one of these columns — "every branch",
    "every order type" — and has nothing to verify."""
    db = _db([])
    await reference_integrity.check(db, Discount, {"branch_ids": []})
    await reference_integrity.check(db, Discount, {"branch_ids": None})
    db.execute.assert_not_called()


async def test_a_field_the_edit_did_not_touch_is_not_made_to_re_prove_itself():
    """
    `crud_service.update` dumps with `exclude_unset`. Renaming a promotion must
    not fail because a branch it was scoped to last year has since closed.
    """
    db = _db([])
    await reference_integrity.check(db, Promotion, {"name": "Eid weekend"})
    db.execute.assert_not_called()


async def test_a_model_with_no_uuid_arrays_is_untouched():
    """The check runs from `crud_service.create`, which every small admin table
    goes through. It has to be free for the ones this is not about."""
    db = _db([])
    await reference_integrity.check(db, Branch, {"name": "Al Barsha"})
    db.execute.assert_not_called()


async def test_duplicate_ids_are_asked_about_once():
    """The console posts what the multi-select holds; it is free to repeat."""
    ids = [uuid.uuid4()]
    db = _db(ids)
    await reference_integrity.check(db, Discount, {"branch_ids": ids * 3})

    sql = str(
        db.execute.call_args.args[0].compile(compile_kwargs={"literal_binds": True})
    )
    assert sql.count(ids[0].hex) == 1


@pytest.mark.parametrize(
    "model,field,target",
    [
        # Every column the audit named, so a new one added to a model without a
        # line in `REFERENCES` is visible as a gap rather than as silence.
        (Device, "category_ids", "Category"),
        (Discount, "branch_ids", "Branch"),
        (Promotion, "branch_ids", "Branch"),
        (Promotion, "trigger_product_ids", "Product"),
        (Promotion, "reward_product_ids", "Product"),
        (Promotion, "customer_tag_ids", "Tag"),
        (TimedEvent, "branch_ids", "Branch"),
        (TimedEvent, "product_ids", "Product"),
        (TimedEvent, "category_ids", "Category"),
        (NotificationRule, "branch_ids", "Branch"),
        (NotificationRule, "recipient_user_ids", "User"),
    ],
)
async def test_every_column_the_audit_named_is_covered(model, field, target):
    assert reference_integrity.REFERENCES[model.__name__][field] == target

    with pytest.raises(BadRequestError) as raised:
        await reference_integrity.check(_db([]), model, {field: [uuid.uuid4()]})
    assert target.lower() in raised.value.detail


async def test_the_check_runs_from_the_one_door_all_these_writes_use():
    """
    Three of the five entities are built by `pos_config.build_crud_router` and
    have no hand-written handler to put a check in. `crud_service.create` is
    where they all end up, which is why the check lives there rather than in
    eleven routers.
    """
    from app.services import crud_service

    db = _db([])
    db.add = MagicMock()
    with pytest.raises(BadRequestError):
        await crud_service.create(db, Discount, {"branch_ids": [uuid.uuid4()]})
    # Refused before the row is added, not after — a failed check must not
    # leave a half-built entity in the session for the request commit to find.
    db.add.assert_not_called()
