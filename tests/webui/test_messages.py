"""Every action says what happened, in the interface's own words.

Before this, a successful POST redirected silently and a failing one
answered with hand-written HTML, so "it worked" and "it did not" were told
in three different voices or not at all. webui/messages.py is the single
vocabulary; these tests pin that each action reaches it.
"""

from __future__ import annotations

import functools

import pytest
from fastapi.testclient import TestClient

from webui import config, hcloud_api, inventory, messages, pool, presets, seed
from webui.app import SESSION_COOKIE_NAME, app, secret_store

TOKEN = "test-token"
PASSWORD = "test-vault-password"


@pytest.fixture(autouse=True)
def volume_state(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pool, "status", lambda **kwargs: pool.PoolStatus(entries=["edoras"], used=[], free=["edoras"])
    )
    monkeypatch.setattr(pool, "save", lambda entries, **kwargs: None)
    monkeypatch.setattr(inventory, "list_machines", lambda **kwargs: [])
    monkeypatch.setattr(seed, "needs_defaults_refresh", lambda: False)
    monkeypatch.setattr(seed, "refresh_defaults", lambda: None)
    path = tmp_path / "presets.json"
    monkeypatch.setattr(config, "PRESETS_FILE", path)
    for name in ("list_presets", "get", "save", "delete", "rename"):
        monkeypatch.setattr(
            presets, name, functools.partial(getattr(presets, name), presets_file=path)
        )
    monkeypatch.setattr(
        hcloud_api,
        "find_image",
        lambda token, reference: {"name": reference, "description": reference},
    )
    monkeypatch.setattr(hcloud_api, "list_servers", lambda token: [])
    monkeypatch.setattr(config, "COST_DB_FILE", tmp_path / "costs.sqlite3")


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


@pytest.mark.parametrize(
    ("action", "code"),
    [
        ("/session/lock", "session-locked"),
        ("/pool", "pool-saved"),
        ("/defaults/refresh", "defaults-refreshed"),
    ],
)
def test_an_action_lands_on_a_page_that_says_what_happened(client, unlocked, action, code):
    response = client.post(action, data={"entries": "edoras"})

    assert response.status_code == 200  # followed the redirect
    assert response.url.query.decode() == f"done={code}"
    assert messages.DONE[code] in response.text


def test_deleting_and_renaming_a_preset_are_both_confirmed(client, unlocked):
    presets.save(
        presets.Preset(
            name="dev-desktop",
            profile="desktop",
            server_type="cx33",
            location="nbg1",
            image="ubuntu-24.04",
        )
    )

    renamed = client.post("/presets/dev-desktop/rename", data={"new_name": "box"})
    assert messages.DONE["preset-renamed"] in renamed.text

    deleted = client.post("/presets/box/delete")
    assert messages.DONE["preset-deleted"] in deleted.text


def test_an_invented_code_says_nothing(client, unlocked):
    body = client.get("/?done=everything-is-fine").text
    assert "everything-is-fine" not in body
    assert 'class="banner banner-notice"' not in body


def test_a_saved_preset_is_confirmed_in_the_fragment(client, unlocked):
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
    assert response.text.strip() == '<p class="banner banner-notice">Preset dev-desktop saved.</p>'


def test_a_refused_save_uses_the_same_banner_as_a_successful_one(client, unlocked):
    response = client.post("/presets", data={"name": ""})

    assert response.status_code == 400
    assert 'class="banner banner-error"' in response.text
    assert messages.preset_name_required() in response.text
