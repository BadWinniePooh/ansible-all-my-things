"""Application assembly: routes, middleware, startup.

Every route that refuses an action names the cause (constitution
Principle XII) -- see the exception handling in each handler below.
FR-049: binds to loopback by default; see config.py for why that is
enforced by the compose files' port publishing rather than by this
process's own bind address.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, hcloud_api, inventory, pool, presets, seed, sshkeys, vault
from .runner import (
    RunAlreadyActive,
    Runner,
    build_configure_command,
    build_destroy_command,
    build_provision_command,
)
from .secrets import SecretsUnavailable, SecretStore

SESSION_COOKIE_NAME = "webui_session"

secret_store = SecretStore()
runner = Runner(secret_store)

app = FastAPI(title="Hetzner web frontend")

_templates_dir = Path(__file__).parent / "templates"
_static_dir = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(_templates_dir))

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


def _dashboard_context(request: Request, *, error: str | None = None, notice: str | None = None) -> dict:
    session_id = request.state.session_id
    status = session_status_context(session_id)
    pool_status = pool.status()
    return {
        "error": error,
        "notice": notice,
        "machines": inventory.list_machines(),
        "pool_free": pool_status.free,
        "pool_used": pool_status.used,
        "defaults_refresh_available": seed.needs_defaults_refresh(),
        "vault_configured": config.VAULT_FILE.exists(),
        **status,
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", _dashboard_context(request))


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

    return RedirectResponse("/", status_code=303)


@app.post("/session/lock")
async def session_lock(request: Request):
    secret_store.lock(request.state.session_id)
    return RedirectResponse("/", status_code=303)


@app.get("/session/status", response_class=HTMLResponse)
async def session_status(request: Request):
    status = session_status_context(request.state.session_id)
    return templates.TemplateResponse(request, "fragments/session_status.html", status)


def _desktop_users_from_form(form) -> list[dict]:
    names = form.getlist("desktop_user_name")
    passwords = form.getlist("desktop_user_password")
    exa_keys = form.getlist("desktop_user_exa_api_key")
    return [
        {"name": name, "password": password, "exa_api_key": exa_key}
        for name, password, exa_key in zip(names, passwords, exa_keys)
    ]


@app.get("/vault", response_class=HTMLResponse)
async def vault_form(request: Request):
    status = session_status_context(request.state.session_id)
    if not status["vault_unlocked"]:
        return templates.TemplateResponse(
            request, "vault.html", {**status, "values": {}, "desktop_users": []}
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
        request, "vault.html", {**status, "values": existing, "desktop_users": desktop_users}
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
    return RedirectResponse("/vault", status_code=303)


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
async def sshkey_page(request: Request):
    session_id = request.state.session_id
    status = session_status_context(session_id)
    context = _sshkey_base_context(status)
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

    return RedirectResponse("/sshkey", status_code=303)


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
    }


def _selected_from_preset(preset: presets.Preset) -> dict:
    known_images = {image["value"] for image in config.UBUNTU_LTS_IMAGES}
    return {
        "profile": preset.profile,
        "server_type": preset.server_type,
        "location": preset.location,
        "image": preset.image if preset.image in known_images else None,
        "image_custom": "" if preset.image in known_images else preset.image,
    }


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
    status: dict, session_id: str, *, error: str | None = None, selected: dict | None = None
) -> dict:
    # Always the curated static lists on a fresh render (FR: "by default
    # the hardcoded defaults should be shown") -- the live catalogue and
    # live sizes are only ever fetched from the toggle-backed fragment
    # routes below, on explicit user request.
    images, _ = _resolve_images(session_id, status, live=False)
    server_types, _ = _resolve_server_types(session_id, status, live=False)

    selected = dict(selected) if selected else _default_selected(server_types)
    if selected["server_type"] not in server_types:
        selected["server_type"] = next(iter(server_types))
    locations = _locations_for_server_type(server_types, selected["server_type"])
    if selected.get("location") not in locations:
        selected["location"] = next(iter(locations), None)

    return {
        **status,
        "error": error,
        "profiles": config.PROFILES,
        "server_types": server_types,
        "locations": locations,
        "images": images,
        "presets": presets.list_presets(),
        "selected": selected,
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
    known_images = {image["value"] for image in config.UBUNTU_LTS_IMAGES}
    image_select = (form.get("image_select") or "").strip()
    image_custom = (form.get("image_custom") or "").strip()
    return {
        "profile": (form.get("profile") or "").strip() or next(iter(config.PROFILES)),
        "server_type": (form.get("server_type") or "").strip(),
        "location": (form.get("location") or "").strip(),
        # Free text overrides the list when both arrive (a submission made
        # with scripting off), so the redisplayed list shows nothing
        # selected rather than an option that would be ignored.
        "image": None if image_custom else (image_select if image_select in known_images else None),
        "image_custom": image_custom,
    }


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


@app.get("/create", response_class=HTMLResponse)
async def create_form(request: Request, preset: str | None = None):
    session_id = request.state.session_id
    status = session_status_context(session_id)
    selected = None
    error = None
    if preset:
        try:
            selected = _selected_from_preset(presets.get(preset))
        except presets.PresetNotFound:
            error = f"No preset named '{preset}'."
    context = _create_form_context(status, session_id, error=error, selected=selected)
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
    # name from the pool before it ever contacts Hetzner.
    if pool.next_free_name() is None:
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
        )
        return templates.TemplateResponse(request, "create.html", context, status_code=400)

    command = build_provision_command(**choices)

    try:
        await runner.start(session_id=session_id, action="provision", target=None, command=command)
    except RunAlreadyActive as exc:
        context = _create_form_context(
            status, session_id, error=f"A {exc.active.action} run is already active. Wait for it to finish."
        )
        return templates.TemplateResponse(request, "create.html", context, status_code=409)
    except SecretsUnavailable as exc:
        context = _create_form_context(status, session_id, error=f"Missing: {', '.join(exc.missing)}.")
        return templates.TemplateResponse(request, "create.html", context, status_code=400)

    return RedirectResponse("/run", status_code=303)


@app.get("/run", response_class=HTMLResponse)
async def run_view(request: Request):
    status = session_status_context(request.state.session_id)
    run = runner.active
    if run is None:
        context = {**status, "command": None, "output": [], "outcome": None, "exit_code": None}
    else:
        context = {
            **status,
            "command": run.command,
            "output": list(run.output),
            "outcome": run.outcome,
            "exit_code": run.exit_code,
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
        yield (
            "event: terminal\n"
            f"data: {json.dumps({'outcome': run.outcome, 'exit_code': run.exit_code})}\n\n"
        )

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
            request, error=f"A {exc.active.action} run is already active. Wait for it to finish."
        )
        return templates.TemplateResponse(request, "dashboard.html", context, status_code=409)
    except SecretsUnavailable as exc:
        context = _dashboard_context(request, error=f"Missing: {', '.join(exc.missing)}.")
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
            request, error=f"A {exc.active.action} run is already active. Wait for it to finish."
        )
        return templates.TemplateResponse(request, "dashboard.html", context, status_code=409)
    except SecretsUnavailable as exc:
        context = _dashboard_context(request, error=f"Missing: {', '.join(exc.missing)}.")
        return templates.TemplateResponse(request, "dashboard.html", context, status_code=400)

    return RedirectResponse("/run", status_code=303)


@app.get("/pool", response_class=HTMLResponse)
async def pool_page(request: Request):
    status = session_status_context(request.state.session_id)
    pool_status = pool.status()
    return templates.TemplateResponse(
        request,
        "pool.html",
        {**status, "entries": pool_status.entries, "used": pool_status.used, "free": pool_status.free},
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

    return RedirectResponse("/pool", status_code=303)


@app.get("/presets", response_class=HTMLResponse)
async def presets_page(request: Request):
    status = session_status_context(request.state.session_id)
    return templates.TemplateResponse(
        request, "presets.html", {**status, "presets": presets.list_presets()}
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
        return HTMLResponse(
            '<p class="banner banner-error">A preset name is required.</p>', status_code=400
        )

    # Same validation as provisioning itself: a preset that silently
    # recorded a default the user never picked would hand that wrong choice
    # back on every future load of it (Principle XII).
    session_id = request.state.session_id
    status = session_status_context(session_id)
    server_types, notice = _server_types_for_submission(session_id, status, form)
    try:
        choices = _create_choices_from_form(form, server_types)
    except CreateChoicesInvalid as exc:
        message = html.escape(_with_catalogue_notice(exc.message, notice))
        return HTMLResponse(f'<p class="banner banner-error">{message}</p>', status_code=400)

    preset = presets.Preset(name=name, **choices)

    try:
        presets.save(preset)
    except presets.PresetNameTaken:
        return HTMLResponse(
            f'<p class="banner banner-error">A preset named &#39;{name}&#39; already exists. '
            "Rename or delete it first.</p>",
            status_code=409,
        )

    return HTMLResponse(f'<p class="banner banner-notice">Saved preset &#39;{name}&#39;.</p>')


@app.post("/presets/{name}/delete")
async def presets_delete(name: str):
    try:
        presets.delete(name)
    except presets.PresetNotFound:
        pass
    return RedirectResponse("/presets", status_code=303)


@app.post("/presets/{name}/rename")
async def presets_rename(name: str, request: Request, new_name: str = Form(default="")):
    status = session_status_context(request.state.session_id)
    try:
        presets.rename(name, new_name)
    except presets.PresetNameTaken:
        context = {
            **status,
            "presets": presets.list_presets(),
            "error": f"A preset named '{new_name}' already exists.",
        }
        return templates.TemplateResponse(request, "presets.html", context, status_code=409)
    except presets.PresetNotFound:
        context = {
            **status,
            "presets": presets.list_presets(),
            "error": f"No preset named '{name}'.",
        }
        return templates.TemplateResponse(request, "presets.html", context, status_code=404)

    return RedirectResponse("/presets", status_code=303)


@app.post("/defaults/refresh")
async def defaults_refresh(request: Request):
    """FR-047/FR-048: contracts/http-routes.md "Defaults refresh". Copies
    only pristine-tracked files the user has not edited; never touches the
    encrypted configuration, machine records, name pool or presets.
    """
    seed.refresh_defaults()
    return RedirectResponse("/", status_code=303)
