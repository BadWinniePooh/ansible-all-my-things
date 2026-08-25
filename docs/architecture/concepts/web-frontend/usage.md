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

**Unlocking is the first step, not an optional one.** While the API token is
locked, the dashboard is the unlock form and nothing else: the machine list,
provisioning and destroy all need that token, so showing them would only offer
actions that are refused. Once it is unlocked the dashboard shows the machines
and the name pool. The sidebar marks the same rule everywhere — Create VM, Run,
Vault and SSH key carry a padlock until the secrets they need are present,
while Name pool and Presets stay open because neither reads a secret.

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

## The create screen

Four choices make a machine: a profile (`basic` or `desktop`), a server size,
a location, and an operating system image. The screen opens on the built-in
lists — three sizes, six locations, the current Ubuntu LTS releases — which
need no Hetzner account to display and are enough for the common case.

**Size and location are coupled.** A Hetzner server size can only be ordered
in the locations it is actually priced in, so selecting a size refreshes the
location list to what that size offers. Picking the location first and the
size second can therefore change the location under you; the form always shows
a valid pair.

**Two toggles widen the choice.** *Show all sizes available from Hetzner* and
*Show full image catalogue from Hetzner* query the account itself. Both need
the API token unlocked on the dashboard; without it, the built-in lists stay
on screen with a note saying why. The same note appears, naming the cause, if
the lookup fails or comes back empty — a live lookup never blocks the screen.

Live sizes are limited to current x86 types. Deprecated sizes are dropped, and
so are the arm64 (`cax`) ones, which cannot boot the x86 images this interface
offers.

**The price column is a "from" figure.** A size can cost different amounts in
different locations, so the table shows the cheapest one across the locations
that size is available in — not the price in the location currently selected.

**The size table sorts.** Click any column header to sort by it; click again
to reverse. Sorting is display-only and never changes the selection.

**Nothing is guessed for you.** A submission missing a profile, size, location
or image is refused with a message naming the field, as is a size paired with
a location it cannot be ordered in. The interface never silently substitutes a
default for a choice that was not made — including when saving a preset, which
would otherwise hand the wrong choice back on every later load of it.

**The image list and the *Other* field are exclusive.** *Other* takes a
free-text image name for anything not listed, such as `debian-12`. Typing into
it clears the selection in the list above and dims it, because free text is
what actually gets used — leaving an entry looking selected would misreport
what is about to be provisioned. Clicking an entry in the list is how you
switch back; that empties the free-text field. Emptying it by hand restores
whatever was selected before.

**The image name is checked against your account, not guessed at.** As you
type into *Other*, the field reports whether that image actually exists —
green when it does, red with the reason when it does not. A deprecated image
is flagged but allowed; it still boots until Hetzner withdraws it. Both forms
work: an image name such as `debian-12`, and a numeric image id, which is how
to boot one of your own snapshots.

The check needs the API token unlocked; until then the field says so instead
of guessing. It also runs at submission time on whichever image is selected —
including one picked from the built-in list, which goes stale as Hetzner adds
and withdraws releases — so provisioning is refused up front rather than
failing minutes later with a name already taken from the pool.

*Preview command* renders the exact `ansible-playbook` invocation the current
choices produce, without running anything.

## Presets

A preset is a saved set of create-form choices. The selector at the top of the
create screen loads one; it is not rendered at all until at least one preset
exists, since it would otherwise offer only *Custom*, which is what the form
already is.

**The selector names what the form currently is.** Editing any choice moves it
to *Custom*, because the form no longer describes the preset that was loaded.
Arriving at a saved preset's choices by hand moves it back to that preset —
matching is on the choices, not on which preset was clicked.

**Choices that are already saved cannot be saved again.** When the form
matches a preset, the save box says which one holds them and refuses input:
saving would either fail on the taken name or leave two presets saying the
same thing.

**A preset records which catalogue it was made from.** A size or an image that
only exists in the live Hetzner catalogue is not in the built-in lists, so a
preset saved with *Show all sizes* or *Show full image catalogue* on records
that, and loading it turns the same toggles back on and re-runs the lookups.
Without this the form would match those choices against the built-in lists,
find them unknown, and quietly provision something else.

**A partial restore says which part failed.** The image can restore while the
size cannot — the account no longer offers it, the live lookup fell back to
the built-in lists, or the location is not orderable for the size. The page
names each part that could not be honoured and what was selected instead,
rather than reporting a clean load of a form that is now half something else.

## Runs

The run screen names the machine the run is about and keeps a spinner beside
it while it is going: *Provisioning osgiliath…*, *Configuring edoras…*. A
provision run knows the name because `create-vm.yml` claims the first free
name in the pool, which is the same rule the interface applies to decide what
to display.

When a run ends, the screen says what happened to that machine —
*Provisioned osgiliath*, or *Provisioning osgiliath failed (exit 2)* with a
line on what the automation did about it. A cancelled run is reported as
cancelled, not as a failure.

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
