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
    """OS images available for the account, restricted to x86 system images
    (this UI only offers x86 server types below) -- not snapshots, apps,
    backups, or deprecated images."""
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


def find_image(token: str, reference: str) -> dict | None:
    """Look up one image by the reference a user typed into the create
    form's free-text field, returning None when the account has no such
    image. Both forms the playbook's `image:` parameter accepts are
    supported: a name (`ubuntu-24.04`) and a numeric id, which is the only
    way to reach a snapshot -- snapshots carry a description rather than a
    name, so the name filter never matches one.

    Restricted to x86, matching the server sizes this interface offers: an
    arm64 image reported as "found" here would fail at provisioning time,
    which is exactly what checking up front exists to prevent.
    """
    reference = reference.strip()
    if not reference:
        return None

    if reference.isdigit():
        response = httpx.get(
            f"{API_BASE}/images/{reference}", headers=_headers(token), timeout=_TIMEOUT
        )
        if response.status_code == 404:
            return None
        _raise_for_status(response)
        image = response.json().get("image")
        # The by-id endpoint has no architecture filter, so it is applied
        # here to keep both branches answering the same question.
        if image and image.get("architecture") != "x86":
            return None
        return image

    response = httpx.get(
        f"{API_BASE}/images",
        headers=_headers(token),
        params={"name": reference, "architecture": "x86", "status": "available"},
        timeout=_TIMEOUT,
    )
    _raise_for_status(response)
    images = response.json().get("images", [])
    return images[0] if images else None


def list_servers(token: str) -> list[dict]:
    """Every server in the account.

    The machine records the playbooks write only carry what an SSH
    connection needs, and the interface's own side file only knows about
    machines it provisioned itself -- so size and location read as
    "unknown" for anything created from the command line, restored from a
    backup, or provisioned before that file existed. The account itself is
    the authority on what a machine actually is, so the dashboard asks it.

    Paginated defensively: the default page size is 25, and a pool of ten
    names is not a guarantee that an account holds ten servers.
    """
    servers: list[dict] = []
    page = 1
    while True:
        response = httpx.get(
            f"{API_BASE}/servers",
            headers=_headers(token),
            params={"page": page, "per_page": 50, "sort": "name:asc"},
            timeout=_TIMEOUT,
        )
        _raise_for_status(response)
        payload = response.json()
        servers.extend(payload.get("servers", []))
        next_page = ((payload.get("meta") or {}).get("pagination") or {}).get("next_page")
        if not next_page:
            return servers
        page = next_page


def list_server_types(token: str) -> list[dict]:
    """Server types (sizes) available for the account. /server_types has no
    architecture or deprecation filter params (unlike /images), so both are
    applied client-side by the caller. Each entry's `prices` array names
    the locations that type can actually be ordered in -- there is no
    separate per-type "locations" field -- which is also this UI's only
    source for which locations are selectable for a given server type."""
    response = httpx.get(
        f"{API_BASE}/server_types",
        headers=_headers(token),
        params={"sort": "name:asc"},
        timeout=_TIMEOUT,
    )
    _raise_for_status(response)
    return response.json().get("server_types", [])
