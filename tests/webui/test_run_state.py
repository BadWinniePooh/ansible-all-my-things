"""A run says which machine it is about, in the interface's own words.

runner.describe is the single place a run's state is put into words: the
run view renders it and the SSE terminal event carries it already rendered.
These tests pin both ends, so the browser can never be left re-implementing
the wording.
"""

from __future__ import annotations

import json
from collections import deque

import pytest
from fastapi.testclient import TestClient

from webui import runner as runner_module
from webui.app import app, runner


def make_run(action, target, outcome="running", exit_code=None, output=()):
    run = runner_module.Run(action=action, target=target, command=["ansible-playbook", "x.yml"])
    run.outcome = outcome
    run.exit_code = exit_code
    run.output = deque(output)
    return run


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def active_run(monkeypatch):
    def install(run):
        monkeypatch.setattr(runner, "_active", run)
        return run

    yield install


def test_a_running_provision_names_the_machine():
    state = runner_module.describe(make_run("provision", "osgiliath"))
    assert state["label"] == "Provisioning osgiliath…"
    assert state["tone"] == "running"


def test_each_action_has_its_own_verb():
    assert runner_module.describe(make_run("configure", "edoras"))["label"] == "Configuring edoras…"
    assert runner_module.describe(make_run("destroy", "edoras"))["label"] == "Destroying edoras…"


def test_success_reads_as_a_finished_action_not_as_the_outcome_literal():
    state = runner_module.describe(
        make_run("provision", "osgiliath", outcome="succeeded", exit_code=0)
    )
    assert state["label"] == "Provisioned osgiliath"
    assert state["detail"] == "Finished cleanly (exit 0)."
    assert state["tone"] == "ok"


def test_failure_names_the_action_and_says_what_happened_to_the_machine():
    state = runner_module.describe(
        make_run("provision", "osgiliath", outcome="failed", exit_code=2)
    )
    assert state["label"] == "Provisioning osgiliath failed (exit 2)"
    assert "half-created" in state["detail"]
    assert state["tone"] == "failed"


def test_cancelling_is_not_reported_as_a_failure():
    state = runner_module.describe(make_run("destroy", "edoras", outcome="cancelled"))
    assert state["tone"] == "cancelled"
    assert "cancelled" in state["label"]


def test_a_run_without_a_target_still_reads_as_a_sentence():
    assert runner_module.describe(make_run("provision", None))["label"] == "Provisioning a machine…"


def test_the_run_view_shows_the_spinner_and_the_machine(client, active_run):
    active_run(make_run("provision", "osgiliath"))
    body = client.get("/run").text
    assert "Provisioning osgiliath" in body
    assert 'class="spinner"' in body


def test_a_finished_run_shows_no_spinner(client, active_run):
    active_run(make_run("provision", "osgiliath", outcome="succeeded", exit_code=0))
    body = client.get("/run").text
    assert "Provisioned osgiliath" in body
    assert 'class="spinner"' not in body
    assert "Finished cleanly (exit 0)." in body


def test_the_terminal_event_carries_the_wording_rendered(client, active_run):
    active_run(
        make_run("provision", "osgiliath", outcome="failed", exit_code=2, output=["a line"])
    )
    body = client.get("/run/stream").text
    payload = json.loads(body.rsplit("data: ", 1)[1].strip())
    assert payload["outcome"] == "failed"
    assert payload["label"] == "Provisioning osgiliath failed (exit 2)"
    assert payload["tone"] == "failed"
    assert payload["detail"]
