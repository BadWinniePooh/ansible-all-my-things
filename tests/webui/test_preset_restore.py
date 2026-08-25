"""Presets remember the catalogue they were saved against, and a restore
that cannot be honoured says which part failed.

A preset saved while a live-catalogue toggle was on records a size or image
that is not in the built-in lists. Restoring it against those lists would
silently substitute a default -- a different machine than the preset
describes -- so these tests pin both the round trip and the reporting.
"""

from __future__ import annotations

import functools
import json

import pytest
from fastapi.testclient import TestClient

from webui import config, hcloud_api, presets
from webui.app import SESSION_COOKIE_NAME, app, secret_store

TOKEN = "test-token"
PASSWORD = "test-vault-password"

LIVE_SERVER_TYPES = [
    {
        "name": "ccx13",
        "architecture": "x86",
        "cores": 2,
        "memory": 8,
        "disk": 80,
        "prices": [{"location": "fsn1", "price_monthly": {"gross": "24.49"}}],
    },
]

LIVE_IMAGES = [
    {"name": "debian-12", "description": "Debian 12"},
    {"name": "rocky-9", "description": "Rocky Linux 9"},
]


@pytest.fixture
def presets_file(tmp_path, monkeypatch):
    """Point the store at a temporary file.

    presets.py captures config.PRESETS_FILE as a keyword default at import
    time, so re-pointing config is not enough; the module's functions are
    rebound with the path already applied. The routes call them through the
    module (presets.get(...)), so they see the bound versions.
    """
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
    client.get("/create")
    session_id = secret_store.verify(client.cookies[SESSION_COOKIE_NAME])
    secret_store.unlock(session_id, hcloud_token=TOKEN, vault_password=PASSWORD)
    return session_id


@pytest.fixture
def live_catalogue(monkeypatch):
    monkeypatch.setattr(hcloud_api, "list_server_types", lambda token: list(LIVE_SERVER_TYPES))
    monkeypatch.setattr(hcloud_api, "list_images", lambda token: list(LIVE_IMAGES))
    monkeypatch.setattr(
        hcloud_api,
        "find_image",
        lambda token, reference: {"name": reference, "description": reference},
    )


def test_preset_records_the_catalogue_it_was_saved_against(
    client, unlocked, presets_file, live_catalogue
):
    response = client.post(
        "/presets",
        data={
            "name": "live-box",
            "profile": "basic",
            "server_type": "ccx13",
            "location": "fsn1",
            "image_select": "rocky-9",
            "server_types_live": "1",
            "images_live": "1",
        },
    )
    assert response.status_code == 200, response.text

    saved = presets.get("live-box")
    assert saved.server_type == "ccx13"
    assert saved.image == "rocky-9"
    assert saved.server_types_live is True
    assert saved.images_live is True


def test_loading_a_live_preset_restores_its_size_and_image(
    client, unlocked, presets_file, live_catalogue
):
    presets.save(
        presets.Preset(
            name="live-box",
            profile="basic",
            server_type="ccx13",
            location="fsn1",
            image="rocky-9",
            server_types_live=True,
            images_live=True,
        )
    )

    body = client.get("/create?preset=live-box").text

    assert "was not restored in full" not in body
    assert 'value="ccx13" checked' in body
    assert 'value="rocky-9" checked' in body
    # Both toggles come back on, so the lists on screen are the ones the
    # choices belong to.
    assert 'name="server_types_live" value="1" checked' in body
    assert 'name="images_live" value="1" checked' in body


def test_a_restore_names_the_part_that_could_not_be_honoured(client, unlocked, presets_file):
    """The image restores from the built-in list while the size cannot:
    the preset names a live-only size but did not record the toggle, which
    is exactly the shape of a presets.json written before this feature."""
    presets.save(
        presets.Preset(
            name="stale",
            profile="basic",
            server_type="ccx13",
            location="fsn1",
            image="ubuntu-24.04",
        )
    )

    body = client.get("/create?preset=stale").text

    assert "Preset &#39;stale&#39; was not restored in full." in body
    assert "Server size &#39;ccx13&#39; is not in the catalogue shown here" in body
    # The part that did restore is not reported as a failure.
    assert "ubuntu-24.04" in body
    assert "Image &#39;ubuntu-24.04&#39;" not in body


def test_a_live_lookup_that_falls_back_is_reported_as_such(
    client, unlocked, presets_file, monkeypatch
):
    def unreachable(token):
        raise hcloud_api.HetznerApiError("503 Service Unavailable")

    monkeypatch.setattr(hcloud_api, "list_server_types", unreachable)
    monkeypatch.setattr(hcloud_api, "list_images", lambda token: list(LIVE_IMAGES))
    monkeypatch.setattr(
        hcloud_api, "find_image", lambda token, reference: {"name": reference, "description": reference}
    )
    presets.save(
        presets.Preset(
            name="live-box",
            profile="basic",
            server_type="ccx13",
            location="fsn1",
            image="rocky-9",
            server_types_live=True,
            images_live=True,
        )
    )

    body = client.get("/create?preset=live-box").text

    assert "Preset &#39;live-box&#39; was not restored in full." in body
    assert "Could not load server sizes from Hetzner" in body
    assert "503 Service Unavailable" in body


def test_presets_written_before_the_toggles_still_load(presets_file):
    presets_file.write_text(
        json.dumps(
            [
                {
                    "name": "old",
                    "profile": "basic",
                    "server_type": "cx23",
                    "location": "fsn1",
                    "image": "ubuntu-24.04",
                }
            ]
        ),
        encoding="utf-8",
    )

    saved = presets.get("old")
    assert saved.server_types_live is False
    assert saved.images_live is False


def test_an_unknown_key_from_a_newer_image_is_dropped_rather_than_fatal(presets_file):
    presets_file.write_text(
        json.dumps(
            [
                {
                    "name": "future",
                    "profile": "basic",
                    "server_type": "cx23",
                    "location": "fsn1",
                    "image": "ubuntu-24.04",
                    "something_new": True,
                }
            ]
        ),
        encoding="utf-8",
    )

    assert presets.get("future").name == "future"
