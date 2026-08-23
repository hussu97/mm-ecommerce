"""
GrubOps: the payload says what the terminal said, and a lapsed hour is noticed.

Two things here would break silently in production and nothing else would
notice.

The first is the **timed return**. Out-of-stock expires on read, so nothing
fires when a one-hour window lapses; if the diff did not treat "the clock
passed" as a change worth pushing, every timed out-of-stock would leave an item
dark on the aggregators until somebody marked it out and back by hand. That is
the failure the reconcile loop exists to prevent, and the test for it is the
reason to trust the loop at all.

The second is the **token**. It lasts an hour, a sweep can start at minute
fifty-nine, and a 401 that is not retried looks like an item that just did not
sync.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services import grubops_service as svc
from app.services.providers.grubops_provider import (
    STATUS_UNTIL_FURTHER_NOTICE,
    STATUS_UNTIL_SPECIFIC_DATE,
    GrubOpsClient,
    GrubOpsConfig,
    GrubOpsError,
)

PARTNER = "6922fe267f5b1c6d208c634f"
LOCATION = "692300947f5b1c6d208c6352"
BRAND = "6922ff03323715175fede66b"


def _desired(*, available: bool, until: datetime | None = None) -> svc.Desired:
    return svc.Desired(
        item_map_id=uuid.uuid4(),
        brand_id=BRAND,
        recipe_id="recipe-1",
        modifier_id=None,
        child_modifier_id=None,
        grubops_type="RECIPE",
        available=available,
        until=until,
    )


def _state(*, available: bool | None, until: datetime | None = None):
    class _S:
        last_pushed_available = available
        last_pushed_until = until

    return _S()


# ── the payload ───────────────────────────────────────────────────────────────


def test_indefinite_out_of_stock_carries_no_date():
    """ "Until somebody says so" is the absence of a moment, not a far-off one."""
    body = svc.unavailable_body(
        _desired(available=False),
        partner_id=PARTNER,
        location_id=LOCATION,
        source="POS",
    )
    assert body["status"] == STATUS_UNTIL_FURTHER_NOTICE
    assert body["unavailabilityInfo"]["unavailableTill"] is None
    assert body["itemInfo"]["locationId"] == LOCATION
    assert body["itemInfo"]["brandId"] == BRAND
    assert body["itemInfo"]["type"] == "RECIPE"


def test_a_timed_window_sends_the_moment_it_returns():
    """
    The instant, not the duration.

    `availability_service` has already turned "until close" into the branch's
    own rollover — sending a duration would ask GrubOps to reinterpret a
    decision that has already been made correctly.
    """
    until = datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc)
    body = svc.unavailable_body(
        _desired(available=False, until=until),
        partner_id=PARTNER,
        location_id=LOCATION,
        source="POS",
    )
    assert body["status"] == STATUS_UNTIL_SPECIFIC_DATE
    assert body["unavailabilityInfo"]["unavailableTill"].startswith("2026-08-23T20:00")


def test_the_identity_names_every_id_even_when_null():
    """An absent key is not a null one to the service on the other end."""
    body = svc.available_body(
        _desired(available=True),
        partner_id=PARTNER,
        location_id=LOCATION,
        source="POS",
    )
    for key in ("recipeId", "modifierId", "childModifierId", "brandId", "type"):
        assert key in body["itemInfo"]


# ── the diff ──────────────────────────────────────────────────────────────────


def test_a_lapsed_window_is_a_change_worth_pushing():
    """
    The whole reason this is a loop and not a listener.

    Nothing fires when `out_of_stock_until` passes. The next tick simply
    computes "available" where it computed "out" before, and that difference is
    what has to reach the aggregators.
    """
    was_out = _state(
        available=False, until=datetime(2026, 8, 23, 18, 0, tzinfo=timezone.utc)
    )
    now_back = _desired(available=True)
    assert svc.needs_push(was_out, now_back) is True


def test_nothing_is_pushed_when_nothing_changed():
    """A quiet tick must cost zero calls to somebody else's API."""
    unchanged = _desired(available=True)
    assert svc.needs_push(_state(available=True), unchanged) is False


def test_a_mapping_never_pushed_is_always_pushed():
    """
    "Never told" is not "told it was available".

    An item already out when its mapping is approved would otherwise stay on
    the aggregators until somebody touched it again.
    """
    assert svc.needs_push(None, _desired(available=True)) is True
    assert svc.needs_push(_state(available=None), _desired(available=True)) is True


def test_end_of_day_drift_does_not_resend_all_evening():
    """
    `end_of_day` resolves a few milliseconds differently every tick.

    Without a tolerance the loop would push the same fact every two minutes
    from the moment an item went out until closing time.
    """
    first = datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc)
    later = first + timedelta(seconds=5)
    state = _state(available=False, until=first)
    assert svc.needs_push(state, _desired(available=False, until=later)) is False


