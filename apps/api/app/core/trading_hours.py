"""
When the shop is open, on its own clock.

One definition, shared. The dispatcher needs it to decide whether retrying a
failed booking is worth anything at 3am; the delivery promise needs it to know
that an order placed at 23:30 against a 23:00 close cannot be baked until
tomorrow. Two answers to "is the kitchen open" is how a customer gets told
"tomorrow" for something that will not be started until the day after.

Hours are `"HH:MM"` strings on the branch, and they are read in `Asia/Dubai`
because that is the clock the staff and the customer are both standing on.

**Closed days belong here too.** A branch holiday is not a different question
from "is the kitchen open" — it is the same question answered for a whole day,
and putting it anywhere else would give the dispatcher and the customer's
promise two calendars to disagree about. Every function that can be affected by
one takes `closed_dates`: a set of `YYYY-MM-DD` strings, empty by default, so a
caller that has no opinion about holidays behaves exactly as it did before this
existed.

The shop trades seven days a week, so there is deliberately no weekday rule
anywhere in this module. A day is closed because somebody wrote it down.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Collection
from zoneinfo import ZoneInfo

from app.models.delivery_batch import DELIVERY_TIMEZONE

__all__ = [
    "TZ",
    "at_minute",
    "first_open_day",
    "is_closed",
    "local",
    "minutes_of",
    "next_opening",
    "is_open",
    "is_after_close",
    "trading_date",
]

TZ = ZoneInfo(DELIVERY_TIMEZONE)

#: How far forward `first_open_day` and `next_opening` will walk before giving
#: up and returning the day they were asked about.
#:
#: A guard against configuration, not against arithmetic: nothing stops an
#: admin from marking a whole year closed, and a checkout that hangs looking
#: for an opening is a worse failure than a promise made on a shut day. Two
#: months is longer than any real run of closures and short enough to be
#: bounded.
MAX_CLOSED_RUN = 62


def local(moment: datetime) -> datetime:
    """The same instant, read on the shop's clock."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(TZ)


def minutes_of(clock: str | None) -> int | None:
    """`"HH:MM"` as a minute of the day, or None if it is not that."""
    if not clock:
        return None
    try:
        hour, _, minute = clock.partition(":")
        total = int(hour) * 60 + int(minute)
    except ValueError:
        return None
    return total if 0 <= total <= 1440 else None


