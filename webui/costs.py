"""The cost ledger: when each machine appeared, and what it had cost when
it went away.

The account answers what a machine costs *now*, but it forgets a server the
moment it is destroyed -- and the end-of-month figure has to include the
machine that ran for nine days and was destroyed on the tenth. So the
interface keeps its own record: one row per life of a machine, opened the
first time it is seen and closed when the account stops reporting it.

**Why SQLite and not a Postgres container.** This holds a handful of rows
per month for one operator, is read on a page render, and has no second
writer. SQLite is in the Python standard library, needs no service, no
port, no credential, no second image to publish, and lands in the volume
that is already the one thing to back up -- while a Postgres container
would add all of those to a feature whose whole data set fits in a few
kilobytes (constitution Principle IV, YAGNI). Everything that talks to
storage is in this module, so if this installation ever grows a second
writer or an operator who wants the data queryable from outside, swapping
the four functions below is the whole change.

Timestamps are stored as UTC ISO-8601 strings: sortable as text, readable
in a `sqlite3` shell, and unambiguous across the restart of a container
whose timezone nobody set.
"""

from __future__ import annotations

import sqlite3
from calendar import month_name
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import config, pricing

SCHEMA = """
CREATE TABLE IF NOT EXISTS machine_life (
    name          TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    server_type   TEXT,
    location      TEXT,
    hourly_gross  REAL,
    monthly_gross REAL,
    destroyed_at  TEXT,
    final_cost    REAL,
    PRIMARY KEY (name, created_at)
);
"""


@dataclass(frozen=True)
class Life:
    """One machine, from the first time it was seen to the last."""

    name: str
    created_at: datetime
    server_type: str | None
    location: str | None
    rates: pricing.Rates
    destroyed_at: datetime | None
    final_cost: float | None

    @property
    def is_open(self) -> bool:
        return self.destroyed_at is None


@contextmanager
def _connect(database: Path | None = None):
    path = database or config.COST_DB_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(SCHEMA)
        yield connection
        connection.commit()
    finally:
        connection.close()


def _row_to_life(row: sqlite3.Row) -> Life:
    return Life(
        name=row["name"],
        created_at=pricing.parse_timestamp(row["created_at"]),
        server_type=row["server_type"],
        location=row["location"],
        rates=pricing.Rates(hourly=row["hourly_gross"], monthly=row["monthly_gross"]),
        destroyed_at=pricing.parse_timestamp(row["destroyed_at"]),
        final_cost=row["final_cost"],
    )


def record_running(
    machines: list, now: datetime, *, database: Path | None = None
) -> None:
    """Open a row for every machine the account currently reports.

    Keyed on (name, created_at) rather than on the name alone, because a
    name comes back: the pool hands out `edoras` again a month after the
    first one was destroyed, and the two are different machines whose costs
    must not be merged into one row. Re-seeing a machine refreshes what it
    is -- a resized server is priced at what it is now, from now on.
    """
    with _connect(database) as connection, closing(connection.cursor()) as cursor:
        for machine in machines:
            if machine.created_at is None:
                continue
            cursor.execute(
                """
                INSERT INTO machine_life
                    (name, created_at, server_type, location, hourly_gross, monthly_gross)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name, created_at) DO UPDATE SET
                    server_type = excluded.server_type,
                    location = excluded.location,
                    hourly_gross = excluded.hourly_gross,
                    monthly_gross = excluded.monthly_gross
                """,
                (
                    machine.name,
                    machine.created_at.astimezone(timezone.utc).isoformat(),
                    machine.server_type,
                    machine.location,
                    machine.rates.hourly,
                    machine.rates.monthly,
                ),
            )


def close_missing(
    live_names: set[str], now: datetime, *, database: Path | None = None
) -> list[str]:
    """Close every open row whose machine the account no longer reports.

    Closing here rather than in the destroy route is deliberate: a machine
    destroyed from the command line, from the Hetzner console, or by a run
    this container did not start is just as gone, and the bill counts it
    the same way. The cost is frozen at closing time, because after this
    the account can no longer say what the machine was.
    """
    closed: list[str] = []
    with _connect(database) as connection, closing(connection.cursor()) as cursor:
        cursor.execute("SELECT * FROM machine_life WHERE destroyed_at IS NULL")
        for row in cursor.fetchall():
            life = _row_to_life(row)
            if life.name in live_names:
                continue
            final = pricing.month_to_date(life.created_at, now, life.rates)
            cursor.execute(
                "UPDATE machine_life SET destroyed_at = ?, final_cost = ? "
                "WHERE name = ? AND created_at = ?",
                (
                    now.astimezone(timezone.utc).isoformat(),
                    final,
                    life.name,
                    row["created_at"],
                ),
            )
            closed.append(life.name)
    return closed


