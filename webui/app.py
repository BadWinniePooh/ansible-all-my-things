"""Application assembly: routes, middleware, startup.

Every route that refuses an action names the cause (constitution
Principle XII) -- see the exception handling in each handler below.
FR-049: binds to loopback by default; see config.py for why that is
enforced by the compose files' port publishing rather than by this
process's own bind address.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import (
    config,
    costs,
    hcloud_api,
    inventory,
    messages,
    pool,
    presets,
    pricing,
    seed,
    sshkeys,
    vault,
)
from .runner import (
    RunAlreadyActive,
    Runner,
    build_configure_command,
    build_destroy_command,
    build_provision_command,
)
from .runner import describe as describe_run  # runner owns the wording for a run's state
from .secrets import SecretsUnavailable, SecretStore

SESSION_COOKIE_NAME = "webui_session"

secret_store = SecretStore()
runner = Runner(secret_store)

app = FastAPI(title="Hetzner web frontend")

_templates_dir = Path(__file__).parent / "templates"
_static_dir = Path(__file__).parent / "static"


def _shell_context(request: Request) -> dict:
    """The session lock state the shell in base.html needs on every page.

    The sidebar marks the actions a locked session cannot perform, so this
    belongs to every response rather than to the handful of handlers that
    happen to pass it. Routes that also compute it explicitly agree with
    this by construction: both read the same store.
    """
    session_id = getattr(request.state, "session_id", None)
    # The sidebar carries the machine count beside the lock states, so it
    # is answered for every page rather than for the dashboard alone. Read
    # from the ledger, which holds what the account last reported, so no
    # page render costs an API call to draw a badge; the local records
    # answer before the account has ever been read.
    return {
        **secret_store.status(session_id),
        "machine_count": len(costs.open_lives()) or len(inventory.list_machines()),
    }


templates = Jinja2Templates(directory=str(_templates_dir), context_processors=[_shell_context])


def _euro(amount: float | None) -> str:
    """A money figure, or "unknown" when there is nothing to compute one
    from -- never €0.00, which would read as "this machine is free"."""
    if amount is None:
        return "unknown"
    return f"€{amount:,.2f}"


def _euro_rate(amount: float | None) -> str:
    """An hourly rate. Three decimals, because a cent an hour is the order
    of magnitude here and €0.01 would round two machines to the same
    figure."""
    if amount is None:
        return "nothing"
    return f"€{amount:,.3f}/h"


templates.env.filters["euro"] = _euro
templates.env.filters["euro_rate"] = _euro_rate

if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.middleware("http")
async def session_cookie_middleware(request: Request, call_next):
    """Resolve (or create) the session id for every request and make sure
    the response always carries a validly signed cookie for it. No route
    handler has to think about cookie plumbing.
    """
    incoming = request.cookies.get(SESSION_COOKIE_NAME)
    session_id = secret_store.verify(incoming)
    session_id = secret_store.ensure_session(session_id)
    request.state.session_id = session_id

    response = await call_next(request)

    response.set_cookie(
        SESSION_COOKIE_NAME,
        secret_store.sign(session_id),
        httponly=True,
        samesite="strict",
    )
    return response


def session_status_context(session_id: str) -> dict:
    return secret_store.status(session_id)


def _machines_with_account_detail(session_id: str, status: dict) -> tuple[list, str | None]:
    """The managed machines, with what the Hetzner account says about them.

    Size, location and address are only recorded locally for machines this
    installation provisioned itself, so anything created from the command
    line reads as "unknown" until the account is asked. A lookup that fails
    is reported rather than swallowed: the rows are then the local records,
    which is a weaker answer than the one the page normally shows.
    """
    machines = inventory.list_machines()
    if not machines or not status["token_unlocked"]:
        return machines, None
    try:
        servers = hcloud_api.list_servers(secret_store.hcloud_token(session_id))
    except hcloud_api.HetznerApiError as exc:
        return machines, f"Could not read machine details from Hetzner ({exc})."

    merged = inventory.merge_account_detail(machines, servers)

    # The account is the only place a machine's creation time and price
    # exist, and it forgets both the moment the machine is destroyed. So
    # every reading of it is also written down: rows are opened for what is
    # running and closed for what is not, which is what lets the month's
    # estimate include a machine that ran for nine days and is now gone.
    now = datetime.now(timezone.utc)
    costs.record_running(merged, now)
    costs.close_missing({machine.name for machine in merged if machine.status}, now)
    # A row that matched nothing keeps whatever the local records knew,
    # which is usually nothing -- and "unknown" on its own does not say
    # why. The two sides disagreeing about names is the reason worth
    # naming, because it is the one the operator can act on.
    unmatched = [machine.name for machine in merged if machine.status is None]
    if unmatched and servers:
        return merged, (
            f"The Hetzner account has no server named {', '.join(unmatched)}. "
            f"It holds {len(servers)}: {', '.join(sorted(_server_names(servers))) or 'none'}."
        )
    return merged, None


def _server_names(servers: list[dict]) -> list[str]:
    return [name for name in (server.get("name") for server in servers) if name]


def _spark(points: list[float], *, width: float = 300, height: float = 62) -> dict[str, str]:
    """A sparkline as two paths: the line, and the area under it.

    Computed here rather than in the template because a chart is
    arithmetic, and Jinja is a bad place to do arithmetic. A flat run of
    months sits on the baseline rather than dividing by a zero range.
    """
    if not points:
        return {"line": "", "area": "", "last_x": "0", "last_y": "0"}
    top, bottom = 4.0, height - 4
    highest = max(points) or 1.0
    step = (width - 12) / max(len(points) - 1, 1)
    coordinates = [
        (6 + index * step, bottom - (value / highest) * (bottom - top))
        for index, value in enumerate(points)
    ]
    line = " ".join(
        f"{'M' if index == 0 else 'L'}{x:.1f},{y:.1f}"
        for index, (x, y) in enumerate(coordinates)
    )
    last_x, last_y = coordinates[-1]
    return {
        "line": line,
        "area": f"{line} L{last_x:.1f},{bottom:.1f} L{coordinates[0][0]:.1f},{bottom:.1f} Z",
        "last_x": f"{last_x:.1f}",
        "last_y": f"{last_y:.1f}",
    }


def _cost_summary(now: datetime, *, months: int = 6) -> dict:
    """What the dashboard card and the costs screen both say.

    Every figure here is an estimate of the server line of the invoice and
    nothing else -- the templates say so wherever one is shown.
    """
    history = costs.month_totals(now, months=months)
    current = history[-1] if history else None
    return {
        "months": history,
        "month_machines": costs.machine_names_by_month(history, now),
        "current": current,
        "spark": _spark([month.total for month in history]),
        "hourly": costs.hourly_now(),
        "running": costs.open_lives(),
        "machines": costs.machine_costs_this_month(now),
    }


def _dashboard_context(request: Request, *, error: str | None = None, notice: str | None = None) -> dict:
    session_id = request.state.session_id
    status = session_status_context(session_id)
    pool_status = pool.status()
    machines, machines_notice = _machines_with_account_detail(session_id, status)
    if machines_notice:
        notice = f"{notice} {machines_notice}" if notice else machines_notice
    now = datetime.now(timezone.utc)
    return {
        "cost": _cost_summary(now),
        "machine_costs": {
            machine.name: pricing.month_to_date(machine.created_at, now, machine.rates)
            for machine in machines
        },
        "error": error,
        "notice": notice,
        "machines": machines,
        "pool_entries": pool_status.entries,
        "pool_free": pool_status.free,
        "pool_used": pool_status.used,
        "pool_next": pool_status.free[0] if pool_status.free else None,
        "locations": config.LOCATIONS,
        "defaults_refresh_available": seed.needs_defaults_refresh(),
        "vault_configured": config.VAULT_FILE.exists(),
        **status,
    }


@app.get("/costs", response_class=HTMLResponse)
async def costs_page(request: Request):
    """Every month the ledger knows about, and what this one is made of.

    Reads the ledger only: no API call, so the page answers the same way
    with the session locked -- what it cost is not a secret, and the
    machines it is about may not exist any more.
    """
    now = datetime.now(timezone.utc)
    return templates.TemplateResponse(
        request,
        "costs.html",
        {"cost": _cost_summary(now, months=24), "now": now},
    )


@app.get("/machines/table", response_class=HTMLResponse)
async def machines_table(request: Request):
    """The machine list on its own, so it can refresh itself every minute.

    A running machine's cost changes with the clock while nothing else on
    the dashboard does, and reloading the whole page to watch a number tick
    would throw away anything half-typed elsewhere on it.
    """
    return templates.TemplateResponse(
        request, "fragments/machines_table.html", _dashboard_context(request)
    )


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, done: str | None = None):
    context = _dashboard_context(request, notice=messages.notice_for(done))
    return templates.TemplateResponse(request, "dashboard.html", context)


@app.post("/session/unlock")
async def session_unlock(
    request: Request,
    hcloud_token: str = Form(default=""),
    vault_password: str = Form(default=""),
):
    """Rejects a value that doesn't actually work, rather than storing it
    unlocked and letting it fail later deep inside a playbook run or a
    vault read. A blank field means "leave unchanged" (SecretStore.unlock
    only overwrites a field given a non-None value), so an invalid entry
    is downgraded to blank rather than aborting the whole submission --
    the other field in the same form may still be valid.
    """
    session_id = request.state.session_id
    errors: list[str] = []

    validated_token = hcloud_token or None
    if hcloud_token:
        try:
            hcloud_api.validate_token(hcloud_token)
        except hcloud_api.HetznerApiError as exc:
            errors.append(f"Hetzner API token rejected: {exc}")
            validated_token = None

    validated_password = vault_password or None
    if vault_password:
        if config.VAULT_FILE.exists():
            try:
                vault.read_vault(vault_password)
            except vault.VaultPasswordMismatch:
                errors.append("Vault password does not match the existing configuration.")
                validated_password = None
        else:
            # First-time setup (SC-006: no vault.yml is baked into the
            # image or seeded into a fresh volume): whichever password is
            # entered here becomes canonical, by creating an empty
            # encrypted vault.yml under it now. Every later unlock is then
            # checked against this file via the branch above.
            vault.write_vault({}, vault_password)

    secret_store.unlock(session_id, hcloud_token=validated_token, vault_password=validated_password)

    if errors:
        context = _dashboard_context(request, error=" ".join(errors))
        return templates.TemplateResponse(request, "dashboard.html", context, status_code=400)

    return messages.done("/", "session-unlocked")


@app.post("/session/lock")
async def session_lock(request: Request):
    secret_store.lock(request.state.session_id)
    return messages.done("/", "session-locked")


@app.get("/session/status", response_class=HTMLResponse)
async def session_status(request: Request):
    status = session_status_context(request.state.session_id)
    return templates.TemplateResponse(request, "fragments/session_status.html", status)


def _flash(
    request: Request, message: str, *, tone: str = "notice", status_code: int = 200
) -> HTMLResponse:
    """One banner, swapped into the page by htmx.

    Rendered from the same template the full pages use, so a message
    delivered into a fragment looks like a message delivered on a reload.
    """
    return templates.TemplateResponse(
        request,
        "fragments/flash.html",
        {"message": message, "tone": tone},
        status_code=status_code,
    )


def _desktop_users_from_form(form) -> list[dict]:
    names = form.getlist("desktop_user_name")
    passwords = form.getlist("desktop_user_password")
    exa_keys = form.getlist("desktop_user_exa_api_key")
    return [
        {"name": name, "password": password, "exa_api_key": exa_key}
        for name, password, exa_key in zip(names, passwords, exa_keys)
    ]


@app.get("/vault", response_class=HTMLResponse)
async def vault_form(request: Request, done: str | None = None):
    status = session_status_context(request.state.session_id)
    notice = messages.notice_for(done)
    if not status["vault_unlocked"]:
        return templates.TemplateResponse(
            request, "vault.html", {**status, "notice": notice, "values": {}, "desktop_users": []}
        )

    password = secret_store.vault_password(request.state.session_id)
    try:
        existing = vault.read_vault(password)
    except vault.VaultPasswordMismatch as exc:
        return templates.TemplateResponse(
            request,
            "vault.html",
            {**status, "error": str(exc), "values": {}, "desktop_users": []},
        )

    template = vault.load_template()
    desktop_users = existing.get("vault_desktop_users") or template.get("vault_desktop_users") or []
    return templates.TemplateResponse(
        request,
        "vault.html",
        {**status, "notice": notice, "values": existing, "desktop_users": desktop_users},
    )


@app.post("/vault")
async def vault_save(request: Request):
    session_id = request.state.session_id
    status = session_status_context(session_id)
    if not status["vault_unlocked"]:
        return templates.TemplateResponse(
            request,
            "vault.html",
            {
                **status,
                "error": "Vault password is required to save the configuration.",
                "values": {},
                "desktop_users": [],
            },
            status_code=400,
        )

    password = secret_store.vault_password(session_id)
    form = await request.form()
    desktop_users = _desktop_users_from_form(form)
    form_values = {
        "vault_my_ansible_user_name": form.get("vault_my_ansible_user_name", ""),
        "vault_my_ansible_user_password": form.get("vault_my_ansible_user_password", ""),
        "vault_gnome_keyring_password": form.get("vault_gnome_keyring_password", ""),
        "vault_windows_admin_password": form.get("vault_windows_admin_password", ""),
        "vault_desktop_users": desktop_users,
    }

    try:
        existing = vault.read_vault(password)
    except vault.VaultPasswordMismatch as exc:
        return templates.TemplateResponse(
            request,
            "vault.html",
            {**status, "error": str(exc), "values": {}, "desktop_users": desktop_users},
            status_code=400,
        )

    merged = vault.apply_form_values(existing, form_values)
    vault.write_vault(merged, password)
    return messages.done("/vault", "vault-saved")


@app.post("/vault/users", response_class=HTMLResponse)
async def vault_users(request: Request):
    """Fragment: add or remove a desktop-user row before saving (FR-030's
    sibling for the vault form -- values already typed into other rows are
    preserved because the triggering button includes the whole form).
    """
    form = await request.form()
    desktop_users = _desktop_users_from_form(form)
    action = form.get("action")

    if action == "add":
        desktop_users.append({"name": "", "password": "", "exa_api_key": ""})
    elif action == "remove" and len(desktop_users) > 1:
        index = int(form.get("index", -1))
        if 0 <= index < len(desktop_users):
            desktop_users.pop(index)

    return templates.TemplateResponse(
        request, "fragments/desktop_users.html", {"desktop_users": desktop_users}
    )


def _sshkey_base_context(status: dict) -> dict:
    public_key = sshkeys.read_public_key()
    return {
        **status,
        "public_key": public_key,
        "fingerprint": sshkeys.fingerprint() if public_key else None,
        "registered_name": None,
        "remote_conflict": None,
        "pending_action": None,
    }


@app.get("/sshkey", response_class=HTMLResponse)
async def sshkey_page(request: Request, done: str | None = None):
    session_id = request.state.session_id
    status = session_status_context(session_id)
    context = _sshkey_base_context(status)
    context["notice"] = messages.notice_for(done)
    if context["public_key"] and status["vault_unlocked"]:
        try:
            existing = vault.read_vault(secret_store.vault_password(session_id))
            context["registered_name"] = existing.get("vault_my_ssh_key_name")
        except vault.VaultPasswordMismatch:
            pass
    return templates.TemplateResponse(request, "sshkey.html", context)


async def _handle_sshkey_action(request: Request, *, action_path: str, confirm_replace: bool):
    session_id = request.state.session_id
    status = session_status_context(session_id)

    if not (status["token_unlocked"] and status["vault_unlocked"]):
        context = _sshkey_base_context(status)
        context["error"] = "Both the Hetzner API token and the vault password must be unlocked."
        return templates.TemplateResponse(request, "sshkey.html", context, status_code=400)

    token = secret_store.hcloud_token(session_id)
    password = secret_store.vault_password(session_id)

    try:
        sshkeys.generate_and_register(
            token=token, vault_password=password, confirm_replace=confirm_replace
        )
    except sshkeys.SshKeyExistsRemotely as exc:
        context = _sshkey_base_context(status)
        context["remote_conflict"] = exc.existing
        context["pending_action"] = action_path
        return templates.TemplateResponse(request, "sshkey.html", context, status_code=409)
    except (hcloud_api.HetznerApiError, vault.VaultPasswordMismatch) as exc:
        context = _sshkey_base_context(status)
        context["error"] = str(exc)
        return templates.TemplateResponse(request, "sshkey.html", context, status_code=502)

    return messages.done(
        "/sshkey",
        "sshkey-rotated" if action_path == "/sshkey/rotate" else "sshkey-generated",
    )


@app.post("/sshkey/generate")
async def sshkey_generate(request: Request, confirm_replace: str = Form(default="")):
    return await _handle_sshkey_action(
        request, action_path="/sshkey/generate", confirm_replace=bool(confirm_replace)
    )


@app.post("/sshkey/rotate")
async def sshkey_rotate(
    request: Request,
    confirm_rotation: str = Form(default=""),
    confirm_replace: str = Form(default=""),
):
    # FR-026: rotation requires explicit confirmation of the warning that
    # already-provisioned machines become unreachable, checked before any
    # rotation work happens.
    if not confirm_rotation:
        status = session_status_context(request.state.session_id)
        context = _sshkey_base_context(status)
        context["error"] = "Rotation requires confirming the warning first."
        return templates.TemplateResponse(request, "sshkey.html", context, status_code=400)

    return await _handle_sshkey_action(
        request, action_path="/sshkey/rotate", confirm_replace=bool(confirm_replace)
    )


def _default_selected(server_types: dict) -> dict:
    return {
        "profile": next(iter(config.PROFILES)),
        "server_type": next(iter(server_types)),
        # Left for _create_form_context to fill in once it knows which
        # locations that server type actually offers.
        "location": None,
        "image": config.UBUNTU_LTS_IMAGES[0]["value"],
        "image_custom": "",
        "server_types_live": False,
        "images_live": False,
    }


def _selected_from_preset(preset: presets.Preset, images: list[dict]) -> dict:
    """A preset's choices in the shape the create template expects.

    ``images`` is the catalogue the form is about to be rendered against
    -- the live one when the preset was saved with the full catalogue
    showing. An image in that list is a list selection; anything else goes
    into the free-text field, which is the only place it could be shown.
    """
    known_images = {image["value"] for image in images}
    return {
        "profile": preset.profile,
        "server_type": preset.server_type,
        "location": preset.location,
        "image": preset.image if preset.image in known_images else None,
        "image_custom": "" if preset.image in known_images else preset.image,
        "server_types_live": preset.server_types_live,
        "images_live": preset.images_live,
    }


def _resolved_image(selected: dict) -> str:
    """The image a submission would actually provision: free text wins over
    the list, matching _create_choices_from_form."""
    return (selected.get("image_custom") or "").strip() or (selected.get("image") or "")


def _preset_fields(selected: dict) -> dict[str, str]:
    """The current choices as the rail compares them: short labels, short
    values. The two catalogue toggles are part of a preset, so a form that
    differs only in which list it was picked from is a form that differs."""
    return {
        "Profile": selected.get("profile") or "",
        "Size": selected.get("server_type") or "",
        "Location": selected.get("location") or "",
        "Image": _resolved_image(selected),
        "Size list": "live" if selected.get("server_types_live") else "built-in",
        "Image list": "live" if selected.get("images_live") else "built-in",
    }


def _preset_diff(selected: dict, preset: presets.Preset | None) -> list[dict[str, str]]:
    """Field by field, what the form says now against what the preset says.

    An empty list means the form still is that preset. The rail renders
    this on load; static/preset-selection.js recomputes it after every
    edit, from the same preset data, so the two agree.
    """
    if preset is None:
        return []
    saved = _preset_fields(
        {
            "profile": preset.profile,
            "server_type": preset.server_type,
            "location": preset.location,
            "image_custom": preset.image,
            "server_types_live": preset.server_types_live,
            "images_live": preset.images_live,
        }
    )
    current = _preset_fields(selected)
    return [
        {"field": field, "was": saved[field], "now": current[field]}
        for field in saved
        if saved[field] != current[field]
    ]


def _matching_preset(selected: dict, all_presets: list[presets.Preset]) -> str | None:
    """The preset the current choices already are, if any.

    Saving is refused for a name that is taken, so offering to save
    choices that are already stored under some name only leads to a
    rejection or to a second preset saying the same thing. Matching on the
    choices rather than on "which preset was loaded" also catches the user
    who arrived at an existing preset by hand.
    """
    image = _resolved_image(selected)
    for preset in all_presets:
        if (
            preset.profile == selected.get("profile")
            and preset.server_type == selected.get("server_type")
            and preset.location == selected.get("location")
            and preset.image == image
            and preset.server_types_live == bool(selected.get("server_types_live"))
            and preset.images_live == bool(selected.get("images_live"))
        ):
            return preset.name
    return None


def _static_server_types() -> dict[str, dict]:
    return {
        value: {**spec, "locations": list(config.LOCATIONS)} for value, spec in config.SERVER_TYPES.items()
    }


def _fetch_live_images(token: str) -> tuple[list[dict], str | None]:
    """Called only when the "show full catalogue" toggle is on (create.html)
    -- the page's default is always the curated static list
    (config.UBUNTU_LTS_IMAGES), never a background live lookup."""
    try:
        raw = hcloud_api.list_images(token)
    except hcloud_api.HetznerApiError as exc:
        return config.UBUNTU_LTS_IMAGES, f"Could not load images from Hetzner ({exc}); showing built-in defaults."

    images = [
        {"value": image["name"], "label": image.get("description") or image["name"], "support_end": None}
        for image in raw
        if image.get("name")
    ]
    if not images:
        return config.UBUNTU_LTS_IMAGES, "Hetzner returned no available system images; showing built-in defaults."
    return images, None


def _fetch_live_server_types(token: str) -> tuple[dict[str, dict], str | None]:
    """Called only when the "show all sizes" toggle is on (create.html) --
    mirrors _fetch_live_images. Restricted to non-deprecated x86 types: this
    UI's image list is x86-only, so an arm64 (cax) server type could never
    actually boot one."""
    try:
        raw = hcloud_api.list_server_types(token)
    except hcloud_api.HetznerApiError as exc:
        return (
            _static_server_types(),
            f"Could not load server sizes from Hetzner ({exc}); showing built-in defaults.",
        )

    server_types: dict[str, dict] = {}
    for server_type in raw:
        if server_type.get("architecture") != "x86" or server_type.get("deprecation"):
            continue
        prices = server_type.get("prices") or []
        locations = sorted({price["location"] for price in prices if price.get("location")})
        if not locations:
            continue
        monthly_prices = [
            float(price["price_monthly"]["gross"])
            for price in prices
            if price.get("price_monthly", {}).get("gross") is not None
        ]
        server_types[server_type["name"]] = {
            "vcpu": server_type.get("cores"),
            "memory_gb": server_type.get("memory"),
            "disk_gb": server_type.get("disk"),
            # Price can differ by location; the cheapest one is shown with
            # "from" in the template rather than an exact per-location figure.
            "monthly_eur": min(monthly_prices) if monthly_prices else None,
            "locations": locations,
        }

    if not server_types:
        return (
            _static_server_types(),
            "Hetzner returned no available x86 server types; showing built-in defaults.",
        )
    return server_types, None


def _resolve_images(session_id: str, status: dict, *, live: bool) -> tuple[list[dict], str | None]:
    if not live:
        return config.UBUNTU_LTS_IMAGES, None
    if not status["token_unlocked"]:
        return (
            config.UBUNTU_LTS_IMAGES,
            "Unlock the Hetzner API token first to load the live catalogue; showing built-in defaults.",
        )
    return _fetch_live_images(secret_store.hcloud_token(session_id))


def _resolve_server_types(session_id: str, status: dict, *, live: bool) -> tuple[dict[str, dict], str | None]:
    if not live:
        return _static_server_types(), None
    if not status["token_unlocked"]:
        return (
            _static_server_types(),
            "Unlock the Hetzner API token first to load live sizes; showing built-in defaults.",
        )
    return _fetch_live_server_types(secret_store.hcloud_token(session_id))


def _locations_for_server_type(server_types: dict, server_type_value: str | None) -> dict[str, str]:
    spec = server_types.get(server_type_value) if server_type_value else None
    codes = spec["locations"] if spec else list(config.LOCATIONS)
    return {code: config.LOCATIONS.get(code, code) for code in codes}


def _create_form_context(
    status: dict,
    session_id: str,
    *,
    error: str | None = None,
    selected: dict | None = None,
    restoring: str | None = None,
    preset_base: str | None = None,
) -> dict:
    # The curated static lists on a fresh render (FR: "by default the
    # hardcoded defaults should be shown"). A live catalogue is fetched
    # here only when the selection being rendered was made against one --
    # a preset saved with a toggle on, or a rejected submission carrying
    # its toggles back -- otherwise only from the fragment routes below,
    # on explicit user request.
    images_live = bool((selected or {}).get("images_live"))
    server_types_live = bool((selected or {}).get("server_types_live"))
    images, images_notice = _resolve_images(session_id, status, live=images_live)
    server_types, server_types_notice = _resolve_server_types(
        session_id, status, live=server_types_live
    )

    selected = dict(selected) if selected else _default_selected(server_types)

    # A choice that is not in the catalogue this render offers cannot be
    # shown as selected, so it is replaced. Silent while the user is
    # driving the form; named, part by part, while restoring a preset --
    # the size can fail to restore while the image succeeds, and "it
    # loaded" would then be a lie about half the form (Principle XII).
    gaps: list[str] = []
    if server_types_notice:
        gaps.append(server_types_notice)
    if images_notice:
        gaps.append(images_notice)

    if selected["server_type"] not in server_types:
        wanted = selected["server_type"]
        selected["server_type"] = next(iter(server_types))
        if wanted:
            gaps.append(
                f"Server size '{wanted}' is not in the catalogue shown here, "
                f"so '{selected['server_type']}' is selected instead."
            )

    locations = _locations_for_server_type(server_types, selected["server_type"])
    if selected.get("location") not in locations:
        wanted = selected.get("location")
        selected["location"] = next(iter(locations), None)
        if wanted:
            gaps.append(
                f"Location '{wanted}' cannot be ordered for server size "
                f"'{selected['server_type']}', so '{selected['location']}' is selected instead."
            )

    known_images = {image["value"] for image in images}
    if selected.get("image") and selected["image"] not in known_images:
        wanted = selected["image"]
        selected["image"] = images[0]["value"] if images else None
        gaps.append(
            f"Image '{wanted}' is not in the image list shown here, "
            f"so '{selected['image']}' is selected instead."
        )

    if restoring and gaps:
        restore_error = f"Preset '{restoring}' was not restored in full. " + " ".join(gaps)
        error = f"{error} {restore_error}" if error else restore_error

    all_presets = presets.list_presets()
    # Which preset the form started from, kept across edits by a hidden
    # field, so the rail can keep saying what changed since it was loaded
    # rather than only until the first edit.
    base_name = preset_base or restoring
    base = next((preset for preset in all_presets if preset.name == base_name), None)
    return {
        **status,
        "error": error,
        "profiles": config.PROFILES,
        "server_types": server_types,
        "locations": locations,
        "images": images,
        "presets": all_presets,
        "preset_base": base.name if base else None,
        "preset_diff": _preset_diff(selected, base),
        # The same list the matching above walks, for the browser to walk
        # after every edit without a round trip (static/preset-selection.js).
        "presets_json": json.dumps([asdict(preset) for preset in all_presets]),
        "selected": selected,
        "selected_preset": restoring,
        # Both the initial render and preset-selection.js compute this;
        # the server's answer is what a scripting-off browser gets.
        "matching_preset": _matching_preset(selected, all_presets),
        # A preset can carry a free-text image, and a rejected submission
        # carries back whatever was typed, so the check runs on render too
        # rather than waiting for the field to be touched. An empty field
        # costs no API call.
        "check": _check_image(session_id, status, selected.get("image_custom") or ""),
    }


def _with_catalogue_notice(message: str, notice: str | None) -> str:
    """A live lookup that fell back to the built-in lists changes what
    counts as a valid size, so its notice is appended to the rejection --
    otherwise "not available" reads as wrong when the real cause is that
    the live catalogue could not be reached."""
    return f"{message} {notice}" if notice else message


class CreateChoicesInvalid(ValueError):
    """A create-form submission is missing a required choice, or names one
    that cannot be ordered. Carries a user-facing message naming the field
    at fault."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _selected_from_form(form) -> dict:
    """The submitted choices in the shape the create template expects, so a
    rejected submission is redisplayed with what the user picked rather
    than reset to defaults."""
    images_live = bool(form.get("images_live"))
    # With the full catalogue showing, any name the radios offered is a
    # valid list selection; against the built-in list, only its own names
    # are, so a stale one is dropped rather than redisplayed as selected.
    known_images = None if images_live else {image["value"] for image in config.UBUNTU_LTS_IMAGES}
    image_select = (form.get("image_select") or "").strip()
    image_custom = (form.get("image_custom") or "").strip()
    return {
        "profile": (form.get("profile") or "").strip() or next(iter(config.PROFILES)),
        "server_type": (form.get("server_type") or "").strip(),
        "location": (form.get("location") or "").strip(),
        # Free text overrides the list when both arrive (a submission made
        # with scripting off), so the redisplayed list shows nothing
        # selected rather than an option that would be ignored.
        "image": None
        if image_custom
        else (image_select if (known_images is None or image_select in known_images) else None),
        "image_custom": image_custom,
        # Which catalogue the submission was made against, so a redisplay
        # (and a preset saved from it) matches its choices against the same
        # lists the user was looking at.
        "server_types_live": bool(form.get("server_types_live")),
        "images_live": bool(form.get("images_live")),
    }


