# Feature Specification: Hetzner Web Frontend for VM Provisioning

**Feature Branch**: `017-hcloud-web-frontend`

**Created**: 2026-08-24

**Status**: Draft

**Input**: User description: "Hetzner web frontend for VM provisioning. A self-contained
container image serving a clean, easy-to-use web UI that provisions, configures and
destroys Hetzner Cloud VMs by driving this repository's existing playbooks. Anyone can
use it against their own Hetzner account; nothing in the image is tied to a particular
account. Scope: provider hcloud only, full lifecycle create → configure → destroy,
profiles basic and desktop. All non-secret variables are selectable in the UI and applied
per run; the repository's own default files are never rewritten. The vault is editable
from the UI, preserving values the form does not model. The Hetzner API token and the
vault password are entered in the UI, kept in memory only, and never persisted. An SSH
keypair is generated in the UI and registered with Hetzner, with rotation available at any
time. The hostname pool is user-configurable. The image is fully self-contained and
minimal in size. A pipeline publishes a pre-built multi-architecture image. Compose files
are provided for both local build and pre-built deployment."

**Tracking**: beads epic `ansible-all-my-things-bgvv`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Get the tool running (Priority: P1)

Someone who has never used this repository wants to try it. They install a container
runtime, fetch one compose file, start it, and open a browser. They do not clone the
repository, install Ansible, or learn any command-line flags.

**Why this priority**: Nothing else in this feature is reachable until the tool starts.
This is also the requirement that makes the project usable by people other than its
author.

**Independent Test**: On a machine with only a container runtime and no checkout of this
repository, fetch the pre-built-image compose file, start it, and confirm the interface
loads in a browser.

**Acceptance Scenarios**:

1. **Given** a machine with a container runtime and no copy of this repository, **When**
   the operator starts the published image using the provided compose file, **Then** the
   interface is reachable in a browser and no further installation is required.
2. **Given** a checkout of this repository, **When** the operator uses the local-build
   compose file, **Then** the image builds from source in a single step and the interface
   behaves identically to the published one.
3. **Given** a running instance, **When** the operator restarts it, **Then** all
   previously saved configuration is still present and only the two in-memory secrets must
   be re-entered.

---

### User Story 2 - Complete first-run setup (Priority: P1)

A new user opens the interface for the first time. They supply their Hetzner API token and
choose a vault password, fill in the account details the playbooks need, and have the tool
generate an SSH keypair and register its public half with their Hetzner account. From then
on they are ready to provision.

**Why this priority**: Provisioning cannot succeed without credentials, vault contents,
and a registered SSH key. This story is what makes the tool usable against *any* Hetzner
account rather than one specific person's.

**Independent Test**: Starting from an empty installation, complete setup and verify that
the SSH key appears in the Hetzner console and that the saved configuration survives a
restart.

**Acceptance Scenarios**:

1. **Given** a fresh installation, **When** the user enters their Hetzner API token and a
   vault password twice, **Then** the interface indicates both are unlocked for this session
   and neither value is displayed back to them afterwards.
2. **Given** a fresh installation, **When** the user enters the vault password twice and the
   two entries differ, **Then** no configuration is created, nothing is unlocked, and the
   interface explains that this first password cannot be checked against anything later.
3. **Given** a configuration whose password the user can no longer reproduce, **When** they
   discard it from the configuration screen after typing the confirmation word, **Then** the
   encrypted configuration is deleted, the vault password is forgotten, and setup can start
   again.
4. **Given** unlocked credentials, **When** the user fills in the configuration form and
   saves, **Then** the values are stored encrypted and can be read back after re-entering
   the same password.
5. **Given** an incorrect vault password, **When** the user attempts to read existing
   configuration, **Then** the interface reports that the password does not match and
   changes nothing.
6. **Given** unlocked credentials, **When** the user requests key generation, **Then** a
   keypair is created, its public half is registered with their Hetzner account under the
   configured key name, and the private half is never shown or offered for download.
7. **Given** an existing keypair, **When** the user triggers rotation, **Then** the
   interface first warns that machines already provisioned will become unreachable, and
   proceeds only on confirmation.
8. **Given** the tool is restarted, **When** the user returns, **Then** the configuration
   and keypair are still present but the API token and vault password must be entered
   again.

