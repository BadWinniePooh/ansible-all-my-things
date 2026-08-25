"""Volume seeding and image-version-skew detection and refresh.

See specs/017-hcloud-web-frontend/research.md section 6. A fresh named
volume is populated by the container runtime from the image's own
``/ansible/inventories`` on first mount -- this module does not duplicate
that copy. Its job starts afterwards: recording which image version last
touched the volume (FR-047), and later refreshing only default files the
user has not edited (FR-048), using a build-time snapshot at
``/ansible/.inventories-pristine`` as the source of truth for "what a
fresh install of the new image would contain".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from . import config


def _read_text_or_none(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None


def current_image_version(image_version_file: Path = config.IMAGE_VERSION_FILE) -> str:
    version = _read_text_or_none(image_version_file)
    if not version:
        raise RuntimeError(
            f"{image_version_file} is missing or empty. It is stamped by the "
            "Dockerfile's IMAGE_VERSION build arg; this should never happen "
            "inside a built image."
        )
    return version


def seeded_version(seeded_version_file: Path = config.SEEDED_VERSION_FILE) -> str | None:
    return _read_text_or_none(seeded_version_file)


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _iter_pristine_files(pristine_dir: Path):
    for path in sorted(pristine_dir.rglob("*")):
        if path.is_file():
            yield path.relative_to(pristine_dir)


def _load_manifest(manifest_file: Path) -> dict[str, str]:
    text = _read_text_or_none(manifest_file)
    return json.loads(text) if text else {}


def _write_manifest(manifest_file: Path, manifest: dict[str, str]) -> None:
    manifest_file.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


@dataclass
class RefreshResult:
    updated: list[str]
    skipped_edited: list[str]
    added: list[str]


def ensure_seeded(
    *,
    state_dir: Path = config.STATE_DIR,
    pristine_dir: Path = config.PRISTINE_DEFAULTS_DIR,
    image_version_file: Path = config.IMAGE_VERSION_FILE,
    seeded_version_file: Path = config.SEEDED_VERSION_FILE,
    manifest_file: Path | None = None,
) -> None:
    """Run once at container startup, before the server accepts requests.

    On first run, records the current image version and a manifest of the
    pristine defaults' hashes -- the volume already equals those defaults,
    since the container runtime copies image content into an empty named
    volume on first mount. Idempotent: a later run is a no-op, because
    refresh_defaults() -- not this function -- is what ever overwrites
    volume content, and only on explicit request.
    """
    manifest_file = manifest_file or (state_dir / "seeded-manifest.json")
    state_dir.mkdir(parents=True, exist_ok=True)

    if seeded_version_file.exists():
        return

    version = current_image_version(image_version_file)
    manifest = {
        rel.as_posix(): _hash_file(pristine_dir / rel)
        for rel in _iter_pristine_files(pristine_dir)
    }
    _write_manifest(manifest_file, manifest)
    seeded_version_file.write_text(version + "\n", encoding="utf-8")


def needs_defaults_refresh(
    *,
    image_version_file: Path = config.IMAGE_VERSION_FILE,
    seeded_version_file: Path = config.SEEDED_VERSION_FILE,
) -> bool:
    seeded = seeded_version(seeded_version_file)
    if seeded is None:
        return False
    return seeded != current_image_version(image_version_file)


def refresh_defaults(
    *,
    inventories_dir: Path = config.INVENTORIES_DIR,
    state_dir: Path = config.STATE_DIR,
    pristine_dir: Path = config.PRISTINE_DEFAULTS_DIR,
    image_version_file: Path = config.IMAGE_VERSION_FILE,
    seeded_version_file: Path = config.SEEDED_VERSION_FILE,
    manifest_file: Path | None = None,
) -> RefreshResult:
    """Copy newer default files from the pristine snapshot into the volume.

    Only overwrites a file whose current content still matches the hash
    recorded the last time it was seeded or refreshed -- i.e. the user has
    not edited it since. FR-048: never touches the encrypted configuration,
    the machine records, the name pool or the presets, because none of
    those live under the pristine snapshot's file set.
    """
    manifest_file = manifest_file or (state_dir / "seeded-manifest.json")
    manifest = _load_manifest(manifest_file)

    updated: list[str] = []
    skipped_edited: list[str] = []
    added: list[str] = []

    for rel in _iter_pristine_files(pristine_dir):
        key = rel.as_posix()
        source = pristine_dir / rel
        target = inventories_dir / rel
        new_hash = _hash_file(source)

        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            manifest[key] = new_hash
            added.append(key)
            continue

        recorded_hash = manifest.get(key)
        current_hash = _hash_file(target)
        if recorded_hash is not None and current_hash != recorded_hash:
            skipped_edited.append(key)
            continue

        if current_hash != new_hash:
            target.write_bytes(source.read_bytes())
            updated.append(key)
        manifest[key] = new_hash

    _write_manifest(manifest_file, manifest)
    seeded_version_file.write_text(
        current_image_version(image_version_file) + "\n", encoding="utf-8"
    )

    return RefreshResult(updated=updated, skipped_edited=skipped_edited, added=added)


if __name__ == "__main__":
    ensure_seeded()
