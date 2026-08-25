"""Every page renders through the shared shell, and the dashboard keeps the
machine list behind an unlocked Hetzner API token.

These are rendering tests: they assert the shell's contract (one sidebar on
every page, the current page marked, locked actions padlocked) and the
locked-first rule on the dashboard. They deliberately do not assert on
styling.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from webui import config, hcloud_api, inventory, pool, seed, vault
from webui.app import SESSION_COOKIE_NAME, app, secret_store

TOKEN = "test-token"
PASSWORD = "test-vault-password"

PAGES = ["/", "/create", "/run", "/vault", "/sshkey", "/pool", "/presets"]

POOL = pool.PoolStatus(
    entries=["edoras", "shire", "osgiliath"],
    used=["edoras"],
    free=["shire", "osgiliath"],
)
MACHINES = [
    inventory.Machine(
        name="edoras",
        address="49.12.113.84",
        profile="desktop",
        server_type="cx33",
        location="nbg1",
    )
]


@pytest.fixture(autouse=True)
def volume_state(monkeypatch, tmp_path):
    """The volume these pages read lives at /ansible in the image and does
    not exist on a developer machine, so the reads the shell makes on every
    dashboard render are answered from here instead -- including the two
    that would otherwise reach outside the checkout: the Hetzner account,
    and the cost ledger's file."""
    monkeypatch.setattr(config, "COST_DB_FILE", tmp_path / "costs.sqlite3")
    monkeypatch.setattr(hcloud_api, "list_servers", lambda token: [])
    monkeypatch.setattr(pool, "status", lambda **kwargs: POOL)
    monkeypatch.setattr(inventory, "list_machines", lambda **kwargs: list(MACHINES))
    monkeypatch.setattr(seed, "needs_defaults_refresh", lambda: False)
    monkeypatch.setattr(vault, "read_vault", lambda password, **kwargs: {})
    monkeypatch.setattr(vault, "load_template", lambda **kwargs: {"vault_desktop_users": []})


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def unlocked(client):
    """Unlock both secrets directly: POST /session/unlock validates the
    token against the real API and writes a vault file, neither of which
    belongs in a rendering test."""
    client.get("/")
    session_id = secret_store.verify(client.cookies[SESSION_COOKIE_NAME])
    secret_store.unlock(session_id, hcloud_token=TOKEN, vault_password=PASSWORD)
    return session_id


@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders_the_shell(client, path):
    response = client.get(path)
    assert response.status_code == 200
    body = response.text
    assert "Steward" in body
    assert body.count('class="sidebar"') == 1
    # Every nav destination is reachable from every page.
    for destination in PAGES:
        assert f'href="{destination}"' in body


@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders_the_shell_unlocked(client, unlocked, path):
    response = client.get(path)
    assert response.status_code == 200
    assert 'class="sidebar"' in response.text


def test_nav_padlocks_the_actions_a_locked_session_cannot_perform(client):
    body = client.get("/").text
    assert 'href="/create" class="is-locked"' in body
    assert 'href="/vault" class="is-locked"' in body
    # Neither of these needs a secret, so neither is padlocked.
    assert 'href="/pool" class="is-locked"' not in body
    assert 'href="/presets" class="is-locked"' not in body


def test_nav_unlocks_once_both_secrets_are_present(client, unlocked):
    body = client.get("/").text
    assert "is-locked" not in body


def test_dashboard_hides_machines_until_the_api_token_is_unlocked(client):
    body = client.get("/").text
    assert "Unlock this session to continue" in body
    assert "Name pool" not in body.split('class="sidebar"')[1].split("</aside>")[1]


def test_dashboard_shows_machines_once_the_api_token_is_unlocked(client, unlocked):
    body = client.get("/").text
    assert "Unlock this session to continue" not in body
    main = body.split("</aside>")[1]
    assert "Machines" in main
    assert "Name pool" in main


def test_session_status_fragment_reports_both_secrets(client, unlocked):
    body = client.get("/session/status").text
    assert body.count("unlocked") >= 2
    assert "Lock session" in body
