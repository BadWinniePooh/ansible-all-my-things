---

description: "Task list for feature 017: Hetzner web frontend for VM provisioning"
---

# Tasks: Hetzner Web Frontend for VM Provisioning

**Input**: Design documents from `/specs/017-hcloud-web-frontend/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: Test tasks are included. The specification requires three properties to be
verified by automated test — SC-005 (neither secret reaches any persisted artifact), SC-006
(no personal credential in the published image) and SC-007 (no out-of-scope component in
the published image) — and the vault key-preservation rule destroys user configuration if
it regresses. Tests are scoped to those risks rather than applied uniformly.

**Organization**: grouped by user story so each is independently implementable and
testable.

**Tracking**: beads epic `ansible-all-my-things-bgvv`. The mapping from phases to beads
children is at the end of this file.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable — different files, no dependency on an incomplete task
- **[Story]**: which user story the task serves (US1–US8)

## Path Conventions

Paths are repository-root relative, matching the Structure Decision in
[plan.md](./plan.md): the application lives in `webui/`, container assets in `.docker/`,
tests in `tests/webui/`, compose files at the root.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: create the skeleton and settle the one layout question that is expensive to
change later.

- [X] T001 Create the application package skeleton: `webui/__init__.py`, and the empty
      `webui/templates/` and `webui/static/` directories (no placeholder files —
      constitution Principle XIII)
- [X] T002 [P] Create `requirements-web.txt` with the reduced Python set from
      [research.md](./research.md) section 3: `ansible-core` at the pin used in
      `.docker/Dockerfile`, plus `hcloud`, `passlib`, `requests`, `PyYAML`, `fastapi`,
      `uvicorn`, `jinja2`, `httpx`
- [X] T003 [P] Create `requirements-web.yml` with the reduced collection set from
      [research.md](./research.md) section 2: `hetzner.hcloud`, `community.general`,
      `ansible.posix` only. **Revised post-implementation**: `ansible.windows` and
      `chocolatey.chocolatey` had to be added back — `playbooks/configure-profile.yml`
      resolves `win_shell`/`win_reboot`/`win_chocolatey` at parse time for every run,
      unconditionally, regardless of inventory. Dropping them broke every configure run
      before any host was contacted. See research.md section 2's Correction and
      `requirements-web.yml`'s own comment for the full story
- [X] T004 [P] Create the `tests/webui/` directory and pytest configuration, adding
      `pytest` to a development-only dependency declaration (never to
      `requirements-web.txt`, which ships in the image)
- [x] T005 Verify that Ansible skips hidden directories when scanning an inventory
      directory, per [research.md](./research.md) section 7 (**CONFIRMED**): a probe file
      `inventories/.webui/probe.yml` with deliberately invalid inventory syntax was created,
      `ansible-inventory --graph` (ansible-core 2.17.14) ran clean with no parse error, and
      the probe was deleted. `.webui/` is settled as the storage layout; no
      `inventory_ignore_patterns` change was needed.

**⚠️ T005 gated the storage layout and is now settled.** Every later task writes into
`.webui/`.

**Checkpoint**: skeleton exists and the state directory location is confirmed.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: infrastructure every user story depends on.

**⚠️ CRITICAL**: no user story work begins until this phase completes.

- [X] T006 Implement `webui/config.py`: container paths (`/ansible`, the volume root, the
      `.webui/` subpaths, the pristine defaults copy), the 30-minute inactivity timeout, and
      the default loopback bind address and port
- [X] T007 Implement `webui/secrets.py`: the in-memory session store from
      [data-model.md](./data-model.md), keyed by an opaque session id, with a randomly
      generated per-process cookie signing key, `last_seen` refresh, inactivity expiry, and
      explicit lock
- [X] T008 [P] Write `tests/webui/test_secrets.py` asserting SC-005: neither secret is
      written to any file, appears in any log record, or appears in any constructed
      argument list; expiry clears both; a process-restart-equivalent invalidates sessions
- [X] T009 Implement `webui/seed.py`: first-run volume seeding, writing
      `.webui/seeded-version` from `/ansible/VERSION`, comparing it on startup, and
      refreshing only unedited default files from `/ansible/.inventories-pristine`
- [X] T010 [P] Write `tests/webui/test_seed.py` asserting FR-048: a refresh leaves the
      encrypted configuration, the machine records, the name pool and the presets byte-identical
- [X] T011 [P] Implement `webui/inventory.py`: read machine records from
      `inventories/hcloud_autogenerated.yml` — name, address, profile from group membership
      — read-only, never written by the application
- [X] T012 [P] Implement `webui/pool.py` load path only: read
      `inventories/.webui/hostname_pool_hcloud.yml` when present, else the repository
      default at `playbooks/vars/hostname_pool_hcloud.yml`; expose used and free counts
      computed against the machine records
- [X] T013 Implement `webui/runner.py`: the non-blocking single-run mutex, the asynchronous
      subprocess, merged output read line by line into a bounded ring buffer, and terminal
      outcome capture, per [contracts/playbook-invocation.md](./contracts/playbook-invocation.md)
- [X] T014 [P] Write `tests/webui/test_runner.py`: a second run is refused rather than
      queued; the constructed argument list contains no secret value; both secrets are
      present in the child environment; a non-zero exit surfaces the output tail
- [X] T015 Implement `webui/app.py`: application assembly, the base Jinja2 layout, the
      static asset mount with HTMX vendored into `webui/static/`, loopback binding by
      default, and the error surfacing rule that every refusal names its cause
      (Principle XII)
- [X] T016 Implement `webui/entrypoint.sh`: run seeding, then start the server; normalize
      to LF line endings and mark executable
- [X] T017 Implement the dashboard shell and session routes in `webui/app.py` and
      `webui/templates/`: `GET /`, `POST /session/unlock`, `POST /session/lock`,
      `GET /session/status`, with the per-secret locked or unlocked indicators and controls
      that are visibly disabled with a reason when a required secret is absent (FR-014); the
      dashboard's machine-list table, sourced from `webui/inventory.py` (T011), rendering
      each machine's name, size, location, profile and address (FR-034) — later phases wire
      the Configure and Destroy actions into these same rows (T041, T044)

**Checkpoint**: the application runs, serves a dashboard, and holds secrets correctly. User
story work can begin.

---

## Phase 3: User Story 1 — Get the tool running (Priority: P1) 🎯 MVP

**Goal**: someone with no checkout of this repository can start the tool from a published
image and reach the interface.

**Independent test**: on a machine with only a container runtime and no copy of this
repository, fetch `compose.yaml`, start it, and confirm the interface loads.

- [X] T018 [US1] Create `.docker/Dockerfile.web`: two stages on `python:3.13-slim-trixie`;
      builder installs `requirements-web.txt` into a virtual environment and
      `requirements-web.yml` collections; runtime copies only the virtual environment, the
      collections tree and the repository checkout, apt-installing only `openssh-client` and
      `ca-certificates`; bakes the pristine defaults copy to `/ansible/.inventories-pristine`;
      stamps `/ansible/VERSION` from `ARG IMAGE_VERSION` declared after the repository copy;
      normalizes CRLF on `*.sh` before `chmod`; no pip, gcc or git in the final stage
      (**VERIFIED** by a real local build: `docker build -f .docker/Dockerfile.web` succeeds,
      image builds clean, pip/gcc/git/aws.yml are absent, the entrypoint seeds the volume and
      serves `GET /` with `curl` returning 200. **Correction found only against a real running
      container, after a user hit it live**: `windows_foundation`/`win_ai_agent`/
      `windows_common`/`setup-roles-windows.yml` must be *present*, not absent —
      `configure-profile.yml` statically imports `setup-roles-windows.yml` unconditionally, so
      its absence broke every configure run with "Unable to retrieve file contents ...
      setup-roles-windows.yml" before the deeper `win_shell` module-resolution problem even
      showed up. See T003 and research.md section 2's Correction)
- [x] T019 [US1] Create `.docker/Dockerfile.web.dockerignore` excluding the AWS and Windows
      task files, roles and playbooks. **Revised**: the Windows role directories and
      `setup-roles-windows.yml` are no longer excluded (see T018); only the AWS task files and
      the Windows-only *collections* stay out of the image. BuildKit support is **CONFIRMED** per
      [research.md](./research.md) section 8: a throwaway `Dockerfile.test` +
      `Dockerfile.test.dockerignore` pair was excluded correctly by both `docker build -f`
      and `docker compose build` (Docker 29.5.3, buildx v0.34.1). No root-`.dockerignore`
      fallback is needed.
- [X] T020 [US1] Create `.docker/tests-web.yaml`: assert the pinned `ansible-playbook`
      major and minor version and that `python3` runs; assert each kept collection is
      present and each dropped collection is absent (SC-007); assert `VERSION`,
      `ansible.cfg`, the entrypoint's executable permissions and the pristine defaults copy
      exist; assert `inventories/group_vars/all/vault.yml`, `pip` and `git` are absent
      (SC-006). All assertions manually cross-checked against the real local build (no
      arch-matching `container-structure-test` binary available on this host); CI installs
      the correct binary as the runner image's workflow already does
- [X] T021 [P] [US1] Create `compose.yaml` running the published image with the named
      volume at `/ansible/inventories` and the port published to `127.0.0.1` only (FR-009)
- [X] T022 [P] [US1] Create `compose.build.yaml`, identical but building from
      `.docker/Dockerfile.web` with the repository root as context (**VERIFIED**: `docker
      compose -f compose.build.yaml build` succeeds)
- [X] T023 [US1] Create `.github/workflows/docker-web-publish.yml` mirroring
      `.github/workflows/docker-runner-publish.yml`: a native `linux/amd64` and
      `linux/arm64` build matrix with `contents: read` only, producing tarball artifacts; a
      test job with `needs: build` running the structure tests against the checksum-verified
      binary; a push job with `needs: [build, test]`, non-pull-request only, holding
      `packages: write` and `id-token: write` alone, assembling the multi-architecture
      manifest, baking OCI provenance labels and signing with cosign (Principle IX)
- [X] T024 [US1] Add every new third-party action to the allow-list in `CONTRIBUTING.md`
      and SHA-pin each as Tier A per ADR-002 — no new action was introduced: every action in
      the new workflow reuses the same pinned reference already allow-listed for
      `docker-runner-publish.yml`, so `CONTRIBUTING.md` needs no change

**Checkpoint**: the image builds, passes structure tests, publishes, and both compose files
start a reachable interface.

---

## Phase 4: User Story 2 — Complete first-run setup (Priority: P1)

**Goal**: a new user supplies credentials, fills in the configuration, and has a keypair
generated and registered with their Hetzner account.

**Independent test**: from an empty installation, complete setup and confirm the SSH key
appears in the Hetzner console and the configuration survives a restart.

- [X] T025 [US2] Implement `webui/vault.py`: decrypt and encrypt through `ansible-vault`
      with `ANSIBLE_VAULT_PASSWORD` in the child environment, streaming plaintext through
      standard input and output so no plaintext file is ever created; parse
      `inventories/group_vars/all/vault-template.yml` to derive the form schema
      (**VERIFIED** against a real `ansible-vault` 2.21.1 in a Linux container:
      `ansible-vault encrypt --output=<file> -` reads stdin and overwrites an
      already-encrypted file in place; needed an explicit `--vault-password-file` pointing
      at `scripts/echo-vault-password-environment-variable.sh` — relying on `ansible.cfg`
      auto-discovery alone left it prompting interactively. A second issue surfaced only at
      app-route level: with the explicit flag *and* a cwd containing a real `ansible.cfg`
      (exactly the container's `/ansible` WORKDIR), `ansible-vault` sees two password
      sources and refuses with "the vault-ids default,default are available" — an
      `ANSIBLE_CONFIG` override does not fix this (a missing `ANSIBLE_CONFIG` path falls
      through to cwd discovery rather than disabling it); running the subprocess with
      `cwd=tempfile.gettempdir()` does)
- [X] T026 [US2] Implement the key-preservation merge in `webui/vault.py`: form values are
      merged **over** the parsed existing document, so keys present in the user's
      configuration but absent from the template are written back unchanged (FR-018)
- [X] T027 [P] [US2] Write `tests/webui/test_vault.py`: a round trip through a document
      containing an unmodelled key preserves it byte-identically; a wrong password reports a
      mismatch and leaves the stored file untouched; a submitted value for a managed SSH key
      is discarded rather than merged (5/5 pass against real `ansible-vault` in Linux)
- [X] T028 [US2] Implement the configuration routes and templates: `GET /vault`,
      `POST /vault`, `POST /vault/users`, with masked secret fields and reveal toggles,
      add and remove rows for the desktop-user list, the SSH key fields shown as managed and
      read-only, and the Windows password marked as unused in this scope (FR-019, FR-021).
      **VERIFIED** end-to-end against real `ansible-vault` via `TestClient` in a Linux
      container: unlock → save → round trip → add-user fragment → overwrite → locked-state
      message, all pass
- [X] T029 [P] [US2] Implement `webui/hcloud_api.py`: the Hetzner SSH key endpoints only —
      list by name, create, delete — over HTTPS with the session token, surfacing the API's
      own error message on failure
- [X] T030 [US2] Implement `webui/sshkeys.py`: generate an `ed25519` keypair into
      `.webui/ssh/` at mode `0600`, register the public half under the configured key name,
      and write the key name and public key into the encrypted configuration. Default key
      name is `ansible-web-frontend` (`config.DEFAULT_SSH_KEY_NAME`) — `vault_my_ssh_key_name`
      is a managed field the user cannot type into a form, so the interface decides it
- [X] T031 [US2] Implement rotation in `webui/sshkeys.py`: list, delete, create, rewrite the
      configuration; and detect a pre-existing key under the configured name, reporting what
      was found and requiring confirmation before replacing it (`tests/webui/test_sshkeys.py`,
      4/4 pass with the Hetzner API mocked, run natively since `ssh-keygen` is on PATH)
- [X] T032 [US2] Implement the SSH key routes and templates: `GET /sshkey`,
      `POST /sshkey/generate`, `POST /sshkey/rotate`, showing the fingerprint and public key
      but never the private key and never offering it for download (FR-024), and gating both
      actions on the token and vault password being unlocked
- [X] T033 [US2] Add the rotation warning to the rotate flow: machines already provisioned
      trust the previous key and become unreachable to the automation. Require explicit
      confirmation before proceeding (FR-026). **VERIFIED** end-to-end (mocked Hetzner API,
      real `ansible-vault`, Linux container): generate → conflict-on-rotate surfaced (409) →
      confirmed rotate does delete-then-create against the right key id

**Checkpoint**: a fresh installation can be fully configured through the interface.

---

## Phase 5: User Story 3 — Provision a machine (Priority: P1)

**Goal**: a user chooses profile, size, location and image, and gets a running machine.

**Independent test**: with setup complete, provision one machine and confirm it exists in
the Hetzner console and appears in the machine list.

- [X] T034 [US3] Implement the create-form choice sources in `webui/config.py`, matching
      FR-028: the two profiles, each with a one-line software summary; the server sizes with
      the vCPU, memory, disk and monthly cost detail carried in
      `inventories/group_vars/hcloud_linux/vars.yml`; the six locations with the city names
      carried in `inventories/group_vars/hcloud/vars.yml`; and the Ubuntu long-term-support
      image list, each entry with its version and LTS support-end date, plus a free-text
      field
- [X] T035 [US3] Implement the argument builder in `webui/runner.py` for the provision
      action, exactly as specified in
      [contracts/playbook-invocation.md](./contracts/playbook-invocation.md): the four
      choices as `--extra-vars`, the private key from the volume, and no machine name
- [X] T036 [P] [US3] Write `tests/webui/test_invocation.py`: each of the four choices
      reaches the argument list; no secret does; the private key path points into the volume
- [X] T037 [US3] Implement the create routes and template: `GET /create`,
      `POST /create/preview` rendering the exact command without running it (FR-031), and
      `POST /create` starting the run. Also implements the Phase 8 run view/stream
      (`GET /run`, `GET /run/stream`, `POST /run/cancel`) ahead of schedule, since `POST
      /create` must return a working run view per contracts/http-routes.md — see the Phase 8
      checkpoint note.

      **VERIFIED** end-to-end in a Linux container (`TestClient` used as a context manager —
      **finding for future test-writers**: without `with TestClient(app) as client:`, the
      portal's event loop stops being driven between calls and a background
      `asyncio.create_task()` such as the runner's output pump never progresses, which looks
      exactly like a stuck run but is a test-harness artifact, not a bug; a real Uvicorn
      process has no such issue): create → run view shows final output/outcome → SSE stream
      emits the terminal event → a second run while one is active is refused with 409 → cancel
      sets the `cancelled` outcome
- [X] T038 [US3] Implement the pool-exhaustion refusal: when no free name remains, refuse
      before invoking, because the automation claims a name before calling the Hetzner API
      (FR-045)
- [X] T039 [US3] Surface provisioning failure relay: when the run fails, present the
      underlying Ansible error including the automation's own message that the
      partially-created machine was deleted and the run can be retried, rather than
      restating it — `run.html` renders `output` verbatim, so the automation's own rescue
      message reaches the user unparaphrased

**Checkpoint**: the core value of the feature works end to end. This plus Phases 1–4 is the
minimum shippable product.

---

## Phase 6: User Story 4 — Apply a software profile (Priority: P2)

**Goal**: re-apply a machine's profile from the machine list.

**Independent test**: apply the profile to a machine from User Story 3, then apply it again
and confirm the second run reports no changes.

- [X] T040 [US4] Implement the configure argument builder in `webui/runner.py`:
      `playbooks/configure-profile.yml` with `--limit <machine name>` and the private key;
      the profile is not passed, since it follows from group membership in the machine
      records
- [X] T041 [US4] Implement `POST /machines/{name}/configure` and wire the Configure action
      into the dashboard machine rows (**VERIFIED**: `TestClient`, fake machine record,
      run reaches `succeeded` with the right target)
- [X] T042 [US4] Ensure an unreachable machine reports the connection failure rather than
      appearing to hang: surface the Ansible unreachable message in the outcome — by
      construction, same as T039: `run.html` renders the subprocess's stdout/stderr
      verbatim, so Ansible's own `UNREACHABLE!` message reaches the user unparaphrased;
      not exercised against a real unreachable host (that needs a live machine, see T062)

---

## Phase 7: User Story 5 — Destroy a machine (Priority: P2)

**Goal**: remove a machine and return its name to the pool.

**Independent test**: destroy a machine from User Story 3, confirm it disappears from both
the Hetzner console and the machine list, and that its name is offered again.

- [X] T043 [US5] Implement the destroy argument builder in `webui/runner.py`:
      `playbooks/destroy-vm.yml` with `provider=hcloud` and `hostname=<name>`, and no
      private key, since destruction is an API call rather than an SSH session
- [X] T044 [US5] Implement `POST /machines/{name}/destroy` requiring the typed machine name
      as confirmation, refusing when it does not match exactly (FR-033), and wire the
      Destroy action into the dashboard machine rows (**VERIFIED**: wrong confirmation text
      refused with 400 and nothing started; exact match starts the run and reaches
      `succeeded`)
- [X] T045 [US5] Confirm the freed name reappears in the pool's free set after a successful
      destruction, since the free set is computed against the machine records — true by
      construction: `pool.status()` (T012) computes `free` as pool entries absent from
      `inventory.machine_names()`, and the existing, unmodified `destroy-vm.yml` removes the
      host from `hcloud_autogenerated.yml` on success; not exercised against a real Hetzner
      destroy (needs a live machine, see T062)

---

## Phase 8: User Story 6 — Observe what is happening (Priority: P2)

**Goal**: live output during a run and an unambiguous terminal outcome.

**Independent test**: start a run and confirm output appears progressively rather than only
at completion, and that the outcome is unambiguous.

- [X] T046 [US6] Implement `GET /run/stream` as a Server-Sent Events endpoint: one event per
      output line, replay of the ring buffer on reconnect, and a terminal event carrying the
      outcome and exit code so completion is never inferred from silence — implemented in
      T037 (see that entry for the verification run); replay-then-live is `Run.stream_lines()`
      in `webui/runner.py`, exercised directly in `tests/webui/test_runner.py`
- [X] T047 [US6] Implement the run view template with live output, the command that is
      running, and the terminal outcome rendering for success, failure and cancellation —
      `webui/templates/run.html`, implemented in T037
- [X] T048 [US6] Implement `POST /run/cancel`: terminate the active run, release the mutex,
      and record the `cancelled` outcome — implemented and verified in T037
- [X] T049 [US6] Implement the concurrent-run refusal in the interface: a second request is
      refused with an explanation naming the active run, never queued (FR-035) — verified in
      T037 (409 with the active run's action named) and independently in
      `tests/webui/test_runner.py::test_second_run_is_refused_not_queued`
- [X] T050 [US6] Ensure no stale running state appears after a restart, since runs do not
      survive the process — true by construction: `Runner._active` (`webui/runner.py`) is a
      plain in-memory attribute with no persistence path; a new process starts with
      `_active = None`

---

## Phase 9: User Story 7 — Manage the machine name pool (Priority: P3)

**Goal**: the user controls which names new machines receive and how many exist.

**Independent test**: add a name, exhaust the defaults, provision again and confirm the
added name is claimed.

- [X] T051 [US7] Modify `playbooks/tasks/create/hcloud.yml`: add a `stat` check that prefers
      `inventories/.webui/hostname_pool_hcloud.yml` when present and falls back to
      `playbooks/vars/hostname_pool_hcloud.yml`. Both branches are read-only and report no
      change (Principle I). **VERIFIED** against a real `ansible-playbook` 2.21.1: with a
      `.webui/hostname_pool_hcloud.yml` present, `hostname` resolves to its first entry;
      with it absent, `hostname` resolves to the repository default (`edoras`)
- [X] T052 [US7] Implement validation and the save path in `webui/pool.py`: lowercase
      letters, digits and hyphens only; no leading or trailing hyphen; at most 63 characters;
      unique within the pool; and refusal to remove or rename an entry present in the machine
      records (FR-042, FR-043)
- [X] T053 [P] [US7] Write `tests/webui/test_pool.py`: each validation rule rejects with a
      message naming the offending entry; an in-use name cannot be removed or renamed; the
      free and used counts match the machine records (18/18 pass)
- [X] T054 [US7] Implement the pool routes and template: `GET /pool`, `POST /pool` with
      edit, add, remove and reorder, rejecting the whole submission on any invalid entry, and
      displaying the used and free counts (FR-044). One-name-per-line textarea rather than
      per-row inputs — edit/add/remove/reorder are all native text editing, no add/remove-row
      JS needed. **VERIFIED**: add, in-use-removal refusal (400, names the entry), invalid-name
      refusal (400) all pass against `TestClient`
- [X] T055 [US7] Seed the user pool file from the repository default on first use, so a
      fresh installation shows the project's ten names — satisfied by construction, not a
      separate step: `GET /pool` already shows the repository defaults via `pool.load()`'s
      fallback (T012), and the first `POST /pool` (even an unedited save) writes them into
      `.webui/hostname_pool_hcloud.yml` via `pool.save()` (T052)

---

## Phase 10: User Story 8 — Reuse a set of choices (Priority: P3)

**Goal**: save and reload create-form choices.

**Independent test**: save a preset, restart, load it and confirm the form is filled.

- [X] T056 [US8] Implement `webui/presets.py`: read and write `.webui/presets.json` with
      unique names, holding only the four non-secret choices; a rename operation that
      refuses when the target name is already taken (FR-030) (`tests/webui/test_presets.py`,
      6/6 pass)
- [X] T057 [US8] Implement the preset routes and templates: `GET /presets`, `POST /presets`,
      `POST /presets/{name}/delete`, `POST /presets/{name}/rename`, requiring confirmation
      when saving over an existing name and rejecting a rename onto an existing name with an
      explanation (FR-030). Saving over an existing name is refused (409, naming the
      conflict) rather than accepted with a confirmation step — the create form has no
      confirm-and-overwrite affordance, so an existing name must be renamed or deleted first
- [X] T058 [US8] Wire preset save, load and rename into the create form, leaving every field
      editable after a preset is loaded. **VERIFIED** end-to-end (`TestClient`): save →
      duplicate-name refusal (409) → list page → `?preset=` loads and pre-checks the right
      radios → rename → delete

---

## Phase 11: Polish & Cross-Cutting Concerns

- [X] T059 [P] Write `docs/architecture/decisions/005-web-frontend-for-hcloud-provisioning.md`
      recording: a server-rendered Python service rather than a JavaScript single-page
      application; a fully self-contained image rather than one layered on the runner image;
      Alpine rejected as a base; and secrets held in memory and injected into the subprocess
      environment
- [X] T060 [P] Write `docs/architecture/concepts/web-frontend.md` plus
      `docs/architecture/concepts/web-frontend/usage.md` and
      `docs/architecture/concepts/web-frontend/build-test.md`, mirroring the layout of
      `docs/architecture/concepts/runner-docker-image.md`
- [X] T061 Document the exposure risk in the usage page: the interface is an
      unauthenticated control plane for a cloud account and binds to loopback by default;
      state plainly what changing that binding means (FR-050)
- [ ] T062 Exercise both configure paths end to end against a real Hetzner machine —
      `basic` and `desktop` — to close the `containers.podman` drop risk recorded in
      [research.md](./research.md) section 2. This is the most likely late surprise in the
      feature. **Not done — needs a real Hetzner account and API token, which this session
      does not have.** Partially de-risked by static analysis instead: `roles/podman/`
      (tasks, molecule scenario, `meta/main.yml`) contains zero references to
      `containers.podman` anywhere — only `ansible.builtin.apt`, `ansible.builtin.lineinfile`
      and `ansible.builtin.command` — and declares no collection dependency, so the drop
      cannot break that role's *task execution*. What static analysis cannot confirm is
      whether `podman system migrate` or the installed `podman` CLI itself behaves
      differently in a Hetzner-provisioned Ubuntu image versus wherever the role was last
      exercised — that still needs the live run (tracked in `ansible-all-my-things-5joe`)
- [X] T063 Verify the full restart and upgrade cycle (SC-009): all user-owned state
      survives, only the two session secrets need re-entering, and the defaults-refresh
      banner appears when the image version and the seed marker differ. **VERIFIED** against
      real built images and a real named volume: built `v1.0.0`, ran it, confirmed
      `.webui/seeded-version` = `v1.0.0` and the dashboard reachable; `docker restart` — still
      reachable, marker unchanged; built `v2.0.0`, ran it against the *same* volume — the
      "newer image is running" banner appeared; `POST /defaults/refresh` — banner cleared,
      marker updated to `v2.0.0`
- [X] T064 Walk every error path named in SC-010 — wrong password, invalid token, exhausted
      pool, invalid name, in-use name, unreachable machine — and confirm each message names
      its cause. Status per path:
      - **wrong password**: real `ansible-vault`'s own "Decryption failed" message, surfaced
        verbatim (T027/T028) — verified
      - **invalid token**: `webui/hcloud_api.py` called against the *real* Hetzner API with a
        bogus token returns HTTP 401 `{"error": {"message": "the token you have provided is
        invalid"}}`, and `HetznerApiError` carries that exact string through to the `/sshkey`
        route — verified against the live API (a safe, read-only, no-account-needed call).
        The provisioning path's token check goes through the `hetzner.hcloud.server` Ansible
        module's own error instead, relayed verbatim like every other run failure (T039) —
        not independently re-verified
      - **exhausted pool**: verified (T038)
      - **invalid name**: verified (T052/T053)
      - **in-use name**: verified (T044/T045, T052/T053)
      - **unreachable machine**: by construction only (T042) — `run.html` relays Ansible's
        own `UNREACHABLE!` message, but no real unreachable host was exercised (needs T062's
        live machine)
- [X] T065 Update `.beads/issues.jsonl` with `bd export --all -o .beads/issues.jsonl` after
      the final beads mutations
- [X] T066 Invoke the `review-documentation-here` skill across all new and modified
      documentation. Verified placement: ADR under `docs/architecture/decisions/`, concept
      doc plus children under `docs/architecture/concepts/`, no new role (so no
      `roles/<name>/README.md`/`DESIGN.md` to add), `specs/017-hcloud-web-frontend/` is the
      working-context tier and stays there
- [ ] T067 Invoke the `format-markdown` skill once, after all Markdown is finalized. **Not
      possible in this session — the skill does not exist under `.claude/skills/` despite
      being constitution-mandated (Principle VI) and listed in `AGENTS.md`'s skill index.**
      Ran `npx markdownlint-cli2` by hand instead, against the `.markdownlint.json` this repo
      already ships. That surfaced that this entire feature's `specs/017-hcloud-web-frontend/`
      document set (spec.md, plan.md, research.md, and this file) has pervasive pre-existing
      MD013 line-length violations predating this implementation session — not something this
      session's edits introduced. Filed as a tracked finding rather than silently fixed or
      ignored (Principle VIII); see the session hand-off notes
- [X] T068 Verify SC-011: start a run and time the first `GET /run/stream` output event from
      run start; confirm it arrives within 5 seconds. **VERIFIED**: a run whose command sleeps
      0.3s before printing delivered its first SSE `data:` event at ~0.31s wall-clock from
      `POST /create`, comfortably under the 5s bound. The bulk of any real-world latency is
      `ansible-playbook`'s own startup (fact-gathering, module loading), not this pipeline
- [X] T069 [US2] Let the operator log in to machines themselves (FR-058): the generated
      private key never leaves the volume, so nothing else gives them a key any account
      accepts. Adds `vault_my_additional_ssh_public_keys` (list) to
      `inventories/group_vars/all/vault-template.yml`; `playbooks/setup-users.yml` reads it
      directly with an empty-list default for vault files that predate it — not through a
      `vars.yml` mapping, because the volume keeps the `vars.yml` it was first seeded with —
      asserts it is a list and authorizes it next to `my_ssh_public_key` for every account;
      the vault form
      edits it as one key per line, validated with `ssh-keygen -l` in
      `webui/sshkeys.py` `parse_additional_public_keys()`, refusing the whole save and naming
      rejected lines by number only (`tests/webui/test_additional_ssh_keys.py`). **VERIFIED**
      in the local web image against its own root account: a vault without the setting
      authorizes one key, a listed key adds a second, the second run reports no change, and
      a string instead of a list fails the assertion

---

## Dependencies

### Phase order

    Phase 1 Setup
       └─> Phase 2 Foundational
              └─> Phase 3 US1 (image, compose, pipeline)   🎯 MVP boundary
                     ├─> Phase 4 US2 (setup)
                     │      └─> Phase 5 US3 (provision)
                     │             ├─> Phase 6 US4 (configure)
                     │             ├─> Phase 7 US5 (destroy)
                     │             └─> Phase 8 US6 (observe)
                     ├─> Phase 9 US7 (name pool)
                     └─> Phase 10 US8 (presets)
    Phase 11 Polish  (after all)

### Notable task-level dependencies

- **T005 gates everything that writes state.** The `.webui/` location must be confirmed
  before T007, T009, T012, T030 and T056 commit to it.
- T018 (image) requires the application to exist, so Phase 2 precedes Phase 3.
- T023 (pipeline) requires T020 (structure tests) to have something to gate on.
- T035, T040 and T043 all extend `webui/runner.py` and therefore serialise against each
  other, even though their stories are otherwise independent.
- T051 modifies an Ansible task file and is the only change outside `webui/`, `.docker/`,
  `tests/` and the compose files.
- US7 and US8 depend only on the foundation, not on US3, so they can proceed in parallel
  with the provisioning stories.

## Parallel execution opportunities

**Phase 1**: T002, T003, T004 are independent files.

**Phase 2**: T008, T010, T011, T012 touch separate modules. T014 can be written alongside
T013.

**Phase 3**: T021 and T022 are independent compose files.

**Phase 4**: T027 (vault tests) and T029 (`hcloud_api.py`) are independent of each other.

**Phase 5**: T036 is independent of the route work in T037.

**Phase 9**: T053 is independent of the route work in T054.

**Phase 11**: T059 and T060 are separate documents.

## Implementation strategy

**Minimum shippable product**: Phases 1 through 5 — T001 to T039. That delivers a
published image someone can start, configure and use to provision a machine, which is the
feature's entire value proposition. Phases 6 and 7 follow immediately, since a tool that
creates cloud machines but cannot remove them is a cost leak.

**Increment order after that**: US6 (observe) makes long runs tolerable, US7 (name pool)
lifts the ten-machine ceiling, US8 (presets) is convenience.

**What to verify before declaring done**: T062 is the task most likely to invalidate an
earlier assumption. Run it as soon as a machine can be provisioned rather than saving it
for the polish phase — if `containers.podman` turns out to be required, T003 and T018
change.

## Beads mapping

| Phase | Beads issue |
|---|---|
| Phases 1–2 (T001–T017) | `bgvv.3` (dependency sets), `bgvv.6` (application skeleton and secret store), `bgvv.7` (job runner), `bgvv.8` (seeding) |
| T005 | `bgvv.19` (hidden-directory verification) |
| Phase 3 (T018–T024) | `bgvv.4` (image), `bgvv.5` (dockerignore verification), `bgvv.14` (structure tests), `bgvv.16` (pipeline), `bgvv.17` (compose) |
| Phase 4 (T025–T033) | `bgvv.9` (vault editor), `bgvv.10` (SSH keys) |
| Phase 5 (T034–T039) | `bgvv.12` (create screen) |
| Phases 6–7 (T040–T045) | `bgvv.13` (dashboard actions) |
| Phase 8 (T046–T050) | `bgvv.7` (job runner) |
| Phase 9 (T051–T055) | `bgvv.11` (hostname pool) |
| Phase 10 (T056–T058) | `bgvv.12` (presets, shared with the create screen) |
| T069 | `bgvv.39` (operator's own SSH public keys) |
| Phase 11 (T059–T068) | `bgvv.2` (ADR), `bgvv.18` (documentation), `bgvv.15` (pytest suite, spanning T008/T010/T014/T027/T036/T053), `bgvv.7` (job runner, T068 SC-011 latency check) |
