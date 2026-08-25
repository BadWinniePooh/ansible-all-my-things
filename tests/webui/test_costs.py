"""The cost ledger: what it keeps, and what it must never merge.

The account forgets a destroyed server, so the month's estimate can only
include the machine that ran for nine days if this wrote it down while it
still existed.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from webui import costs, inventory, pricing

RATES = pricing.Rates(hourly=0.01, monthly=6.53)


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def machine(name: str, created: datetime, server_type: str = "cx23") -> inventory.Machine:
    return inventory.Machine(
        name=name,
        address="1.2.3.4",
        profile="basic",
        server_type=server_type,
        location="hel1",
        status="running",
        created_at=created,
        rates=RATES,
    )


@pytest.fixture
def ledger(tmp_path):
    return tmp_path / "costs.sqlite3"


def test_a_machine_is_recorded_the_first_time_it_is_seen(ledger):
    costs.record_running([machine("mordor", utc(2026, 8, 25, 11))], utc(2026, 8, 25, 12), database=ledger)

    (life,) = costs.lives(database=ledger)
    assert life.name == "mordor"
    assert life.created_at == utc(2026, 8, 25, 11)
    assert life.rates.hourly == pytest.approx(0.01)
    assert life.is_open


def test_seeing_it_again_does_not_add_a_second_row(ledger):
    for hour in (12, 13, 14):
        costs.record_running(
            [machine("mordor", utc(2026, 8, 25, 11))], utc(2026, 8, 25, hour), database=ledger
        )

    assert len(costs.lives(database=ledger)) == 1


def test_a_resized_machine_is_priced_at_what_it_is_now(ledger):
    created = utc(2026, 8, 25, 11)
    costs.record_running([machine("mordor", created)], utc(2026, 8, 25, 12), database=ledger)

    bigger = machine("mordor", created, server_type="cx43")
    bigger.rates = pricing.Rates(hourly=0.03, monthly=19.03)
    costs.record_running([bigger], utc(2026, 8, 25, 13), database=ledger)

    (life,) = costs.lives(database=ledger)
    assert life.server_type == "cx43"
    assert life.rates.monthly == pytest.approx(19.03)


def test_the_same_name_used_twice_is_two_machines(ledger):
    """The pool hands `edoras` out again a month after the first one was
    destroyed. Merging the two would bill one machine for both lives."""
    costs.record_running([machine("edoras", utc(2026, 7, 1))], utc(2026, 7, 2), database=ledger)
    costs.close_missing(set(), utc(2026, 7, 20), database=ledger)
    costs.record_running([machine("edoras", utc(2026, 8, 5))], utc(2026, 8, 6), database=ledger)

    lives = costs.lives(database=ledger)
    assert len(lives) == 2
    assert [life.is_open for life in lives] == [True, False]


def test_a_machine_the_account_stops_reporting_is_closed_with_its_cost(ledger):
    costs.record_running([machine("mordor", utc(2026, 8, 1))], utc(2026, 8, 2), database=ledger)

    closed = costs.close_missing(set(), utc(2026, 8, 3), database=ledger)

    assert closed == ["mordor"]
    (life,) = costs.lives(database=ledger)
    assert not life.is_open
    assert life.destroyed_at == utc(2026, 8, 3)
    # Two days at a cent an hour, under the monthly cap.
    assert life.final_cost == pytest.approx(0.48)


def test_closing_covers_a_machine_destroyed_outside_this_interface(ledger):
    """Nothing here is told about a destroy from the Hetzner console or the
    command line -- the account simply stops reporting the server, and the
    bill counts it the same way."""
    costs.record_running(
        [machine("mordor", utc(2026, 8, 1)), machine("edoras", utc(2026, 8, 1))],
        utc(2026, 8, 2),
        database=ledger,
    )

    costs.close_missing({"edoras"}, utc(2026, 8, 3), database=ledger)

    still_open = [life.name for life in costs.open_lives(database=ledger)]
    assert still_open == ["edoras"]


def test_a_closed_row_is_not_closed_again(ledger):
    costs.record_running([machine("mordor", utc(2026, 8, 1))], utc(2026, 8, 2), database=ledger)
    costs.close_missing(set(), utc(2026, 8, 3), database=ledger)

    assert costs.close_missing(set(), utc(2026, 8, 9), database=ledger) == []
    (life,) = costs.lives(database=ledger)
    assert life.destroyed_at == utc(2026, 8, 3)


def test_a_machine_with_no_creation_time_is_not_recorded(ledger):
    """Without a start there is no cost to compute, and a row that cannot
    be costed is worse than no row: it would look like a free machine."""
    unknown = machine("mordor", utc(2026, 8, 1))
    unknown.created_at = None

    costs.record_running([unknown], utc(2026, 8, 2), database=ledger)

    assert costs.lives(database=ledger) == []
