# Contract: Interface Routes

**Feature**: `017-hcloud-web-frontend` | **Date**: 2026-08-24

Routes the application exposes. All are server-rendered HTML except the run stream, which
is a Server-Sent Events endpoint, and the fragment responses that HTMX swaps into the page.

The **Requires** column names which session secrets must be unlocked. A route whose
requirement is unmet returns the page with an explanation and a prompt to unlock, rather
than attempting the action and failing (FR-014).

## Dashboard and session

| Method | Path | Requires | Purpose |
|---|---|---|---|
| `GET` | `/` | — | Dashboard: machine list, free-name count, secret indicators, defaults-refresh banner when versions differ |
| `POST` | `/session/unlock` | — | Accept the Hetzner token, the vault password, or both. Values are stored in memory only. While no encrypted configuration exists, the vault password is also required a second time in `vault_password_confirm` and creates the configuration when both entries match |
| `POST` | `/session/lock` | — | Discard both secrets immediately |
| `GET` | `/session/status` | — | Fragment: the per-secret locked or unlocked indicators |

The dashboard renders whether or not anything is unlocked. Actions that need a secret are
visibly disabled with the reason, so the interface never presents a control that will fail.

## Configuration

| Method | Path | Requires | Purpose |
|---|---|---|---|
| `GET` | `/vault` | vault password | Form generated from the configuration template, populated from the decrypted document |
| `POST` | `/vault` | vault password | Merge submitted values over the existing document, re-encrypt, write |
| `POST` | `/vault/users` | vault password | Fragment: add or remove a desktop-user row before saving |
| `POST` | `/vault/discard` | — | Delete the encrypted configuration, on the typed confirmation `discard`, and drop the vault password from every session |

`POST /vault` never replaces the document wholesale. Keys present in the stored
configuration but absent from the template are written back unchanged (see
[data-model.md](../data-model.md)).

Submitted values for the managed SSH keys are discarded rather than merged.

`POST /vault/discard` deliberately requires no unlocked session: the operator who needs it
is the one whose password no longer opens the configuration, and until that file is gone no
playbook runs at all — Ansible loads `group_vars/all/vault.yml` for every host and fails at
decryption before the play starts, so even destroying a machine is refused. It answers `400`
when there is nothing to discard or the confirmation does not match, and `409` while a run is
active, because that run is reading the file.

## SSH key

| Method | Path | Requires | Purpose |
|---|---|---|---|
| `GET` | `/sshkey` | — | Fingerprint, public key and registered name; generation controls |
| `POST` | `/sshkey/generate` | token + vault password | Generate a keypair, register the public half, record it in the configuration |
| `POST` | `/sshkey/rotate` | token + vault password | Same, after an explicit confirmation of the rotation warning |

`POST /sshkey/rotate` refuses without the confirmation field. When a key already exists in
the Hetzner account under the configured name, the response reports what was found and
requires a second confirmation before replacing it.

## Provisioning lifecycle

| Method | Path | Requires | Purpose |
|---|---|---|---|
| `GET` | `/create` | — | Create form: profile, server size, location, image, plus preset controls |
| `GET` | `/create/server-types` | — (token for live sizes) | Fragment: the server-size table, plus the location fieldset out of band |
| `GET` | `/create/locations` | — (token for live sizes) | Fragment: the location fieldset scoped to one server size |
| `GET` | `/create/images` | — (token for the live catalogue) | Fragment: the operating-system image options |
| `GET` | `/create/validate-image` | — (token to actually check) | Fragment: whether one free-text image name exists in the account |
| `POST` | `/create/preview` | — | Fragment: the exact command that would run, without running it |
| `POST` | `/create` | token + vault password | Start a provisioning run |
| `POST` | `/machines/{name}/configure` | token + vault password | Start a configuration run against one machine |
| `POST` | `/machines/{name}/destroy` | token + vault password | Start a destruction run; requires the typed machine name as confirmation |
| `POST` | `/machines/{name}/preset` | token | Save this machine's profile, size, location and image as a preset under the submitted name |

`POST /create` and both machine routes return the run view. Each refuses with an
explanation when a run is already active (FR-035).

`POST /machines/{name}/preset` reads the size, location and image from the Hetzner account,
which is why it needs the token and why it answers `400` when the account does not report
all of them — naming the ones it did not. A name already taken answers `409` rather than
overwriting, matching `POST /presets`.

`POST /machines/{name}/destroy` refuses when the submitted confirmation does not match the
machine name exactly.

### Create-form fragments

The create form renders the built-in size, location and image lists on every
fresh load. The three `GET` fragment routes above back its interactive parts;
none of them mutates anything, which is why they are `GET` under the
no-`GET`-mutates rule below.

