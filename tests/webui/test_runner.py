"""Contract: playbook-invocation.md. A second run is refused rather than
queued; the constructed argument list never contains a secret; both
secrets reach the child through its environment; a non-zero exit surfaces
the output tail.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from webui.runner import (
    RunAlreadyActive,
    Runner,
    build_configure_command,
    build_destroy_command,
)
from webui.secrets import SecretsUnavailable, SecretStore

TOKEN = "hcloud-secret-token"  # noqa: S105
PASSWORD = "vault-secret-password"  # noqa: S105


def _unlocked_store() -> tuple[SecretStore, str]:
    store = SecretStore()
    session_id = store.ensure_session(None)
    store.unlock(session_id, hcloud_token=TOKEN, vault_password=PASSWORD)
    return store, session_id


async def _await_completion(run) -> None:
    while run.outcome == "running":
        await asyncio.sleep(0.01)


def test_configure_command_has_no_profile_and_uses_limit():
    command = build_configure_command(machine_name="edoras")
    assert "--limit" in command
    assert "edoras" in command
    assert "--extra-vars" not in command


def test_destroy_command_has_no_private_key():
    command = build_destroy_command(machine_name="edoras")
    assert "--private-key" not in command
    assert "hostname=edoras" in " ".join(command)


def test_start_raises_when_secrets_missing():
    store = SecretStore()
    session_id = store.ensure_session(None)
    runner = Runner(store)

    async def main():
        with pytest.raises(SecretsUnavailable):
            await runner.start(
                session_id=session_id, action="provision", target=None, command=["true"]
            )
        assert runner.active is None

    asyncio.run(main())


def test_second_run_is_refused_not_queued():
    store, session_id = _unlocked_store()
    runner = Runner(store, cwd=str(Path(__file__).resolve().parents[2]))
    sleep_command = [sys.executable, "-c", "import time; time.sleep(1)"]

    async def main():
        first = await runner.start(
            session_id=session_id, action="provision", target=None, command=sleep_command
        )
        with pytest.raises(RunAlreadyActive) as excinfo:
            await runner.start(
                session_id=session_id, action="destroy", target="edoras", command=["true"]
            )
        assert excinfo.value.active is first
        await runner.cancel()
        await _await_completion(first)

    asyncio.run(main())


def test_both_secrets_reach_only_the_child_environment():
    store, session_id = _unlocked_store()
    runner = Runner(store, cwd=str(Path(__file__).resolve().parents[2]))
    print_env_command = [
        sys.executable,
        "-c",
        "import os; print(os.environ.get('HCLOUD_TOKEN')); "
        "print(os.environ.get('ANSIBLE_VAULT_PASSWORD'))",
    ]

    async def main():
        run = await runner.start(
            session_id=session_id, action="provision", target=None, command=print_env_command
        )
        await _await_completion(run)
        assert run.outcome == "succeeded"
        assert list(run.output) == [TOKEN, PASSWORD]
        # Never part of the displayed command itself.
        assert TOKEN not in run.command
        assert PASSWORD not in run.command

    asyncio.run(main())


def test_nonzero_exit_is_reported_failed_with_output_tail():
    store, session_id = _unlocked_store()
    runner = Runner(store, cwd=str(Path(__file__).resolve().parents[2]))
    failing_command = [
        sys.executable,
        "-c",
        "print('about to fail'); import sys; sys.exit(3)",
    ]

    async def main():
        run = await runner.start(
            session_id=session_id, action="provision", target=None, command=failing_command
        )
        await _await_completion(run)
        assert run.outcome == "failed"
        assert run.exit_code == 3
        assert "about to fail" in list(run.output)

    asyncio.run(main())


def test_cancel_records_cancelled_outcome():
    store, session_id = _unlocked_store()
    runner = Runner(store, cwd=str(Path(__file__).resolve().parents[2]))
    sleep_command = [sys.executable, "-c", "import time; time.sleep(5)"]

    async def main():
        run = await runner.start(
            session_id=session_id, action="configure", target="edoras", command=sleep_command
        )
        await asyncio.sleep(0.05)
        await runner.cancel()
        await _await_completion(run)
        assert run.outcome == "cancelled"

    asyncio.run(main())


def test_stream_lines_replays_buffer_then_new_lines():
    store, session_id = _unlocked_store()
    runner = Runner(store, cwd=str(Path(__file__).resolve().parents[2]))
    command = [
        sys.executable,
        "-c",
        "import time\nprint('one')\ntime.sleep(0.05)\nprint('two')\n",
    ]

    async def main():
        run = await runner.start(
            session_id=session_id, action="provision", target=None, command=command
        )
        lines = []
        async for line in run.stream_lines():
            lines.append(line)
        assert lines == ["one", "two"]
        assert run.outcome == "succeeded"

    asyncio.run(main())
