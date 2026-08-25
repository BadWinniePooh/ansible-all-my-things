"""find_image resolves the two references the playbook's `image:` parameter
accepts -- a name and a numeric id -- against different Hetzner endpoints,
and each branch has to answer the same question: is there an x86 image this
account can boot? httpx is stubbed here; nothing reaches the network.
"""

from __future__ import annotations

import pytest

from webui import hcloud_api

TOKEN = "hcloud-token"  # noqa: S105


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300
        self._payload = payload

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def captured_get(monkeypatch):
    """Record every httpx.get and answer it from a queue the test sets."""
    calls: list[dict] = []
    responses: list[FakeResponse] = []

    def fake_get(url, **kwargs):
        calls.append({"url": url, "params": kwargs.get("params")})
        return responses.pop(0)

    monkeypatch.setattr(hcloud_api.httpx, "get", fake_get)
    return calls, responses


def test_a_blank_reference_is_never_looked_up(captured_get):
    calls, _ = captured_get

    assert hcloud_api.find_image(TOKEN, "   ") is None
    assert calls == []


def test_a_name_is_looked_up_filtered_to_available_x86_images(captured_get):
    calls, responses = captured_get
    responses.append(FakeResponse({"images": [{"name": "debian-12", "architecture": "x86"}]}))

    found = hcloud_api.find_image(TOKEN, "debian-12")

    assert found["name"] == "debian-12"
    assert calls[0]["url"].endswith("/images")
    assert calls[0]["params"] == {
        "name": "debian-12",
        "architecture": "x86",
        "status": "available",
    }


def test_an_unknown_name_returns_none_rather_than_raising(captured_get):
    _, responses = captured_get
    responses.append(FakeResponse({"images": []}))

    assert hcloud_api.find_image(TOKEN, "ubuntu-99.04") is None


def test_a_numeric_reference_goes_to_the_by_id_endpoint(captured_get):
    calls, responses = captured_get
    responses.append(FakeResponse({"image": {"id": 42, "architecture": "x86"}}))

    found = hcloud_api.find_image(TOKEN, "42")

    assert found["id"] == 42
    assert calls[0]["url"].endswith("/images/42")


def test_a_missing_id_returns_none_rather_than_raising(captured_get):
    _, responses = captured_get
    responses.append(FakeResponse({"error": {"message": "not found"}}, status_code=404))

    assert hcloud_api.find_image(TOKEN, "999999") is None


def test_a_non_x86_image_found_by_id_is_rejected(captured_get):
    """The by-id endpoint takes no architecture filter, so the check has to
    happen here -- an arm64 image reported as found would fail during
    provisioning, which is what checking up front exists to prevent."""
    _, responses = captured_get
    responses.append(FakeResponse({"image": {"id": 7, "architecture": "arm"}}))

    assert hcloud_api.find_image(TOKEN, "7") is None


def test_any_other_error_is_surfaced_with_the_api_message(captured_get):
    _, responses = captured_get
    responses.append(
        FakeResponse({"error": {"message": "token is invalid"}}, status_code=401)
    )

    with pytest.raises(hcloud_api.HetznerApiError, match="token is invalid"):
        hcloud_api.find_image(TOKEN, "debian-12")
