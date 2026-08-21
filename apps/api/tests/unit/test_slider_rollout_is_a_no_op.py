"""
Publishing the Slider map must change nothing for anybody but the pilot account.

That is the whole safety argument for shipping the map and the code before the
courier is proven, and it is easy to break in ways nothing else notices, because
every one of them is a *quiet* change: a promise that got shorter, a run that
stopped collecting, a fee that moved. The routing itself is covered by
`test_courier_routing`; this file covers the three things around it that are
properties of the migrations rather than of any function.

Read out of the migration files rather than restated here, so a change to one of
them fails this rather than agreeing with it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"

MAP_MIGRATION = VERSIONS / "126_cost_banded_map_v2.py"
COURIER_MIGRATION = VERSIONS / "127_slider_courier.py"
ROUTING_MIGRATION = VERSIONS / "128_slider_zones.py"

#: The promise noon Send carries, set by `108`. Inside Sharjah it is the courier
#: that actually carries every order but the pilot account's.
NOON_SEND_PROMISE_MINUTES = 90


def _literal(path: Path, name: str, annotation: str):
    """One list literal out of a migration, with its named constants resolved.

    `ast.literal_eval` rather than `exec`, so a migration that grew an import or
    a side effect cannot run it here — and the two fee constants substituted by
    hand, because they are the only names the literals reference.
    """
    source = path.read_text()
    start = source.index("= [", source.index(f"{name}: {annotation}")) + 2
    end = source.index("\n]", start) + 2
    return ast.literal_eval(
        source[start:end]
        .replace("OUTER_FEE", '"80.00"')
        .replace("OUTER_THRESHOLD", '"200.00"')
    )


def _batch_groups() -> dict[str, str]:
    """zone -> the run it rides, out of the map migration."""
    source = MAP_MIGRATION.read_text()
    start = (
        source.index("= {", source.index("BATCH_GROUPS: dict[str, tuple[str, ...]]"))
        + 2
    )
    end = source.index("\n}", start) + 2
    return {
        zone: group
        for group, zones in ast.literal_eval(source[start:end]).items()
        for zone in zones
    }


def _routing_rows() -> list[tuple[str, str, str, str]]:
    """(zone, courier it was on, alternates after, alternates on the way back)."""
    return _literal(ROUTING_MIGRATION, "ZONES", "list[tuple[str, str, str, str]]")


def _moved_to_slider() -> dict[str, str]:
    """zone -> the courier it was on before, out of the routing migration."""
    return {name: was for name, was, *_ in _routing_rows()}


def test_a_zone_moved_to_slider_keeps_the_run_it_was_riding():
    """
    A grouped zone is promised its group's next window close; an ungrouped one
    is promised its courier's own answer. So detaching `Ajman City` or a Dubai
    band from its run would change what every customer in it is told — from
    "after the next run leaves" to "within the hour" — while their orders
    carried on riding that run, because `batching_service.reserve` asks who is
    *actually* carrying the order and gets Lalamove for everyone off the list.

    The group is not a schedule Slider rides. It is the schedule the zone's
    fallback rides, and it has to survive for as long as the fallback carries
    almost every order in the zone.
    """
    groups = _batch_groups()
    routing = ROUTING_MIGRATION.read_text()

    for zone, was in _moved_to_slider().items():
        if was != "lalamove":
            # `Sharjah Core` came off noon Send, which is unbatched and rides
            # nothing. There is no run for it to keep.
            assert zone not in groups, zone
            continue
        assert zone in groups, (
            f"{zone} was on a Lalamove run and the routing migration must not "
            "quietly take it off one"
        )

    assert "batch_group_id" not in routing, (
        "127 may change `fulfilment_provider` and nothing else — a zone's run "
        "is what its promise is read off, and the fallback still rides it"
    )


def test_slider_never_promises_sooner_than_the_courier_that_actually_carries_it():
    """
    The promise is read off the **zone's** declared courier, while the pilot
    gate hands all but one account back to somebody else. Sixty minutes is
    Slider's own figure and is what this row should say once the gate comes off;
    saying it today would tell every customer in `Sharjah Core` their cake
    arrives within the hour and then hand it to noon Send, who promise ninety.

    Under-promising is the safe direction, and correcting it later is one edit
    in Admin → Delivery → Couriers rather than another migration.
    """
    source = COURIER_MIGRATION.read_text()
    match = re.search(
        r"VALUES \(:code, 'Slider', (?P<batching>\w+), '(?P<kind>\w+)', (?P<minutes>\d+)\)",
        source,
    )
    assert match, "126 no longer inserts a recognisable Slider courier row"

    assert match["kind"] == "minutes"
    assert int(match["minutes"]) >= NOON_SEND_PROMISE_MINUTES, (
        "a Slider zone inside Sharjah falls back to noon Send, so promising "
        "sooner than noon Send do is promising something we will miss"
    )
    assert match["batching"] == "false", (
        "Slider has no multi-stop product, and this flag is what stops an admin "
        "attaching a schedule to a Slider zone"
    )


def test_the_routing_migration_is_guarded_against_the_admin():
    """
    Every update names the provider it means to replace as well as the zone, and
    touches only the active map. Once somebody moves one of these in the console
    the migration matches nothing and does nothing — including on a database
    restored from an older dump.
    """
    source = ROUTING_MIGRATION.read_text()

    for clause in ("p.fulfilment_provider = :was", "v.is_active"):
        assert clause in source, f"the upgrade is not guarded on {clause}"
    assert source.count("v.is_active") >= 2, "the downgrade needs the same guard"


def test_every_zone_the_routing_migration_names_exists_on_the_published_map():
    """A zone this cannot find is a courier change that silently did not happen."""
    published = {
        name
        for name, *_ in _literal(
            MAP_MIGRATION, "ZONES", "list[tuple[str, str, str, str, bool]]"
        )
    }
    missing = sorted(set(_moved_to_slider()) - published)
    assert not missing, missing
