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

## The first vault password, and losing it

On a fresh installation there is no encrypted configuration yet, so the first
vault password entered on the dashboard becomes *the* vault password: it
creates `group_vars/all/vault.yml`, and every password entered afterwards is
checked against that file. That first one is the only password nothing can
check, which is why the unlock form asks for it twice while no configuration
exists. Enter it identically both times or nothing is created (spec.md FR-051).

If a vault password is lost, the installation is stuck rather than merely
inconvenienced: Ansible loads `group_vars/all/vault.yml` for every host, so a
file nobody can decrypt fails every run before the first task — provisioning,
configuring, **and destroying**, which means a running machine keeps costing
money with no way to remove it from the interface.

The way out is on the Vault screen, at the bottom: **Discard configuration**.
Type `discard` to confirm. It deletes the encrypted configuration and forgets
the vault password in every session, so setup starts over with a new password
(spec.md FR-052). What it costs:

- Every value the configuration held is gone for good — the Ansible user
  password, the GNOME keyring password, and each desktop user's password and
  Exa API key. There is no backup copy, deliberately: a file nobody can open,
  kept "just in case", is a permanent copy of those same passwords.
- The SSH keypair in `inventories/.webui/ssh/` is **not** deleted, so machines
  already provisioned stay reachable. Register the key again from the SSH key
  screen once a new configuration is saved, so its name and public half are
  recorded in it.
- A machine that outlives the configuration can then be destroyed normally
  through the interface, once the new configuration and key registration are in
  place.

Discarding is refused while a run is active — that run is reading the file.

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

## What the dashboard knows about a machine

Two sources, each authoritative for different things. The local records —
`hcloud_autogenerated.yml` plus the interface's own side file — decide **which**
machines this installation manages and which profile each was given; Hetzner
has never heard of a profile. The account decides **what** each machine is:
size, location, address and power state are read from `GET /servers` with the
unlocked API token and overlaid on the local rows.

That overlay is why a machine created from the command line, restored from a
backup, or provisioned before the side file existed shows its real size and
location rather than "unknown". A server in the account that this installation
does not manage is not listed — it is not this installation's to configure or
destroy. While the API token is locked the account is not read at all, and a
lookup that fails says so and leaves the local records on screen.

## What the interface says when something happens

Every sentence the interface says about an action lives in
[webui/messages.py](../../../../webui/messages.py), in one style: one sentence,
past tense, naming what happened to what — *Name pool saved.*, *Preset
dev-desktop saved.*, *Key rotated. Machines provisioned with the previous key
can no longer be reached by the automation.*

A successful action redirects to the page it belongs on and that page shows the
confirmation once. Failures use the same banner in the same voice, so "it
worked" and "it did not" are told the same way. Run outcomes are the same idea
one level down: [webui/runner.py](../../../../webui/runner.py) `describe()`
owns those, because they are about a machine rather than about a form.

## Presets

A preset is a saved set of create-form choices. They live in a column beside
the create form — the rail — which is not rendered at all until at least one
preset exists.

**The rail says where the form came from and what you have changed since.**
The preset the form was loaded from is marked and offers *Reset*, which loads
it again and throws the edits away. Under the list, the difference from that
preset is spelled out field by field — *Size cx33 → cx43* — and updates as you
edit, without a page load.

**Two ways to keep a change.** *Update &lt;preset&gt;* writes the current choices
over the preset the form came from; *Save as new* stores them under a new name.
Both validate exactly as provisioning does, so a preset can never record a
default nobody picked.

**Choices that are already saved cannot be saved again.** When the form matches
a preset exactly, saving is refused and the box says which preset holds them —
otherwise it would either fail on the taken name or leave two presets saying
the same thing. Matching is on the choices, not on which preset was clicked, so
it catches choices arrived at by hand too.

Renaming and deleting stay on the presets screen, one click away through
*Manage presets*.

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

## What it costs

Hetzner bills a server by the hour and stops charging once the hours reach the
server type's monthly price, and `GET /servers` reports both rates for the
location the machine actually runs in. So the cost of a running machine is
arithmetic, not a guess: the machines table shows what each one has cost since
the first of the month, beside when it was created, and refreshes itself every
minute.

**The dashboard card** carries two figures — spent so far this month, and a
projection for month end that assumes nothing is created or destroyed — over a
six-month sparkline. *All months* leads to the costs screen, which splits the
current month per machine (including the ones already destroyed, shaded) and
lists every month the ledger knows about.

**What the running machines cost per hour rides in the sidebar badge**, beside
the machine count and above the two lock states — *2 machines · €0.023/h*. It
belongs in the corner of the eye rather than in a section of its own, and it is
as true on the vault screen as on the dashboard. No machines running means no
rate: €0.00/h would read as "these are free".

**Machines that no longer exist still count.** The account forgets a server the
moment it is destroyed, but the invoice does not, so the interface keeps its own
ledger: one row per life of a machine, opened the first time it is seen and
closed when the account stops reporting it. Closing on absence rather than in
the destroy route means a machine destroyed from the command line or the Hetzner
console is counted too. A pool name handed out again next month is a second row,
never a longer first one.

**Every figure is an estimate of the server line and says so.** Not counted,
because nothing here can see them: the primary IPv4 address, traffic over the
included volume, snapshots, volumes and backups. The invoice will be a little
higher, never lower.

The ledger is a SQLite file in the volume
(`inventories/.webui/costs.sqlite3`), so it survives restarts and image
upgrades along with everything else you own, and is backed up by copying the
same volume. The costs screen reads only that file, so it answers with the
session locked — what a machine cost is not a secret.

## Runs

The run screen names the machine the run is about, in the same badge the
sidebar uses for a secret's lock state — a coloured box with a coloured
indicator and the state in words. While the run is going the indicator spins in
place: *Provisioning osgiliath…*, *Configuring edoras…*. A provision run knows
the name because `create-vm.yml` claims the first free name in the pool, which
is the same rule the interface applies to decide what to display.

When a run ends the badge takes the colour of how it ended — green for
*Provisioned osgiliath*, red for *Provisioning osgiliath failed (exit 2)*,
neutral for a cancelled one — with a line underneath on what the automation did
about it. Cancelling is reported as cancelling, not as a failure.

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
