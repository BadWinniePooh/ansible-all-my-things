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

import functools
import json

import pytest
from fastapi.testclient import TestClient

from webui import app as app_module
from webui import config
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
def image_found(monkeypatch):
    """The image-name check is a deliberate API call on every render that
    has a free-text image and on every submission, so it is stubbed for
    every test here rather than left to reach the network."""

    def find_image(token, reference):
        return {"name": reference, "description": reference, "architecture": "x86"}

    monkeypatch.setattr(hcloud_api, "find_image", find_image)


@pytest.fixture
def live_catalogue(monkeypatch, image_found):
    monkeypatch.setattr(hcloud_api, "list_server_types", lambda token: list(LIVE_SERVER_TYPES))
    monkeypatch.setattr(hcloud_api, "list_images", lambda token: list(LIVE_IMAGES))


@pytest.fixture
def no_network(monkeypatch, image_found):
    """The two catalogue lookups must not run on the built-in path. The
    image check is exempt: it is what replaces failing during the run."""

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
def machine_details(tmp_path, monkeypatch):
    """Where a started run writes down what it ordered. Bound on the
    module's own function too, not just on config: inventory.py takes the
    path as a default argument, which is read at import time."""
    path = tmp_path / "machine_details.json"
    monkeypatch.setattr(config, "MACHINE_DETAILS_FILE", path)
    for name in ("record_choices", "recorded_choices", "list_machines"):
        monkeypatch.setattr(
            app_module.inventory,
            name,
            functools.partial(getattr(app_module.inventory, name), details_file=path),
        )
    return path


@pytest.fixture
def ready_to_provision(monkeypatch, machine_details):
    """A pool with a free name and a runner that records instead of
    spawning ansible-playbook."""
    started: list[list[str]] = []

    async def fake_start(*, session_id, action, target, command):
        started.append(command)

    monkeypatch.setattr(app_module.pool, "next_free_name", lambda: "vm-1")
    monkeypatch.setattr(app_module.runner, "start", fake_start)
    return started


def test_a_started_run_records_what_it_ordered(
    client, unlocked, live_catalogue, ready_to_provision, machine_details
):
    """The account will answer the size and location later, but never which
    catalogue they were picked from -- and a preset made from this machine
    has to load against the same list its choices came from."""
    client.post(
        "/create",
        data={
            **VALID_FORM,
            "server_type": "ccx13",
            "location": "ash",
            "server_types_live": "on",
        },
    )

    recorded = json.loads(machine_details.read_text(encoding="utf-8"))["vm-1"]
    assert recorded["server_type"] == "ccx13"
    assert recorded["location"] == "ash"
    assert recorded["image"] == "ubuntu-24.04"
    assert recorded["server_types_live"] is True
    assert recorded["images_live"] is False


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


