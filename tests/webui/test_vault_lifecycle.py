"""The two ends of the encrypted configuration's life: creating it, and
discarding it.

Both exist because of the same failure. The password that creates
vault.yml is the only one the interface cannot check against anything --
every later one is checked against that file -- so a typo at creation
becomes the real password, and nothing afterwards can tell it apart from
the one the operator meant. From then on no playbook runs at all: Ansible
loads group_vars/all/vault.yml for every host and fails at decryption
before the play starts, so even destroying a machine that is costing money
is refused. Asking for the password twice is the guard; discarding the
file is the way out when the guard was not there.

ansible-vault itself is stubbed here (it does not start natively on
Windows, where these tests also run -- see tests/webui/test_vault.py); what
is under test is the routes' decisions, not the encryption.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from webui import config, hcloud_api, inventory, pool, seed
from webui import vault as vault_module
from webui.app import SESSION_COOKIE_NAME, app, runner, secret_store
from webui.runner import Run

TOKEN = "hcloud-token"  # noqa: S105
PASSWORD = "vault-password"  # noqa: S105


@pytest.fixture(autouse=True)
def volume_state(monkeypatch, tmp_path):
    """The volume lives at /ansible in the image and nowhere on a developer
    machine, so every read the dashboard and vault screens make on render
    is answered from tmp_path instead -- including the Hetzner account and
    the cost ledger, which would otherwise reach outside the checkout."""
    monkeypatch.setattr(config, "VAULT_FILE", tmp_path / "vault.yml")
    monkeypatch.setattr(config, "COST_DB_FILE", tmp_path / "costs.sqlite3")
    monkeypatch.setattr(hcloud_api, "list_servers", lambda token: [])
    monkeypatch.setattr(hcloud_api, "validate_token", lambda token: None)
    monkeypatch.setattr(inventory, "list_machines", lambda **kwargs: [])
    monkeypatch.setattr(pool, "status", lambda **kwargs: pool.PoolStatus([], [], []))
    monkeypatch.setattr(seed, "needs_defaults_refresh", lambda: False)
    monkeypatch.setattr(vault_module, "load_template", lambda **kwargs: {})
    monkeypatch.setattr(vault_module, "read_vault", lambda password, **kwargs: {})


@pytest.fixture
def created_vaults(monkeypatch, tmp_path):
    """Records every password create_vault() was called with, and leaves a
    vault.yml behind exactly as the real one does."""
    passwords: list[str] = []

    def create_vault(vault_password, **kwargs):
        passwords.append(vault_password)
        config.VAULT_FILE.write_text("$ANSIBLE_VAULT;1.1;AES256\n", encoding="utf-8")

    monkeypatch.setattr(vault_module, "create_vault", create_vault)
    return passwords


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def no_active_run():
    """The Runner is module-level state shared by every test in the suite;
    a run left active here would refuse discards in unrelated tests."""
    yield
    runner._active = None


def session_of(client) -> str:
    return secret_store.verify(client.cookies[SESSION_COOKIE_NAME])


def existing_vault() -> None:
    config.VAULT_FILE.write_text("$ANSIBLE_VAULT;1.1;AES256\n", encoding="utf-8")


def test_creating_the_configuration_needs_the_password_twice(client, created_vaults):
    response = client.post(
        "/session/unlock",
        data={"vault_password": PASSWORD, "vault_password_confirm": PASSWORD},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert created_vaults == [PASSWORD]
    assert secret_store.vault_password(session_of(client)) == PASSWORD


def test_a_mistyped_repeat_creates_nothing_and_unlocks_nothing(client, created_vaults):
    response = client.post(
        "/session/unlock",
        data={"vault_password": PASSWORD, "vault_password_confirm": "vault-passwrod"},
    )

    assert response.status_code == 400
    assert "do not match" in response.text
    assert created_vaults == []
    assert not config.VAULT_FILE.exists()
    assert secret_store.vault_password(session_of(client)) is None


def test_a_missing_repeat_is_a_mistyped_repeat(client, created_vaults):
    """The field is required for a reason; a form posted without it (an
    older bookmarklet, a script) must not fall through to creation."""
    response = client.post("/session/unlock", data={"vault_password": PASSWORD})

    assert response.status_code == 400
    assert created_vaults == []
    assert not config.VAULT_FILE.exists()


def test_the_repeat_is_not_asked_for_once_a_configuration_exists(client, created_vaults):
    """Every later password is checked against the file itself, so a second
    entry would add nothing -- and a submission without one still unlocks."""
    existing_vault()

    body = client.get("/").text
    assert "vault_password_confirm" not in body

    response = client.post(
        "/session/unlock", data={"vault_password": PASSWORD}, follow_redirects=False
    )

    assert response.status_code == 303
    assert created_vaults == []
    assert secret_store.vault_password(session_of(client)) == PASSWORD


def test_the_token_still_unlocks_when_the_repeat_is_wrong(client, created_vaults):
    """One invalid field is downgraded to blank rather than aborting the
    whole submission -- the other may still be good."""
    response = client.post(
        "/session/unlock",
        data={
            "hcloud_token": TOKEN,
            "vault_password": PASSWORD,
            "vault_password_confirm": "typo",
        },
    )

    assert response.status_code == 400
    session_id = session_of(client)
    assert secret_store.hcloud_token(session_id) == TOKEN
    assert secret_store.vault_password(session_id) is None


def test_discarding_deletes_the_configuration_and_forgets_the_password(client):
    existing_vault()
    client.get("/")
    session_id = session_of(client)
    secret_store.unlock(session_id, hcloud_token=TOKEN, vault_password=PASSWORD)

    response = client.post("/vault/discard", data={"confirm": "discard"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/vault?done=vault-discarded"
    assert not config.VAULT_FILE.exists()
    # The file that password opened is gone; keeping it would write the
    # next configuration under a password nobody entered again.
    assert secret_store.vault_password(session_id) is None
    # The Hetzner token has nothing to do with the vault and survives.
    assert secret_store.hcloud_token(session_id) == TOKEN


def test_discarding_without_the_typed_word_removes_nothing(client):
    existing_vault()

    response = client.post("/vault/discard", data={"confirm": "yes"})

    assert response.status_code == 400
    assert "discard" in response.text
    assert config.VAULT_FILE.exists()


def test_discarding_works_while_the_session_is_locked(client):
    """The operator who needs this most is the one whose password no longer
    opens the file, so the action cannot require unlocking first."""
    existing_vault()

    response = client.post("/vault/discard", data={"confirm": "discard"}, follow_redirects=False)

    assert response.status_code == 303
    assert not config.VAULT_FILE.exists()


def test_discarding_nothing_says_so(client):
    response = client.post("/vault/discard", data={"confirm": "discard"})

    assert response.status_code == 400
    assert "no encrypted configuration" in response.text


def test_a_run_in_flight_blocks_the_discard(client):
    """ansible-playbook is reading vault.yml right now; pulling it out from
    under a provisioning run would fail it half-way through."""
    existing_vault()
    runner._active = Run(action="create", target="shire", command=["true"])

    response = client.post("/vault/discard", data={"confirm": "discard"})

    assert response.status_code == 409
    assert config.VAULT_FILE.exists()


def test_the_vault_screen_offers_the_discard_only_when_there_is_one(client):
    assert "/vault/discard" not in client.get("/vault").text

    existing_vault()
    assert "/vault/discard" in client.get("/vault").text
