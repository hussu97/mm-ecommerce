"""
The default alternates matrix, in the two places it has to be written down.

`DEFAULT_ALTERNATES` is what the running code falls back to for an order with no
zone to ask. `SEED` in migration `125_zone_alternates` is what every existing
zone was given on the way in. They say the same thing and they have to keep
saying it, but the migration cannot import the constant — a migration describes
the database as it was at the moment it ran, and a constant that a later commit
edits would quietly rewrite history.

So there are two copies, deliberately, and this is the thing that stops them
drifting. It is the pattern the lessons file asks for whenever a stored
vocabulary is duplicated into a migration: the map is the deliverable, and a
test compares the copies.

**They agree where they overlap, and the code is allowed to be longer.** A
courier added after `125` ran cannot appear in it — the migration describes the
database as it was, and back-filling a new provider into an old migration would
rewrite what it did on every database that has already run it. So Slider is in
the code and not in `125`, and it is seeded instead by the migration that
introduces it. What is *not* allowed is a provider nobody seeds anywhere: that
is a zone whose orders can never be moved, which looks exactly like the feature
being broken.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.models.delivery_polygon import DEFAULT_ALTERNATES, FulfilmentProviderEnum

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "125_zone_alternate_providers.py"
)


def _migration():
    """Load `125` as a module without going through Alembic's env."""
    spec = importlib.util.spec_from_file_location("_m125", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seeded() -> dict[str, list[str]]:
    import json

    return {preferred: json.loads(alts) for preferred, alts in _migration().DEFAULTS}


def test_the_code_and_the_migration_agree():
    """The whole point of the file. Two copies, one meaning.

    Compared over what `125` actually names rather than over both sets, because
    a courier that did not exist when it ran cannot be in it — see the module
    docstring. Every provider it *does* name must still say the same thing here.
    """
    seeded = _seeded()
    assert seeded == {k: v for k, v in DEFAULT_ALTERNATES.items() if k in seeded}


def test_a_courier_the_seed_migration_never_saw_is_seeded_by_a_later_one():
    """
    Otherwise the matrix in the code is the only place it exists, and every zone
    already in the database publishes with `[]` — "these orders may not be moved
    anywhere", which is the outage `125` was written to prevent, reintroduced by
    adding a courier.

    Named by file rather than parsed, because the point is that a human decided
    where the new courier's rows come from and wrote it down.
    """
    later = {
        "slider": "128_slider_zones.py",
    }
    for provider in set(DEFAULT_ALTERNATES) - set(_seeded()):
        assert provider in later, (
            f"{provider} is in DEFAULT_ALTERNATES and in no migration — every "
            "zone naming it will publish with no alternates at all"
        )
        migration = MIGRATION.parent / later[provider]
        assert migration.exists(), migration
        assert "alternate_providers" in migration.read_text(), (
            f"{later[provider]} is supposed to seed {provider}'s alternates "
            "and does not mention the column"
        )


def test_every_courier_has_an_answer():
    """
    A provider missing from the map is a zone whose orders can never be moved,
    which would look exactly like the feature not working.
    """
    assert set(DEFAULT_ALTERNATES) == {p.value for p in FulfilmentProviderEnum}


def test_no_courier_is_its_own_alternate():
    """Moving an order to where it already is is not a move."""
    for preferred, alternates in DEFAULT_ALTERNATES.items():
        assert preferred not in alternates


def test_a_zone_outside_sharjah_is_never_offered_noon_send():
    """
    The rule the shop actually asked for, and the reason this is a map rather
    than "every other courier".

    noon Send cannot cross an emirate boundary and will not carry a run past
    20 km, so a zone we gave to Lalamove is a zone they probably cannot reach.
    `courier_service._dispatch_once` already refuses to fall that way
    automatically; this is the same refusal on the manual path.

    Slider is in the same position by default — five of its six zones are
    outside Sharjah. `Sharjah Core` is the sixth and is the one place noon Send
    is a real answer, which a per-provider matrix cannot express; that zone's
    row is seeded explicitly by `128_slider_zones`, and it is also exactly what
    the pilot gate falls back to there.
    """
    for preferred in (
        FulfilmentProviderEnum.LALAMOVE.value,
        FulfilmentProviderEnum.SLIDER.value,
    ):
        assert (
            FulfilmentProviderEnum.NOON_SEND.value not in DEFAULT_ALTERNATES[preferred]
        ), preferred


def test_a_noon_send_zone_may_go_either_way():
    """It is inside Sharjah by construction, so both of the others reach it."""
    assert set(DEFAULT_ALTERNATES[FulfilmentProviderEnum.NOON_SEND.value]) == {
        FulfilmentProviderEnum.THIRD_PARTY.value,
        FulfilmentProviderEnum.LALAMOVE.value,
    }