---

### User Story 3 - Provision a machine (Priority: P1)

A user picks how big the machine should be, where it should run, which operating system
image to use, and which software profile to apply, then starts provisioning and watches it
happen.

**Why this priority**: This is the feature's core value. Everything else supports it.

**Independent Test**: With setup complete, provision one machine and confirm it exists in
the Hetzner console and appears in the tool's machine list.

**Acceptance Scenarios**:

1. **Given** completed setup, **When** the user opens the create form, **Then** they can
   choose a software profile, a server size, a location and an operating system image, each
   presented with the detail FR-028 requires: a one-line summary per profile, cost per
   server size, the meaning of each location, and the Ubuntu version plus LTS support-end
   date per image option.
2. **Given** choices made, **When** the user starts provisioning, **Then** the tool shows
   exactly what it is about to run before it runs it.
3. **Given** a provisioning run, **When** it completes successfully, **Then** the new
   machine appears in the machine list with its assigned name, size, location, profile and
   address.
4. **Given** every name in the hostname pool is already taken, **When** the user attempts
   to provision, **Then** the tool refuses before contacting Hetzner and explains that the
   pool is exhausted.
5. **Given** an invalid or expired API token, **When** the user attempts to provision,
   **Then** the failure is reported with the underlying reason rather than a generic error.
6. **Given** a provisioning run fails partway, **When** the user reads the outcome, **Then**
   the tool reports that any partially created machine was removed and that the run can be
   retried.

---

### User Story 4 - Apply a software profile (Priority: P2)

A user selects a machine they have already provisioned and applies its software profile,
either because provisioning and configuration were done separately or because they want to
re-apply after a change.

**Why this priority**: A bare operating system is not the point of this project; the
configured profile is. It is separated from provisioning because re-applying to an existing
machine is a distinct, repeatable action.

**Independent Test**: Against a machine provisioned in User Story 3, apply the profile and
confirm the expected software is present; run it a second time and confirm it reports no
further changes.

**Acceptance Scenarios**:

1. **Given** a machine in the list, **When** the user applies its profile, **Then** the
   configuration run starts against that machine only.
2. **Given** a profile that has already been applied, **When** the user applies it again,
   **Then** the run completes reporting no changes.
3. **Given** a machine that cannot be reached, **When** the user applies its profile,
   **Then** the connection failure is reported clearly rather than appearing to hang.

---

### User Story 5 - Destroy a machine (Priority: P2)

A user removes a machine they no longer need, so that it stops costing money and its name
returns to the pool.

**Why this priority**: Without this, the tool creates cost the user cannot clear from the
same interface, and the hostname pool fills permanently.

**Independent Test**: Destroy a machine created in User Story 3 and confirm it disappears
from both the Hetzner console and the tool's machine list, and that its name becomes
available again.

**Acceptance Scenarios**:

1. **Given** a machine in the list, **When** the user chooses to destroy it and confirms,
   **Then** the machine is removed and disappears from the list.
2. **Given** a destroy action, **When** the user has not confirmed, **Then** nothing is
   removed.
3. **Given** a destroyed machine, **When** the user opens the create form, **Then** its
   name is available in the pool again.

---

### User Story 6 - Observe what is happening (Priority: P2)

While any run is in progress the user sees live output and, when it ends, a clear success
or failure outcome.

**Why this priority**: Provisioning takes minutes. Without live feedback the user cannot
tell a slow run from a stuck one, and a failure without output is undiagnosable.

**Independent Test**: Start a run and confirm output appears progressively rather than only
at the end, and that the final outcome is unambiguous.

**Acceptance Scenarios**:

1. **Given** a run in progress, **When** the user watches it, **Then** output appears
   progressively while the run is still going.
2. **Given** a run in progress, **When** the user attempts to start a second run, **Then**
   the tool refuses and explains that only one run may be active at a time.
3. **Given** a failed run, **When** it ends, **Then** the outcome states that it failed and
   shows the relevant output.
4. **Given** a run in progress, **When** the user cancels it, **Then** the run stops and the
   outcome records that it was cancelled.

---

### User Story 7 - Manage the machine name pool (Priority: P3)

A user changes the set of names available for new machines: renaming entries, adding more
so they can run more machines at once, or removing ones they do not want.

