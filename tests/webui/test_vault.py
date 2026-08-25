"""FR-018 (unmodelled keys survive a save untouched), the wrong-password
error path, and FR-019 (a submitted managed-key value is discarded rather
than merged). Exercises the real ansible-vault binary -- these tests only
run where ansible-vault runs (Linux; not natively on Windows, where the
ansible CLI's blocking-io check fails regardless of this feature).
"""

from __future__ import annotations

import shutil

import pytest

from webui import vault

pytestmark = pytest.mark.skipif(
    shutil.which("ansible-vault") is None, reason="ansible-vault not on PATH"
)

PASSWORD = "correct-vault-password"  # noqa: S105
WRONG_PASSWORD = "wrong-vault-password"  # noqa: S105


def test_round_trip_preserves_an_unmodelled_key(tmp_path):
    vault_file = tmp_path / "vault.yml"
    original = {
        "vault_my_ansible_user_name": "alice",
        "vault_my_ssh_key_name": "prior-key",
        "vault_my_ssh_public_key": "ssh-ed25519 AAAA...",
        "future_field_the_template_does_not_know_about": "keep-me",
    }
    vault.write_vault(original, PASSWORD, vault_file=vault_file)

    read_back = vault.read_vault(PASSWORD, vault_file=vault_file)
    assert read_back["future_field_the_template_does_not_know_about"] == "keep-me"

    form_values = {"vault_my_ansible_user_name": "bob"}
    merged = vault.apply_form_values(read_back, form_values)
    vault.write_vault(merged, PASSWORD, vault_file=vault_file)

    final = vault.read_vault(PASSWORD, vault_file=vault_file)
    assert final["vault_my_ansible_user_name"] == "bob"
    assert final["future_field_the_template_does_not_know_about"] == "keep-me"
    assert final["vault_my_ssh_key_name"] == "prior-key"


def test_wrong_password_raises_and_leaves_file_untouched(tmp_path):
    vault_file = tmp_path / "vault.yml"
    vault.write_vault({"vault_my_ansible_user_name": "alice"}, PASSWORD, vault_file=vault_file)
    before = vault_file.read_bytes()

    with pytest.raises(vault.VaultPasswordMismatch):
        vault.read_vault(WRONG_PASSWORD, vault_file=vault_file)

    assert vault_file.read_bytes() == before


def test_missing_vault_file_reads_as_empty(tmp_path):
    assert vault.read_vault(PASSWORD, vault_file=tmp_path / "does-not-exist.yml") == {}


def test_apply_form_values_discards_submitted_managed_keys():
    existing = {
        "vault_my_ssh_key_name": "real-key",
        "vault_my_ssh_public_key": "ssh-ed25519 REAL...",
    }
    form_values = {
        "vault_my_ssh_key_name": "attacker-supplied-name",
        "vault_my_ssh_public_key": "ssh-ed25519 FAKE...",
        "vault_my_ansible_user_name": "alice",
    }
    merged = vault.apply_form_values(existing, form_values)
    assert merged["vault_my_ssh_key_name"] == "real-key"
    assert merged["vault_my_ssh_public_key"] == "ssh-ed25519 REAL..."
    assert merged["vault_my_ansible_user_name"] == "alice"


def test_merge_keeps_unmentioned_existing_keys():
    existing = {"a": 1, "b": 2}
    merged = vault.merge(existing, {"b": 20})
    assert merged == {"a": 1, "b": 20}
