"""FR-018 (unmodelled keys survive a save untouched), the wrong-password
error path, and FR-019 (a submitted managed-key value is discarded rather
than merged). Exercises the real ansible-vault binary -- these tests only
run where ansible-vault actually starts (Linux; not natively on Windows,
where ansible-core's CLI startup guards in ansible/cli/__init__.py reject
the process before any of our code runs -- either check_blocking_io() or
initialize_locale(), depending on how stdio is attached. The locale guard
specifically calls the raw C setlocale(LC_ALL, ''), which resolves from
the Windows OS-level Region setting alone; confirmed neither PYTHONUTF8=1
nor an LC_ALL/LANG env var changes its result on Windows -- there is no
in-repo fix, only a machine-wide Windows Region setting change (out of
scope here). shutil.which() alone can't detect this, since the binary is
present and merely refuses to start, so this probes it for real instead.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from webui import vault


def _ansible_vault_usable() -> bool:
    if shutil.which("ansible-vault") is None:
        return False
    result = subprocess.run(["ansible-vault", "--version"], capture_output=True, text=True)
    return result.returncode == 0


# Scoped to only the two tests below that actually shell out to the real
# binary -- test_missing_vault_file_reads_as_empty, apply_form_values and
# merge exercise pure Python with no subprocess involved, so they keep
# running (and passing) even where ansible-vault itself can't start.
requires_ansible_vault = pytest.mark.skipif(
    not _ansible_vault_usable(),
    reason="ansible-vault not usable in this environment (missing, or an ansible-core CLI "
    "startup guard rejects it -- see module docstring)",
)

PASSWORD = "correct-vault-password"  # noqa: S105
WRONG_PASSWORD = "wrong-vault-password"  # noqa: S105


@requires_ansible_vault
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


@requires_ansible_vault
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