def _check_image(session_id: str, status: dict, image: str) -> dict:
    """Ask Hetzner whether one image reference exists for this account.

    Returns a state the fragment renders and POST /create gates on:

    - ``empty``    nothing typed yet, say nothing
    - ``unlocked`` the token is locked, so the check cannot run
    - ``ok``       found (``note`` carries a deprecation warning if any)
    - ``missing``  the account has no such x86 image
    - ``error``    the lookup itself failed

    Checking here rather than letting the run fail is the whole point: a
    bad image name otherwise surfaces minutes later, inside playbook
    output, after a machine has already been claimed from the name pool.
    """
    image = image.strip()
    if not image:
        return {"state": "empty", "message": "", "note": None}
    if not status["token_unlocked"]:
        return {
            "state": "unlocked",
            "message": "Unlock the Hetzner API token to check this image name.",
            "note": None,
        }

    try:
        found = hcloud_api.find_image(secret_store.hcloud_token(session_id), image)
    except hcloud_api.HetznerApiError as exc:
        return {
            "state": "error",
            "message": f"Could not check image '{image}' with Hetzner ({exc}).",
            "note": None,
        }

    if found is None:
        return {
            "state": "missing",
            "message": (
                f"No x86 image named '{image}' in this account. Check the spelling, "
                "or turn on the full catalogue above to pick from the list."
            ),
            "note": None,
        }

    label = found.get("description") or found.get("name") or image
    note = None
    if found.get("deprecated"):
        # Still bootable until Hetzner withdraws it, so this is a warning
        # rather than a refusal.
        note = f"This image is deprecated (withdrawn {found['deprecated']})."
    return {"state": "ok", "message": f"{label} is available.", "note": note}


