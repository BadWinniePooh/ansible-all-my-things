"""Direct Hetzner Cloud API calls: SSH key management (research.md section
10) and OS image listing. Everything about a machine's actual lifecycle
still goes through the playbooks via webui/runner.py -- this module exists
only for read-only lookups and account mutations that Principle II does not
let a playbook express inline.
"""

from __future__ import annotations

import httpx

API_BASE = "https://api.hetzner.cloud/v1"
_TIMEOUT = 15.0


class HetznerApiError(Exception):
    """Surfaces the API's own error message rather than a generic one
    (Principle XII)."""


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    detail = None
    try:
        detail = response.json().get("error", {}).get("message")
    except ValueError:
        pass
    raise HetznerApiError(detail or f"Hetzner API returned HTTP {response.status_code}")


def find_ssh_key_by_name(token: str, name: str) -> dict | None:
    response = httpx.get(
        f"{API_BASE}/ssh_keys", headers=_headers(token), params={"name": name}, timeout=_TIMEOUT
    )
    _raise_for_status(response)
    keys = response.json().get("ssh_keys", [])
    return keys[0] if keys else None


def create_ssh_key(token: str, name: str, public_key: str) -> dict:
    response = httpx.post(
        f"{API_BASE}/ssh_keys",
        headers=_headers(token),
        json={"name": name, "public_key": public_key},
        timeout=_TIMEOUT,
    )
    _raise_for_status(response)
    return response.json()["ssh_key"]


def delete_ssh_key(token: str, key_id: int) -> None:
    response = httpx.delete(
        f"{API_BASE}/ssh_keys/{key_id}", headers=_headers(token), timeout=_TIMEOUT
    )
    _raise_for_status(response)


def validate_token(token: str) -> None:
    """Raises HetznerApiError if Hetzner rejects the token. /session/unlock
    calls this before storing anything, rather than accepting any string
    and letting a bogus token surface only much later, deep inside a
    playbook run. /locations is a cheap, read-only, always-present
    endpoint -- any valid token can read it, so it makes a minimal-impact
    liveness check."""
    response = httpx.get(f"{API_BASE}/locations", headers=_headers(token), timeout=_TIMEOUT)
    _raise_for_status(response)


def list_images(token: str) -> list[dict]:
    """OS images available for the account, restricted to the x86 system
    images the configured server types (cx23/cx33/cx43, all shared-vCPU
    x86) can actually boot -- not snapshots, apps, backups, or deprecated
    images."""
    response = httpx.get(
        f"{API_BASE}/images",
        headers=_headers(token),
        params={
            "type": "system",
            "status": "available",
            "architecture": "x86",
            "sort": "name:asc",
        },
        timeout=_TIMEOUT,
    )
    _raise_for_status(response)
    return response.json().get("images", [])