def test_a_real_change_of_return_time_is_pushed():
    """An hour becoming until-close is a different promise to the customer."""
    first = datetime(2026, 8, 23, 18, 0, tzinfo=timezone.utc)
    later = first + timedelta(hours=2)
    state = _state(available=False, until=first)
    assert svc.needs_push(state, _desired(available=False, until=later)) is True


# ── the token ─────────────────────────────────────────────────────────────────


def _client() -> GrubOpsClient:
    return GrubOpsClient(
        GrubOpsConfig(
            username="someone@example.com",
            password="secret",
            cognito_client_id="client",
            cognito_region="eu-west-2",
            partner_id=PARTNER,
            api_base="https://api.example.invalid",
            catalog_api_base="https://catalog.example.invalid",
            source="POS",
            timeout=1.0,
        )
    )


@pytest.mark.asyncio
async def test_the_token_is_minted_once_and_reused():
    client = _client()
    with patch.object(
        client,
        "_cognito",
        new=AsyncMock(
            return_value={
                "IdToken": "id-1",
                "RefreshToken": "refresh-1",
                "ExpiresIn": 3600,
            }
        ),
    ) as cognito:
        assert await client.token() == "id-1"
        assert await client.token() == "id-1"
    assert cognito.await_count == 1


@pytest.mark.asyncio
async def test_an_expired_token_is_refreshed_not_re_logged_in():
    client = _client()
    with patch.object(
        client,
        "_cognito",
        new=AsyncMock(
            side_effect=[
                {"IdToken": "id-1", "RefreshToken": "r-1", "ExpiresIn": 3600},
                {"IdToken": "id-2", "ExpiresIn": 3600},
            ]
        ),
    ) as cognito:
        await client.token()
        # Walk the clock past the skew rather than sleeping through it.
        client._expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert await client.token() == "id-2"

    assert cognito.await_args_list[1].args[0]["AuthFlow"] == "REFRESH_TOKEN_AUTH"
    # A refresh does not reissue one, and the original must survive.
    assert client._refresh_token == "r-1"


@pytest.mark.asyncio
async def test_a_dead_refresh_token_falls_back_to_a_full_login():
    """
    A rotated password invalidates everything.

    Without the fallback that is an outage lasting until somebody restarts the
    container, rather than one failed call.
    """
    client = _client()
    with patch.object(
        client,
        "_cognito",
        new=AsyncMock(
            side_effect=[
                {"IdToken": "id-1", "RefreshToken": "r-1", "ExpiresIn": 3600},
                GrubOpsError("refresh token expired"),
                {"IdToken": "id-3", "RefreshToken": "r-3", "ExpiresIn": 3600},
            ]
        ),
    ) as cognito:
        await client.token()
        client._expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert await client.token() == "id-3"

    assert cognito.await_args_list[2].args[0]["AuthFlow"] == "USER_PASSWORD_AUTH"


@pytest.mark.asyncio
async def test_a_challenge_is_reported_rather_than_crashing():
    """
    MFA or a forced password change is not something this can answer.

    Driven through the real HTTP boundary rather than by patching `_cognito`,
    because the guard being tested lives inside it — mocking that method would
    test the mock.
    """

    class _Response:
        status_code = 200
        text = "{}"

        @staticmethod
        def json():
            # A challenge: Cognito answers 200 with no AuthenticationResult.
            return {"ChallengeName": "SOFTWARE_TOKEN_MFA", "Session": "abc"}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return _Response()

    with patch("app.services.providers.grubops_provider.httpx.AsyncClient", _Client):
        with pytest.raises(GrubOpsError, match="challenge"):
            await _client().token()


# ── the flag ──────────────────────────────────────────────────────────────────


def test_the_immediate_push_is_inert_while_the_flag_is_off():
    """Shipping dark must mean dark, including from the register's routes."""
    with patch.object(svc, "is_enabled", return_value=False):
        # No running loop needed, because it must return before creating a task.
        svc.push_change_in_background(
            branch_id=uuid.uuid4(), product_ids=[uuid.uuid4()], in_stock=False
        )
    assert not svc._pending


# ── matching two catalogues ───────────────────────────────────────────────────


class _Branch:
    def __init__(self, name, city=None):
        self.id = uuid.uuid4()
        self.name = name
        self.city = city


