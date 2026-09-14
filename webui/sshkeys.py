"""SSH keypair generation, Hetzner registration and rotation.

research.md section 10: rotation is list-then-delete-then-create under the
configured key name. If a key already exists in the Hetzner account under
that name -- from an earlier installation, or a prior rotation -- this
module reports what it found and requires `confirm_replace=True` before
touching it, rather than silently clobbering a key that may belong to
something else (spec.md edge case "Key name already registered").
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import config, hcloud_api, vault


class SshKeyExistsRemotely(Exception):
    """A key is already registered in Hetzner under the configured name;
    the caller must re-invoke with confirm_replace=True to replace it."""

    def __init__(self, existing: dict):
        self.existing = existing
        super().__init__(f"a key named {existing.get('name')!r} already exists in Hetzner")


@dataclass
class KeyPairInfo:
    public_key: str
    fingerprint: str | None
    registered_name: str


def public_key_file(private_key_file: Path = config.SSH_PRIVATE_KEY_FILE) -> Path:
    return private_key_file.with_name(private_key_file.name + ".pub")


def read_public_key(private_key_file: Path = config.SSH_PRIVATE_KEY_FILE) -> str | None:
    pub = public_key_file(private_key_file)
    if not pub.exists():
        return None
    return pub.read_text(encoding="utf-8").strip()


def fingerprint(private_key_file: Path = config.SSH_PRIVATE_KEY_FILE) -> str | None:
    pub = public_key_file(private_key_file)
    if not pub.exists():
        return None
    result = subprocess.run(
        ["ssh-keygen", "-lf", str(pub)], capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else None


def is_public_key(line: str) -> bool:
    """True when `line` is one OpenSSH public key, as ssh-keygen reads it."""
    result = subprocess.run(
        ["ssh-keygen", "-l", "-f", "-"], input=line + "\n", capture_output=True, text=True
    )
    return result.returncode == 0


def parse_additional_public_keys(text: str) -> tuple[list[str], list[int]]:
    """The operator's own public keys, one per line, and the 1-based numbers
    of the lines that are not a public key. Blank lines are ignored.

    Line numbers rather than line content are what a caller reports: a
    line that fails is as likely a pasted private key as a typo, and it
    must not be repeated in a message.
    """
    keys: list[str] = []
    invalid_lines: list[int] = []
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if is_public_key(line):
            keys.append(line)
        else:
            invalid_lines.append(number)
    return keys, invalid_lines


def _generate_local_keypair(private_key_file: Path) -> None:
    private_key_file.parent.mkdir(parents=True, exist_ok=True)
    pub = public_key_file(private_key_file)
    for existing in (private_key_file, pub):
        if existing.exists():
            existing.unlink()
    subprocess.run(
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-f",
            str(private_key_file),
            "-N",
            "",
            "-C",
            "ansible-web-frontend",
            "-q",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    private_key_file.chmod(0o600)


def generate_and_register(
    *,
    token: str,
    vault_password: str,
    key_name: str = config.DEFAULT_SSH_KEY_NAME,
    confirm_replace: bool = False,
    private_key_file: Path = config.SSH_PRIVATE_KEY_FILE,
    vault_file: Path = config.VAULT_FILE,
) -> KeyPairInfo:
    """Generate a fresh local keypair and register it in Hetzner under
    `key_name`. Used for both first-time generation and rotation (T030,
    T031) -- the two differ only in the confirmation flow the route layer
    applies before calling this (T032, T033).
    """
    existing_remote = hcloud_api.find_ssh_key_by_name(token, key_name)
    if existing_remote is not None and not confirm_replace:
        raise SshKeyExistsRemotely(existing_remote)

    _generate_local_keypair(private_key_file)
    public_key = read_public_key(private_key_file)

    if existing_remote is not None:
        hcloud_api.delete_ssh_key(token, existing_remote["id"])
    hcloud_api.create_ssh_key(token, key_name, public_key)

    existing_doc = vault.read_vault(vault_password, vault_file=vault_file)
    updated = vault.merge(
        existing_doc,
        {"vault_my_ssh_key_name": key_name, "vault_my_ssh_public_key": public_key},
    )
    vault.write_vault(updated, vault_password, vault_file=vault_file)

    return KeyPairInfo(
        public_key=public_key,
        fingerprint=fingerprint(private_key_file),
        registered_name=key_name,
    )
