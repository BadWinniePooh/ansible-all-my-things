"""In-memory session secret store.

Holds the two secrets the specification forbids persisting: the Hetzner
API token and the Ansible Vault password (FR-011, FR-012, SC-005). Nothing
in this module ever writes either value to a file, a log line, or a
constructed argument list — they leave this process only through
:meth:`SecretStore.environment`, which callers use to build a subprocess
environment (see contracts/playbook-invocation.md).

The cookie signing key is generated fresh at process start and never
persisted, so a restart invalidates every session (data-model.md, Session
Secrets state transitions).
"""

from __future__ import annotations

import hmac
import os
import secrets as _secrets
import threading
import time
from dataclasses import dataclass, field

from . import config


class SecretsUnavailable(Exception):
    """Raised when an action needs a secret that is not currently unlocked."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(f"missing required secret(s): {', '.join(missing)}")


@dataclass
class _Session:
    session_id: str
    # repr=False: the default dataclass repr would otherwise print these
    # values verbatim into any accidental log or debugger dump of a
    # _Session (SC-005 — neither secret may appear in any log record).
    hcloud_token: str | None = field(default=None, repr=False)
    vault_password: str | None = field(default=None, repr=False)
    last_seen: float = field(default_factory=time.monotonic)


class SecretStore:
    def __init__(self, inactivity_timeout: float = config.INACTIVITY_TIMEOUT_SECONDS):
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()
        self._inactivity_timeout = inactivity_timeout
        # Random per process, never written to disk: a restart is a hard
        # invalidation of every outstanding session cookie.
        self._signing_key = os.urandom(32)

    def sign(self, session_id: str) -> str:
        mac = hmac.new(self._signing_key, session_id.encode(), "sha256").hexdigest()
        return f"{session_id}.{mac}"

    def verify(self, cookie_value: str | None) -> str | None:
        """Return the session id carried by a cookie, or None if absent/tampered."""
        if not cookie_value or "." not in cookie_value:
            return None
        session_id, _, mac = cookie_value.rpartition(".")
        expected = hmac.new(self._signing_key, session_id.encode(), "sha256").hexdigest()
        if not hmac.compare_digest(mac, expected):
            return None
        return session_id

    def _get_locked(self, session_id: str) -> _Session | None:
        entry = self._sessions.get(session_id)
        if entry is None:
            return None
        if time.monotonic() - entry.last_seen > self._inactivity_timeout:
            del self._sessions[session_id]
            return None
        return entry

    def ensure_session(self, session_id: str | None) -> str:
        """Return a valid, touched session id, creating one if needed."""
        with self._lock:
            if session_id is not None:
                entry = self._get_locked(session_id)
                if entry is not None:
                    entry.last_seen = time.monotonic()
                    return session_id
            new_id = _secrets.token_urlsafe(32)
            self._sessions[new_id] = _Session(session_id=new_id)
            return new_id

    def unlock(
        self,
        session_id: str,
        *,
        hcloud_token: str | None = None,
        vault_password: str | None = None,
    ) -> None:
        with self._lock:
            entry = self._get_locked(session_id) or _Session(session_id=session_id)
            self._sessions[session_id] = entry
            if hcloud_token is not None:
                entry.hcloud_token = hcloud_token
            if vault_password is not None:
                entry.vault_password = vault_password
            entry.last_seen = time.monotonic()

    def lock(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def forget_vault_password(self) -> None:
        """Drop the vault password from every session, keeping the tokens.

        Called when the encrypted configuration is discarded: the file
        those passwords opened no longer exists, so a session still
        holding one would write the next configuration under a password
        the user never re-entered -- the same silent-canonicalisation
        problem discarding is there to escape. Every session, not just the
        one that asked, because there is one vault.yml for the whole
        installation.
        """
        with self._lock:
            for entry in self._sessions.values():
                entry.vault_password = None

    def status(self, session_id: str | None) -> dict[str, bool]:
        with self._lock:
            entry = self._get_locked(session_id) if session_id else None
            return {
                "token_unlocked": entry is not None and entry.hcloud_token is not None,
                "vault_unlocked": entry is not None and entry.vault_password is not None,
            }

    def vault_password(self, session_id: str) -> str | None:
        with self._lock:
            entry = self._get_locked(session_id)
            return entry.vault_password if entry else None

    def hcloud_token(self, session_id: str) -> str | None:
        with self._lock:
            entry = self._get_locked(session_id)
            return entry.hcloud_token if entry else None

    def environment(self, session_id: str) -> dict[str, str]:
        """Environment variables to inject into an ansible-playbook subprocess.

        Raises SecretsUnavailable naming exactly what is missing (FR-014)
        rather than letting the subprocess start and fail.
        """
        with self._lock:
            entry = self._get_locked(session_id)
        missing = []
        if entry is None or entry.hcloud_token is None:
            missing.append("Hetzner API token")
        if entry is None or entry.vault_password is None:
            missing.append("vault password")
        if missing:
            raise SecretsUnavailable(missing)
        return {
            "HCLOUD_TOKEN": entry.hcloud_token,
            "ANSIBLE_VAULT_PASSWORD": entry.vault_password,
        }
