"""What a machine costs, from what the account charges for it.

Hetzner bills a server by the hour and stops charging once the hours reach
the server type's monthly price, per calendar month. Both rates come from
the account itself -- ``server_type.prices`` in the GET /servers response
carries them per location, gross -- so a running machine's cost is
arithmetic rather than a guess, and it stays right when Hetzner changes a
price or when the account is billed in a different currency band than the
table in inventories/group_vars/hcloud_linux/vars.yml.

What this cannot see, and therefore never claims: the primary IPv4
address, traffic over the included volume, snapshots, volumes and backups.
Every screen that shows a figure from here says so. The invoice is always a
little higher than this, never lower.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Rates:
    """Gross euro per hour and the monthly cap for one server type in one
    location. Either may be None when the account did not price it."""

    hourly: float | None
    monthly: float | None


NO_RATES = Rates(hourly=None, monthly=None)


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def rates_for(server_type: object, location: str | None) -> Rates:
    """The rates the account charges for this type in this location.

    A server type is priced per location, so the location is part of the
    question: the same size can cost different amounts in Falkenstein and
    Singapore. A type whose price list does not mention the location the
    machine actually runs in is not priced by guesswork -- it answers with
    nothing, and the interface says "unknown" rather than a wrong number.
    """
    if not isinstance(server_type, dict):
        return NO_RATES
    for price in server_type.get("prices") or []:
        if not isinstance(price, dict) or price.get("location") != location:
            continue
        return Rates(
            hourly=_as_float((price.get("price_hourly") or {}).get("gross")),
            monthly=_as_float((price.get("price_monthly") or {}).get("gross")),
        )
    return NO_RATES


def parse_timestamp(value: str | None) -> datetime | None:
    """An ISO-8601 instant as the account writes it ("2026-08-25T11:20:52Z").

    Anything unparsable answers None: a machine whose creation time cannot
    be read has no cost this can compute, which is a better answer than one
    computed from a made-up start.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def month_start(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


def month_end(moment: datetime) -> datetime:
    start = month_start(moment)
    days = monthrange(start.year, start.month)[1]
    return start.replace(day=days, hour=23, minute=59, second=59)


def cost_between(start: datetime, end: datetime, rates: Rates) -> float | None:
    """What the account charges for one machine running from start to end.

    The cap is what makes this more than hours × rate: past roughly 30 days
    of running, Hetzner charges the monthly price and stops. Applying it
    per call is correct because every caller here asks about a window
    inside one calendar month, which is the period the cap resets on.
    """
    if rates.hourly is None:
        return None
    hours = max((end - start).total_seconds(), 0.0) / 3600
    cost = hours * rates.hourly
    if rates.monthly is not None:
        return min(cost, rates.monthly)
    return cost


def month_to_date(created_at: datetime | None, now: datetime, rates: Rates) -> float | None:
    """What this machine has cost since the month began.

    A machine created before this month started billing at zero again on
    the first, so the window opens at whichever is later.
    """
    if created_at is None:
        return None
    start = max(created_at, month_start(now))
    return cost_between(start, now, rates)


def projected_month_end(
    created_at: datetime | None, now: datetime, rates: Rates
) -> float | None:
    """What it will have cost by the end of the month if nothing changes.

    "If nothing changes" is the whole of the forecast: no growth curve, no
    average of past months. A machine that is destroyed tomorrow costs less
    than this, and the screen says the number is a projection.
    """
    if created_at is None:
        return None
    start = max(created_at, month_start(now))
    return cost_between(start, month_end(now), rates)
