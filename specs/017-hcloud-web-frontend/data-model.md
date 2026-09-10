# Phase 1 Data Model: Hetzner Web Frontend

**Feature**: `017-hcloud-web-frontend` | **Date**: 2026-08-24

The seven entities from [spec.md](./spec.md), with fields, storage location, validation
rules and state transitions. Storage falls into three tiers:

| Tier | Location | Survives restart | Survives image upgrade |
|---|---|---|---|
| Memory | process only | no | no |
| Volume | named volume at `/ansible/inventories` | yes | yes |
| Image | baked into the image | yes | replaced by the new image |

## Session Secrets

Storage: **memory**. Never written anywhere.

| Field | Type | Notes |
|---|---|---|
| `session_id` | opaque string | Carried in a signed, `HttpOnly`, `SameSite=Strict` cookie |
| `hcloud_token` | string, optional | Hetzner API token |
| `vault_password` | string, optional | Ansible Vault password |
| `last_seen` | timestamp | Refreshed on every request |

Validation:

- Neither value is checked for format on entry. A token is proven by the first Hetzner call
  that uses it; a vault password is proven by the first successful decryption. Guessing at
  format would reject valid values.
- A failed decryption reports "password does not match" and leaves the stored configuration
  untouched, distinguishable from "no configuration exists yet".

State transitions:

```text
absent --enter--> held --inactivity timeout--> absent
held --process stop--> absent
held --user locks--> absent
```

The cookie signing key is generated at process start and never persisted, so every session
is invalidated by a restart. This is by design and the interface presents re-entry as
normal rather than as an error.

Rules:

- Neither value is ever rendered back into a page after entry. The interface shows only a
  per-secret locked or unlocked indicator.
- Neither value appears in any log line, run output line, or constructed argument list.
- Both are passed to a child process exclusively through its environment.

## Encrypted Configuration

Storage: **volume**, at `group_vars/all/vault.yml`, Ansible-Vault-encrypted.

Field structure is not defined by this feature. It is read from
`inventories/group_vars/all/vault-template.yml`, which stays the single definition of what
configuration exists. As of this writing the template models:

| Key | Shape | Rendering |
|---|---|---|
| `vault_my_ansible_user_name` | string | editable |
| `vault_my_ansible_user_password` | string, secret | editable, masked, reveal toggle |
| `vault_my_ssh_key_name` | string | read-only, managed by the keypair feature |
| `vault_my_ssh_public_key` | string | read-only, managed by the keypair feature |
| `vault_gnome_keyring_password` | string, secret | editable, masked |
| `vault_windows_admin_password` | string, secret | editable, marked as unused in this scope |
| `vault_desktop_users` | list of objects with `name`, `password`, `exa_api_key` | add and remove rows, at least one required |

Validation:

- `vault_desktop_users` must contain at least one entry, matching the template's own stated
  requirement.
- Managed keys are not accepted from form input at all; a submitted value for them is
  discarded rather than merged.

Preservation rule, load-bearing:

> On save, form values are merged **over** the parsed existing document. Any key present in
> the stored configuration but absent from the template is written back unchanged.

A silent drop here destroys user configuration with no error, so the pytest suite covers
this case directly.

State transitions:

```text
absent --password entered twice, entries match--> encrypted (empty document)
absent --entries differ--> absent, error reported
encrypted --save--> encrypted (re-encrypted with the same session password)
encrypted --wrong password--> unchanged, error reported
encrypted --discard, confirmation typed--> absent, vault password forgotten in every session
```

The password that makes the file is the only one nothing can check: every later password is
checked against this file, so a typo at creation becomes the real password and no run works
afterwards — Ansible loads `group_vars/all/vault.yml` for every host and fails at decryption
before the play starts, so even destroying a machine is refused. Hence both edges above that
the other transitions do not need: the repeat entry that guards creation, and the discard
that is the only way back when the guard was not there.

## SSH Keypair

Storage: **volume**, private key at `.webui/ssh/id_ed25519` with mode `0600`, public key
alongside it. The public half is additionally recorded in the encrypted configuration and
registered with the user's Hetzner account.

| Field | Type | Notes |
|---|---|---|
| private key | file | Never rendered, never downloadable |
| public key | file and configuration value | Displayed in full |
| fingerprint | derived | Displayed for comparison against the Hetzner console |
| registered name | configuration value | `vault_my_ssh_key_name` |

Validation:

- Generation and registration both require the Hetzner token and the vault password to be
  unlocked, since the action touches the cloud account and the encrypted configuration.
- If a key already exists in the Hetzner account under the configured name, the interface
  reports what it found and requires confirmation before replacing it, rather than
  clobbering a key that may belong to something else.

State transitions:

