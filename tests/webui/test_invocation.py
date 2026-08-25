"""Contract: playbook-invocation.md, Action: provision. Each of the four
create-form choices reaches the argument list; no secret does; the
private key path points into the mounted volume.
"""

from __future__ import annotations

from webui import config
from webui.runner import build_provision_command

TOKEN = "hcloud-secret-token"  # noqa: S105
PASSWORD = "vault-secret-password"  # noqa: S105


def test_provision_command_carries_all_four_choices():
    command = build_provision_command(
        profile="desktop", server_type="cx43", location="hel1", image="ubuntu-22.04"
    )
    joined = " ".join(command)
    assert "provider=hcloud" in joined
    assert "profile=desktop" in joined
    assert "hcloud_server_type=cx43" in joined
    assert "hcloud_server_location=hel1" in joined
    assert "image=ubuntu-22.04" in joined


def test_provision_command_names_no_machine():
    command = build_provision_command(
        profile="basic", server_type="cx23", location="fsn1", image="ubuntu-24.04"
    )
    assert "hostname" not in " ".join(command)


def test_provision_command_contains_no_secret_value():
    command = build_provision_command(
        profile="basic", server_type="cx23", location="fsn1", image="ubuntu-24.04"
    )
    joined = " ".join(command)
    assert TOKEN not in joined
    assert PASSWORD not in joined


def test_provision_command_private_key_points_into_the_volume():
    command = build_provision_command(
        profile="basic", server_type="cx23", location="fsn1", image="ubuntu-24.04"
    )
    assert "--private-key" in command
    index = command.index("--private-key")
    assert command[index + 1] == str(config.SSH_PRIVATE_KEY_FILE)
    assert str(config.STATE_DIR) in command[index + 1]
