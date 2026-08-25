"""The job runner: the single-active-run mutex, the ansible-playbook
subprocess, merged output read line by line into a bounded ring buffer,
and terminal outcome capture.

This module is the implementation of contracts/playbook-invocation.md,
the feature's security boundary. Two rules from that contract shape every
line below:

1. Secrets travel only in the child process environment, built fresh for
   each run and never logged, never stored, and never part of `command`.
2. The command shown to the user is the command that runs -- `command` is
   built once and used verbatim for both display and execution.
"""

from __future__ import annotations

import asyncio
import os
from collections import deque
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal

from . import config
from .secrets import SecretStore

Action = Literal["provision", "configure", "destroy"]
Outcome = Literal["running", "succeeded", "failed", "cancelled"]


class RunAlreadyActive(Exception):
    """FR-035: a second run is refused rather than queued."""

    def __init__(self, active: "Run"):
        self.active = active
        super().__init__(f"a {active.action} run is already active")


@dataclass
class Run:
    action: Action
    target: str | None
    command: list[str]
    outcome: Outcome = "running"
    exit_code: int | None = None
    output: deque = field(
        default_factory=lambda: deque(maxlen=config.RUN_OUTPUT_BUFFER_LINES)
    )
    process: asyncio.subprocess.Process | None = field(default=None, repr=False, compare=False)
    _waiters: list[asyncio.Event] = field(default_factory=list, repr=False, compare=False)

    def append(self, line: str) -> None:
        self.output.append(line)
        self._wake_waiters()

    def _wake_waiters(self) -> None:
        for waiter in self._waiters:
            waiter.set()

    def finish(self, outcome: Outcome, exit_code: int | None) -> None:
        if self.outcome == "running":
            self.outcome = outcome
        self.exit_code = exit_code
        self._wake_waiters()

    async def stream_lines(self) -> AsyncIterator[str]:
        """Replay the ring buffer, then yield new lines as they arrive.

        A client that connects after the run has finished simply replays
        the buffer and returns -- there is nothing further to wait for.
        """
        sent = 0
        buffered = list(self.output)
        for line in buffered:
            yield line
        sent = len(buffered)

        if self.outcome != "running":
            return

        event = asyncio.Event()
        self._waiters.append(event)
        try:
            while True:
                await event.wait()
                event.clear()
                pending = list(self.output)[sent:]
                sent += len(pending)
                for line in pending:
                    yield line
                if self.outcome != "running":
                    return
        finally:
            try:
                self._waiters.remove(event)
            except ValueError:
                pass


_ACTION_VERBS: dict[Action, tuple[str, str]] = {
    # action -> (present participle, past participle)
    "provision": ("Provisioning", "Provisioned"),
    "configure": ("Configuring", "Configured"),
    "destroy": ("Destroying", "Destroyed"),
}

_FAILURE_DETAIL: dict[Action, str] = {
    "provision": (
        "The automation removes a machine it half-created and prints how to re-run. "
        "The end of the output above says what went wrong."
    ),
    "configure": "The machine is left as the run found it. The end of the output above says what went wrong.",
    "destroy": (
        "The machine may still exist. Check it on the dashboard before running destroy again. "
        "The end of the output above says what went wrong."
    ),
}


def describe(run: "Run") -> dict[str, str | None]:
    """The one place a run's state is put into words.

    The run view renders this, and the SSE terminal event carries it
    already rendered, so the browser never re-implements the wording and
    the two can't drift apart.
    """
    present, past = _ACTION_VERBS[run.action]
    target = run.target or "a machine"
    exit_code = "" if run.exit_code is None else f" (exit {run.exit_code})"

    if run.outcome == "running":
        return {
            "label": f"{present} {target}…",
            "detail": None,
            "tone": "running",
        }
    if run.outcome == "succeeded":
        return {
            "label": f"{past} {target}",
            "detail": f"Finished cleanly{exit_code}.",
            "tone": "ok",
        }
    if run.outcome == "cancelled":
        return {
            "label": f"{present} {target} — cancelled",
            "detail": "Stopped on your request. Anything already created is left as it is.",
            "tone": "cancelled",
        }
    return {
        "label": f"{present} {target} failed{exit_code}",
        "detail": _FAILURE_DETAIL[run.action],
        "tone": "failed",
    }


def build_provision_command(
    *, profile: str, server_type: str, location: str, image: str
) -> list[str]:
    return [
        "ansible-playbook",
        str(config.CREATE_VM_PLAYBOOK),
        "--private-key",
        str(config.SSH_PRIVATE_KEY_FILE),
        "--extra-vars",
        "provider=hcloud",
        "--extra-vars",
        f"profile={profile}",
        "--extra-vars",
        f"hcloud_server_type={server_type}",
        "--extra-vars",
        f"hcloud_server_location={location}",
        "--extra-vars",
        f"image={image}",
    ]


def build_configure_command(*, machine_name: str) -> list[str]:
    return [
        "ansible-playbook",
        str(config.CONFIGURE_PROFILE_PLAYBOOK),
        "--private-key",
        str(config.SSH_PRIVATE_KEY_FILE),
        "--limit",
        machine_name,
    ]


def build_destroy_command(*, machine_name: str) -> list[str]:
    return [
        "ansible-playbook",
        str(config.DESTROY_VM_PLAYBOOK),
        "--extra-vars",
        "provider=hcloud",
        "--extra-vars",
        f"hostname={machine_name}",
    ]


class Runner:
    """Holds at most one active Run. Not thread-safe -- relies on asyncio's
    single-threaded event loop: the mutex check-and-set in start() has no
    ``await`` between the check and the assignment, so it cannot race.
    """

    def __init__(self, secret_store: SecretStore, *, cwd: str = str(config.ANSIBLE_ROOT)):
        self._secret_store = secret_store
        self._active: Run | None = None
        self._cwd = cwd

    @property
    def active(self) -> Run | None:
        return self._active

    async def start(
        self, *, session_id: str, action: Action, target: str | None, command: list[str]
    ) -> Run:
        if self._active is not None and self._active.outcome == "running":
            raise RunAlreadyActive(self._active)

        # Raises SecretsUnavailable naming what's missing (FR-014) before
        # anything is spawned. Full parent environment is inherited so PATH
        # and the baked ANSIBLE_CONFIG resolve; only the two secrets are
        # added on top -- never passed as arguments (Invariant 1).
        environment = {**os.environ, **self._secret_store.environment(session_id)}

        run = Run(action=action, target=target, command=command)
        self._active = run

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self._cwd,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        run.process = process
        asyncio.create_task(self._pump(run, process))
        return run

    async def _pump(self, run: Run, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        async for raw_line in process.stdout:
            run.append(raw_line.decode(errors="replace").rstrip("\r\n"))
        exit_code = await process.wait()
        outcome = "succeeded" if exit_code == 0 else "failed"
        run.finish(outcome, exit_code)

    async def cancel(self) -> Run | None:
        """Terminate the active run, if any, and release the mutex."""
        run = self._active
        if run is None or run.outcome != "running" or run.process is None:
            return None
        run.outcome = "cancelled"
        try:
            run.process.terminate()
        except ProcessLookupError:
            pass
        return run
