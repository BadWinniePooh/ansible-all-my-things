# Phase 0 Research: Hetzner Web Frontend

**Feature**: `017-hcloud-web-frontend` | **Date**: 2026-08-24

Resolves the open technical questions from [plan.md](./plan.md). Two items, sections 7 and
8, were mechanisms the design leaned on that needed confirming empirically before
implementation could depend on them. Both are now **CONFIRMED** — see the verification
results in each section.

## 1. Base image and Python version

**Decision**: `python:3.13-slim-trixie` for both the builder and the runtime stage.

**Rationale**: the image is independent of the runner image, so it is free to pick a base
rather than inherit `ubuntu:26.04`. A slim Debian Python base provides the interpreter and
`pip` without apt-installing a full Python stack, and Python 3.13 is comfortably inside the
controller Python range that `ansible-core` 2.21 supports.

**Alternatives considered**:

- `ubuntu:26.04`, matching the runner image — rejected. It buys consistency the user
  explicitly did not want and costs size the user explicitly asked to minimise.
- Alpine — rejected. `cryptography` and other Ansible dependencies have no musl wheels, so
  the build would need a compiler toolchain. That trades image size for build time and a
  larger builder stage, defeating the purpose.
- A newer Python than 3.13 — rejected for now. The controller Python range declared by the
  pinned `ansible-core` is the binding constraint, and moving to the newest interpreter
  gains nothing here.

**Follow-up**: confirm the pinned `ansible-core` version's declared controller Python
support before finalising the tag. If 3.13 falls outside it, move to the highest supported
version rather than changing the base family.

## 2. Reduced Ansible collection set

**Decision**: `requirements-web.yml` keeps `hetzner.hcloud`, `community.general` and
`ansible.posix`. It drops `amazon.aws`, `ansible.windows`, `community.windows`,
`chocolatey.chocolatey` and `containers.podman`.

**Rationale**: the repository declares no `collections:` keyword in any role `meta/main.yml`
or playbook. Short module names therefore resolve against `ansible.builtin` only, which
makes a fully-qualified-name search an authoritative usage list rather than a heuristic.
That search returns:

| Collection | References found |
|---|---|
| `hetzner.hcloud` | `playbooks/tasks/create/hcloud.yml`, `playbooks/tasks/destroy/hcloud.yml` |
| `community.general` | `roles/android_studio/tasks/main.yml`, `playbooks/update-versions/tasks/fetch-java-version.yml` |
| `ansible.posix` | `playbooks/tasks/create/docker.yml` |
| `amazon.aws` | `playbooks/tasks/create/aws.yml`, `playbooks/tasks/destroy/aws.yml` |
| `ansible.windows`, `community.windows`, `chocolatey.chocolatey` | `roles/win_ai_agent`, `roles/windows_foundation` |
| `containers.podman` | none anywhere in the repository |

Every dropped collection is referenced only by the AWS provider or the Windows profile,
both out of scope, or by nothing at all.

`community.general` and `ansible.posix` are kept even though their current references sit
outside this feature's execution paths. Both are small, and dropping a general-purpose
collection to save little would make the next role that needs it fail in a confusing way.

**Risk to close during implementation**: `containers.podman` has no references at all, yet
the `podman` role runs in the `basic` profile that this feature applies. That role
evidently uses builtin modules, but the basic and desktop configure paths must both be
exercised end to end before the drop is considered proven. This is the single most likely
source of a late surprise.

## 3. Reduced Python dependency set

**Decision**: `requirements-web.txt` keeps `ansible-core` at the same pin as the runner
image, plus `hcloud`, `passlib`, `requests` and `PyYAML`, and adds `fastapi`, `uvicorn`,
`jinja2` and `httpx`. It drops `boto3`, `botocore`, `molecule` and `molecule-plugins`.

**Rationale**: `hcloud` is the SDK the `hetzner.hcloud` collection requires. `passlib` is
required because `playbooks/setup-users.yml` uses `password_hash`, which fails without it
on the controller. `boto3` and `botocore` exist solely for the AWS provider.

`molecule` and `molecule-plugins[podman]` are the notable find. The runner image installs
them today, because `.docker/Dockerfile` injects the whole of `requirements.txt` into the
Ansible virtual environment. A role-testing framework in a runtime image is pure weight.
This feature simply does not repeat that; the runner image is out of scope here, and
correcting it there is left as separate work.

**Alternatives considered**: reusing the root `requirements.txt` and accepting the size —
rejected, as it directly contradicts the minimal-size requirement.

## 4. Keeping secrets off disk and out of the process list

**Decision**: hold both secrets in a process-memory map keyed by an opaque session
identifier carried in a signed, `HttpOnly`, `SameSite=Strict` cookie. Pass them to
`ansible-playbook` through the child process environment only.