def _server_types_for_submission(session_id: str, status: dict, form) -> tuple[dict[str, dict], str | None]:
    """Resolve the size catalogue a submission was rendered against. The
    create form carries its own "show all sizes" toggle state in
    server_types_live (create.html), so a live-only size validates as
    itself instead of looking unknown against the static list."""
    return _resolve_server_types(session_id, status, live=bool(form.get("server_types_live")))


def _create_choices_from_form(form, server_types: dict) -> dict:
    """Validate a create-form submission against the catalogue it was
    rendered from, and return the four provisioning choices.

    Principle XII (Fail Loud): a missing or unorderable choice raises here
    rather than falling back to a hardcoded default. Substituting one
    silently would provision a machine whose profile, size, location or
    image differs from what the operator selected, and would defer an
    unorderable size/location pair to a failure inside the Hetzner API
    instead of reporting it at the point of the gap.
    """
    profile = (form.get("profile") or "").strip()
    if not profile:
        raise CreateChoicesInvalid("Select a profile before provisioning.")
    if profile not in config.PROFILES:
        raise CreateChoicesInvalid(
            f"Profile '{profile}' is not one of: {', '.join(config.PROFILES)}."
        )

    server_type = (form.get("server_type") or "").strip()
    if not server_type:
        raise CreateChoicesInvalid("Select a server size before provisioning.")
    if server_type not in server_types:
        raise CreateChoicesInvalid(
            f"Server size '{server_type}' is not available. Pick one from the size table."
        )

    locations = _locations_for_server_type(server_types, server_type)
    location = (form.get("location") or "").strip()
    if not location:
        raise CreateChoicesInvalid("Select a location before provisioning.")
    if location not in locations:
        raise CreateChoicesInvalid(
            f"Server size '{server_type}' cannot be ordered in location '{location}'. "
            f"Available: {', '.join(locations) or 'none'}."
        )

    # Free text is a deliberate escape hatch (create.html "Other"), so the
    # image is checked for presence only -- an unknown name is the user's
    # choice to make, and Hetzner rejects it by name if it does not exist.
    image = (form.get("image_custom") or "").strip() or (form.get("image_select") or "").strip()
    if not image:
        raise CreateChoicesInvalid(
            "Select an operating system image, or type one into the free-text field."
        )

    return {
        "profile": profile,
        "server_type": server_type,
        "location": location,
        "image": image,
    }


