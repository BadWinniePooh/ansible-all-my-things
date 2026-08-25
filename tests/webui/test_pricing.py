"""What a machine costs, and what the interface refuses to claim.

The arithmetic is Hetzner's: hourly, capped at the server type's monthly
price, per calendar month. The refusals matter as much as the sums -- a
figure invented from a guessed start time or an unpriced location would be
worse than "unknown", because it would look like a number.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from webui import pricing

# One server type as the account prices it: the same size costs different
# amounts in different locations.
SERVER_TYPE = {
    "name": "cx23",
    "prices": [
        {
            "location": "fsn1",
            "price_hourly": {"gross": "0.0104720000000000"},
            "price_monthly": {"gross": "6.5331000000000000"},
        },
        {
            "location": "sin",
            "price_hourly": {"gross": "0.0200000000000000"},
            "price_monthly": {"gross": "12.0000000000000000"},
        },
    ],
}

HEL = pricing.Rates(hourly=0.01, monthly=6.53)


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def test_the_rate_is_the_one_for_the_location_the_machine_runs_in():
    assert pricing.rates_for(SERVER_TYPE, "fsn1").hourly == pytest.approx(0.010472)
    assert pricing.rates_for(SERVER_TYPE, "sin").hourly == pytest.approx(0.02)


def test_a_location_the_type_is_not_priced_in_has_no_rate():
    """Not a fallback to another location's price: a machine somewhere the
    account did not quote is reported as unknown, not as cheap."""
    assert pricing.rates_for(SERVER_TYPE, "ash") == pricing.NO_RATES


def test_a_server_type_the_account_did_not_send_has_no_rate():
    assert pricing.rates_for(None, "fsn1") == pricing.NO_RATES
    assert pricing.rates_for({"name": "cx23"}, "fsn1") == pricing.NO_RATES


def test_the_creation_time_is_read_as_the_account_writes_it():
    assert pricing.parse_timestamp("2026-08-25T11:20:52Z") == utc(2026, 8, 25, 11, 20, 52)


def test_an_unreadable_creation_time_is_not_guessed_at():
    assert pricing.parse_timestamp("last tuesday") is None
    assert pricing.parse_timestamp(None) is None


def test_cost_is_hours_times_the_hourly_rate():
    cost = pricing.month_to_date(utc(2026, 8, 25, 0, 0), utc(2026, 8, 25, 10, 0), HEL)

    assert cost == pytest.approx(0.10)


def test_cost_stops_at_the_monthly_price():
    """The cap is the point: 31 days at €0.01/h would be €7.44, but Hetzner
    stops charging at the monthly price."""
    cost = pricing.month_to_date(utc(2026, 8, 1), utc(2026, 8, 31, 23, 59), HEL)

    assert cost == pytest.approx(6.53)


def test_a_machine_older_than_the_month_is_billed_from_the_first():
    """The bill resets on the first, so June's machine does not carry June
    into August's total."""
    cost = pricing.month_to_date(utc(2026, 6, 10), utc(2026, 8, 2), HEL)

    assert cost == pytest.approx(pricing.cost_between(utc(2026, 8, 1), utc(2026, 8, 2), HEL))


def test_the_projection_runs_the_clock_to_the_end_of_the_month():
    projected = pricing.projected_month_end(utc(2026, 8, 25), utc(2026, 8, 25, 12), HEL)
    so_far = pricing.month_to_date(utc(2026, 8, 25), utc(2026, 8, 25, 12), HEL)

    assert projected > so_far
    month_end = pricing.month_end(utc(2026, 8, 25))
    assert projected == pytest.approx(pricing.cost_between(utc(2026, 8, 25), month_end, HEL))


def test_a_projection_is_capped_like_everything_else():
    assert pricing.projected_month_end(utc(2026, 8, 1), utc(2026, 8, 2), HEL) == pytest.approx(6.53)


def test_nothing_is_computed_without_a_start_or_a_rate():
    assert pricing.month_to_date(None, utc(2026, 8, 25), HEL) is None
    assert pricing.month_to_date(utc(2026, 8, 1), utc(2026, 8, 25), pricing.NO_RATES) is None


def test_an_unpriced_month_still_bills_by_the_hour():
    """A type quoted hourly but with no monthly cap is charged by the hour.
    Better than refusing: the hourly rate is the one that was quoted."""
    uncapped = pricing.Rates(hourly=0.01, monthly=None)

    assert pricing.month_to_date(utc(2026, 8, 1), utc(2026, 8, 31), uncapped) == pytest.approx(7.20)


def test_the_month_boundaries_are_the_calendar_ones():
    assert pricing.month_start(utc(2026, 8, 25, 13, 5)) == utc(2026, 8, 1)
    assert pricing.month_end(utc(2026, 2, 10)).day == 28  # 2026 is not a leap year
    assert pricing.month_end(utc(2026, 8, 10)).day == 31