def lives(*, database: Path | None = None) -> list[Life]:
    """Every recorded life, newest first."""
    with _connect(database) as connection, closing(connection.cursor()) as cursor:
        cursor.execute("SELECT * FROM machine_life ORDER BY created_at DESC")
        return [_row_to_life(row) for row in cursor.fetchall()]


def open_lives(*, database: Path | None = None) -> list[Life]:
    return [life for life in lives(database=database) if life.is_open]


@dataclass(frozen=True)
class MonthCost:
    """One calendar month's estimate. ``projected`` is only ever set on the
    month that has not finished yet."""

    year: int
    month: int
    total: float
    projected: float | None = None

    @property
    def label(self) -> str:
        return f"{month_name[self.month][:3]} {self.year}"

    @property
    def long_label(self) -> str:
        return f"{month_name[self.month]} {self.year}"

    @property
    def is_current(self) -> bool:
        return self.projected is not None


def _window(life: Life, start: datetime, end: datetime, now: datetime) -> tuple[datetime, datetime]:
    """The part of this month the machine was actually alive for, never
    running past now -- a month is not billed before it happens."""
    began = max(life.created_at, start)
    ended = min(life.destroyed_at or now, end, now)
    return began, ended


def cost_in_month(life: Life, year: int, month: int, now: datetime) -> float:
    """One machine's share of one month.

    The cap is applied to this window rather than to the machine's whole
    life, because that is how Hetzner charges: a server running since March
    is capped again every month.
    """
    if life.created_at is None:
        return 0.0
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    began, ended = _window(life, start, pricing.month_end(start), now)
    if ended <= began:
        return 0.0
    return pricing.cost_between(began, ended, life.rates) or 0.0


def _projected_in_month(life: Life, year: int, month: int, now: datetime) -> float:
    """What this machine will have cost by month end if nothing changes --
    which for a machine already destroyed is simply what it cost."""
    if life.created_at is None:
        return 0.0
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = pricing.month_end(start)
    began = max(life.created_at, start)
    ended = min(life.destroyed_at or end, end)
    if ended <= began:
        return 0.0
    return pricing.cost_between(began, ended, life.rates) or 0.0


def month_totals(now: datetime, *, months: int = 6, database: Path | None = None) -> list[MonthCost]:
    """The last few months, oldest first, ending with the current one.

    Months with no machines are included as a real zero rather than left
    out: an empty month is an answer, and a chart with a gap in it invites
    the reader to interpolate across a month that genuinely cost nothing.
    """
    recorded = lives(database=database)
    current = pricing.month_start(now)
    result: list[MonthCost] = []
    for offset in range(months - 1, -1, -1):
        year, month = _shift_month(current.year, current.month, -offset)
        total = sum(cost_in_month(life, year, month, now) for life in recorded)
        projected = None
        if (year, month) == (current.year, current.month):
            projected = sum(_projected_in_month(life, year, month, now) for life in recorded)
        result.append(MonthCost(year=year, month=month, total=total, projected=projected))
    return result


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = (year * 12 + (month - 1)) + delta
    return index // 12, index % 12 + 1


def machine_costs_this_month(
    now: datetime, *, database: Path | None = None
) -> list[tuple[Life, float]]:
    """Every machine that cost anything this month, dearest first --
    including the ones that no longer exist, which is the whole reason the
    ledger is written."""
    current = pricing.month_start(now)
    costed = [
        (life, cost_in_month(life, current.year, current.month, now))
        for life in lives(database=database)
    ]
    return sorted(
        [entry for entry in costed if entry[1] > 0], key=lambda entry: entry[1], reverse=True
    )


def machine_names_by_month(
    history: list[MonthCost], now: datetime, *, database: Path | None = None
) -> dict[str, list[str]]:
    """Which machines each month's figure is made of, so a month in the
    table can be read without opening the ledger."""
    recorded = lives(database=database)
    return {
        month.label: sorted(
            {
                life.name
                for life in recorded
                if cost_in_month(life, month.year, month.month, now) > 0
            }
        )
        for month in history
    }


def hourly_now(*, database: Path | None = None) -> float | None:
    """What the machines still running are costing per hour, together.

    None when nothing is running, which is different from €0.00 an hour --
    and different again from a machine whose location the account did not
    price, which contributes nothing and is named on the costs screen.
    """
    running = open_lives(database=database)
    if not running:
        return None
    return sum(life.rates.hourly or 0.0 for life in running)