@app.get("/create/locations", response_class=HTMLResponse)
async def create_locations_fragment(
    request: Request, server_type: str = "", location: str = "", server_types_live: str = ""
):
    """Backs the location fieldset's htmx refresh on server_type change
    (create.html) -- a server type is only orderable in the locations its
    own prices list names, so the two fieldsets can't be independent.
    server_types_live must mirror whichever source (static/live) the
    server-size table currently shows, or a live-only type's own name
    would look "unknown" against the static list and get silently reset.
    """
    session_id = request.state.session_id
    status = session_status_context(session_id)
    server_types, _ = _resolve_server_types(session_id, status, live=bool(server_types_live))
    if server_type not in server_types:
        server_type = next(iter(server_types), None)
    locations = _locations_for_server_type(server_types, server_type)
    if location not in locations:
        location = next(iter(locations), None)
    return templates.TemplateResponse(
        request,
        "fragments/location_fieldset.html",
        {"locations": locations, "selected": {"location": location}},
    )


@app.get("/create/server-types", response_class=HTMLResponse)
async def create_server_types_fragment(
    request: Request, server_types_live: str = "", server_type: str = "", location: str = ""
):
    """Backs the "show all sizes" toggle (create.html). Swaps the size table
    and, out-of-band, the location fieldset in the same response -- toggling
    sources can change which server type ends up selected, which must carry
    a fresh, correctly-scoped location list with it rather than leaving the
    old fieldset showing locations for a size that's no longer selected."""
    session_id = request.state.session_id
    status = session_status_context(session_id)
    server_types, notice = _resolve_server_types(session_id, status, live=bool(server_types_live))

    if server_type not in server_types:
        server_type = next(iter(server_types))
    locations = _locations_for_server_type(server_types, server_type)
    if location not in locations:
        location = next(iter(locations), None)

    table_html = templates.get_template("fragments/server_types_table.html").render(
        request=request,
        server_types=server_types,
        selected={"server_type": server_type},
        notice=notice,
    )
    location_html = templates.get_template("fragments/location_fieldset.html").render(
        request=request,
        locations=locations,
        selected={"location": location},
        oob=True,
    )
    return HTMLResponse(table_html + location_html)


