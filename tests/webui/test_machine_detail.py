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


@pytest.mark.parametrize(
    "server",
    [
        pytest.param(
            {"datacenter": {"name": "nbg1-dc3", "location": {"name": "nbg1"}}},
            id="documented: nested in the datacenter",
        ),
        pytest.param({"location": {"name": "nbg1"}}, id="reported at the top level"),
        pytest.param({"location": "nbg1"}, id="reported as the name itself"),
    ],
)
def test_the_location_is_read_wherever_the_response_carries_it(server):
    merged = inventory.merge_account_detail([LOCAL_ONLY], [{"name": "edoras", **server}])

    assert merged[0].location == "nbg1"


def test_a_datacenter_without_a_location_block_still_names_the_place():
    """Better than "unknown": it is the right place, just more precisely
    than the location codes the create form offers."""
    merged = inventory.merge_account_detail(
        [LOCAL_ONLY], [{"name": "edoras", "datacenter": {"name": "nbg1-dc3"}}]
    )

    assert merged[0].location == "nbg1-dc3"


def test_an_ipv6_only_machine_still_shows_an_address():
    merged = inventory.merge_account_detail(
        [LOCAL_ONLY], [{"name": "edoras", "public_net": {"ipv6": {"ip": "2a01:4f8::1"}}}]
    )

    assert merged[0].address == "2a01:4f8::1"


def test_a_name_the_account_does_not_know_is_reported_with_what_it_does(
    client, unlocked, monkeypatch
):
    """"unknown" in every column does not say why. A mismatch between the
    machine records and the account is the reason an operator can act on,
    so it is named along with the names the account does hold."""
    monkeypatch.setattr(
        hcloud_api, "list_servers", lambda token: [{**SERVERS[0], "name": "edoras-old"}]
    )

    body = client.get("/").text

    assert "The Hetzner account has no server named edoras" in body
    assert "edoras-old" in body


# One server as GET /servers actually answers it, trimmed of the fields the
# interface does not read. Note what is *not* here: no "datacenter" key at
# all -- the location is reported at the top level, which is what the
# first version of the merge missed and read as "unknown".
REAL_RESPONSE = {
    "id": 163550810,
    "name": "mordor",
    "status": "running",
    "server_type": {
        "id": 114,
        "name": "cx23",
        "architecture": "x86",
        "cores": 2,
        "disk": 40,
        "memory": 4,
        "locations": [{"id": 1, "name": "fsn1"}, {"id": 3, "name": "hel1"}],
    },
    "location": {
        "id": 3,
        "name": "hel1",
        "description": "Helsinki DC Park 1",
        "city": "Helsinki",
        "country": "FI",
        "network_zone": "eu-central",
    },
    "image": {"id": 387894169, "name": "ubuntu-26.04", "description": "Ubuntu 26.04"},
    "public_net": {
        "ipv4": {"id": 146490926, "ip": "204.168.148.135", "blocked": False},
        "ipv6": {"id": 146490927, "ip": "2a01:4f9:c013:2b89::/64", "blocked": False},
    },
}


def test_a_real_server_response_fills_in_every_column(client, unlocked, monkeypatch):
    monkeypatch.setattr(
        inventory,
        "list_machines",
        lambda **kwargs: [
            inventory.Machine(
                name="mordor", address=None, profile="basic", server_type=None, location=None
            )
        ],
    )
    monkeypatch.setattr(hcloud_api, "list_servers", lambda token: [REAL_RESPONSE])

    body = client.get("/").text

    assert "204.168.148.135" in body
    assert "cx23" in body
    assert "hel1 · Helsinki, Finland" in body
    assert "unknown" not in body


def test_list_servers_stops_when_the_account_says_there_is_no_next_page(monkeypatch):
    """The response's own pagination decides, not a guess at how many
    servers an account holds."""
    calls = []

    class Response:
        is_success = True

        def json(self):
            return {
                "servers": [REAL_RESPONSE],
                "meta": {"pagination": {"page": 1, "next_page": None, "total_entries": 1}},
            }

    def get(url, headers=None, params=None, timeout=None):
        calls.append(params)
        return Response()

    monkeypatch.setattr(hcloud_api.httpx, "get", get)

    servers = hcloud_api.list_servers("token")

    assert [server["name"] for server in servers] == ["mordor"]
    assert len(calls) == 1