| Route | Query parameters | Returns |
|---|---|---|
| `/create/server-types` | `server_types_live`, `server_type`, `location` | `#server-types-table`, followed by `#location-fieldset` carrying `hx-swap-oob="true"` |
| `/create/locations` | `server_type`, `location`, `server_types_live` | `#location-fieldset` |
| `/create/images` | `images_live`, `image` | `#image-options` |
| `/create/validate-image` | `image_custom` | `#image-validation` |

`server_types_live` and `images_live` carry the state of the two "show
everything from Hetzner" toggles on the form. When either is set, the account's
own catalogue is queried; otherwise the built-in lists are returned without any
API call.

A live lookup never fails the request. When the token is locked, when the API
call errors, or when it returns nothing usable, the built-in list is returned
with a notice stating which of the three happened. Live sizes are restricted to
non-deprecated x86 types, since the image list this interface offers is x86-only.

A server size is orderable only in the locations its own price entries name, so
the two fieldsets are not independent: selecting a size refreshes the location
list, and `/create/server-types` refreshes it out of band because toggling the
source can change which size is selected. The size table shows the cheapest
monthly price across a size's locations, labelled as a "from" figure.

`server_types_live` is also submitted with the form, so `POST /create`,
`POST /create/preview` and `POST /presets` validate against the same catalogue
the user was looking at and a live-only size is not mistaken for an unknown one.

### Create-form validation

`POST /create`, `POST /create/preview` and `POST /presets` share one validation
step. A submission is refused, naming the field at fault, when the profile,
server size or location is missing or unknown, when no image is given, or when
the chosen size cannot be ordered in the chosen location. No hardcoded default
is ever substituted for a missing choice (Principle XII). `POST /create`
re-renders the form with the submitted choices intact; `POST /create/preview`
returns the message in place of a command.

### Image existence

Whichever image the submission resolves to — free text or an entry from the
list — is looked up in the account before a run starts. `POST /create` refuses
when the image is not found, and equally when the lookup itself fails: an
unverifiable image is not a verified one, and the same failure would almost
certainly break the run seconds later.

The lookup accepts both forms the playbook's `image:` parameter takes: a name
(`ubuntu-24.04`), resolved through `GET /images` filtered to available x86
images; and a numeric id, resolved through `GET /images/{id}`, which is the
only way to reach a snapshot, since snapshots carry a description rather than
a name. An image found by id but not on x86 is treated as not found.

A deprecated image is a warning, not a refusal — it still boots until Hetzner
withdraws it.

`GET /create/validate-image` reports the same verdict live as the free-text
field is typed into. It is advisory: with scripting off, or from a preset
carrying an image that has since been withdrawn, only the gate on `POST
/create` is reached. The built-in list is checked on the same terms, since it
goes stale as Hetzner adds and withdraws releases.

## Runs

| Method | Path | Requires | Purpose |
|---|---|---|---|
| `GET` | `/run` | — | Run view: the command, live output, outcome |
| `GET` | `/run/stream` | — | Server-Sent Events: one event per output line, then a terminal outcome event |
| `POST` | `/run/cancel` | — | Terminate the active run |

The stream emits a terminal event carrying the outcome and exit code, so a client never has
to infer completion from silence. Reconnecting replays the ring buffer before resuming live
output.

No run output is written to disk (FR-038).

## Name pool

| Method | Path | Requires | Purpose |
|---|---|---|---|
| `GET` | `/pool` | — | Editor with used and free counts |
| `POST` | `/pool` | — | Validate and save the full list |

`POST /pool` validates every entry against the naming rules and rejects the whole
submission on any failure, naming the offending entry. It refuses to remove or rename an
entry that appears in the machine records.

## Presets

| Method | Path | Requires | Purpose |
|---|---|---|---|
| `GET` | `/presets` | — | List with rename and delete controls |
| `POST` | `/presets` | — | Save the current create-form choices under a name |
| `POST` | `/presets/{name}/delete` | — | Delete one preset |
| `POST` | `/presets/{name}/rename` | — | Rename one preset |

Saving under a name that is already taken is refused rather than silently
overwritten; the form offers no confirm-and-overwrite step, so the existing
preset must be renamed or deleted first. Renaming onto a taken name is refused
for the same reason.

`POST /presets` runs the same validation as `POST /create`, so a preset cannot
record a choice the user did not make.

## Defaults refresh

| Method | Path | Requires | Purpose |
|---|---|---|---|
| `POST` | `/defaults/refresh` | — | Copy newer default files from the pristine image copy into the volume |

Refresh copies only default files the user has not edited. It never touches the encrypted
configuration, the machine records, the name pool or the presets (FR-048).

## Cross-cutting rules

- No route accepts or returns a secret value in a URL, a query string, or a rendered page
  after entry.
- Every state-changing route is a `POST`; no `GET` mutates anything.
- Every refusal states the cause: which secret is missing, which entry is invalid, which
  machine is using a name, or that a run is already active. No generic error is returned
  (Principle XII).
- The server binds to the loopback interface by default (FR-049).
