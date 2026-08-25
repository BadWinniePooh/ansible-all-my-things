"""Size and location come from the Hetzner account, not only from the
side file this interface writes for machines it provisioned itself.

Anything created from the command line, restored from a backup, or made
before that file existed has no local entry, and used to read "unknown" in
every column but its name.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from webui import hcloud_api, inventory, pool, seed
from webui.app import SESSION_COOKIE_NAME, app, secret_store

TOKEN = "test-token"
PASSWORD = "test-vault-password"

# What the interface knows by itself about a machine it did not provision.
LOCAL_ONLY = inventory.Machine(
    name="edoras", address=None, profile="desktop", server_type=None, location=None
)

SERVERS = [
    {
        "name": "edoras",
        "status": "running",
        "server_type": {"name": "cx33"},
        "datacenter": {"location": {"name": "nbg1"}},
        "public_net": {"ipv4": {"ip": "49.12.113.84"}},
    },
    {
        "name": "not-managed-here",
        "status": "running",
        "server_type": {"name": "cx43"},
        "datacenter": {"location": {"name": "fsn1"}},
        "public_net": {"ipv4": {"ip": "1.2.3.4"}},
    },
]


@pytest.fixture(autouse=True)
def volume_state(monkeypatch):
    monkeypatch.setattr(
        pool,
        "status",
        lambda **kwargs: pool.PoolStatus(entries=["edoras"], used=["edoras"], free=[]),
    )
    monkeypatch.setattr(inventory, "list_machines", lambda **kwargs: [LOCAL_ONLY])
    monkeypatch.setattr(seed, "needs_defaults_refresh", lambda: False)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def unlocked(client):
    client.get("/")
    session_id = secret_store.verify(client.cookies[SESSION_COOKIE_NAME])
    secret_store.unlock(session_id, hcloud_token=TOKEN, vault_password=PASSWORD)
    return session_id


def test_the_account_fills_in_what_the_local_records_never_knew(client, unlocked, monkeypatch):
    monkeypatch.setattr(hcloud_api, "list_servers", lambda token: list(SERVERS))

    body = client.get("/").text

    assert "cx33" in body
    assert "nbg1" in body
    assert "49.12.113.84" in body
    assert "unknown" not in body


def test_a_server_the_installation_does_not_manage_is_not_listed(client, unlocked, monkeypatch):
    """The local records decide which machines this installation is
    responsible for; the account only says what they are."""
    monkeypatch.setattr(hcloud_api, "list_servers", lambda token: list(SERVERS))

    body = client.get("/").text

    assert "not-managed-here" not in body


def test_the_power_state_is_shown_when_the_account_reports_it(client, unlocked, monkeypatch):
    monkeypatch.setattr(
        hcloud_api,
        "list_servers",
        lambda token: [{**SERVERS[0], "status": "off"}],
    )

    body = client.get("/").text

    # Colour is a second signal only, so the state is also written out.
    assert "machine-off" in body
    assert ">off<" in body


def test_a_failed_lookup_says_so_and_still_shows_the_local_records(
    client, unlocked, monkeypatch
):
    def unreachable(token):
        raise hcloud_api.HetznerApiError("503 Service Unavailable")

    monkeypatch.setattr(hcloud_api, "list_servers", unreachable)

    body = client.get("/").text

    assert "Could not read machine details from Hetzner" in body
    assert "503 Service Unavailable" in body
    assert "edoras" in body


def test_the_account_is_not_asked_while_the_token_is_locked(client, monkeypatch):
    def fail(token):  # pragma: no cover - asserted by not being called
        raise AssertionError("the account must not be read without an unlocked token")

    monkeypatch.setattr(hcloud_api, "list_servers", fail)

    assert client.get("/").status_code == 200


def test_merging_keeps_what_only_the_local_records_know():
    """Hetzner has never heard of a profile: it is this project's own idea,
    read from the inventory groups, so a merge must not drop it."""
    merged = inventory.merge_account_detail([LOCAL_ONLY], SERVERS)

    assert merged[0].profile == "desktop"
    assert merged[0].server_type == "cx33"


def test_merging_leaves_a_machine_the_account_does_not_know_alone():
    merged = inventory.merge_account_detail([LOCAL_ONLY], [])

    assert merged == [LOCAL_ONLY]
