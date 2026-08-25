"""The create screen's HTMX fragment routes and the validation guarding a
submission.

Two concerns live together here because they share one fixture set. First,
the fragment routes backing the location refresh and the two live-lookup
toggles (GET /create/locations, /create/server-types, /create/images):
which server types survive filtering, which locations a type offers, and
what happens when the live lookup cannot run. Second, that a submission
missing a required choice — or naming a size/location pair Hetzner cannot
order — is refused at the point of the gap instead of falling back to a
hardcoded default (Principle XII).

Every Hetzner call is stubbed; nothing here reaches the network.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from webui import app as app_module
from webui import hcloud_api
from webui.app import SESSION_COOKIE_NAME, app, secret_store

TOKEN = "hcloud-token"  # noqa: S105
PASSWORD = "vault-password"  # noqa: S105

# Two x86 types with different location coverage, one arm64 type and one
# deprecated type -- the last two must never reach the size table.
LIVE_SERVER_TYPES = [
    {
        "name": "cx23",
        "architecture": "x86",
        "cores": 2,
        "memory": 4,
        "disk": 40,
        "deprecation": None,
        "prices": [
            {"location": "fsn1", "price_monthly": {"gross": "6.53"}},
            {"location": "hel1", "price_monthly": {"gross": "5.99"}},
        ],
    },
    {
        "name": "ccx13",
        "architecture": "x86",
        "cores": 2,
        "memory": 8,
        "disk": 80,
        "deprecation": None,
        "prices": [{"location": "ash", "price_monthly": {"gross": "14.00"}}],
    },
    {
        "name": "cax11",
        "architecture": "arm",
        "cores": 2,
        "memory": 4,
        "disk": 40,
        "deprecation": None,
        "prices": [{"location": "fsn1", "price_monthly": {"gross": "3.79"}}],
    },
    {
        "name": "cx11",
        "architecture": "x86",
        "cores": 1,
        "memory": 2,
        "disk": 20,
        "deprecation": {"unavailable_after": "2026-01-01T00:00:00+00:00"},
        "prices": [{"location": "fsn1", "price_monthly": {"gross": "4.15"}}],
    },
]

LIVE_IMAGES = [
    {"name": "debian-12", "description": "Debian 12"},
    {"name": "rocky-9", "description": "Rocky Linux 9"},
]


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def unlocked(client):
    """Unlock both secrets for this client's session directly, rather than
    through POST /session/unlock -- that route validates the token against
    the real API and writes a vault file, neither of which belongs in a
    route-rendering test."""
    client.get("/create")
    session_id = secret_store.verify(client.cookies[SESSION_COOKIE_NAME])
    secret_store.unlock(session_id, hcloud_token=TOKEN, vault_password=PASSWORD)
    return session_id


@pytest.fixture
def live_catalogue(monkeypatch):
    monkeypatch.setattr(hcloud_api, "list_server_types", lambda token: list(LIVE_SERVER_TYPES))
    monkeypatch.setattr(hcloud_api, "list_images", lambda token: list(LIVE_IMAGES))


@pytest.fixture
def no_network(monkeypatch):
    """Any Hetzner call is a test failure unless a test opts into one."""

    def refuse(*args, **kwargs):
        pytest.fail("the static path must not call the Hetzner API")

    monkeypatch.setattr(hcloud_api, "list_server_types", refuse)
    monkeypatch.setattr(hcloud_api, "list_images", refuse)


# --------------------------------------------------------------------------
# Fragment routes, static source
# --------------------------------------------------------------------------


def test_create_page_renders_the_static_catalogue_without_calling_hetzner(client, no_network):
    response = client.get("/create")

    assert response.status_code == 200
    body = response.text
    assert 'id="server-types-table"' in body
    assert 'id="location-fieldset"' in body
    assert 'id="image-options"' in body
    for server_type in ("cx23", "cx33", "cx43"):
        assert server_type in body
    assert "ubuntu-24.04" in body


def test_locations_fragment_returns_the_location_fieldset(client, no_network):
    response = client.get("/create/locations", params={"server_type": "cx33"})

    assert response.status_code == 200
    assert 'id="location-fieldset"' in response.text
    assert "hel1" in response.text
    # Not an out-of-band swap: this route replaces its own hx-target.
    assert "hx-swap-oob" not in response.text


def test_images_fragment_returns_the_curated_list_by_default(client, no_network):
    response = client.get("/create/images")

    assert response.status_code == 200
    assert 'id="image-options"' in response.text
    assert "ubuntu-24.04" in response.text


def test_server_types_fragment_carries_the_location_fieldset_out_of_band(client, no_network):
    """Toggling the size source can change which size is selected, so the
    location list scoped to it must travel in the same response."""
    response = client.get("/create/server-types")

    assert response.status_code == 200
    assert 'id="server-types-table"' in response.text
    assert 'id="location-fieldset"' in response.text
    assert 'hx-swap-oob="true"' in response.text


# --------------------------------------------------------------------------
# Fragment routes, live source
# --------------------------------------------------------------------------


def test_live_sizes_drop_non_x86_and_deprecated_types(client, unlocked, live_catalogue):
    response = client.get("/create/server-types", params={"server_types_live": "1"})

    assert response.status_code == 200
    body = response.text
    assert "cx23" in body
    assert "ccx13" in body
    assert "cax11" not in body, "arm64 types cannot boot this UI's x86-only images"
    assert "cx11" not in body, "a deprecated type must not be offered"


def test_live_size_price_is_the_cheapest_location(client, unlocked, live_catalogue):
    """cx23 costs 5.99 in hel1 and 6.53 in fsn1; the table shows a 'from'
    figure, so the lower one wins."""
    response = client.get("/create/server-types", params={"server_types_live": "1"})

    assert "5.99" in response.text
    assert "6.53" not in response.text


def test_live_locations_come_from_the_selected_type_prices(client, unlocked, live_catalogue):
    """ccx13 is priced in ash only, so no other location may be offered for
    it even though the static list names six."""
    response = client.get(
        "/create/locations", params={"server_type": "ccx13", "server_types_live": "1"}
    )

    assert response.status_code == 200
    body = response.text
    assert "ash" in body
    for elsewhere in ("fsn1", "hel1", "nbg1", "sin"):
        assert f'value="{elsewhere}"' not in body


def test_live_images_replace_the_curated_list(client, unlocked, live_catalogue):
    response = client.get("/create/images", params={"images_live": "1"})

    assert response.status_code == 200
    assert "debian-12" in response.text
    assert "Rocky Linux 9" in response.text


def test_live_lookup_without_an_unlocked_token_shows_defaults_and_says_why(client, no_network):
    sizes = client.get("/create/server-types", params={"server_types_live": "1"})
    images = client.get("/create/images", params={"images_live": "1"})

    assert "cx33" in sizes.text
    assert "Unlock the Hetzner API token" in sizes.text
    assert "ubuntu-24.04" in images.text
    assert "Unlock the Hetzner API token" in images.text


def test_live_lookup_failure_falls_back_to_defaults_with_a_notice(client, unlocked, monkeypatch):
    def boom(token):
        raise hcloud_api.HetznerApiError("503 Service Unavailable")

    monkeypatch.setattr(hcloud_api, "list_server_types", boom)
    monkeypatch.setattr(hcloud_api, "list_images", boom)

    sizes = client.get("/create/server-types", params={"server_types_live": "1"})
    images = client.get("/create/images", params={"images_live": "1"})

    assert "cx33" in sizes.text
    assert "503 Service Unavailable" in sizes.text
    assert "ubuntu-24.04" in images.text
    assert "503 Service Unavailable" in images.text


def test_empty_live_result_falls_back_to_defaults_with_a_notice(client, unlocked, monkeypatch):
    monkeypatch.setattr(hcloud_api, "list_server_types", lambda token: [])
    monkeypatch.setattr(hcloud_api, "list_images", lambda token: [])

    sizes = client.get("/create/server-types", params={"server_types_live": "1"})
    images = client.get("/create/images", params={"images_live": "1"})

    assert "cx33" in sizes.text
    assert "no available x86 server types" in sizes.text
    assert "ubuntu-24.04" in images.text
    assert "no available system images" in images.text


# --------------------------------------------------------------------------
# Submission validation (Principle XII)
# --------------------------------------------------------------------------

VALID_FORM = {
    "profile": "basic",
    "server_type": "cx23",
    "location": "fsn1",
    "image_select": "ubuntu-24.04",
}


@pytest.fixture
def ready_to_provision(monkeypatch):
    """A pool with a free name and a runner that records instead of
    spawning ansible-playbook."""
    started: list[list[str]] = []

    async def fake_start(*, session_id, action, target, command):
        started.append(command)

    monkeypatch.setattr(app_module.pool, "next_free_name", lambda: "vm-1")
    monkeypatch.setattr(app_module.runner, "start", fake_start)
    return started


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"profile": ""}, ["Select a profile"]),
        ({"server_type": ""}, ["Select a server size"]),
        ({"location": ""}, ["Select a location"]),
        ({"image_select": ""}, ["Select an operating system image"]),
        # The quotes wrapping an offending value are HTML-escaped in the
        # rendered page, so value and wording are asserted separately.
        ({"server_type": "cx99"}, ["cx99", "is not available"]),
        ({"profile": "wizard"}, ["wizard", "is not one of"]),
    ],
)
def test_create_refuses_a_submission_with_a_missing_or_unknown_choice(
    client, unlocked, no_network, ready_to_provision, overrides, expected
):
    response = client.post("/create", data={**VALID_FORM, **overrides}, follow_redirects=False)

    assert response.status_code == 400
    for fragment in expected:
        assert fragment in response.text
    assert ready_to_provision == [], "nothing may be provisioned from a rejected submission"


def test_create_refuses_a_size_that_cannot_be_ordered_in_the_chosen_location(
    client, unlocked, live_catalogue, ready_to_provision
):
    """ccx13 is priced in ash only. Accepting fsn1 here would defer the
    failure to the Hetzner API instead of reporting it at the gap."""
    response = client.post(
        "/create",
        data={**VALID_FORM, "server_types_live": "1", "server_type": "ccx13", "location": "fsn1"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "cannot be ordered in location" in response.text
    assert "fsn1" in response.text
    assert "ash" in response.text
    assert ready_to_provision == []


def test_create_keeps_the_submitted_choices_when_it_refuses(
    client, unlocked, no_network, ready_to_provision
):
    response = client.post(
        "/create",
        data={**VALID_FORM, "profile": "desktop", "image_custom": "debian-12", "location": ""},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert 'value="desktop" checked' in response.text.replace(" >", ">")
    assert 'value="debian-12"' in response.text


def test_create_starts_a_run_for_a_valid_submission(
    client, unlocked, no_network, ready_to_provision
):
    response = client.post("/create", data=VALID_FORM, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/run"
    assert len(ready_to_provision) == 1
    command = " ".join(ready_to_provision[0])
    assert "profile=basic" in command
    assert "hcloud_server_type=cx23" in command
    assert "hcloud_server_location=fsn1" in command
    assert "image=ubuntu-24.04" in command


def test_create_accepts_a_live_only_size_in_one_of_its_own_locations(
    client, unlocked, live_catalogue, ready_to_provision
):
    response = client.post(
        "/create",
        data={**VALID_FORM, "server_types_live": "1", "server_type": "ccx13", "location": "ash"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "hcloud_server_type=ccx13" in " ".join(ready_to_provision[0])


def test_preview_reports_the_offending_field_instead_of_a_command(client, unlocked, no_network):
    response = client.post("/create/preview", data={**VALID_FORM, "location": ""})

    assert response.status_code == 400
    assert "Select a location" in response.text
    assert "ansible-playbook" not in response.text


def test_preview_shows_the_command_for_a_valid_submission(client, unlocked, no_network):
    response = client.post("/create/preview", data=VALID_FORM)

    assert response.status_code == 200
    assert "ansible-playbook" in response.text


def test_saving_a_preset_refuses_an_invalid_submission(client, unlocked, no_network, monkeypatch):
    monkeypatch.setattr(
        app_module.presets, "save", lambda *a, **k: pytest.fail("must not save a rejected preset")
    )

    response = client.post(
        "/presets", data={**VALID_FORM, "name": "my-box", "server_type": "cx99"}
    )

    assert response.status_code == 400
    assert "cx99" in response.text
    assert "is not available" in response.text


def test_saving_a_preset_records_exactly_what_was_submitted(
    client, unlocked, no_network, monkeypatch
):
    saved = []
    monkeypatch.setattr(app_module.presets, "save", lambda preset: saved.append(preset))

    response = client.post(
        "/presets",
        data={**VALID_FORM, "name": "my-box", "location": "hel1", "image_custom": "debian-12"},
    )

    assert response.status_code == 200
    assert saved[0].name == "my-box"
    assert saved[0].location == "hel1"
    # The free-text field wins over the radio list, as on the create form.
    assert saved[0].image == "debian-12"