def test_a_location_matches_the_branch_it_names():
    """
    "Sharjah" is "Sharjah Kitchen".

    The first version of this reused the item matcher and matched nothing:
    a plain similarity ratio scores that pair at about a half, because most of
    the difference is a word GrubOps simply does not bother saying. Seeding
    nothing, silently, is the failure this test exists to prevent.
    """
    from app.services.grubops_mapping import match_branch

    sharjah = _Branch("Sharjah Kitchen", "Sharjah")
    barsha = _Branch("Barsha Heights Counter", "Dubai")
    assert match_branch("Sharjah", [sharjah, barsha]) is sharjah
    assert match_branch("Barsha Heights", [sharjah, barsha]) is barsha


def test_a_location_that_is_no_branch_of_ours_matches_nothing():
    """Better an unmatched row somebody reads than a confident wrong one."""
    from app.services.grubops_mapping import match_branch

    assert (
        match_branch("Abu Dhabi Mall", [_Branch("Sharjah Kitchen", "Sharjah")]) is None
    )


def test_names_match_across_case_accents_and_punctuation():
    """Two systems typed into by two people on two days."""
    from app.services.grubops_mapping import normalise

    assert normalise("Crème Brûlée  Tart!") == normalise("creme brulee tart")


def test_a_weak_item_match_is_not_offered_at_all():
    """
    An unmatched item costs somebody a minute; a wrong one takes the wrong
    cake off Talabat.
    """
    from app.services.grubops_mapping import Candidate, best_match

    candidates = [
        Candidate(
            item_id="r1", name="Pistachio Kunafa", brand_id="b", grubops_type="RECIPE"
        )
    ]
    match, _score, _method = best_match("Chocolate Fudge Brownie", candidates)
    assert match is None


def test_an_exact_item_match_is_marked_exact():
    from app.services.grubops_mapping import Candidate, best_match

    candidates = [
        Candidate(
            item_id="r1", name="Pistachio Kunafa", brand_id="b", grubops_type="RECIPE"
        )
    ]
    match, score, method = best_match("pistachio kunafa", candidates)
    assert match is not None and score == 1.0 and method == "exact"


def test_a_modifier_is_pinned_by_its_recipe_as_well_as_its_group():
    """
    Group alone is not enough, and this is the case that proves it.

    GrubOps duplicates a modifier group per recipe: "Your Choice of Quantity"
    exists seventeen times on this account, and the modifiers under those
    seventeen copies share three names between them. Scoping by group name
    only, every "3 Pieces" on the menu matches whichever of the seventeen the
    matcher happened to see first — measured against the live catalogue that
    collapsed 147 options onto 51 modifiers, silently, with every match
    reported as exact.
    """
    from app.services.grubops_mapping import Candidate, best_match, normalise

    modifiers = [
        Candidate(
            item_id=f"mod-{recipe}",
            name="3 Pieces",
            brand_id="b",
            grubops_type="MODIFIER",
            parent_group=normalise("Your Choice of Quantity"),
            parent_recipe_id=recipe,
        )
        for recipe in ("recipe-a", "recipe-b", "recipe-c")
    ]

    for recipe in ("recipe-a", "recipe-b", "recipe-c"):
        scoped = [
            m
            for m in modifiers
            if m.parent_recipe_id == recipe
            and m.parent_group == normalise("Your Choice of Quantity")
        ]
        assert len(scoped) == 1
        match, _score, method = best_match("3 Pieces", scoped)
        assert match is not None and method == "exact"
        assert match.item_id == f"mod-{recipe}"


def test_the_parent_chain_yields_both_group_and_recipe():
    """The shape GrubOps actually answers with — group, then recipe above it."""
    from app.services.grubops_mapping import _parent_of

    group, recipe_id = _parent_of(
        {
            "id": "mod-1",
            "name": {"translations": {"en-US": "3 Pieces"}},
            "parentAssociations": [
                {
                    "type": "MODIFIER_GROUP",
                    "id": "grp-1",
                    "name": {"translations": {"en-US": "Your Choice of Quantity"}},
                    "parentAssociations": [
                        {
                            "type": "RECIPE",
                            "id": "rec-1",
                            "name": {"translations": {"en-US": "Snickers Brownies"}},
                        }
                    ],
                }
            ],
        }
    )
    assert group == "your choice of quantity"
    assert recipe_id == "rec-1"


def test_the_english_name_is_taken_from_the_translation_bundle():
    """`{"name": {"translations": {"en-US": ...}}}` is what the item list sends."""
    from app.services.grubops_mapping import _name_of

    assert (
        _name_of(
            {"name": {"translations": {"en-US": "Fudge Brownies", "ar-ae": "براوني"}}}
        )
        == "Fudge Brownies"
    )
    # `serving-brands` uses a different shape for the same idea.
    assert _name_of({"name": {"text": "Melting Moments"}}) == "Melting Moments"