```text
absent --generate--> generated --register--> registered
registered --rotate--> generated (new pair) --register--> registered
```

Rotation warning, shown before the action and requiring confirmation: machines already
provisioned trust the previous public key. Rotating makes them unreachable to the
automation. Rotate only when no machines are live, or re-key existing hosts afterwards.

## Machine Record

Storage: **volume**, at `hcloud_autogenerated.yml`. Written by the playbooks, read by the
interface. The interface never writes this file.

| Field | Source | Notes |
|---|---|---|
| name | inventory key | Also the pool entry that is claimed |
| address | `ansible_host` | Displayed in the list |
| profile | group membership | `basic` or `desktop` |
| size, location | run parameters | Displayed as recorded at creation |
| status, created, size, location, image | Hetzner account | Overlaid while the API token is unlocked; the image exists nowhere else |

Validation:

- The file is authoritative for what the interface believes exists. It may drift from
  reality if a machine is deleted directly in the Hetzner console; the interface reports
  what it knows rather than silently reconciling.

State transitions:

```text
absent --provision succeeds--> recorded
recorded --destroy succeeds--> absent (name returns to the pool)
```

A failed provisioning run leaves no record, because the existing automation deletes the
partially-created machine and fails.

## Machine Name Pool

Storage: **volume**, at `.webui/hostname_pool_hcloud.yml`, seeded from the repository
default at `playbooks/vars/hostname_pool_hcloud.yml`.

| Field | Type | Notes |
|---|---|---|
| entries | ordered list of strings | Order determines which name is claimed next |

Validation, enforced at save time rather than at use time:

- lowercase letters, digits and hyphens only
- no leading or trailing hyphen
- at most 63 characters
- unique within the pool
- an entry currently present in the machine records may not be removed or renamed

The save-time rule matters because the automation claims a name **before** calling the
Hetzner API. An invalid entry validated only at use time would fail partway through a run
instead of at the moment it was typed.

Defaults: `edoras`, `shire`, `osgiliath`, `bree`, `isengard`, `rohan`, `angmar`,
`minas-tirith`, `helms-deep`, `mordor`.

Derived, displayed values: count used, count free. Exhaustion is a refusal before any
provider call, not a mid-run failure.

## Preset

Storage: **volume**, at `.webui/presets.json`.

| Field | Type | Notes |
|---|---|---|
| `name` | string | Unique, user-supplied |
| `profile` | enum | `basic` or `desktop` |
| `server_type` | string | For example `cx33` |
| `location` | string | For example `fsn1` |
| `image` | string | For example `ubuntu-24.04` |

Validation:

- names are unique; saving over an existing name requires confirmation
- a loaded preset fills the create form but every field remains editable before the run
  starts

A preset has two sources. The create form is one: whatever is typed into it. A machine that
already exists is the other, through the *Save as preset* action on its row — the profile
comes from the machine record, and the size, location and image from the Hetzner account.
The image can come from nowhere else: no local file records what a machine was built from.
A machine the account cannot fully describe is refused rather than saved with a gap, since
a preset with a missing field silently provisions something else.

Presets hold no secrets. They are ordinary configuration and are safe at rest.

## Run

Storage: **memory**. Output is never written to disk, because Ansible can echo decrypted
configuration values into it.

| Field | Type | Notes |
|---|---|---|
| `action` | enum | `provision`, `configure`, `destroy` |
| `target` | string, optional | Machine name, for configure and destroy |
| `command` | list of strings | Shown to the user before the run starts |
| `output` | bounded ring buffer | Streamed live, retained only for this session |
| `outcome` | enum | `running`, `succeeded`, `failed`, `cancelled` |
| `exit_code` | integer, optional | Set on completion |

Validation:

- at most one run may be in the `running` state at any time; a second request is refused
  with an explanation rather than queued
- the constructed `command` must contain no secret value; secrets travel only in the child
  process environment

State transitions:

```text
running --exit 0--> succeeded
running --exit non-zero--> failed
running --user cancels--> cancelled
running --process stop--> lost (no stale running state is shown on next start)
```

A failure surfaces the tail of the underlying error output. It never reports a generic
message, per Principle XII.

## Relationships

```text
Session Secrets ──unlock──> Encrypted Configuration
                └─unlock──> SSH Keypair ──registered with──> Hetzner account
                                    │
Machine Name Pool ──claims name──> Machine Record <──targets── Run
        │                                              ▲
        └── blocked from removing a name in use ───────┘

Preset ──fills──> create form ──starts──> Run ──writes──> Machine Record
```

The one cycle worth naming: the name pool constrains what machines can exist, and existing
machines constrain what the pool may be edited to. That mutual constraint is why pool
validation reads the machine records.