@app.get("/create/images", response_class=HTMLResponse)
async def create_images_fragment(request: Request, images_live: str = "", image: str = ""):
    """Backs the "show full catalogue" toggle (create.html). Only the
    curated-options list is swapped, not the free-text "Other" input next
    to it, so anything the user already typed there survives the toggle."""
    session_id = request.state.session_id
    status = session_status_context(session_id)
    images, notice = _resolve_images(session_id, status, live=bool(images_live))

    known = {entry["value"] for entry in images}
    if image not in known:
        image = images[0]["value"] if images else None

    return templates.TemplateResponse(
        request,
        "fragments/image_options.html",
        {"images": images, "selected": {"image": image}, "notice": notice},
    )


@app.get("/create/validate-image", response_class=HTMLResponse)
async def create_validate_image_fragment(request: Request, image_custom: str = ""):
    """Backs the live check on the free-text image field (create.html). Read
    only, and advisory: POST /create runs the same check as a hard gate, so
    a user who never triggers this one is still not allowed to start a run
    on an image that does not exist."""
    session_id = request.state.session_id
    status = session_status_context(session_id)
    return templates.TemplateResponse(
        request,
        "fragments/image_validation.html",
        {"check": _check_image(session_id, status, image_custom)},
    )


@app.get("/create", response_class=HTMLResponse)
async def create_form(request: Request, preset: str | None = None):
    session_id = request.state.session_id
    status = session_status_context(session_id)
    selected = None
    error = None
    restoring = None
    if preset:
        try:
            saved = presets.get(preset)
        except presets.PresetNotFound:
            error = messages.preset_missing(preset)
        else:
            # Resolved against the catalogue the preset was saved with, so
            # a live-only size or image is matched against the list that
            # actually contains it. _create_form_context reports whichever
            # parts of the restore could not be honoured.
            images, _ = _resolve_images(session_id, status, live=saved.images_live)
            selected = _selected_from_preset(saved, images)
            restoring = preset
    context = _create_form_context(
        status, session_id, error=error, selected=selected, restoring=restoring
    )
    return templates.TemplateResponse(request, "create.html", context)


