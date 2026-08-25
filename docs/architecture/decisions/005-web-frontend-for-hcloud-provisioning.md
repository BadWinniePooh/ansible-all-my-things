# ADR-005: Web Frontend for Hetzner Cloud Provisioning

Date: 2026-08-24
Status: Accepted
Deciders: Stefan (Product Owner)

## Context and Problem Statement

This repository's Hetzner Cloud lifecycle — provision, configure, destroy — is
driven from the command line: a local Ansible installation (or the
`ansible-runner` image from
[ADR-002](./002-github-actions-pinning-policy.md)'s sibling pipeline), a
checkout of this repository, and hand-typed `--extra-vars`. That is fine for
this repository's author, but it is the entire barrier to anyone else trying
the project: no checkout, no Ansible knowledge, no command-line flags to get
wrong.

Feature `017-hcloud-web-frontend`
([spec.md](../../../specs/017-hcloud-web-frontend/spec.md)) asks for a
container image that serves a web interface driving the same playbooks, usable
by anyone against their own Hetzner account, with two secrets (the Hetzner API
token and the Ansible Vault password) that must never touch disk, a log, or
run output.

Decision: **what runtime shape should the interface take, and how should it
reach the two forbidden-to-persist secrets into the automation it drives?**

### Scope

The web frontend's implementation language and framework, its image's
relationship to the existing `ansible-runner` image, its base image, and the
mechanism carrying the Hetzner API token and the vault password from the
browser session to the `ansible-playbook` subprocess. Out of scope: the
playbooks themselves (driven as-is, not reimplemented) and the AWS/Windows
providers (excluded by the feature's own scope).

## Decision Drivers

- **Minimal image (spec.md FR-003).** The image must carry only what a
  Hetzner run needs.
- **Self-contained (spec.md FR-002).** No dependency on, or shared tag with,
  the `ansible-runner` image.
- **Secrets never persisted (spec.md FR-011, SC-005).** The token and vault
  password must never reach a file, a log line, or an argument list.
- **Simplicity (Constitution IV).** No JavaScript build chain, no new
  runtime beyond what the image already needs to run Ansible.
- **Role-First Organisation (Constitution II).** SSH key registration is
  control-plane application logic, not host configuration — it must not
  become inline task logic in a new playbook.

## Considered Options

For the runtime shape:

1. **Server-rendered Python service** (FastAPI + Jinja2 + vendored HTMX).
2. **JavaScript single-page application** with a Python or Node API backend.
3. **Static HTML with client-side JavaScript only**, calling a thin API.

For the image's relationship to the runner image:

1. **Fully self-contained**, own Dockerfile, own dependency set.
2. **Layered on `ansible-runner`** (`FROM ansible-runner`), adding the web
   server on top.

For the base image:

1. **`python:3.13-slim-trixie`** (Debian-based, official Python image).
2. **Alpine** (`python:3.13-alpine`).
3. **`ubuntu:26.04`**, matching the runner image.

For secret delivery to the subprocess:

1. **Process-memory session store, injected into the child environment.**
2. **A temporary file** (including memory-backed / tmpfs).
3. **`--extra-vars`.**

## Decision Outcome

Chosen, in order:

**A server-rendered Python service.** Python is the language this image
already runs Ansible on, so choosing it adds no second language runtime or
toolchain — only libraries. A JavaScript SPA (Option 2) would need a build
chain and a second language runtime in an image required to be minimal; a
static-HTML-plus-JS-API split (Option 3) still needs a server process to hold
secrets out of storage and stream subprocess output, so it does not actually
avoid a backend — it just adds a second surface. HTMX is vendored as a static
asset (no CDN) so the interface works with no outbound network access.

**A fully self-contained image**, independent of `ansible-runner`. Layering on
the runner image (Option 2 there) would pull in the AWS SDK, three Windows
collections, and the Molecule test stack — everything FR-003's minimal-size
requirement exists to keep out — and would couple the two images' release
cadence, which the user explicitly rejected during specification.

**`python:3.13-slim-trixie`** as the base for both build stages. Alpine was
rejected because `cryptography` (an `ansible-core` dependency) and other
wheels have no musl build, which would force a compiler toolchain into the
image — trading the size Alpine is chosen for against a larger builder stage
and slower builds, defeating the purpose. `ubuntu:26.04` would just import the
runner image's size profile into an image required to be smaller than it.

**A process-memory session store**, keyed by a signed session cookie, whose
values are injected into the `ansible-playbook` child's environment and never
appear in its argument list. The repository already has the right seam for
this: `ansible.cfg` routes `vault_password_file` through
`scripts/echo-vault-password-environment-variable.sh`, which reads
`ANSIBLE_VAULT_PASSWORD` from the environment, and
`playbooks/tasks/create/hcloud.yml` already reads `HCLOUD_TOKEN` the same way.
A temporary file (Option 2) is unnecessary given that seam, and risks capture
by a diagnostic bundle or a stray volume mount even when memory-backed.
`--extra-vars` (Option 3) is wrong on its own terms: arguments are readable
through the process table by any local user and are echoed into the run view,
which is exactly what FR-011 forbids.

### Consequences

- The image installs `ansible-core`, `fastapi`, `uvicorn`, `jinja2`, `httpx`
  and the Hetzner-only dependency set
  (`requirements-web.txt` / `requirements-web.yml`) — never the full root
  `requirements.txt` / `requirements.yml`, which would reintroduce the AWS SDK
  and the Windows collections.
- Two Dockerfiles now exist side by side (`.docker/Dockerfile` and
  `.docker/Dockerfile.web`), each with its own build, each filtered by its own
  `.dockerignore` (`.dockerignore` and `.docker/Dockerfile.web.dockerignore`
  respectively — BuildKit's per-Dockerfile ignore-file convention, confirmed
  in [research.md](../../../specs/017-hcloud-web-frontend/research.md)
  section 8). A change to one build never silently alters the other.
- Hetzner SSH key registration (list/create/delete by name) is a direct HTTPS
  call from the application (`webui/hcloud_api.py`), not a playbook, keeping
  Constitution Principle II's role-only rule for playbooks untouched.
- A process restart discards every session, including any unlocked secrets —
  by design, since the signing key and the secret store are both
  process-memory only.
