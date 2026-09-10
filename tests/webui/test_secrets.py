"""SC-005: neither secret is written to any file, logged, or listed as an
argument. Covers expiry and the restart-equivalent (fresh signing key)
invalidation described in data-model.md's Session Secrets state machine.
"""

from __future__ import annotations

import time

import pytest

from webui.secrets import SecretsUnavailable, SecretStore

TOKEN = "hcloud-secret-token"  # noqa: S105 - test fixture value, not a real secret
PASSWORD = "vault-secret-password"  # noqa: S105


def test_environment_raises_when_nothing_unlocked():
    store = SecretStore()
    session_id = store.ensure_session(None)
    with pytest.raises(SecretsUnavailable) as excinfo:
        store.environment(session_id)
    assert "Hetzner API token" in str(excinfo.value)
    assert "vault password" in str(excinfo.value)


def test_environment_raises_naming_only_the_missing_secret():
    store = SecretStore()
    session_id = store.ensure_session(None)
    store.unlock(session_id, hcloud_token=TOKEN)
    with pytest.raises(SecretsUnavailable) as excinfo:
        store.environment(session_id)
    assert excinfo.value.missing == ["vault password"]


def test_environment_carries_both_secrets_once_unlocked():
    store = SecretStore()
    session_id = store.ensure_session(None)
    store.unlock(session_id, hcloud_token=TOKEN, vault_password=PASSWORD)
    env = store.environment(session_id)
    assert env == {"HCLOUD_TOKEN": TOKEN, "ANSIBLE_VAULT_PASSWORD": PASSWORD}


def test_status_reflects_unlock_state():
    store = SecretStore()
    session_id = store.ensure_session(None)
    assert store.status(session_id) == {"token_unlocked": False, "vault_unlocked": False}
    store.unlock(session_id, hcloud_token=TOKEN)
    assert store.status(session_id) == {"token_unlocked": True, "vault_unlocked": False}


def test_lock_discards_both_secrets():
    store = SecretStore()
    session_id = store.ensure_session(None)
    store.unlock(session_id, hcloud_token=TOKEN, vault_password=PASSWORD)
    store.lock(session_id)
    assert store.status(session_id) == {"token_unlocked": False, "vault_unlocked": False}
    with pytest.raises(SecretsUnavailable):
        store.environment(session_id)


def test_inactivity_timeout_clears_secrets():
    store = SecretStore(inactivity_timeout=0.05)
    session_id = store.ensure_session(None)
    store.unlock(session_id, hcloud_token=TOKEN, vault_password=PASSWORD)
    time.sleep(0.1)
    assert store.status(session_id) == {"token_unlocked": False, "vault_unlocked": False}


def test_restart_equivalent_invalidates_the_cookie():
    store = SecretStore()
    session_id = store.ensure_session(None)
    store.unlock(session_id, hcloud_token=TOKEN, vault_password=PASSWORD)
    cookie = store.sign(session_id)

    restarted_store = SecretStore()  # fresh signing key, as after a process restart
    assert restarted_store.verify(cookie) is None


def test_verify_rejects_tampered_cookie():
    store = SecretStore()
    session_id = store.ensure_session(None)
    cookie = store.sign(session_id)
    tampered = cookie[:-1] + ("0" if cookie[-1] != "0" else "1")
    assert store.verify(tampered) is None


def test_session_repr_never_contains_secret_values():
    store = SecretStore()
    session_id = store.ensure_session(None)
    store.unlock(session_id, hcloud_token=TOKEN, vault_password=PASSWORD)
    dump = repr(store._sessions[session_id])
    assert TOKEN not in dump
    assert PASSWORD not in dump