**Rationale**: the repository already has the right seam. `ansible.cfg` sets
`vault_password_file = ./scripts/echo-vault-password-environment-variable.sh`, and that
script reads `ANSIBLE_VAULT_PASSWORD` from the environment and writes it to standard
output. No temporary file is needed for the vault password, and
`playbooks/tasks/create/hcloud.yml` already reads `HCLOUD_TOKEN` through
`lookup('env', ...)`.

The process environment is the correct channel and `--extra-vars` is the wrong one.
Arguments are world-readable through the process list on a shared host and are echoed into
the run log; environment variables of a child process are not exposed the same way.

The cookie signing key is generated randomly at process start and never persisted, so a
restart invalidates every session rather than leaving a resumable one.

**Alternatives considered**:

- Writing the vault password to a temporary file, even on a memory-backed filesystem —
  rejected. It is unnecessary given the existing script, and it creates a path that could
  be captured by a diagnostic bundle or a stray volume mount.
- Passing secrets as `--extra-vars` — rejected for the reasons above.
- Persisting an encrypted copy of the token so it survives restarts — rejected. The
  specification forbids persisting it, and any at-rest form needs a key that would itself
  have to be stored or re-entered.

## 5. Reading and writing the encrypted configuration

**Decision**: shell out to `ansible-vault` with `ANSIBLE_VAULT_PASSWORD` in the
environment, reading plaintext from the child's standard output and writing new plaintext
to its standard input. The decrypted document exists only in memory.

**Rationale**: using the same tool and the same password path as the playbooks guarantees
the file stays readable by them, including any future change to the vault format. Streaming
through standard input and output avoids a plaintext temporary file entirely.

Key preservation is a load-bearing detail. The form is generated from
`inventories/group_vars/all/vault-template.yml`, but a user's real vault may contain keys
that the template does not model. On save, the write merges form values over the parsed
existing document rather than replacing it, so unmodelled keys survive untouched. The
pytest suite covers this directly, since a silent drop here would destroy user
configuration with no error.

**Alternatives considered**: a pure-Python implementation of the vault format — rejected as
a maintenance liability that could drift from whatever `ansible-vault` accepts.

## 6. Volume seeding and image-version skew

**Decision**: mount one named volume at `/ansible/inventories` and rely on the container
runtime's behaviour of copying image content into an empty named volume on first use. Bake
a pristine copy of the defaults at `/ansible/.inventories-pristine`, and record the image
version into `.webui/seeded-version` at seed time.

**Rationale**: a single mount is what the user chose, and it keeps everything the user owns
in one place that can be backed up or discarded as a unit. The known cost is that seeding
happens exactly once: after that the volume shadows the image, so a newer image with
changed defaults is silently ignored.

The pristine copy plus the version marker converts that silence into a visible signal. On
startup the application compares the baked version against the marker and, on mismatch,
shows a banner with the differences and offers to refresh. The refresh copies only default
files the user has not edited, and never touches the encrypted configuration, the machine
records, the name pool, the presets or the host keys.

**Alternatives considered**:

- A separate `/data` volume with the paths symlinked into place — rejected by the user in
  favour of the single mount.
- A host bind mount of a real checkout — rejected. It requires the checkout and the correct
  flags that the interface exists to eliminate, and an empty host directory would blank out
  every default file.

## 7. Hidden directory inside an inventory directory — **CONFIRMED**

**Decision**: place application state in `.webui/` inside `/ansible/inventories`.

**Why it needed verifying**: `ansible.cfg` sets `inventory = ./inventories`, so Ansible
parses that directory as an inventory source. Any file it tries to parse and cannot
understand is a hard failure. The design assumes Ansible skips entries beginning with a dot
when scanning an inventory directory.

**Verification result**: placed a deliberately invalid file at `inventories/.webui/probe.yml`
(`this is: not: valid: inventory: [ syntax`) and ran `ansible-inventory --graph` (ansible-core
2.17.14). It completed cleanly with no parse error, confirming Ansible skips the hidden
directory entirely. The probe file was removed afterwards. No `ansible.cfg` change is needed.

## 8. Per-Dockerfile ignore file — **CONFIRMED**

**Decision**: use `.docker/Dockerfile.web.dockerignore` to exclude the AWS and Windows task
files, roles and playbooks from the web build context.

**Why it needed verifying**: this is a BuildKit feature, and its availability depends on the
builder version and on whether the compose build path honours it. Tracked as beads issue
`ansible-all-my-things-bgvv.5`.

