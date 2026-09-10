"""The preset rail on the create screen.

The rail says which preset the form came from, how the current choices
differ from it, and offers the two ways to keep the difference: update that
preset, or save a new one. static/preset-selection.js recomputes the
difference and both buttons after every edit; these tests pin the answers
the server renders, which are what a browser without scripting gets and
what the script starts from.
"""

from __future__ import annotations

import functools

import pytest
from fastapi.testclient import TestClient

from webui import config, hcloud_api, presets
from webui.app import SESSION_COOKIE_NAME, _preset_diff, app, secret_store

TOKEN = "test-token"
PASSWORD = "test-vault-password"

SAMPLE = presets.Preset(
    name="dev-desktop",
    profile="desktop",
    server_type="cx33",
    location="nbg1",
    image="ubuntu-24.04",
)


def _control(body: str, element_id: str) -> str:
    """The opening tag of one control, read off its id rather than off
    exact markup, so a whitespace change is not a failure."""
    return body.split(f'id="{element_id}"', 1)[1].split(">", 1)[0]


@pytest.fixture
def presets_file(tmp_path, monkeypatch):
    path = tmp_path / "presets.json"
    monkeypatch.setattr(config, "PRESETS_FILE", path)
    for name in ("list_presets", "get", "save", "delete", "rename"):
        monkeypatch.setattr(
            presets, name, functools.partial(getattr(presets, name), presets_file=path)
        )
    return path


@pytest.fixture(autouse=True)
def image_found(monkeypatch):
    monkeypatch.setattr(
        hcloud_api,
        "find_image",
        lambda token, reference: {"name": reference, "description": reference},
    )


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def unlocked(client):
    client.get("/create")
    session_id = secret_store.verify(client.cookies[SESSION_COOKIE_NAME])
    secret_store.unlock(session_id, hcloud_token=TOKEN, vault_password=PASSWORD)
    return session_id


def test_the_first_preset_can_be_saved_when_none_exist_yet(client, unlocked, presets_file):
    """The rail used to render only once a preset existed, which took the
    Save box with it -- so the first preset could never be made from the
    create form at all."""
    body = client.get("/create").text

    assert 'id="preset-rail"' in body
    assert 'id="preset-save-button"' in body
    # The list of saved presets is still dropped: an empty list is a box of
    # nothing, while the Save box is the way out of having none.
    assert "Manage presets" not in body


def test_the_rail_lists_every_preset_with_what_it_contains(client, unlocked, presets_file):
    presets.save(SAMPLE)
    body = client.get("/create").text
    assert 'id="preset-rail"' in body
    assert "dev-desktop" in body
    assert "desktop · cx33 · nbg1" in body


def test_loading_a_preset_marks_it_and_offers_reset(client, unlocked, presets_file):
    presets.save(SAMPLE)
    body = client.get("/create?preset=dev-desktop").text
    assert 'class="rail-item is-active"' in body
    assert ">\n            Reset\n          </a>" in body


def test_a_freshly_loaded_preset_has_nothing_to_update(client, unlocked, presets_file):
    presets.save(SAMPLE)
    body = client.get("/create?preset=dev-desktop").text
    assert "The form still matches this preset." in body
    assert "disabled" in _control(body, "preset-update-button")
    # Saving it again under another name is refused for the same reason.
    assert "disabled" in _control(body, "preset-save-name")


def test_the_rail_is_not_shown_for_a_form_with_no_preset_behind_it(
    client, unlocked, presets_file
):
    presets.save(SAMPLE)
    body = client.get("/create").text
    # The list is there, the difference panel is not: nothing to differ from.
    assert 'id="preset-rail"' in body
    assert "hidden" in _control(body, "preset-changes")


def test_the_difference_is_listed_field_by_field():
    """The rail's own arithmetic, which the browser repeats after each edit."""
    changes = _preset_diff(
        {
            "profile": "desktop",
            "server_type": "cx43",
            "location": "nbg1",
            "image_custom": "ubuntu-24.04",
            "server_types_live": True,
            "images_live": False,
        },
        SAMPLE,
    )

    assert changes == [
        {"field": "Size", "was": "cx33", "now": "cx43"},
        {"field": "Size list", "was": "built-in", "now": "live"},
    ]


def test_a_form_that_still_matches_its_preset_differs_in_nothing():
    assert (
        _preset_diff(
            {
                "profile": "desktop",
                "server_type": "cx33",
                "location": "nbg1",
                "image_custom": "ubuntu-24.04",
            },
            SAMPLE,
        )
        == []
    )