def test_free_text_image_leaves_the_image_list_unselected_on_redisplay(
    client, unlocked, no_network, ready_to_provision
):
    """Free text wins over the radio list, so a redisplayed form must not
    show a list entry as chosen -- it would misreport what will be
    provisioned. The browser enforces the same rule live
    (webui/static/image-choice.js); this is the no-scripting path."""
    response = client.post(
        "/create",
        data={
            **VALID_FORM,
            "image_select": "ubuntu-24.04",
            "image_custom": "debian-12",
            "location": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    body = response.text.replace(" >", ">")
    assert 'value="ubuntu-24.04" checked' not in body
    assert "checked" not in body.split('id="image-options"')[1].split("</div>")[0]
    assert 'value="debian-12"' in body


def test_the_page_loads_the_script_enforcing_that_exclusivity(client, no_network):
    assert "/static/image-choice.js" in client.get("/create").text
    assert client.get("/static/image-choice.js").status_code == 200


# --------------------------------------------------------------------------
# Live image-name check
# --------------------------------------------------------------------------


def test_image_check_says_nothing_for_an_empty_field(client, unlocked, monkeypatch):
    monkeypatch.setattr(
        hcloud_api, "find_image", lambda *a: pytest.fail("must not look up an empty name")
    )

    response = client.get("/create/validate-image", params={"image_custom": "  "})

    assert response.status_code == 200
    assert 'id="image-validation"' in response.text
    assert "available" not in response.text


def test_image_check_confirms_a_name_the_account_has(client, unlocked, monkeypatch):
    monkeypatch.setattr(
        hcloud_api,
        "find_image",
        lambda token, reference: {"name": reference, "description": "Debian 12"},
    )

    response = client.get("/create/validate-image", params={"image_custom": "debian-12"})

    assert "Debian 12 is available." in response.text
    assert "image-check-ok" in response.text


def test_image_check_reports_a_name_the_account_does_not_have(client, unlocked, monkeypatch):
    monkeypatch.setattr(hcloud_api, "find_image", lambda token, reference: None)

    response = client.get("/create/validate-image", params={"image_custom": "ubuntu-99.04"})

    assert "ubuntu-99.04" in response.text
    assert "No x86 image named" in response.text
    assert "image-check-missing" in response.text


def test_image_check_warns_about_a_deprecated_image(client, unlocked, monkeypatch):
    monkeypatch.setattr(
        hcloud_api,
        "find_image",
        lambda token, reference: {
            "name": reference,
            "description": "Ubuntu 20.04",
            "deprecated": "2026-04-01T00:00:00+00:00",
        },
    )

    response = client.get("/create/validate-image", params={"image_custom": "ubuntu-20.04"})

    assert "is available" in response.text
    assert "deprecated" in response.text


def test_image_check_says_why_it_cannot_run_without_a_token(client, monkeypatch):
    monkeypatch.setattr(
        hcloud_api, "find_image", lambda *a: pytest.fail("must not call the API while locked")
    )

    response = client.get("/create/validate-image", params={"image_custom": "debian-12"})

    assert "Unlock the Hetzner API token" in response.text


def test_image_check_surfaces_a_lookup_failure(client, unlocked, monkeypatch):
    def boom(token, reference):
        raise hcloud_api.HetznerApiError("502 Bad Gateway")

    monkeypatch.setattr(hcloud_api, "find_image", boom)

    response = client.get("/create/validate-image", params={"image_custom": "debian-12"})

    assert "502 Bad Gateway" in response.text
    assert "image-check-error" in response.text


def test_create_refuses_an_image_the_account_does_not_have(
    client, unlocked, no_network, ready_to_provision, monkeypatch
):
    """The live check is advisory -- scripting off, a stale preset, or a
    fast submit all bypass it. This gate is what stops a bad name from
    reaching a run that has already claimed a pool name."""
    monkeypatch.setattr(hcloud_api, "find_image", lambda token, reference: None)

    response = client.post(
        "/create",
        data={**VALID_FORM, "image_custom": "ubuntu-99.04"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "No x86 image named" in response.text
    assert ready_to_provision == []


def test_create_refuses_when_the_image_lookup_itself_fails(
    client, unlocked, no_network, ready_to_provision, monkeypatch
):
    def boom(token, reference):
        raise hcloud_api.HetznerApiError("502 Bad Gateway")

    monkeypatch.setattr(hcloud_api, "find_image", boom)

    response = client.post("/create", data=VALID_FORM, follow_redirects=False)

    assert response.status_code == 400
    assert "Could not check image" in response.text
    assert ready_to_provision == []


def test_create_gate_also_covers_an_image_picked_from_the_built_in_list(
    client, unlocked, no_network, ready_to_provision, monkeypatch
):
    """The built-in list goes stale as Hetzner adds and withdraws releases,
    so it is checked on the same terms as free text."""
    checked = []

    def find_image(token, reference):
        checked.append(reference)
        return None

    monkeypatch.setattr(hcloud_api, "find_image", find_image)

    response = client.post(
        "/create", data={**VALID_FORM, "image_select": "ubuntu-26.04"}, follow_redirects=False
    )

    assert response.status_code == 400
    assert checked == ["ubuntu-26.04"]
    assert ready_to_provision == []


def test_create_page_checks_an_image_carried_in_by_a_preset(client, unlocked, no_network, monkeypatch):
    monkeypatch.setattr(
        app_module.presets,
        "get",
        lambda name: app_module.presets.Preset(
            name=name,
            profile="basic",
            server_type="cx23",
            location="fsn1",
            image="ubuntu-99.04",
        ),
    )
    monkeypatch.setattr(hcloud_api, "find_image", lambda token, reference: None)

    response = client.get("/create", params={"preset": "stale-box"})

    assert response.status_code == 200
    assert "No x86 image named" in response.text


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
