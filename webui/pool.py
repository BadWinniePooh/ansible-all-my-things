"""Machine name pool.

Load path (T012): reads inventories/.webui/hostname_pool_hcloud.yml when
present, else falls back to the repository default at
playbooks/vars/hostname_pool_hcloud.yml -- mirroring the stat-check
playbooks/tasks/create/hcloud.yml gains in T051, so the interface and the
command-line path agree on which file is authoritative.

Validation and the save path (US7, T052) are added alongside this module
later; this file starts read-only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import config, inventory

# Mirrors the naming rule Hetzner Cloud enforces on server names
# (data-model.md, Machine Name Pool validation).
NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
MAX_NAME_LENGTH = 63


def load(
    *,
    pool_file: Path = config.POOL_FILE,
    default_pool_file: Path = config.DEFAULT_POOL_FILE,
) -> list[str]:
    source = pool_file if pool_file.exists() else default_pool_file
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    return list(data.get("hostname_pool") or [])


@dataclass
class PoolStatus:
    entries: list[str]
    used: list[str]
    free: list[str]


def status(
    *,
    pool_file: Path = config.POOL_FILE,
    default_pool_file: Path = config.DEFAULT_POOL_FILE,
    autogen_inventory_file: Path = config.AUTOGEN_INVENTORY_FILE,
) -> PoolStatus:
    entries = load(pool_file=pool_file, default_pool_file=default_pool_file)
    used_names = inventory.machine_names(autogen_inventory_file=autogen_inventory_file)
    used = [name for name in entries if name in used_names]
    free = [name for name in entries if name not in used_names]
    return PoolStatus(entries=entries, used=used, free=free)


class PoolValidationError(Exception):
    """Names the offending entry and why it was rejected (Principle XII)."""

    def __init__(self, entry: str, reason: str):
        self.entry = entry
        self.reason = reason
        super().__init__(f"{entry!r}: {reason}")


def validate_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise PoolValidationError(
            name,
            "must be lowercase letters, digits and hyphens only, "
            "with no leading or trailing hyphen",
        )
    if len(name) > MAX_NAME_LENGTH:
        raise PoolValidationError(name, f"must be at most {MAX_NAME_LENGTH} characters")


def validate_entries(entries: list[str]) -> None:
    seen: set[str] = set()
    for name in entries:
        validate_name(name)
        if name in seen:
            raise PoolValidationError(name, "duplicate entry")
        seen.add(name)


def save(
    entries: list[str],
    *,
    pool_file: Path = config.POOL_FILE,
    default_pool_file: Path = config.DEFAULT_POOL_FILE,
    autogen_inventory_file: Path = config.AUTOGEN_INVENTORY_FILE,
) -> None:
    """Validate the whole submission and write it, rejecting on any invalid
    entry or the removal/rename of a name a live machine uses (FR-042,
    FR-043) before anything is written. A rename is a remove-then-add from
    `entries`' point of view, so the same in-use check covers both.
    """
    validate_entries(entries)

    current = load(pool_file=pool_file, default_pool_file=default_pool_file)
    used_names = inventory.machine_names(autogen_inventory_file=autogen_inventory_file)
    removed_but_in_use = (set(current) - set(entries)) & used_names
    if removed_but_in_use:
        raise PoolValidationError(
            next(iter(removed_but_in_use)),
            "is used by a live machine and cannot be removed or renamed",
        )

    pool_file.parent.mkdir(parents=True, exist_ok=True)
    document = {"hostname_pool": entries}
    pool_file.write_text(
        yaml.safe_dump(document, default_flow_style=False, sort_keys=False), encoding="utf-8"
    )


def next_free_name(
    *,
    pool_file: Path = config.POOL_FILE,
    default_pool_file: Path = config.DEFAULT_POOL_FILE,
    autogen_inventory_file: Path = config.AUTOGEN_INVENTORY_FILE,
) -> str | None:
    """First free entry in pool order, or None when exhausted (FR-045)."""
    pool = status(
        pool_file=pool_file,
        default_pool_file=default_pool_file,
        autogen_inventory_file=autogen_inventory_file,
    )
    return pool.free[0] if pool.free else None