**Why this priority**: The default pool works out of the box, so this is a refinement
rather than a blocker. It becomes necessary once a user wants more concurrent machines than
the default pool allows, or wants names that suit them.

**Independent Test**: Add a name, provision a machine after the default names are used up,
and confirm the added name is taken.

**Acceptance Scenarios**:

1. **Given** a fresh installation, **When** the user opens the name pool, **Then** the
   project's ten default names are present.
2. **Given** the name pool, **When** the user edits, adds, removes or reorders entries and
   saves, **Then** the changes persist across restarts and are used for the next
   provisioning.
3. **Given** a name that does not meet the naming rules, **When** the user saves, **Then**
   the tool rejects it and explains why, before any machine can be created with it.
4. **Given** a name currently used by a live machine, **When** the user tries to remove or
   rename it, **Then** the tool refuses and explains that the machine would become
   unmanageable.
5. **Given** the name pool, **When** the user views it, **Then** the counts of used and free
   names are visible.

---

### User Story 8 - Reuse a set of choices (Priority: P3)

A user who repeatedly provisions the same kind of machine saves their choices under a name
and reuses them.

**Why this priority**: Pure convenience. The tool is fully usable without it.

**Independent Test**: Save a preset, restart the tool, load the preset and confirm the form
is filled with the saved values.

**Acceptance Scenarios**:

1. **Given** a filled create form, **When** the user saves it as a named preset, **Then** it
   appears in the preset list.
2. **Given** a saved preset, **When** the user loads it, **Then** the create form is filled
   with its values, which remain editable before the run starts.
3. **Given** saved presets, **When** the tool is restarted, **Then** they are still present.
4. **Given** a saved preset, **When** the user renames it to a name not already in use,
   **Then** the preset is available under the new name and no longer under the old one.
5. **Given** a saved preset, **When** the user renames it to a name already in use, **Then**
   the tool refuses and explains that the name is taken.
6. **Given** no presets at all, **When** the user opens the create screen, **Then** the
   action that saves the current choices as a preset is available.
7. **Given** a running machine, **When** the user saves it as a preset, **Then** the preset
   holds that machine's profile, size, location and image, and provisioning from it produces
   the same kind of machine.
8. **Given** a machine whose size, location or image the provider does not report, **When**
   the user saves it as a preset, **Then** the tool refuses, names what is missing, and
   saves nothing.

---

### Edge Cases

- **Pool exhausted**: every name is in use. The tool must refuse before contacting Hetzner,
  because a name is claimed before the machine is requested.
- **Invalid naming**: a pool entry that Hetzner will reject must be caught at the moment it
  is saved, not at the moment it is used, so the failure does not surface mid-run.
- **Name still in use**: removing or renaming a pool entry that a live machine uses would
  leave that machine unmanageable, since it is identified by that name.
- **Wrong vault password**: existing configuration must remain untouched and the error must
  be distinguishable from an empty configuration.
- **Credentials lost on restart**: a restart clears the two in-memory secrets by design. The
  interface must present re-entry as normal, not as an error or data loss.
- **Restart during a run**: the run does not survive. The tool must not show a stale
  in-progress state on next start, and the machine list must reflect whatever actually
  happened.
- **Concurrent runs**: two runs at once would corrupt the record of which machines exist.
  Only one may be active.
- **Key rotation with live machines**: machines already provisioned trust the previous key.
  Rotating makes them unreachable, so the warning must precede the action.
- **Key name already registered**: the configured key name may already exist in the Hetzner
  account from an earlier installation, and re-registering must not fail silently or
  clobber an unrelated key without saying so.
- **Updated image, existing installation**: an installation carries the defaults it was
  first set up with. A newer image with changed defaults must be surfaced to the user rather
  than silently ignored, and refreshing them must never touch the user's own configuration,
  machine records or name pool.
- **Partial provisioning failure**: the underlying automation removes a half-created machine
  and reports it. The tool must relay that, so the user does not go hunting for an orphan.
- **Unreachable machine**: applying a profile to a machine that is off or unreachable must
  fail with the reason rather than appearing to hang.

## Requirements *(mandatory)*

### Functional Requirements

#### Deployment and distribution

