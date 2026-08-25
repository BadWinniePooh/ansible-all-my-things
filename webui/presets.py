"""Saved create-form choices (data-model.md Preset entity). Holds no
secrets -- ordinary configuration, safe at rest.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from dataclasses import fields as dataclass_fields
from pathlib import Path

from . import config


class PresetNameTaken(Exception):
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"a preset named {name!r} already exists")


class PresetNotFound(Exception):
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"no preset named {name!r}")


@dataclass
class Preset:
    name: str
    profile: str
    server_type: str
    location: str
    image: str
    # Which catalogue the four choices above were picked from. A size or
    # image that only exists in the live Hetzner catalogue is not in the
    # built-in lists, so a preset that does not record how it was made
    # cannot be restored: the create form would match its choices against
    # the static lists, find them unknown, and quietly substitute defaults.
    # Defaulted so presets.json files written before this still load.
    server_types_live: bool = False
    images_live: bool = False


def _load_all(presets_file: Path) -> list[Preset]:
    try:
        raw = json.loads(presets_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    fields = {field.name for field in dataclass_fields(Preset)}
    # Unknown keys are dropped rather than raising: a presets.json written
    # by a newer image must not make this one unusable.
    return [Preset(**{key: value for key, value in entry.items() if key in fields}) for entry in raw]


def _write_all(presets_file: Path, presets: list[Preset]) -> None:
    presets_file.parent.mkdir(parents=True, exist_ok=True)
    presets_file.write_text(
        json.dumps([asdict(preset) for preset in presets], indent=2) + "\n", encoding="utf-8"
    )


def list_presets(*, presets_file: Path = config.PRESETS_FILE) -> list[Preset]:
    return _load_all(presets_file)


def get(name: str, *, presets_file: Path = config.PRESETS_FILE) -> Preset:
    for preset in _load_all(presets_file):
        if preset.name == name:
            return preset
    raise PresetNotFound(name)


def save(
    preset: Preset, *, overwrite: bool = False, presets_file: Path = config.PRESETS_FILE
) -> None:
    """FR-030: saving over an existing name requires the caller to pass
    overwrite=True (the route gates this on an explicit confirmation)."""
    presets = _load_all(presets_file)
    existing_index = next(
        (i for i, existing in enumerate(presets) if existing.name == preset.name), None
    )
    if existing_index is not None:
        if not overwrite:
            raise PresetNameTaken(preset.name)
        presets[existing_index] = preset
    else:
        presets.append(preset)
    _write_all(presets_file, presets)


def delete(name: str, *, presets_file: Path = config.PRESETS_FILE) -> None:
    presets = _load_all(presets_file)
    remaining = [preset for preset in presets if preset.name != name]
    if len(remaining) == len(presets):
        raise PresetNotFound(name)
    _write_all(presets_file, remaining)


def rename(old_name: str, new_name: str, *, presets_file: Path = config.PRESETS_FILE) -> None:
    """FR-030: refuses when the target name is already taken."""
    presets = _load_all(presets_file)
    if any(preset.name == new_name for preset in presets):
        raise PresetNameTaken(new_name)
    for preset in presets:
        if preset.name == old_name:
            preset.name = new_name
            _write_all(presets_file, presets)
            return
    raise PresetNotFound(old_name)