@app.post("/create/preview", response_class=HTMLResponse)
async def create_preview(request: Request):
    """FR-031: show exactly what will run, without running it."""
    session_id = request.state.session_id
    status = session_status_context(session_id)
    form = await request.form()
    server_types, notice = _server_types_for_submission(session_id, status, form)
    try:
        choices = _create_choices_from_form(form, server_types)
    except CreateChoicesInvalid as exc:
        return templates.TemplateResponse(
            request,
            "fragments/command_preview.html",
            {"command": None, "error": _with_catalogue_notice(exc.message, notice)},
            status_code=400,
        )
    return templates.TemplateResponse(
        request, "fragments/command_preview.html", {"command": build_provision_command(**choices)}
    )


@app.post("/create")
async def create_start(request: Request):
    session_id = request.state.session_id
    status = session_status_context(session_id)

    if not (status["token_unlocked"] and status["vault_unlocked"]):
        context = _create_form_context(
            status, session_id, error="Both the Hetzner API token and the vault password must be unlocked."
        )
        return templates.TemplateResponse(request, "create.html", context, status_code=400)

    # FR-045: refuse before any provider call -- create-vm.yml claims a
    # name from the pool before it ever contacts Hetzner. The same first-free
    # rule decides the name here, so it is also the name this run is about,
    # and the run view can say which machine it is provisioning.
    claimed_name = pool.next_free_name()
    if claimed_name is None:
        context = _create_form_context(
            status,
            session_id,
            error="The hostname pool is exhausted. Add more names under Name pool "
            "before provisioning.",
        )
        return templates.TemplateResponse(request, "create.html", context, status_code=400)

    form = await request.form()
    server_types, notice = _server_types_for_submission(session_id, status, form)
    try:
        choices = _create_choices_from_form(form, server_types)
    except CreateChoicesInvalid as exc:
        # FR-045 in spirit: refuse before any provider call. Redisplayed
        # with the submitted choices so nothing the user picked is lost.
        context = _create_form_context(
            status,
            session_id,
            error=_with_catalogue_notice(exc.message, notice),
            selected=_selected_from_form(form),
            preset_base=(form.get("preset_base") or "") or None,
        )
        return templates.TemplateResponse(request, "create.html", context, status_code=400)

    # The live check on the free-text field is advisory -- it can be
    # bypassed with scripting off, by a preset carrying a stale image, or by
    # editing and submitting fast enough. This is the gate that actually
    # holds: an image the account cannot see must not reach a run, where it
    # would fail minutes later with a name already claimed from the pool.
    # It covers the built-in list too, which can go stale as Hetzner adds
    # and withdraws releases.
    image_check = _check_image(session_id, status, choices["image"])
    if image_check["state"] in {"missing", "error"}:
        context = _create_form_context(
            status,
            session_id,
            error=image_check["message"],
            selected=_selected_from_form(form),
            preset_base=(form.get("preset_base") or "") or None,
        )
        return templates.TemplateResponse(request, "create.html", context, status_code=400)

    command = build_provision_command(**choices)

    try:
        await runner.start(
            session_id=session_id, action="provision", target=claimed_name, command=command
        )
    except RunAlreadyActive as exc:
        context = _create_form_context(
            status, session_id, error=messages.run_already_active(exc.active.action)
        )
        return templates.TemplateResponse(request, "create.html", context, status_code=409)
    except SecretsUnavailable as exc:
        context = _create_form_context(
            status, session_id, error=messages.secrets_missing(exc.missing)
        )
        return templates.TemplateResponse(request, "create.html", context, status_code=400)

    return RedirectResponse("/run", status_code=303)


