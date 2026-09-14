"""The operator's own SSH public keys (FR-058): the generated private key
never leaves the volume (FR-024), so these are the only way the operator
can log in to a machine themselves. Every non-blank line must be a public
key, and a rejected line is reported by number only -- it may be a pasted
private key.

ansible-vault is stubbed; ssh-keygen is real, because what is under test
is exactly what ssh-keygen accepts as a public key.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from webui import config, hcloud_api, inventory, pool, seed, sshkeys
from webui import vault as vault_module
from webui.app import SESSION_COOKIE_NAME, app, secret_store

PASSWORD = "vault-password"  # noqa: S105

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen not on PATH")


@pytest.fixture
def keypair(tmp_path) -> tuple[str, str]:
    """A real ed25519 keypair: (public key line, private key file content)."""
    private_key_file = tmp_path / "operator"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(private_key_file), "-N", "", "-C", "you@laptop", "-q"],
        check=True,
        capture_output=True,
    )
    public_key = (tmp_path / "operator.pub").read_text(encoding="utf-8").strip()
    return public_key, private_key_file.read_text(encoding="utf-8")


@pytest.fixture
def written(monkeypatch, tmp_path) -> list[dict]:
    """Every document the vault screen writes, with the rest of the volume
    answered from tmp_path."""
    documents: list[dict] = []
    monkeypatch.setattr(config, "VAULT_FILE", tmp_path / "vault.yml")
    monkeypatch.setattr(config, "COST_DB_FILE", tmp_path / "costs.sqlite3")
    monkeypatch.setattr(hcloud_api, "list_servers", lambda token: [])
    monkeypatch.setattr(inventory, "list_machines", lambda **kwargs: [])
    monkeypatch.setattr(pool, "status", lambda **kwargs: pool.PoolStatus([], [], []))
    monkeypatch.setattr(seed, "needs_defaults_refresh", lambda: False)
    monkeypatch.setattr(vault_module, "load_template", lambda **kwargs: {})
    monkeypatch.setattr(
        vault_module,
        "read_vault",
        lambda password, **kwargs: {"vault_my_ssh_public_key": "ssh-ed25519 MANAGED"},
    )
    monkeypatch.setattr(
        vault_module, "write_vault", lambda document, password, **kwargs: documents.append(document)
    )
    config.VAULT_FILE.write_text("$ANSIBLE_VAULT;1.1;AES256\n", encoding="utf-8")
    return documents


@pytest.fixture
def client(written):
    with TestClient(app) as test_client:
        test_client.get("/")
        session_id = secret_store.verify(test_client.cookies[SESSION_COOKIE_NAME])
        secret_store.unlock(session_id, hcloud_token=None, vault_password=PASSWORD)
        yield test_client


def test_public_keys_are_kept_and_blank_lines_ignored(keypair):
    public_key, _ = keypair

    keys, invalid_lines = sshkeys.parse_additional_public_keys(f"\n  {public_key}  \r\n\n")

    assert keys == [public_key]
    assert invalid_lines == []


def test_a_private_key_and_a_typo_are_rejected_by_line_number(keypair):
    public_key, private_key = keypair
    text = "\n".join([public_key, "ssh-ed25519 not-base64", *private_key.splitlines()])

    keys, invalid_lines = sshkeys.parse_additional_public_keys(text)

    assert keys == [public_key]
    assert invalid_lines == list(range(2, 3 + len(private_key.splitlines())))


def test_saving_stores_the_keys_as_a_list(client, written, keypair):
    public_key, _ = keypair

    response = client.post(
        "/vault",
        data={"vault_my_additional_ssh_public_keys": f"{public_key}\n"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert written[-1]["vault_my_additional_ssh_public_keys"] == [public_key]
    assert written[-1]["vault_my_ssh_public_key"] == "ssh-ed25519 MANAGED"


def test_one_bad_line_saves_nothing_and_names_only_its_number(client, written, keypair):
    public_key, _ = keypair

    response = client.post(
        "/vault",
        data={"vault_my_additional_ssh_public_keys": f"{public_key}\nnot a key\n"},
    )

    assert response.status_code == 400
    assert written == []
    assert "Line 2 of your SSH public keys is not an OpenSSH public key" in response.text


def test_stored_keys_are_shown_one_per_line(client, monkeypatch):
    monkeypatch.setattr(
        vault_module,
        "read_vault",
        lambda password, **kwargs: {"vault_my_additional_ssh_public_keys": ["ssh-ed25519 A", "ssh-ed25519 B"]},
    )

    body = client.get("/vault").text

    assert "ssh-ed25519 A\nssh-ed25519 B</textarea>" in body