**Verification result**: built a throwaway `Dockerfile.test` with a sibling
`Dockerfile.test.dockerignore` excluding one probe file, via both plain `docker build -f` and
`docker compose build` (Docker 29.5.3, buildx v0.34.1). In both paths the excluded file was
absent from the build context while an unexcluded file and the Dockerfile itself were present.
Confirms the per-Dockerfile ignore-file convention (`<Dockerfile-name>.dockerignore` beside the
Dockerfile) is honoured by this environment's builder and by the compose build path.

## 9. Streaming run output

**Decision**: run `ansible-playbook` through an asynchronous subprocess, read its combined
output line by line, fan each line out to a bounded in-memory ring buffer and to a
Server-Sent Events stream, and never write it to disk.

**Rationale**: Server-Sent Events are one-directional and need no client library, which
suits a server-rendered page with vendored HTMX and no build chain. The ring buffer lets a
user who opens the run view late still see recent context, while the bound prevents a long
run from growing memory without limit.

Output is not persisted because Ansible can echo decrypted configuration values into it,
which would defeat the encryption of the file those values came from.

**Alternatives considered**: WebSockets — rejected as bidirectional machinery for a
one-directional problem. Polling a growing log file — rejected because it requires
persisting exactly what must not be persisted.

## 10. Hetzner SSH key registration

**Decision**: call the Hetzner Cloud API directly over HTTPS from the application for the
SSH key endpoints only: list by name, create, and delete.

**Rationale**: this is control-plane application logic, not host configuration. Expressing
it as a playbook would mean a new playbook containing inline tasks, which Principle II
forbids outside a closed allowlist of three maintenance directories. A direct call keeps
the constitution unstrained and keeps the interaction synchronous, so the interface can
report the outcome immediately rather than parsing it out of a run log.

Rotation is list-then-delete-then-create under the configured key name, followed by writing
the new name and public key into the encrypted configuration. If a key already exists under
that name from an earlier installation, the interface reports what it found and asks before
replacing it, rather than silently clobbering a key that may belong to something else.

**Alternatives considered**: the `hetzner.hcloud.ssh_key` module inside a small playbook —
rejected on Principle II, and it would put a synchronous user action behind the same
single-run mutex that provisioning uses.

## 11. Single active run

**Decision**: a process-level mutex, acquired without blocking. A second request is refused
with an explanation rather than queued.

**Rationale**: `playbooks/create-vm.yml` and `playbooks/destroy-vm.yml` both rewrite
`inventories/hcloud_autogenerated.yml`, the file that records which machines exist. Two
concurrent runs can interleave a read-modify-write and lose a machine record, leaving a
running server that the interface no longer knows about and cannot destroy — a silent cost
leak.

Refusing rather than queueing is deliberate. A queued run would start minutes later against
state the user has since changed, and the specification requires that every run report a
clear outcome.

**Alternatives considered**: file locking — unnecessary for a single process, and it would
imply a multi-process design the feature does not have.

## 12. Hostname pool override

**Decision**: add a `stat` check in `playbooks/tasks/create/hcloud.yml` that prefers
`inventories/.webui/hostname_pool_hcloud.yml` when present and falls back to
`playbooks/vars/hostname_pool_hcloud.yml`.

**Rationale**: the existing task file loads the pool with `include_vars`. Extra vars
outrank `include_vars`, so passing the pool with `-e @file` would also work — but it works
by a precedence rule invisible to anyone reading the task file. The explicit check is
self-documenting and benefits command-line users too.

Validation happens in the interface at save time, not at use time, because
`playbooks/tasks/create/hcloud.yml` claims a name before it calls the Hetzner API. An
invalid name would therefore fail partway through a run instead of at the moment it was
entered.

**Alternatives considered**: replacing the repository default file outright — rejected,
because it would make a user edit collide with image updates and would break the
command-line path for anyone not using the interface.

## 13. Front-end assets

**Decision**: vendor HTMX as a static file in the image. Use no content delivery network,
no bundler and no JavaScript build step.

**Rationale**: the container must work on a machine with no outbound access to third-party
hosts, and a network-loaded interface library would make the interface fail in a confusing,
partial way. Vendoring also keeps the published image self-describing: what ships is what
runs.

## 14. Multi-architecture build and publish

**Decision**: mirror the structure already proven in
`.github/workflows/docker-runner-publish.yml` — a native per-architecture build matrix
producing image tarball artifacts, a test job that loads each tarball and runs the
structure tests, and a push job gated on both that assembles the multi-architecture
manifest and signs it.

**Rationale**: native builds per architecture avoid emulation, which is slow and has
produced subtle failures for Python wheel installation. Reusing a shape already working in
this repository means the permission scoping, the checksum-verified test binary and the
signing step are known-good rather than newly invented. Principle IX is satisfied by
construction: write permissions exist only in the push job, and the push depends on the
test job.

**Alternatives considered**: a single job using emulation for the second architecture —
rejected on build time and on the wheel-installation risk.
