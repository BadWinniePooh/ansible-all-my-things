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
    "vault-discarded": "Encrypted configuration discarded. The next vault password you enter becomes the new one.",
    "pool-saved": "Name pool saved.",
    "preset-deleted": "Preset deleted.",
    "preset-saved-from-machine": "Preset saved from that machine's profile, size, location and image.",
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


# What the user types to confirm discarding the encrypted configuration.
# Lives here rather than in the route or the template because all three --
# the form's placeholder, the check, and the sentence naming it when the
# check fails -- must say the same word.
DISCARD_CONFIRMATION = "discard"


def vault_password_repeat_mismatch() -> str:
    return (
        "The two vault passwords do not match. Nothing was created -- enter the same "
        "password twice, because this first one cannot be checked against anything later."
    )


def vault_discard_confirmation_required() -> str:
    return (
        f"Type '{DISCARD_CONFIRMATION}' exactly to confirm discarding the encrypted "
        "configuration. Nothing was removed."
    )


def vault_nothing_to_discard() -> str:
    return "There is no encrypted configuration to discard."


def preset_saved(name: str) -> str:
    return f"Preset {name} saved."


def preset_updated(name: str) -> str:
    return f"Preset {name} updated."


def preset_already_saved(name: str) -> str:
    return f"These choices are already saved as {name}."


def preset_name_taken(name: str) -> str:
    return f"A preset named {name} already exists. Rename or delete that one first."


def preset_name_required() -> str:
    return "Name the preset before saving it."


def preset_needs_the_account() -> str:
    return (
        "The Hetzner API token must be unlocked to save a machine as a preset: its size, "
        "location and image are read from the account, not from the local records."
    )


def machine_unknown(name: str) -> str:
    return f"No machine named {name} is managed here."


def machine_not_describable(name: str, missing: list[str]) -> str:
    return (
        f"Hetzner does not report the {', '.join(missing)} of {name}, so a preset made from "
        "it would provision something different. Nothing was saved."
    )


def preset_missing(name: str) -> str:
    return f"No preset named {name}."


def run_already_active(action: str) -> str:
    return f"A {action} run is already active. Wait for it to finish."


def secrets_missing(missing: list[str]) -> str:
    return f"Locked: {', '.join(missing)}. Unlock on the dashboard and try again."
