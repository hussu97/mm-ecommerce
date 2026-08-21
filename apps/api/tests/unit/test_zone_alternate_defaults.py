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


def test_the_code_and_the_migration_agree():
    """The whole point of the file. Two copies, one meaning."""
    import json

    seeded = {preferred: json.loads(alts) for preferred, alts in _migration().DEFAULTS}
    assert seeded == DEFAULT_ALTERNATES


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


def test_a_lalamove_zone_is_never_offered_noon_send():
    """
    The rule the shop actually asked for, and the reason this is a map rather
    than "every other courier".

    noon Send cannot cross an emirate boundary and will not carry a run past
    20 km, so a zone we gave to Lalamove is a zone they probably cannot reach.
    `courier_service._dispatch_once` already refuses to fall that way
    automatically; this is the same refusal on the manual path.
    """
    assert (
        FulfilmentProviderEnum.NOON_SEND.value
        not in DEFAULT_ALTERNATES[FulfilmentProviderEnum.LALAMOVE.value]
    )


def test_a_noon_send_zone_may_go_either_way():
    """It is inside Sharjah by construction, so both of the others reach it."""
    assert set(DEFAULT_ALTERNATES[FulfilmentProviderEnum.NOON_SEND.value]) == {
        FulfilmentProviderEnum.THIRD_PARTY.value,
        FulfilmentProviderEnum.LALAMOVE.value,
    }
