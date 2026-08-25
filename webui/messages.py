"""What the interface says when an action finishes.

Every message a route hands the user comes from here, for the same reason
webui/runner.py owns the wording of a run's state: an interface that says
"Saved preset 'x'." in one place, "ok" in another and nothing at all in a
third has no voice. The house style is one sentence, past tense, naming
what happened to what:

    Preset dev-desktop saved.
    Name pool saved. 8 of 10 names are free.

A POST that succeeds redirects with ``?done=<code>``; the page it lands on
turns that code back into a sentence through :func:`notice_for`. Codes
rather than the text itself, because a message assembled from a query
string is a message anyone with a link can put on the page.
"""

from __future__ import annotations

from fastapi.responses import RedirectResponse

# code -> the sentence shown once, on the page the action lands on.
DONE: dict[str, str] = {
    "session-unlocked": "Session unlocked.",
    "session-locked": "Session locked. Both secrets are gone from memory.",
    "vault-saved": "Configuration saved, encrypted with your vault password.",
    "pool-saved": "Name pool saved.",
    "preset-deleted": "Preset deleted.",
    "preset-renamed": "Preset renamed.",
    "sshkey-generated": "Keypair generated and registered with Hetzner.",
    "sshkey-rotated": "Key rotated. Machines provisioned with the previous key can no longer be reached by the automation.",
    "defaults-refreshed": "Defaults refreshed. Your configuration, machines, name pool and presets were not touched.",
}


def notice_for(code: str | None) -> str | None:
    """The sentence for a ``?done=`` code, or None for anything unknown --
    an invented code says nothing rather than something."""
    return DONE.get(code) if code else None


def done(path: str, code: str) -> RedirectResponse:
    """Redirect to ``path`` with the confirmation the landing page shows.

    303 for the same reason every redirect here is: the action was a POST,
    and the page the user ends up on must be a GET they can reload.
    """
    assert code in DONE, f"unknown message code {code!r}"
    return RedirectResponse(f"{path}?done={code}", status_code=303)


def preset_saved(name: str) -> str:
    return f"Preset {name} saved."


def preset_already_saved(name: str) -> str:
    return f"These choices are already saved as {name}."


def preset_name_taken(name: str) -> str:
    return f"A preset named {name} already exists. Rename or delete that one first."


def preset_name_required() -> str:
    return "Name the preset before saving it."


def preset_missing(name: str) -> str:
    return f"No preset named {name}."


def run_already_active(action: str) -> str:
    return f"A {action} run is already active. Wait for it to finish."


def secrets_missing(missing: list[str]) -> str:
    return f"Locked: {', '.join(missing)}. Unlock on the dashboard and try again."
