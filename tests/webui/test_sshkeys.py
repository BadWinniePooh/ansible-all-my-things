"""Rotation is list-then-delete-then-create under the configured key name;
a pre-existing remote key blocks the action until confirmed; the vault
write preserves fields the key-management flow doesn't touch. Hetzner API
calls and vault I/O are mocked here -- test_vault.py already covers the
real ansible-vault round trip.
"""

from __future__ import annotations

import shutil

import pytest

from webui import hcloud_api, sshkeys, vault

PASSWORD = "vault-password"  # noqa: S105
TOKEN = "hcloud-token"  # noqa: S105

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen not on PATH")


@pytest.fixture
def vault_store(monkeypatch):
    """A tiny in-memory stand-in for vault.read_vault/write_vault, keyed
    only by the path passed in -- enough to assert merge behaviour without
    needing a real ansible-vault binary."""
    store: dict[str, dict] = {}

    def fake_read(password, *, vault_file):
        assert password == PASSWORD
        return dict(store.get(str(vault_file), {}))

    def fake_write(document, password, *, vault_file):
        assert password == PASSWORD
        store[str(vault_file)] = dict(document)

    monkeypatch.setattr(vault, "read_vault", fake_read)
    monkeypatch.setattr(vault, "write_vault", fake_write)
    return store


def test_generate_creates_key_when_none_exists_remotely(tmp_path, monkeypatch, vault_store):
    monkeypatch.setattr(hcloud_api, "find_ssh_key_by_name", lambda token, name: None)
    created = {}

    def fake_create(token, name, public_key):
        created["name"] = name
        created["public_key"] = public_key
        return {"id": 1, "name": name}

    monkeypatch.setattr(hcloud_api, "create_ssh_key", fake_create)
    monkeypatch.setattr(
        hcloud_api, "delete_ssh_key", lambda *a, **k: pytest.fail("should not delete")
    )

    private_key_file = tmp_path / "ssh" / "id_ed25519"
    vault_file = tmp_path / "vault.yml"

    info = sshkeys.generate_and_register(
        token=TOKEN,
        vault_password=PASSWORD,
        key_name="my-key",
        private_key_file=private_key_file,
        vault_file=vault_file,
    )

    assert created["name"] == "my-key"
    assert created["public_key"] == info.public_key
    assert private_key_file.exists()
    assert vault_store[str(vault_file)]["vault_my_ssh_key_name"] == "my-key"
    assert vault_store[str(vault_file)]["vault_my_ssh_public_key"] == info.public_key


def test_generate_raises_when_remote_key_exists_without_confirmation(
    tmp_path, monkeypatch, vault_store
):
    monkeypatch.setattr(
        hcloud_api, "find_ssh_key_by_name", lambda token, name: {"id": 99, "name": name}
    )
    monkeypatch.setattr(
        hcloud_api, "create_ssh_key", lambda *a, **k: pytest.fail("should not create")
    )

    private_key_file = tmp_path / "ssh" / "id_ed25519"
    vault_file = tmp_path / "vault.yml"

    with pytest.raises(sshkeys.SshKeyExistsRemotely) as excinfo:
        sshkeys.generate_and_register(
            token=TOKEN,
            vault_password=PASSWORD,
            key_name="my-key",
            private_key_file=private_key_file,
            vault_file=vault_file,
        )

    assert excinfo.value.existing["id"] == 99
    assert not private_key_file.exists()
    assert str(vault_file) not in vault_store


def test_rotate_deletes_then_creates_when_confirmed(tmp_path, monkeypatch, vault_store):
    calls = []
    monkeypatch.setattr(
        hcloud_api,
        "find_ssh_key_by_name",
        lambda token, name: {"id": 7, "name": name},
    )
    monkeypatch.setattr(
        hcloud_api, "delete_ssh_key", lambda token, key_id: calls.append(("delete", key_id))
    )
    monkeypatch.setattr(
        hcloud_api,
        "create_ssh_key",
        lambda token, name, public_key: calls.append(("create", name)) or {"id": 8, "name": name},
    )

    private_key_file = tmp_path / "ssh" / "id_ed25519"
    vault_file = tmp_path / "vault.yml"

    sshkeys.generate_and_register(
        token=TOKEN,
        vault_password=PASSWORD,
        key_name="my-key",
        confirm_replace=True,
        private_key_file=private_key_file,
        vault_file=vault_file,
    )

    assert calls == [("delete", 7), ("create", "my-key")]


def test_unrelated_vault_field_survives_key_registration(tmp_path, monkeypatch, vault_store):
    monkeypatch.setattr(hcloud_api, "find_ssh_key_by_name", lambda token, name: None)
    monkeypatch.setattr(
        hcloud_api, "create_ssh_key", lambda token, name, public_key: {"id": 1, "name": name}
    )

    private_key_file = tmp_path / "ssh" / "id_ed25519"
    vault_file = tmp_path / "vault.yml"
    vault_store[str(vault_file)] = {"vault_my_ansible_user_name": "alice"}

    sshkeys.generate_and_register(
        token=TOKEN,
        vault_password=PASSWORD,
        key_name="my-key",
        private_key_file=private_key_file,
        vault_file=vault_file,
    )

    assert vault_store[str(vault_file)]["vault_my_ansible_user_name"] == "alice"
    assert vault_store[str(vault_file)]["vault_my_ssh_key_name"] == "my-key"
