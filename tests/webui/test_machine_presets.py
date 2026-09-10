"""Saving a running machine as a preset.

A preset is a set of create-form choices, and until now those choices
could only come from the create form itself -- no help for a machine that
already exists and turned out to be exactly the right shape. What a
machine is lives in two places: the profile in the local records, and the
size, location and image in the Hetzner account. The image lives *only*
there: no local file has ever recorded what a machine was built from.
"""

from __future__ import annotations

import functools

import pytest
from fastapi.testclient import TestClient

from webui import config, hcloud_api, inventory, pool, presets, seed, vault
from webui.app import SESSION_COOKIE_NAME, app, secret_store

TOKEN = "hcloud-token"  # noqa: S105

MACHINE = inventory.Machine(
    name="edoras",
    address="1.2.3.4",
    profile="basic",
    server_type=None,
    location=None,
)

SERVER = {
    "name": "edoras",
    "status": "running",
    "created": "2026-09-01T10:00:00+00:00",
    "server_type": {"name": "cx23", "architecture": "x86", "prices": []},
    "location": {"name": "hel1"},
    "image": {"id": 387894169, "name": "ubuntu-26.04", "description": "Ubuntu 26.04"},
    "public_net": {"ipv4": {"ip": "1.2.3.4"}},
}


@pytest.fixture(autouse=True)
def volume_state(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "COST_DB_FILE", tmp_path / "costs.sqlite3")
    # No record by default: the machine reads as one this interface did not
    # provision, which is the case the inference below has to cover.
    monkeypatch.setattr(
        inventory,
        "recorded_choices",
        functools.partial(inventory.recorded_choices, details_file=tmp_path / "details.json"),
    )
    monkeypatch.setattr(pool, "status", lambda **kwargs: pool.PoolStatus([], [], []))
    monkeypatch.setattr(seed, "needs_defaults_refresh", lambda: False)
    monkeypatch.setattr(inventory, "list_machines", lambda **kwargs: [MACHINE])
    monkeypatch.setattr(hcloud_api, "list_servers", lambda token: [SERVER])
    monkeypatch.setattr(vault, "read_vault", lambda password, **kwargs: {})
    monkeypatch.setattr(vault, "load_template", lambda **kwargs: {})


@pytest.fixture
def presets_file(tmp_path, monkeypatch):
    """presets.py binds config.PRESETS_FILE as a default argument at import
    time, so redirecting the writes means binding the module's own
    functions to tmp_path -- same shape as tests/webui/test_presets.py."""
    path = tmp_path / "presets.json"
    monkeypatch.setattr(config, "PRESETS_FILE", path)
    for name in ("list_presets", "get", "save", "delete", "rename"):
        monkeypatch.setattr(
            presets, name, functools.partial(getattr(presets, name), presets_file=path)
        )
    return path


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def unlocked(client):
    client.get("/")
    session_id = secret_store.verify(client.cookies[SESSION_COOKIE_NAME])
    secret_store.unlock(session_id, hcloud_token=TOKEN, vault_password="password")
    return session_id


def test_a_running_machine_becomes_a_preset(client, unlocked, presets_file):
    response = client.post(
        "/machines/edoras/preset", data={"preset_name": "small-hel"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/presets?done=preset-saved-from-machine"

    (saved,) = presets.list_presets()
    assert saved.name == "small-hel"
    assert saved.profile == "basic"
    assert saved.server_type == "cx23"
    assert saved.location == "hel1"
    # Only the account knows this, which is the whole reason the route
    # reads the account rather than the local records.
    assert saved.image == "ubuntu-26.04"


def test_choices_the_built_in_lists_do_not_hold_are_marked_as_live(
    client, unlocked, presets_file, monkeypatch
):
    """A preset that does not record which catalogue its choices came from
    cannot be restored: the create form would match them against the static
    lists, find them unknown, and substitute defaults."""
    exotic = {
        **SERVER,
        "server_type": {"name": "ccx13"},
        "image": {"id": 1, "name": "rocky-9", "description": "Rocky Linux 9"},
    }
    monkeypatch.setattr(hcloud_api, "list_servers", lambda token: [exotic])

    client.post("/machines/edoras/preset", data={"preset_name": "exotic"})

    (saved,) = presets.list_presets()
    assert saved.server_types_live is True
    assert saved.images_live is True


def test_built_in_choices_are_not_marked_as_live(client, unlocked, presets_file):
    client.post("/machines/edoras/preset", data={"preset_name": "small-hel"})

    (saved,) = presets.list_presets()
    assert saved.server_types_live is False
    assert saved.images_live is False


def test_the_catalogue_the_machine_was_ordered_from_is_kept(
    client, unlocked, presets_file, tmp_path, monkeypatch
):
    """A cx23 picked from the full live catalogue is still a cx23 in the
    account, so nothing about the machine can say which list it came from.
    Only what was written down when the run started can, and a preset that
    gets it wrong loads against a different list than its choices came
    from."""
    details = tmp_path / "details.json"
    inventory.record_choices(
        "edoras",
        {
            "profile": "basic",
            "server_type": "cx23",
            "location": "hel1",
            "image": "ubuntu-26.04",
            "server_types_live": True,
            "images_live": True,
        },
        details_file=details,
    )
    monkeypatch.setattr(
        inventory,
        "recorded_choices",
        functools.partial(inventory.recorded_choices, details_file=details),
    )

    client.post("/machines/edoras/preset", data={"preset_name": "small-hel"})

    (saved,) = presets.list_presets()
    assert saved.server_type == "cx23"
    assert saved.server_types_live is True
    assert saved.images_live is True


def test_a_taken_name_is_refused_rather_than_overwritten(client, unlocked, presets_file):
    client.post("/machines/edoras/preset", data={"preset_name": "small-hel"})

    response = client.post("/machines/edoras/preset", data={"preset_name": "small-hel"})

    assert response.status_code == 409
    assert "already exists" in response.text
    assert len(presets.list_presets()) == 1


def test_a_nameless_preset_is_refused(client, unlocked, presets_file):
    response = client.post("/machines/edoras/preset", data={"preset_name": "   "})

    assert response.status_code == 400
    assert presets.list_presets() == []


def test_saving_needs_the_account(client, presets_file):
    """The size, location and image are read from Hetzner, so a locked
    token cannot produce a preset -- and must say that rather than saving
    one with holes in it."""
    response = client.post("/machines/edoras/preset", data={"preset_name": "small-hel"})

    assert response.status_code == 400
    assert "API token must be unlocked" in response.text
    assert presets.list_presets() == []


def test_a_machine_this_installation_does_not_manage_is_refused(client, unlocked, presets_file):
    response = client.post("/machines/mordor/preset", data={"preset_name": "whatever"})

    assert response.status_code == 404
    assert presets.list_presets() == []


def test_a_machine_the_account_cannot_describe_names_what_is_missing(
    client, unlocked, presets_file, monkeypatch
):
    """A snapshot has no image name -- only a description, which nothing
    can provision from. Saving that as a preset would produce one that
    silently builds a different machine."""
    snapshot_built = {**SERVER, "image": {"id": 5, "name": None, "description": "my snapshot"}}
    monkeypatch.setattr(hcloud_api, "list_servers", lambda token: [snapshot_built])

    response = client.post("/machines/edoras/preset", data={"preset_name": "from-snapshot"})

    assert response.status_code == 400
    assert "image" in response.text
    assert presets.list_presets() == []


def test_the_dashboard_offers_the_action_on_every_row(client, unlocked, presets_file):
    body = client.get("/").text

    assert 'action="/machines/edoras/preset"' in body
    assert 'value="edoras"' in body