@app.get("/run", response_class=HTMLResponse)
async def run_view(request: Request):
    status = session_status_context(request.state.session_id)
    run = runner.active
    if run is None:
        context = {**status, "command": None, "output": [], "outcome": None, "state": None}
    else:
        context = {
            **status,
            "command": run.command,
            "output": list(run.output),
            "outcome": run.outcome,
            "state": describe_run(run),
        }
    return templates.TemplateResponse(request, "run.html", context)


@app.get("/run/stream")
async def run_stream(request: Request):
    """Server-Sent Events: one event per output line, then a terminal
    event carrying the outcome and exit code, so a client never has to
    infer completion from silence (FR-036, FR-037).
    """
    run = runner.active

    async def event_source():
        if run is None:
            yield f"event: terminal\ndata: {json.dumps({'outcome': None, 'exit_code': None})}\n\n"
            return
        async for line in run.stream_lines():
            yield f"data: {line}\n\n"
        # The terminal event carries the state already put into words:
        # runner.describe is the only place that wording lives, so the
        # browser renders what the server would have rendered.
        terminal = {"outcome": run.outcome, "exit_code": run.exit_code, **describe_run(run)}
        yield f"event: terminal\ndata: {json.dumps(terminal)}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")


@app.post("/run/cancel")
async def run_cancel(request: Request):
    await runner.cancel()
    return RedirectResponse("/run", status_code=303)


@app.post("/machines/{name}/configure")
async def machine_configure(name: str, request: Request):
    session_id = request.state.session_id
    status = session_status_context(session_id)

    if not (status["token_unlocked"] and status["vault_unlocked"]):
        context = _dashboard_context(
            request, error="Both the Hetzner API token and the vault password must be unlocked."
        )
        return templates.TemplateResponse(request, "dashboard.html", context, status_code=400)

    command = build_configure_command(machine_name=name)
    try:
        await runner.start(session_id=session_id, action="configure", target=name, command=command)
    except RunAlreadyActive as exc:
        context = _dashboard_context(
            request, error=messages.run_already_active(exc.active.action)
        )
        return templates.TemplateResponse(request, "dashboard.html", context, status_code=409)
    except SecretsUnavailable as exc:
        context = _dashboard_context(request, error=messages.secrets_missing(exc.missing))
        return templates.TemplateResponse(request, "dashboard.html", context, status_code=400)

    return RedirectResponse("/run", status_code=303)