def test_update_writes_the_current_choices_over_the_preset(client, unlocked, presets_file):
    presets.save(SAMPLE)

    response = client.post(
        "/presets/dev-desktop/update",
        data={
            "profile": "basic",
            "server_type": "cx43",
            "location": "fsn1",
            "image_select": "ubuntu-22.04",
        },
    )

    assert response.status_code == 200
    assert "Preset dev-desktop updated." in response.text
    saved = presets.get("dev-desktop")
    assert (saved.profile, saved.server_type, saved.location, saved.image) == (
        "basic",
        "cx43",
        "fsn1",
        "ubuntu-22.04",
    )
    # Still one preset: update writes over, it does not add.
    assert len(presets.list_presets()) == 1


def test_a_saved_preset_appears_in_the_rail_it_answers_with(client, unlocked, presets_file):
    """The answer to a save is the rail, not just a sentence: the preset is
    in the list and in the data the browser diffs against, so it is there
    without the reload nobody would think to perform."""
    response = client.post(
        "/presets",
        data={
            "name": "dev-desktop",
            "profile": "desktop",
            "server_type": "cx33",
            "location": "nbg1",
            "image_select": "ubuntu-24.04",
        },
    )

    assert response.status_code == 200
    assert 'id="preset-rail"' in response.text
    assert 'data-preset="dev-desktop"' in response.text
    assert '"name": "dev-desktop"' in response.text


def test_saving_makes_the_form_that_preset_so_the_change_list_empties(
    client, unlocked, presets_file
):
    """Having just saved these choices, the form is that preset -- so the
    rail comes back anchored to it, with nothing changed since, rather than
    still counting changes against the preset the form was loaded from."""
    presets.save(SAMPLE)

    response = client.post(
        "/presets",
        data={
            "preset_base": "dev-desktop",
            "name": "smaller",
            "profile": "desktop",
            "server_type": "cx23",
            "location": "nbg1",
            "image_select": "ubuntu-24.04",
        },
    )

    body = response.text
    # The hidden field the form carries, swapped out of band.
    assert 'id="preset-base" value="smaller"' in body
    # Zero changes, so the section that lists them is hidden and Update has
    # nothing to write.
    assert '<span id="preset-changes-count">0</span>' in body
    assert 'id="preset-changes" hidden' not in body
    assert "These choices are already saved as" in body


def test_an_update_empties_the_changes_it_wrote(client, unlocked, presets_file):
    presets.save(SAMPLE)

    response = client.post(
        "/presets/dev-desktop/update",
        data={
            "preset_base": "dev-desktop",
            "profile": "basic",
            "server_type": "cx43",
            "location": "fsn1",
            "image_select": "ubuntu-22.04",
        },
    )

    assert '<span id="preset-changes-count">0</span>' in response.text
    assert 'id="preset-base" value="dev-desktop"' in response.text


def test_update_validates_exactly_as_saving_does(client, unlocked, presets_file):
    presets.save(SAMPLE)

    response = client.post("/presets/dev-desktop/update", data={"profile": "desktop"})

    assert response.status_code == 400
    assert 'class="banner banner-error"' in response.text
    assert presets.get("dev-desktop").server_type == "cx33"


def test_update_refuses_a_preset_that_is_not_there(client, unlocked, presets_file):
    response = client.post(
        "/presets/ghost/update",
        data={
            "profile": "basic",
            "server_type": "cx23",
            "location": "fsn1",
            "image_select": "ubuntu-24.04",
        },
    )

    assert response.status_code == 404
    assert "No preset named ghost" in response.text


def test_saving_choices_that_are_already_saved_is_refused_by_name(client, unlocked, presets_file):
    """Not only for the preset that was loaded: the same choices reached by
    hand and offered under a different name are the same preset."""
    presets.save(SAMPLE)

    response = client.post(
        "/presets",
        data={
            "name": "another-name",
            "profile": "desktop",
            "server_type": "cx33",
            "location": "nbg1",
            "image_select": "ubuntu-24.04",
        },
    )

    assert response.status_code == 409
    assert "already saved as" in response.text
    assert [preset.name for preset in presets.list_presets()] == ["dev-desktop"]


def test_a_preset_matching_the_default_form_disables_saving_on_arrival(
    client, unlocked, presets_file
):
    presets.save(
        presets.Preset(
            name="stock",
            profile=next(iter(config.PROFILES)),
            server_type=next(iter(config.SERVER_TYPES)),
            location=next(iter(config.LOCATIONS)),
            image=config.UBUNTU_LTS_IMAGES[0]["value"],
        )
    )

    body = client.get("/create").text

    assert "These choices are already saved as" in body
    assert "disabled" in _control(body, "preset-save-button")