- **FR-001**: The system MUST be distributed as a container image that requires only a
  container runtime to run — no local Ansible installation and no checkout of this
  repository.
- **FR-002**: The image MUST be fully self-contained. It MUST NOT depend on, derive from,
  or require the separately published ansible-runner image, and building it MUST NOT
  require that image to exist.
- **FR-003**: The image MUST be minimal in size, carrying only what a Hetzner provisioning,
  configuration and destruction run requires. Components serving only out-of-scope
  providers, out-of-scope platforms, or testing MUST NOT be present.
- **FR-004**: An automated pipeline MUST publish a pre-built image for both 64-bit x86 and
  64-bit ARM, so it can be run on either without a local build.
- **FR-005**: Published images MUST be test-gated: no image reaches the registry without
  first passing automated verification.
- **FR-006**: Published images MUST carry provenance identifying the source repository,
  the exact revision, and the build time, and MUST be signed.
- **FR-007**: A compose file MUST be provided that runs the pre-built image with no build
  step.
- **FR-008**: A separate compose file MUST be provided that builds the image from a local
  checkout in a single step.
- **FR-009**: Both compose files MUST publish the interface to the local machine only by
  default.

#### Credentials and secrets

- **FR-010**: Users MUST be able to supply their Hetzner API token and their vault password
  through the interface.
- **FR-011**: The Hetzner API token and the vault password MUST NOT be written to any
  persistent storage, MUST NOT appear in any log or run output, and MUST NOT be displayed
  back to the user after entry.
- **FR-012**: Both values MUST be discarded when the tool stops, and MUST be discarded after
  a period of inactivity.
- **FR-013**: The interface MUST show, at all times, whether each of the two secrets is
  currently available.
- **FR-014**: Actions requiring a secret that is not currently available MUST be blocked
  with an explanation, rather than attempted and failed.
- **FR-015**: The system MUST NOT ship with, or require, any credential belonging to any
  particular person or account.

#### Configuration and vault

- **FR-016**: Users MUST be able to view and edit the encrypted configuration through a
  form, without writing configuration syntax by hand.
- **FR-017**: The form's fields MUST be derived from the project's own configuration
  template, so that the template remains the single definition of what configuration exists.
- **FR-018**: Values present in a user's saved configuration but absent from the template
  MUST be preserved unchanged on save.
- **FR-019**: Values managed by the system itself, specifically the SSH key name and public
  key, MUST be shown as managed and MUST NOT be hand-editable.
- **FR-020**: The configuration MUST be stored encrypted at rest, readable only with the
  user's vault password.
- **FR-021**: Configuration entries that apply only to out-of-scope providers or platforms
  MUST be identified as such where they are shown.

#### SSH key management

- **FR-022**: The system MUST be able to generate an SSH keypair on the user's behalf.
- **FR-023**: The system MUST register the public key with the user's Hetzner account under
  the configured key name, and record the key name and public key in the encrypted
  configuration.
- **FR-024**: The private key MUST NOT be displayed in the interface and MUST NOT be
  offered for download.
- **FR-025**: Users MUST be able to re-trigger key generation and registration at any time.
- **FR-026**: Before rotating a key, the system MUST warn that machines already provisioned
  will become unreachable, and MUST require confirmation.

#### Provisioning, configuration and destruction

- **FR-027**: Users MUST be able to provision a Hetzner machine by choosing a software
  profile, a server size, a location and an operating system image.
- **FR-028**: Each choice MUST be presented with the information needed to make it: a
  one-line software summary for each profile, the cost implication of each server size, the
  meaning of each location, and the Ubuntu version plus LTS support-end date for each
  operating system image option.
- **FR-029**: Choices MUST apply to a single run only. The repository's own default
  configuration files MUST NOT be modified by any user action.
- **FR-030**: Users MUST be able to save a set of choices under a name, reuse it, rename it
  and delete it, and these MUST survive a restart.
- **FR-031**: The system MUST show exactly what it is going to run before running it.
- **FR-032**: Users MUST be able to apply a software profile to an already-provisioned
  machine.
- **FR-033**: Users MUST be able to destroy a machine, and destruction MUST require an
  explicit confirmation.
- **FR-034**: The system MUST display the machines it knows about, with name, size,
  location, profile and address.
