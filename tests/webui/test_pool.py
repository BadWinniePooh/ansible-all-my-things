"""FR-042 (validation at save time, naming the offending entry), FR-043
(an in-use name cannot be removed or renamed), FR-044 (free/used counts
match the machine records).
"""

from __future__ import annotations

import pytest

from webui import pool


def _write_yaml(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.parametrize(
    "name",
    ["Edoras", "edoras_shire", "-edoras", "edoras-", "ed*ras", "a" * 64, ""],
)
def test_validate_name_rejects_bad_entries(name):
    with pytest.raises(pool.PoolValidationError) as excinfo:
        pool.validate_name(name)
    assert excinfo.value.entry == name


@pytest.mark.parametrize("name", ["edoras", "a", "a-b-c", "a1", "a" * 63])
def test_validate_name_accepts_good_entries(name):
    pool.validate_name(name)  # does not raise


def test_validate_entries_rejects_duplicates():
    with pytest.raises(pool.PoolValidationError) as excinfo:
        pool.validate_entries(["edoras", "shire", "edoras"])
    assert excinfo.value.entry == "edoras"


def test_save_rejects_whole_submission_on_one_bad_entry(tmp_path):
    pool_file = tmp_path / "pool.yml"
    default_pool_file = tmp_path / "default.yml"
    autogen = tmp_path / "autogen.yml"
    _write_yaml(default_pool_file, "hostname_pool:\n  - edoras\n")
    _write_yaml(autogen, "all:\n  hosts: {}\n")

    with pytest.raises(pool.PoolValidationError) as excinfo:
        pool.save(
            ["edoras", "Bad_Name"],
            pool_file=pool_file,
            default_pool_file=default_pool_file,
            autogen_inventory_file=autogen,
        )
    assert excinfo.value.entry == "Bad_Name"
    assert not pool_file.exists()


def test_save_refuses_to_remove_an_in_use_name(tmp_path):
    pool_file = tmp_path / "pool.yml"
    default_pool_file = tmp_path / "default.yml"
    autogen = tmp_path / "autogen.yml"
    _write_yaml(default_pool_file, "hostname_pool:\n  - edoras\n  - shire\n")
    _write_yaml(autogen, "all:\n  hosts: {edoras: {}}\n")

    with pytest.raises(pool.PoolValidationError) as excinfo:
        pool.save(
            ["shire"],  # edoras dropped, but it is live
            pool_file=pool_file,
            default_pool_file=default_pool_file,
            autogen_inventory_file=autogen,
        )
    assert excinfo.value.entry == "edoras"
    assert not pool_file.exists()


def test_save_refuses_to_rename_an_in_use_name(tmp_path):
    pool_file = tmp_path / "pool.yml"
    default_pool_file = tmp_path / "default.yml"
    autogen = tmp_path / "autogen.yml"
    _write_yaml(default_pool_file, "hostname_pool:\n  - edoras\n")
    _write_yaml(autogen, "all:\n  hosts: {edoras: {}}\n")

    with pytest.raises(pool.PoolValidationError):
        pool.save(
            ["edoras-renamed"],
            pool_file=pool_file,
            default_pool_file=default_pool_file,
            autogen_inventory_file=autogen,
        )


def test_save_persists_a_valid_submission(tmp_path):
    pool_file = tmp_path / "pool.yml"
    default_pool_file = tmp_path / "default.yml"
    autogen = tmp_path / "autogen.yml"
    _write_yaml(default_pool_file, "hostname_pool:\n  - edoras\n")
    _write_yaml(autogen, "all:\n  hosts: {}\n")

    pool.save(
        ["edoras", "shire", "bree"],
        pool_file=pool_file,
        default_pool_file=default_pool_file,
        autogen_inventory_file=autogen,
    )

    assert pool.load(pool_file=pool_file, default_pool_file=default_pool_file) == [
        "edoras",
        "shire",
        "bree",
    ]


def test_status_counts_match_machine_records(tmp_path):
    pool_file = tmp_path / "pool.yml"
    default_pool_file = tmp_path / "default.yml"
    autogen = tmp_path / "autogen.yml"
    _write_yaml(default_pool_file, "hostname_pool:\n  - edoras\n  - shire\n  - bree\n")
    _write_yaml(autogen, "all:\n  hosts: {edoras: {}}\n")

    status = pool.status(
        pool_file=pool_file, default_pool_file=default_pool_file, autogen_inventory_file=autogen
    )
    assert status.used == ["edoras"]
    assert status.free == ["shire", "bree"]