@app.post("/machines/{name}/destroy")
async def machine_destroy(name: str, request: Request, confirm_name: str = Form(default="")):
    """FR-033: destruction requires the typed machine name as confirmation,
    matching exactly."""
    session_id = request.state.session_id
    status = session_status_context(session_id)

    if confirm_name != name:
        context = _dashboard_context(
            request,
            error=f"Type '{name}' exactly to confirm destroying it. Nothing was removed.",
        )
        return templates.TemplateResponse(request, "dashboard.html", context, status_code=400)

    if not (status["token_unlocked"] and status["vault_unlocked"]):
        context = _dashboard_context(
            request, error="Both the Hetzner API token and the vault password must be unlocked."
        )
        return templates.TemplateResponse(request, "dashboard.html", context, status_code=400)

    command = build_destroy_command(machine_name=name)
    try:
        await runner.start(session_id=session_id, action="destroy", target=name, command=command)
    except RunAlreadyActive as exc:
        context = _dashboard_context(
            request, error=messages.run_already_active(exc.active.action)
        )
        return templates.TemplateResponse(request, "dashboard.html", context, status_code=409)
    except SecretsUnavailable as exc:
        context = _dashboard_context(request, error=messages.secrets_missing(exc.missing))
        return templates.TemplateResponse(request, "dashboard.html", context, status_code=400)

    return RedirectResponse("/run", status_code=303)


@app.get("/pool", response_class=HTMLResponse)
async def pool_page(request: Request, done: str | None = None):
    status = session_status_context(request.state.session_id)
    pool_status = pool.status()
    return templates.TemplateResponse(
        request,
        "pool.html",
        {
            **status,
            "notice": messages.notice_for(done),
            "entries": pool_status.entries,
            "used": pool_status.used,
            "free": pool_status.free,
        },
    )


@app.post("/pool")
async def pool_save(request: Request):
    """FR-042/FR-043: validates and rejects the whole submission on any
    failure, naming the offending entry; refuses to drop or rename an
    entry a live machine uses.
    """
    status = session_status_context(request.state.session_id)
    form = await request.form()
    raw_entries = form.get("entries", "")
    entries = [line.strip() for line in raw_entries.splitlines() if line.strip()]

    try:
        pool.save(entries)
    except pool.PoolValidationError as exc:
        pool_status = pool.status()
        context = {
            **status,
            "entries": entries,
            "used": pool_status.used,
            "free": pool_status.free,
            "error": f"{exc.entry}: {exc.reason}",
        }
        return templates.TemplateResponse(request, "pool.html", context, status_code=400)

    return messages.done("/pool", "pool-saved")


@app.get("/presets", response_class=HTMLResponse)
async def presets_page(request: Request, done: str | None = None):
    status = session_status_context(request.state.session_id)
    return templates.TemplateResponse(
        request,
        "presets.html",
        {**status, "notice": messages.notice_for(done), "presets": presets.list_presets()},
    )


@app.post("/presets", response_class=HTMLResponse)
async def presets_save(request: Request):
    """Fragment: save the current create-form choices under a name
    (FR-030). Saving over an existing name is refused (409) rather than
    silently overwritten -- the create form does not currently offer a
    confirm-and-overwrite step, so a taken name must be renamed instead.
    """
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return _flash(request, messages.preset_name_required(), tone="error", status_code=400)

    # Same validation as provisioning itself: a preset that silently
    # recorded a default the user never picked would hand that wrong choice
    # back on every future load of it (Principle XII).
    session_id = request.state.session_id
    status = session_status_context(session_id)
    server_types, notice = _server_types_for_submission(session_id, status, form)
    try:
        choices = _create_choices_from_form(form, server_types)
    except CreateChoicesInvalid as exc:
        return _flash(
            request,
            _with_catalogue_notice(exc.message, notice),
            tone="error",
            status_code=400,
        )

    # Saved with the catalogue the choices were made against: a size or
    # image that only exists in the live catalogue is unrestorable without
    # it (presets.Preset).
    preset = presets.Preset(
        name=name,
        **choices,
        server_types_live=bool(form.get("server_types_live")),
        images_live=bool(form.get("images_live")),
    )

    already = _matching_preset(_selected_from_form(form), presets.list_presets())
    if already and already != name:
        return _flash(request, messages.preset_already_saved(already), status_code=409)

    try:
        presets.save(preset)
    except presets.PresetNameTaken:
        return _flash(request, messages.preset_name_taken(name), tone="error", status_code=409)

    return _flash(request, messages.preset_saved(name))


@app.post("/presets/{name}/update", response_class=HTMLResponse)
async def presets_update(name: str, request: Request):
    """Fragment: write the current create-form choices over an existing
    preset (the rail's Update action).

    Validated exactly as saving a new one is, for the same reason: a
    preset that silently recorded a default nobody picked would hand that
    wrong choice back on every later load of it.
    """
    session_id = request.state.session_id
    status = session_status_context(session_id)
    form = await request.form()

    server_types, notice = _server_types_for_submission(session_id, status, form)
    try:
        choices = _create_choices_from_form(form, server_types)
    except CreateChoicesInvalid as exc:
        return _flash(
            request, _with_catalogue_notice(exc.message, notice), tone="error", status_code=400
        )

    try:
        presets.get(name)
    except presets.PresetNotFound:
        return _flash(request, messages.preset_missing(name), tone="error", status_code=404)

    presets.save(
        presets.Preset(
            name=name,
            **choices,
            server_types_live=bool(form.get("server_types_live")),
            images_live=bool(form.get("images_live")),
        ),
        overwrite=True,
    )
    return _flash(request, messages.preset_updated(name))


@app.post("/presets/{name}/delete")
async def presets_delete(name: str):
    try:
        presets.delete(name)
    except presets.PresetNotFound:
        pass
    return messages.done("/presets", "preset-deleted")


@app.post("/presets/{name}/rename")
async def presets_rename(name: str, request: Request, new_name: str = Form(default="")):
    status = session_status_context(request.state.session_id)
    try:
        presets.rename(name, new_name)
    except presets.PresetNameTaken:
        context = {
            **status,
            "presets": presets.list_presets(),
            "error": messages.preset_name_taken(new_name),
        }
        return templates.TemplateResponse(request, "presets.html", context, status_code=409)
    except presets.PresetNotFound:
        context = {
            **status,
            "presets": presets.list_presets(),
            "error": messages.preset_missing(name),
        }
        return templates.TemplateResponse(request, "presets.html", context, status_code=404)

    return messages.done("/presets", "preset-renamed")


@app.post("/defaults/refresh")
async def defaults_refresh(request: Request):
    """FR-047/FR-048: contracts/http-routes.md "Defaults refresh". Copies
    only pristine-tracked files the user has not edited; never touches the
    encrypted configuration, machine records, name pool or presets.
    """
    seed.refresh_defaults()
    return messages.done("/", "defaults-refreshed")