- **FR-035**: Only one run MUST be active at a time; a second request MUST be refused with
  an explanation rather than queued silently.
- **FR-036**: Users MUST be able to watch run output progressively while the run is in
  progress, and MUST be able to cancel a run.
- **FR-037**: A run's final outcome MUST clearly state success, failure or cancellation, and
  a failure MUST surface the underlying reason rather than a generic message.
- **FR-038**: Run output MUST NOT be written to persistent storage, because it can contain
  decrypted configuration values.

#### Machine name pool

- **FR-039**: The system MUST provide the project's existing ten-name pool as the default.
- **FR-040**: Users MUST be able to edit, add, remove and reorder pool entries, and the
  result MUST persist across restarts.
- **FR-041**: Pool order MUST determine which name a new machine receives — the first free
  name in order.
- **FR-042**: Pool entries MUST be validated against the naming rules the cloud provider
  accepts, at the time they are saved.
- **FR-043**: The system MUST refuse to remove or rename a pool entry that a live machine is
  currently using.
- **FR-044**: The system MUST show how many names are used and how many are free.
- **FR-045**: When the pool is exhausted, provisioning MUST fail before any machine is
  requested from the provider, with an explanation.

#### Persistence and updates

- **FR-046**: User-owned state — encrypted configuration, the record of existing machines,
  host key records, the name pool, saved presets and the SSH keypair — MUST persist across
  restarts and upgrades.
- **FR-047**: When the running image is newer than the state it was set up with, the system
  MUST inform the user rather than silently continuing with outdated defaults.
- **FR-048**: Refreshing to newer defaults MUST NOT alter the user's encrypted
  configuration, machine records, name pool or presets.

#### Access

- **FR-049**: The interface MUST bind to the local machine only by default.
- **FR-050**: The documentation MUST state the risk of exposing the interface beyond the
  local machine, given that it controls a cloud account.

- **FR-051**: While no encrypted configuration exists, the system MUST require the vault
  password to be entered twice and MUST refuse to create the configuration unless the two
  entries are identical. This first password is the only one that cannot be checked against
  the stored configuration, so a mistyped entry would silently become the real password.
- **FR-052**: Users MUST be able to discard the encrypted configuration from the interface,
  on an explicit typed confirmation, without holding a working vault password — and the
  interface MUST state that every stored value is lost. Without this, a password that no
  longer opens the configuration blocks every run, including destroying a machine that is
  still being charged for.
- **FR-053**: Users MUST be able to save an existing machine as a preset, taking its
  profile, size, location and image from what that machine actually is, and recording the
  catalogue each of those was chosen from. The interface MUST refuse rather than save an
  incomplete preset when any of those cannot be determined.
- **FR-054**: The action that saves a set of choices as a new preset MUST be reachable when
  no preset exists yet, since otherwise the first one can never be created.
- **FR-055**: The count of running machines MUST agree with the machine list on the same
  screen. Any record of a machine that the provider no longer reports MUST be reconciled
  whenever the provider can be reached, including when the installation manages no machines
  at all.
- **FR-057**: The system MUST record, for each machine it provisions, the choices the
  machine was ordered with — including which catalogue the size and image were picked from,
  which the provider cannot report.

### Key Entities

- **Session secrets**: the Hetzner API token and the vault password. Exist only while the
  tool runs, tied to one browser session, never stored.
- **Encrypted configuration**: the account and user details the automation needs, readable
  only with the vault password. Includes values the form models and values it does not.
- **SSH keypair**: one private key held by the tool and one public key registered with the
  user's Hetzner account under a configured name. Replaceable.
- **Machine record**: what the tool knows about a provisioned machine — name, size,
  location, profile, address — and the basis for applying a profile or destroying it.
- **Machine name pool**: the ordered list of names available to new machines, with the
  project's ten defaults as the starting point.
- **Preset**: a saved, named set of create-form choices.
- **Run**: one execution of an automation task, with live output, a single-active-run
  constraint, and a terminal outcome of success, failure or cancellation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A person with a Hetzner account, a container runtime, and no prior knowledge
  of this project can go from nothing to a running interface in under 5 minutes, using only
  the published compose file and the documentation.
- **SC-002**: The same person can complete first-run setup — credentials, configuration and
  a registered SSH key — in under 10 minutes without editing any file by hand.