def at_minute(day: date, minute: int) -> datetime:
    """
    A minute-of-day on a local date, as a real instant.

    Minute 1440 is the midnight that closes the day — 00:00 the next morning —
    written that way rather than as 23:59 so a window ending at 24:00 lands on
    midnight exactly and not a minute early.
    """
    return datetime.combine(
        day + timedelta(days=minute // 1440),
        time(hour=(minute % 1440) // 60, minute=minute % 60),
        tzinfo=TZ,
    )


def is_closed(day: date, closed_dates: Collection[str] = ()) -> bool:
    """Whether `day` is one the branch does not trade at all."""
    return day.isoformat() in closed_dates


def first_open_day(day: date, closed_dates: Collection[str] = ()) -> date:
    """
    The first date at or after `day` that the branch trades.

    `day` itself when it is not closed, which is the ordinary answer — closures
    are exceptions and most days have none.
    """
    if not closed_dates:
        return day
    candidate = day
    for _ in range(MAX_CLOSED_RUN):
        if not is_closed(candidate, closed_dates):
            return candidate
        candidate += timedelta(days=1)
    return day


def trading_date(moment: datetime, opens_at: str | None, closes_at: str | None) -> date:
    """
    Which trading day this instant belongs to.

    The calendar date, except on a branch whose day runs past midnight: a
    kitchen open 09:00–02:00 is still working Tuesday's shift at 01:00 on
    Wednesday, so the closure that matters at that moment is Tuesday's. Without
    this a holiday would shut the wrong end of the night — it would close the
    small hours that belong to the day *before* it and leave open the small
    hours that belong to the holiday itself.

    Hours that cannot be read fall back to the plain calendar date, matching
    the always-open reading of `is_open`.
    """
    here = local(moment)
    opens, closes = minutes_of(opens_at), minutes_of(closes_at)
    if opens is None or closes is None or closes > opens:
        return here.date()
    minute = here.hour * 60 + here.minute
    return here.date() - timedelta(days=1) if minute < closes else here.date()


def is_open(
    moment: datetime,
    opens_at: str | None,
    closes_at: str | None,
    closed_dates: Collection[str] = (),
) -> bool:
    """
    Whether the branch is trading at this instant.

    Half-open, the same reading a batch window uses, and the same tolerance for
    a day that runs past midnight: a kitchen open 09:00–02:00 is open at 01:00.

    A closed day answers False whatever the hours say — a holiday is the day
    being gone, not a narrower version of it.

    Unparseable hours are treated as **always open**. A promise that is slightly
    too optimistic because a branch record has a typo in it beats a shop that
    silently stops quoting delivery. A holiday still shuts such a branch: that
    date was written down by a person and is not in doubt.
    """
    if is_closed(trading_date(moment, opens_at, closes_at), closed_dates):
        return False
    opens, closes = minutes_of(opens_at), minutes_of(closes_at)
    if opens is None or closes is None:
        return True
    minute = local(moment).hour * 60 + local(moment).minute
    if closes <= opens:  # trades past midnight
        return minute >= opens or minute < closes
    return opens <= minute < closes


def is_after_close(
    moment: datetime, opens_at: str | None, closes_at: str | None
) -> bool:
    """
    Whether today's trading has already finished at this instant.

    Deliberately narrower than `not is_open`. A moment *before* opening is also
    not open, and the two want opposite answers: an order at 07:00 against a
    09:00–23:00 day is still for today, while one at 23:30 is not. Only the
    second should push a next-day promise out by a day.

    A day that runs past midnight has no evening "after close" at all — 23:30 on
    a 09:00–02:00 kitchen is still trading, and the close it eventually reaches
    belongs to tomorrow's date.

    Deliberately knows nothing about closed days. This answers "has today's
    trading finished", and on a holiday there was none to finish; the caller
    that cares — rule 3 of the delivery promise — walks its base day forward to
    the next open one afterwards, which covers a closure whatever this says.
    Teaching it about holidays would make it a second, differently-shaped
    version of that walk.
    """
    opens, closes = minutes_of(opens_at), minutes_of(closes_at)
    if closes is None:
        return False
    if opens is not None and closes <= opens:
        # Trades past midnight: after the close (02:00) but before the open
        # (09:00) is the gap, and that is the only "after close" there is.
        here = local(moment)
        minute = here.hour * 60 + here.minute
        return closes <= minute < opens
    here = local(moment)
    return here.hour * 60 + here.minute >= closes


def next_opening(
    moment: datetime, opens_at: str | None, closed_dates: Collection[str] = ()
) -> datetime:
    """
    The next time the branch opens, at or after `moment`.

    Skips closed days: an order placed the evening before a two-day Eid closure
    does not start its clock at nine the next morning, because nobody is there.

    Returns `moment` itself when the hours cannot be read, which keeps the
    always-open reading of `is_open` consistent — a bad branch record must not
    push every promise to an invented opening time. A branch with unreadable
    hours *and* a closure today still gets pushed past the closure, since a
    closed day is a fact somebody wrote down rather than a parse failure.
    """
    opens = minutes_of(opens_at)
    here = local(moment)
    if opens is None:
        if not is_closed(here.date(), closed_dates):
            return moment
        return at_minute(first_open_day(here.date(), closed_dates), 0)
    candidate = at_minute(here.date(), opens)
    if candidate < here:
        candidate = at_minute(here.date() + timedelta(days=1), opens)
    return at_minute(first_open_day(candidate.date(), closed_dates), opens)
