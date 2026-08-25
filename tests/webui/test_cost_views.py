"""The bill as the interface reports it: a card on the dashboard, a screen
behind it, and a machine count in the sidebar of every page.

The month arithmetic is the part worth pinning. A machine destroyed on the
tenth still belongs to that month's figure, a machine running since March
is capped again every month, and a month with nothing in it is a real zero
rather than a gap.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from webui import config, costs, hcloud_api, inventory, pool, pricing, seed, vault
from webui.app import SESSION_COOKIE_NAME, app, secret_store

CX23 = pricing.Rates(hourly=0.0104720, monthly=6.5331)
CX33 = pricing.Rates(hourly=0.0124, monthly=10.10)


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def machine(name, created, rates=CX23, size="cx23", location="hel1"):
    return inventory.Machine(
        name=name,
        address="1.2.3.4",
        profile="basic",
        server_type=size,
        location=location,
        status="running",
        created_at=created,
        rates=rates,
    )


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "costs.sqlite3"
    monkeypatch.setattr(config, "COST_DB_FILE", path)
    return path


# --------------------------------------------------------------------
# The arithmetic


def test_a_month_counts_a_machine_that_no_longer_exists(ledger):
    """The whole reason the ledger is written: the account has forgotten
    this machine, and the invoice has not."""
    costs.record_running([machine("shire", utc(2026, 6, 1))], utc(2026, 6, 2), database=ledger)
    costs.close_missing(set(), utc(2026, 6, 11), database=ledger)

    (june,) = [m for m in costs.month_totals(utc(2026, 8, 25), database=ledger) if m.month == 6]

    assert june.total == pytest.approx(pricing.cost_between(utc(2026, 6, 1), utc(2026, 6, 11), CX23))


def test_a_long_running_machine_is_capped_again_every_month(ledger):
    costs.record_running([machine("mordor", utc(2026, 5, 1))], utc(2026, 8, 25), database=ledger)

    months = costs.month_totals(utc(2026, 8, 25), database=ledger)
    finished = [month for month in months if not month.is_current and month.total > 0]

    assert finished, "a machine running since May should have cost something in June and July"
    for month in finished:
        assert month.total == pytest.approx(CX23.monthly)


def test_a_month_with_nothing_in_it_is_a_real_zero(ledger):
    months = costs.month_totals(utc(2026, 8, 25), months=3, database=ledger)

    assert [month.total for month in months] == [0.0, 0.0, 0.0]
    assert [month.label for month in months] == ["Jun 2026", "Jul 2026", "Aug 2026"]


def test_the_current_month_carries_a_projection_and_the_others_do_not(ledger):
    costs.record_running([machine("mordor", utc(2026, 8, 20))], utc(2026, 8, 25), database=ledger)

    months = costs.month_totals(utc(2026, 8, 25, 12), database=ledger)

    assert months[-1].is_current
    assert months[-1].projected > months[-1].total
    assert all(not month.is_current for month in months[:-1])


def test_this_month_is_broken_down_dearest_first(ledger):
    costs.record_running(
        [
            machine("cheap", utc(2026, 8, 25, 6)),
            machine("dear", utc(2026, 8, 1), rates=CX33, size="cx33"),
        ],
        utc(2026, 8, 25, 12),
        database=ledger,
    )

    breakdown = costs.machine_costs_this_month(utc(2026, 8, 25, 12), database=ledger)

    assert [life.name for life, _ in breakdown] == ["dear", "cheap"]


def test_the_hourly_rate_covers_what_is_still_running(ledger):
    costs.record_running(
        [machine("a", utc(2026, 8, 1)), machine("b", utc(2026, 8, 1), rates=CX33)],
        utc(2026, 8, 2),
        database=ledger,
    )

    assert costs.hourly_now(database=ledger) == pytest.approx(0.0104720 + 0.0124)

    costs.close_missing({"a"}, utc(2026, 8, 3), database=ledger)
    assert costs.hourly_now(database=ledger) == pytest.approx(0.0104720)


def test_nothing_running_is_not_zero_an_hour(ledger):
    """€0.00/h would read as "these machines are free"; nothing running is
    a different statement."""
    assert costs.hourly_now(database=ledger) is None


# --------------------------------------------------------------------
# The screens


@pytest.fixture(autouse=True)
def volume_state(monkeypatch):
    monkeypatch.setattr(
        pool, "status", lambda **kwargs: pool.PoolStatus(entries=["mordor"], used=["mordor"], free=[])
    )
    monkeypatch.setattr(seed, "needs_defaults_refresh", lambda: False)
    monkeypatch.setattr(hcloud_api, "list_servers", lambda token: [])
    monkeypatch.setattr(inventory, "list_machines", lambda **kwargs: [])
    # The vault screen is one of the pages the sidebar is checked on, and
    # its template lives in the container's volume.
    monkeypatch.setattr(vault, "read_vault", lambda password, **kwargs: {})
    monkeypatch.setattr(vault, "load_template", lambda **kwargs: {"vault_desktop_users": []})


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def unlocked(client):
    client.get("/")
    session_id = secret_store.verify(client.cookies[SESSION_COOKIE_NAME])
    secret_store.unlock(session_id, hcloud_token="token", vault_password="password")
    return session_id


@pytest.fixture
def two_machines(ledger):
    now = datetime.now(timezone.utc)
    costs.record_running(
        [
            machine("mordor", now - timedelta(days=6)),
            machine("edoras", now - timedelta(days=2), rates=CX33, size="cx33", location="nbg1"),
        ],
        now,
        database=ledger,
    )
    return ledger


def test_the_dashboard_carries_the_bill_beside_the_name_pool(client, unlocked, two_machines):
    body = client.get("/").text

    assert 'id="cost-card"' in body
    assert "this month so far" in body
    # The hourly rate is not here: it rides in the sidebar badge, where it
    # is a fact in the corner of the eye rather than a figure competing
    # with the month's total.
    assert "running now" not in body
    # The card is in the row with the pool, not a section of its own.
    row = body.split('<div class="columns">')[1]
    assert 'id="cost-card"' in row
    assert "Name pool" in row


def test_the_sidebar_counts_the_machines_on_every_page(client, unlocked, two_machines):
    for path in ("/", "/vault", "/pool", "/costs"):
        body = client.get(path).text
        foot = body.split('class="sidebar-foot"')[1].split("</div>")[0]
        assert "2 machines" in foot, path
        # ...and what they are costing while they run.
        assert "/h" in foot, path


def test_the_badge_carries_no_rate_when_nothing_is_running(client, ledger):
    body = client.get("/").text
    foot = body.split('class="sidebar-foot"')[1].split("</div>")[0]

    assert "0 machines" in foot
    assert "/h" not in foot


def test_the_count_reads_as_one_machine_when_there_is_one(client, ledger):
    now = datetime.now(timezone.utc)
    costs.record_running([machine("mordor", now - timedelta(days=1))], now, database=ledger)

    body = client.get("/").text

    assert "1 machine" in body
    assert "1 machines" not in body


def test_the_costs_screen_shows_the_breakdown_and_every_month(client, two_machines):
    body = client.get("/costs").text

    assert "Running now" in body
    assert "mordor" in body and "edoras" in body
    assert "Every month" in body
    assert "/h" in body


def test_the_costs_screen_reads_with_the_session_locked(client, two_machines):
    """It reads the ledger, never the account: what a machine cost is not a
    secret, and the machines it is about may not exist any more."""
    assert client.get("/costs").status_code == 200


def test_every_figure_says_what_it_leaves_out(client, unlocked, two_machines):
    for path in ("/", "/costs"):
        body = client.get(path).text
        assert "primary IPv4" in body, path
        assert "never lower" in body, path
