"""The create screen's preset selector, and the refusal to save choices
that are already saved.

The selector and the save box are also maintained in the browser
(static/preset-selection.js) after every edit; these tests pin the answers
the server renders, which are what a browser without scripting gets and
what the script starts from.
"""

from __future__ import annotations

import functools

import pytest
from fastapi.testclient import TestClient

from webui import config, hcloud_api, presets
from webui.app import SESSION_COOKIE_NAME, app, secret_store

TOKEN = "test-token"
PASSWORD = "test-vault-password"

SAMPLE = presets.Preset(
    name="dev-desktop",
    profile="desktop",
    server_type="cx33",
    location="nbg1",
    image="ubuntu-24.04",
)


def _save_is_disabled(body: str) -> bool:
    """Whether the save-preset box refuses input, read off the two controls
    rather than off exact markup, so a whitespace change is not a failure."""
    name_field = body.split('id="preset-save-name"', 1)[1].split(">", 1)[0]
    button = body.split('id="preset-save-button"', 1)[1].split(">", 1)[0]
    return "disabled" in name_field and "disabled" in button


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


def test_no_selector_when_nothing_is_saved(client, unlocked, presets_file):
    body = client.get("/create").text
    assert 'id="preset-select"' not in body
    assert 'id="preset-picker"' not in body


def test_the_selector_lists_every_preset_and_custom(client, unlocked, presets_file):
    presets.save(SAMPLE)
    body = client.get("/create").text
    assert 'id="preset-select"' in body
    assert ">Custom<" in body
    assert ">dev-desktop<" in body


def test_loading_a_preset_marks_it_as_the_selection(client, unlocked, presets_file):
    presets.save(SAMPLE)
    body = client.get("/create?preset=dev-desktop").text
    assert '<option value="dev-desktop" selected>' in body
    assert '<option value="" selected>' not in body


def test_choices_that_are_not_a_preset_render_as_custom(client, unlocked, presets_file):
    presets.save(SAMPLE)
    body = client.get("/create").text
    # The defaults are not this preset, so the selector says Custom and
    # saving stays available: the note is present but hidden, because
    # preset-selection.js reveals it without a round trip.
    assert '<option value="" selected>' in body
    assert 'id="preset-save-note" class="card-note" hidden' in body
    assert not _save_is_disabled(body)


def test_a_loaded_preset_disables_saving_and_names_itself(client, unlocked, presets_file):
    presets.save(SAMPLE)
    body = client.get("/create?preset=dev-desktop").text
    assert "These choices are already saved as" in body
    assert 'id="preset-save-note" class="card-note" hidden' not in body
    assert _save_is_disabled(body)


def test_saving_choices_that_are_already_saved_is_refused_by_name(
    client, unlocked, presets_file
):
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
    assert "dev-desktop" in response.text
    assert [preset.name for preset in presets.list_presets()] == ["dev-desktop"]


def test_a_preset_saved_from_the_defaults_matches_the_default_form(
    client, unlocked, presets_file
):
    first_size = next(iter(config.SERVER_TYPES))
    presets.save(
        presets.Preset(
            name="stock",
            profile=next(iter(config.PROFILES)),
            server_type=first_size,
            location=next(iter(config.LOCATIONS)),
            image=config.UBUNTU_LTS_IMAGES[0]["value"],
        )
    )

    body = client.get("/create").text

    assert '<option value="stock" selected>' in body
    assert "These choices are already saved as" in body
