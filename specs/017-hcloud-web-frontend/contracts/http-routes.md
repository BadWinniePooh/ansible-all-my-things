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
| `POST` | `/session/unlock` | — | Accept the Hetzner token, the vault password, or both. Values are stored in memory only |
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

`POST /vault` never replaces the document wholesale. Keys present in the stored
configuration but absent from the template are written back unchanged (see
[data-model.md](../data-model.md)).

Submitted values for the managed SSH keys are discarded rather than merged.

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
| `POST` | `/create/preview` | — | Fragment: the exact command that would run, without running it |
| `POST` | `/create` | token + vault password | Start a provisioning run |
| `POST` | `/machines/{name}/configure` | token + vault password | Start a configuration run against one machine |
| `POST` | `/machines/{name}/destroy` | token + vault password | Start a destruction run; requires the typed machine name as confirmation |

`POST /create` and both machine routes return the run view. Each refuses with an
explanation when a run is already active (FR-035).

`POST /machines/{name}/destroy` refuses when the submitted confirmation does not match the
machine name exactly.

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

Saving over an existing name requires confirmation.

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