- **SC-003**: Provisioning a machine requires no more than four choices and one
  confirmation.
- **SC-004**: 100% of provisioning, configuration and destruction runs report a clear
  terminal outcome; none end in an ambiguous or silent state.
- **SC-005**: The Hetzner API token and the vault password appear in zero persisted
  artifacts — no file, no log, no run output — verified by automated test.
- **SC-006**: No credential, key or account identifier belonging to any individual is
  present in the published image, verified by automated test.
- **SC-007**: The published image contains no component that serves only out-of-scope
  providers, out-of-scope platforms, or testing, verified by automated test.
- **SC-008**: Applying a software profile a second time to an unchanged machine reports no
  changes.
- **SC-009**: All user-owned state survives a restart and an upgrade to a newer image, with
  only the two session secrets needing re-entry.
- **SC-010**: Every error a user can provoke — wrong password, invalid token, exhausted
  pool, invalid name, in-use name, unreachable machine — produces a message that names the
  cause.
- **SC-011**: Run output becomes visible within 5 seconds of a run starting, rather than
  only at completion.

## Assumptions

Defaults chosen where the description did not specify. Each can be revisited during
planning.

- **Scope is Hetzner Cloud only.** The project's local providers cannot work from inside a
  container, since they need a host-level tool or the host's own container daemon. The AWS
  provider and the Windows profile need a second credential model and are excluded. Backup
  and restore are excluded.
- **Only the `basic` and `desktop` profiles are offered**, matching the two Linux profiles
  the project supports on this provider.
- **Machine sizing comes from the server size alone.** The project's separate CPU, memory
  and disk settings apply only to the excluded local providers and are not shown.
- **Machine names are assigned, not chosen.** The existing automation claims the first free
  name from the pool; the user influences this by editing the pool, not by naming a machine
  at creation time.
- **No user accounts.** Every sensitive action already requires a secret the visitor must
  type in, so a second visitor gets an empty session and can do nothing. Local-only binding
  is the boundary, and it is documented.
- **Session secrets are discarded after 30 minutes of inactivity**, in addition to being
  discarded when the tool stops.
- **Destruction requires typing the machine's name** to confirm, since it is irreversible
  and the list rows sit close together.
- **The image and server-size lists are built-in by default, with an opt-in live
  Hetzner lookup behind a toggle on each, plus a free-text image field.** Revised
  post-implementation, at user request, from the original "static Ubuntu LTS list, no
  live lookup" design. The create form opens on a short built-in Ubuntu LTS list and
  the three built-in server sizes, so it works with no token entered; a per-list
  toggle queries the account itself (`GET /images` with `type=system`, and
  `GET /server_types`). A live lookup that cannot run -- token locked, API error, or
  an empty result -- falls back to the built-in list with a notice naming the cause,
  never an error page. Live sizes are limited to non-deprecated x86 types, since the
  image list is x86-only.
- **A server size is offered only in the locations it is priced in.** `GET
  /server_types` has no per-type location field; each type's `prices` array is the
  only source for where it can be ordered, and the cheapest of those prices is what
  the size table shows, labelled as a "from" figure. Selecting a size therefore
  narrows the location list, and a size/location pair the account cannot order is
  refused before any provisioning starts.
- **Dry-run mode is out of scope** for this version.
- **A single instance serves a single user at a time.** The one-active-run rule is a
  correctness requirement, not a scaling limitation to be engineered around.
- **Run history is not retained** beyond the current session, following from run output not
  being persisted.
- **The user has, or can create, a Hetzner Cloud account and API token with write access.**
  Obtaining one is outside this feature.

## Dependencies

- **Feature 016 (`016-docker-runner-harness`)**: this feature branches from it rather than
  from `main`, deviating from the constitution's branch-from-`main` rule. Three of its
  unmerged changes are load-bearing here: the configuration template shape that the vault
  form is generated from, the packaging and publishing conventions this feature's pipeline
  follows, and the issue-tracking database holding this feature's epic. Building against
  `main` would generate a form from an outdated template.
- **The project's existing playbooks** for creating, configuring and destroying machines
  are driven as-is. This feature adds an interface over them; it does not reimplement them.
- **The project's existing name pool and configuration template** are the sources of the
  defaults this feature exposes.
