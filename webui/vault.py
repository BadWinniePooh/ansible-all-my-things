"""Encrypted-configuration read/write via ansible-vault.

Mirrors the subprocess discipline in runner.py: the vault password
reaches the child only through its environment, and plaintext content
travels only through the child's stdin/stdout -- never through a
temporary file, memory-backed or otherwise (research.md section 5).
Verified against a real ansible-vault 2.21.1: `ansible-vault encrypt
--output=<file> -` reads plaintext from stdin and writes ciphertext to
<file>, including overwriting an already-encrypted file in place; a wrong
password makes `ansible-vault view` exit non-zero without touching the
file on disk.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import yaml

from . import config

# FR-019: managed by the SSH-key feature, never hand-editable through the
# configuration form. webui/sshkeys.py is the only code that legitimately
# writes these, via merge() rather than apply_form_values().
MANAGED_KEYS = {"vault_my_ssh_key_name", "vault_my_ssh_public_key"}


class VaultPasswordMismatch(Exception):
    """Wrong vault password: the stored file is left untouched (spec.md US2
    Acceptance Scenario 3; data-model.md Encrypted Configuration state
    transitions)."""


def _run_vault(
    args: list[str], *, vault_password: str, input_text: str | None = None
) -> subprocess.CompletedProcess:
    # ansible-vault must see exactly one password source: the explicit
    # --vault-password-file below. Two things can hand it a second source
    # and trigger "the vault-ids default,default are available to encrypt"
    # instead of picking one:
    #   1. ANSIBLE_CONFIG in the inherited environment. The webui container
    #      sets this persistently (ENV ANSIBLE_CONFIG=/ansible/ansible.cfg
    #      in .docker/Dockerfile.web) so runner.py's ansible-playbook calls
    #      can find it -- but that same inherited env var makes a plain
    #      **os.environ copy just as visible to ansible-vault here, which
    #      then discovers ansible.cfg's own vault_password_file setting
    #      regardless of cwd. Popped below rather than overridden: a
    #      nonexistent ANSIBLE_CONFIG path does NOT disable discovery, it
    #      falls through to the next source (verified against a real
    #      ansible-vault 2.21.1).
    #   2. ansible.cfg discovered via cwd. The webui process's cwd is
    #      /ansible, which has ansible.cfg. Running from a directory with
    #      no ansible.cfg avoids this source too.
    environment = {**os.environ, "ANSIBLE_VAULT_PASSWORD": vault_password}
    environment.pop("ANSIBLE_CONFIG", None)
    return subprocess.run(
        [
            "ansible-vault",
            *args,
            "--vault-password-file",
            str(config.VAULT_PASSWORD_SCRIPT),
        ],
        input=input_text,
        capture_output=True,
        text=True,
        env=environment,
        cwd=tempfile.gettempdir(),
    )


def load_template(template_file: Path = config.VAULT_TEMPLATE_FILE) -> dict:
    """The form schema's single source of truth (FR-017)."""
    return yaml.safe_load(template_file.read_text(encoding="utf-8")) or {}


def read_vault(vault_password: str, *, vault_file: Path = config.VAULT_FILE) -> dict:
    """The decrypted configuration, or {} when nothing has been saved yet."""
    if not vault_file.exists():
        return {}
    result = _run_vault(["view", str(vault_file)], vault_password=vault_password)
    if result.returncode != 0:
        raise VaultPasswordMismatch(result.stderr.strip() or "vault password does not match")
    return yaml.safe_load(result.stdout) or {}


def write_vault(document: dict, vault_password: str, *, vault_file: Path = config.VAULT_FILE) -> None:
    plaintext = yaml.safe_dump(document, default_flow_style=False, sort_keys=False)
    vault_file.parent.mkdir(parents=True, exist_ok=True)
    result = _run_vault(
        ["encrypt", "--output", str(vault_file), "-"],
        vault_password=vault_password,
        input_text=plaintext,
    )
    if result.returncode != 0:
        raise VaultPasswordMismatch(result.stderr.strip() or "vault password does not match")


def merge(existing: dict, values: dict) -> dict:
    """Merge `values` over `existing`. A key in `existing` absent from
    `values` survives unchanged (FR-018) -- the load-bearing preservation
    rule for keys the template does not model.
    """
    return {**existing, **values}


def apply_form_values(existing: dict, form_values: dict) -> dict:
    """As merge(), but a managed key present in `form_values` is silently
    discarded rather than applied (FR-019). Used by the /vault route;
    webui/sshkeys.py writes managed keys directly through merge().
    """
    filtered = {key: value for key, value in form_values.items() if key not in MANAGED_KEYS}
    return merge(existing, filtered)


def create_vault(vault_password: str, *, vault_file: Path = config.VAULT_FILE) -> None:
    """Create the encrypted configuration under a brand-new password.

    Separate from write_vault() because the first write is the one nobody
    can check afterwards: from here on every password is verified against
    this file, so a file that does not open under the string that made it
    locks the configuration for good. The write is therefore read back
    immediately, and a file that fails that read is removed rather than
    left behind as an undecryptable vault.yml.
    """
    write_vault({}, vault_password, vault_file=vault_file)
    try:
        read_vault(vault_password, vault_file=vault_file)
    except VaultPasswordMismatch:
        vault_file.unlink(missing_ok=True)
        raise


def discard(vault_file: Path = config.VAULT_FILE) -> bool:
    """Delete the encrypted configuration; True if there was one.

    The recovery path for a vault password nobody can reproduce: without
    it, ansible-playbook cannot even destroy a machine, because Ansible
    auto-loads group_vars/all/vault.yml for every host and fails at
    decryption before the play starts. Deliberately deletes rather than
    renames a backup aside -- a file kept "just in case" is a file nobody
    can ever open again, holding the passwords of every account the
    configuration named.
    """
    existed = vault_file.exists()
    vault_file.unlink(missing_ok=True)
    return existed
