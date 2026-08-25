"""FR-030: presets are unique, saving over an existing name requires
explicit overwrite, and renaming onto an already-used name is refused.
"""

from __future__ import annotations

import pytest

from webui import presets

SAMPLE = presets.Preset(
    name="my-dev-box", profile="basic", server_type="cx23", location="fsn1", image="ubuntu-24.04"
)


def test_save_then_list_round_trips(tmp_path):
    presets_file = tmp_path / "presets.json"
    presets.save(SAMPLE, presets_file=presets_file)
    assert presets.list_presets(presets_file=presets_file) == [SAMPLE]


def test_save_over_existing_name_requires_overwrite(tmp_path):
    presets_file = tmp_path / "presets.json"
    presets.save(SAMPLE, presets_file=presets_file)

    with pytest.raises(presets.PresetNameTaken):
        presets.save(SAMPLE, presets_file=presets_file)

    changed = presets.Preset(**{**SAMPLE.__dict__, "server_type": "cx43"})
    presets.save(changed, overwrite=True, presets_file=presets_file)
    assert presets.get("my-dev-box", presets_file=presets_file).server_type == "cx43"


def test_rename_refuses_when_target_name_taken(tmp_path):
    presets_file = tmp_path / "presets.json"
    presets.save(SAMPLE, presets_file=presets_file)
    other = presets.Preset(**{**SAMPLE.__dict__, "name": "other-box"})
    presets.save(other, presets_file=presets_file)

    with pytest.raises(presets.PresetNameTaken):
        presets.rename("my-dev-box", "other-box", presets_file=presets_file)


def test_rename_moves_the_preset_to_the_new_name(tmp_path):
    presets_file = tmp_path / "presets.json"
    presets.save(SAMPLE, presets_file=presets_file)

    presets.rename("my-dev-box", "renamed-box", presets_file=presets_file)

    with pytest.raises(presets.PresetNotFound):
        presets.get("my-dev-box", presets_file=presets_file)
    assert presets.get("renamed-box", presets_file=presets_file).profile == "basic"


def test_delete_removes_the_preset(tmp_path):
    presets_file = tmp_path / "presets.json"
    presets.save(SAMPLE, presets_file=presets_file)
    presets.delete("my-dev-box", presets_file=presets_file)
    assert presets.list_presets(presets_file=presets_file) == []


def test_delete_missing_preset_raises(tmp_path):
    presets_file = tmp_path / "presets.json"
    with pytest.raises(presets.PresetNotFound):
        presets.delete("nope", presets_file=presets_file)
