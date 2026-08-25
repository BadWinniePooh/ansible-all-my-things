# Using the web frontend

## Start it

Fetch [compose.yaml](../../../../compose.yaml) and start it — no checkout of
this repository and no Ansible installation required:

```shell
docker compose up -d
```

Then open <http://127.0.0.1:8080>.

To build from a checkout instead, use
[compose.build.yaml](../../../../compose.build.yaml):

```shell
docker compose -f compose.build.yaml up -d --build
```

## Exposure risk

> **The interface has no login of its own and controls a cloud account.**
> Both compose files publish the port to `127.0.0.1` only, and this is
> deliberate (spec.md FR-049, FR-050). Anyone who can reach the interface can
> unlock it with the two secrets they type in, watch run output (which can
> contain decrypted configuration values), and provision or destroy machines
> against the configured Hetzner account.
>
> Do not change the port binding from `127.0.0.1` to `0.0.0.0`, and do not put
> the container behind a reverse proxy reachable from outside the operator's
> own machine, unless a separate authenticating proxy sits in front of it. The
> project adds no authentication layer of its own — the two session secrets
> are a credential-unlock step, not a login system, and a fresh visitor with
> network access to the port has the same access as the operator until a
> secret has been entered.

## Session secrets

The Hetzner API token and the Ansible Vault password are entered on the
dashboard, held in the server process's memory only, and never written to
disk, logged, or echoed into run output (spec.md FR-011, SC-005). Both are
discarded when the container stops and after 30 minutes of inactivity;
re-entering them is normal, not an error.

## Persistent state

A single named volume is mounted at `/ansible/inventories`. It holds the
Ansible-Vault-encrypted configuration, the record of provisioned machines, the
project-scoped SSH known-hosts file, and a hidden `inventories/.webui/`
directory holding the name pool, presets and the SSH keypair. All of this
survives a restart and an image upgrade (spec.md FR-046).

If a newer image ships changed defaults than the volume was seeded with, the
dashboard shows a banner offering to refresh them. Refreshing only replaces
default files the user has not hand-edited, and never touches the encrypted
configuration, machine records, name pool or presets (spec.md FR-047,
FR-048).

## What the interface does not support

Matching the feature's scope
([spec.md § Assumptions](../../../../specs/017-hcloud-web-frontend/spec.md#assumptions)):

- The `tart` and `docker` providers — both need a host-level tool or the
  host's own container daemon, neither of which exists inside this container.
- The AWS provider and the Windows profile — a second credential model, out
  of scope.
- Backup and restore.
- More than one run at a time.
- Dry-run mode.
- Run history beyond the current browser session, since run output is never
  persisted.

## Command-line users are unaffected

The web frontend and the command line share the same playbooks and the same
hostname pool mechanism: `playbooks/tasks/create/hcloud.yml` prefers
`inventories/.webui/hostname_pool_hcloud.yml` when the web frontend has
created one, and falls back to the repository default at
`playbooks/vars/hostname_pool_hcloud.yml` otherwise. A user who never touches
the web interface sees no change in behaviour.
