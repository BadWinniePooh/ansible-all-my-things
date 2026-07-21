---
name: docker-runner-harness
description: >
  Package an Ansible project into a portable Docker "ansible-runner" image so
  any playbook runs with only Docker plus host-mounted secrets. Use when adding
  a container runner harness to this or another Ansible repository, or when
  maintaining the Dockerfile, entrypoint, structure tests, or publish workflow
  of an existing one.
---
# Docker runner harness for an Ansible project

Package an Ansible project into a portable Docker container so any playbook
runs with only Docker and the project's existing secret files on the host —
no local Ansible install required.

## Reference implementation

This repository contains a working instance of the pattern. Read it before
writing anything; it is the authoritative example of every file described
below:

- [.docker/Dockerfile](../../../.docker/Dockerfile)
- [.docker/entrypoint.sh](../../../.docker/entrypoint.sh)
- [.docker/tests.yaml](../../../.docker/tests.yaml)
- [.dockerignore](../../../.dockerignore)
- [.github/workflows/docker-runner-publish.yml](../../../.github/workflows/docker-runner-publish.yml)
- [runner-docker-image.md](../../../docs/architecture/concepts/runner-docker-image.md)

Adapt every path and name to the target repository. Do not carry over
project-specific names (providers, vault file names, SSH key names) from the
reference.

## Step 0 — discover the target project's shape

Do this before writing anything. Inspect and record:

- Entry-point playbooks a user runs directly (e.g. `site.yml`,
  `create-vm.yml`, `deploy.yml`).
- `requirements.yml` (Galaxy collections) and `requirements.txt` (control-node
  Python packages, e.g. cloud SDKs). If either is missing, note whether the
  step can be skipped or the file must be created.
- Any `ansible.cfg` — note `inventory`, `roles_path`, `vault_password_file`,
  `interpreter_python`, so the container's `ANSIBLE_CONFIG` matches.
- Secret and credential files actually used: vault files, the vault password
  convention, SSH keypairs, cloud API tokens. Grep playbooks, roles and
  inventory for `ansible_ssh_private_key_file`, `vault_password_file`, and
  `lookup('env', ...)` to find the required environment variables.
- Existing CI under `.github/workflows/` for conventions already in use
  (registry, image namespace, tagging scheme, action pinning), so the new
  workflow fits in rather than inventing a second convention.
- Any pinned `ansible-core` version already declared. Otherwise pick the
  current stable release and pin it explicitly — never float `latest`.

If a category does not apply to the target project, drop that section. Do not
invent files that do not exist.

## 1. `.docker/Dockerfile` — multi-stage build

**Builder stage**: `FROM ubuntu:<current LTS>`, `DEBIAN_FRONTEND=noninteractive`.
Install `python3 python3-pip python3-venv pipx git openssh-client`. Then:

```dockerfile
RUN pipx install ansible-core==<pinned-version>
COPY requirements.txt /tmp/requirements.txt
RUN pipx inject ansible-core -r /tmp/requirements.txt
COPY requirements.yml /tmp/requirements.yml
RUN ansible-galaxy collection install -r /tmp/requirements.yml
```

Copy each requirements file on its own line before its install step so the
layer caches independently of code changes.

**Runtime stage**: a fresh `FROM ubuntu:<same LTS>`. Install only `python3` and
`openssh-client` — no compilers, pip, pipx or git. Then:

- `COPY --from=builder /root/.local /root/.local` (pipx venv and wrappers)
- `COPY --from=builder /root/.ansible /root/.ansible` (Galaxy collections)
- `ENV PATH="/root/.local/bin:$PATH"`
- `ENV ANSIBLE_CONFIG=/ansible/ansible.cfg`
- `WORKDIR /ansible`
- `COPY . /ansible` — the whole repo, filtered by `.dockerignore`
- `RUN chmod +x /ansible/.docker/entrypoint.sh`

**Version stamping**: declare `ARG IMAGE_VERSION=dev` *after* the repo `COPY`,
so changing the version does not bust earlier cache layers, then
`RUN echo "${IMAGE_VERSION}" > /ansible/VERSION`.

**Entrypoint**: `ENTRYPOINT ["/ansible/.docker/entrypoint.sh"]`.

**Line endings**: a checkout on a Windows host with `core.autocrlf` yields
CRLF, and a CRLF shebang breaks the in-container interpreter lookup
(`/bin/bash\r: no such file`). Guard it on both sides — add `*.sh text eol=lf`
to `.gitattributes`, and normalize in the image before the `chmod`:

```dockerfile
RUN find /ansible -name '*.sh' -exec sed -i 's/\r$//' {} + \
    && chmod +x /ansible/.docker/entrypoint.sh
```

BuildKit cache mounts on `/var/cache/apt` and `/var/lib/apt` keep apt lists out
of image layers and speed up rebuilds.

## 2. `.docker/entrypoint.sh`

Start with `#!/bin/bash` and `set -euo pipefail`. In order:

1. **`version_check()`**, called before anything else prints:
   - Read `/ansible/VERSION`; return silently if missing or empty.
   - Return silently if the value equals the Dockerfile's default `ARG`
     (`dev`) — locally built images do not check for updates.
   - Query the project's GitHub releases API
     (`https://api.github.com/repos/<owner>/<repo>/releases/latest`) with
     inline Python 3 (`urllib.request` + `json`, stdlib only — do not add
     `curl`, it is not needed and grows the runtime image), 3-second timeout.
   - Fail completely silently on any error: network, non-200, malformed JSON,
     missing `tag_name`. Never abort the container — `return 0` on every path
     so `set -e` cannot propagate out.
   - If the fetched tag differs from the local version, print a short notice
     with both versions and the `docker pull` command that updates.
2. **Mount and secret sanity checks** — for each secret the project actually
   needs (vault file, vault password, SSH key, cloud token env var), check its
   expected container path and print a `WARNING:` naming the exact
   `-v host:container:ro` or `-e VAR` flag that fixes it. Do not fail: the play
   that genuinely requires the value fails loudly on its own (constitution
   Principle XII applies to the plays, not to this dispatcher).
3. **Vault password wiring**, only if the project uses vault — either default
   `ANSIBLE_VAULT_PASSWORD_FILE` to its in-container path, or rely on an
   `ansible.cfg` `vault_password_file` script that reads a password
   environment variable.
4. **Required `PLAYBOOK` environment variable** — when unset, print usage help
   containing a complete `docker run` example with every real mount flag for
   this project, plus a dynamically generated list of available playbooks
   (`find /ansible -maxdepth 1 -name '*.yml' ...` and the playbooks
   directory), then `exit 1`.
5. Finally:

   ```bash
   echo "Running: ansible-playbook ${PLAYBOOK} $*"
   exec ansible-playbook "/ansible/${PLAYBOOK}" "$@"
   ```

   `exec` propagates signals correctly, and the passthrough arguments let
   callers add `--extra-vars`, `--tags`, `--check` and friends unchanged.

## 3. `.dockerignore`

Exclude VCS and tooling metadata (`.git`, `.gitignore`, `.github`, agent and
IDE directories), documentation and specs (`docs`, `specs`, `**/*.md`, lint
configs), the Dockerfile and structure test config themselves — but **not**
`entrypoint.sh`, which must stay in the image.

Exclude every secret (`**/vault.yml`, `.envrc`) — secrets are never baked into
the image. Exclude generated local state (autogenerated inventories,
`known_hosts` files) that belongs on the host mount instead. Exclude the host
`VERSION` file so a stray copy cannot override the build-time `ARG`. Exclude
caches and OS cruft (`.venv`, `__pycache__/`, `**/*.pyc`, `.DS_Store`).

## 4. `.docker/tests.yaml` — container-structure-test config

Use `schemaVersion: 2.0.0`.

`commandTests` verifying:

- `ansible-playbook --version` matches the pinned major.minor.
- `python3 --version` succeeds — the entrypoint's version check depends on it.
- Every Galaxy collection in `requirements.yml` appears in
  `ansible-galaxy collection list <name>`.

`fileExistenceTests` verifying:

- The entrypoint exists and is executable (`permissions: "-rwxr-xr-x"`).
- `VERSION` and `ansible.cfg` are present.
- Vault and other secret files are **absent** (`shouldExist: false`) — this is
  the regression test that keeps secrets out of the image.
- Build tooling is absent from the runtime stage (`/usr/bin/pip3`,
  `/usr/bin/git`).

## 5. `.docker/README.md` and concept documentation

Keep `.docker/README.md` short: state what the image is and link to the
concept document. Put the substance in the documentation tree, following the
target repository's documentation structure, and cover:

- Prerequisites and the local build command, run from the repository root:
  `docker build -t <image-name> -f .docker/Dockerfile .`
- Full `docker run` examples for each real entry-point playbook, with every
  required `-v` and `-e` flag.
- A table of runtime mounts (host path → container path → mode → purpose).
  State explicitly which mounts must be read-write because plays write state
  back to them, and which are read-only secrets.
- A table of environment variables: required versus optional, with defaults.
- A detached-mode example (`docker run -d --name ...` plus `docker logs -f`)
  for long-running plays.
- What is **not** supported in the container — typically local VM providers
  needing a host CLI or the host container daemon.
- The local structure-test invocation:
  `container-structure-test test --image <image-name> --config .docker/tests.yaml`
- A cosign keyless verification example when CI signs images:

  ```shell
  cosign verify \
    --certificate-identity "https://github.com/<owner>/<repo>/.github/workflows/<workflow>.yml@refs/heads/main" \
    --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
    ghcr.io/<owner>/<image>:latest
  ```

## 6. `.github/workflows/<image-name>-publish.yml`

Triggers: `push` to the default branch and `v*.*.*` tags, `pull_request` to
the default branch, a weekly `schedule` cron, and `workflow_dispatch`. Offset
the cron from any sibling image workflow so they do not compete for runners.

Use three jobs in strict `build → test → push` order. **Do not push from the
build job**: constitution Principle IX requires that no artefact reach a shared
registry before it passes automated tests, and that write-level credentials be
scoped to the publishing job alone.

- **build** — `permissions: contents: read` only. Per-architecture native
  matrix (`ubuntu-24.04`/amd64, `ubuntu-24.04-arm`/arm64). Call
  `docker/metadata-action` **without** a `tags:` input, for labels only, so the
  image carries OCI provenance (`org.opencontainers.image.source`,
  `.revision`, `.created`). Build with `load: true` and `push: false`, stamp
  the version from a `build-args` expression that yields the tag name on tag
  builds and `dev` otherwise, and use a per-architecture GHA layer cache
  scope. Save the image with `docker save | gzip` and upload it as an
  artifact.
- **test** (`needs: build`) — same architecture matrix, no registry
  interaction, identical path for pull requests and pushes. Install
  `container-structure-test` from a **pinned release tag**, never a `/latest/`
  URL, and verify the downloaded binary against the release's published
  SHA256 before moving it into `PATH`. Download the artifact, `docker load`
  it, run the structure tests, and add a smoke test asserting that a bare
  `docker run` prints the usage text and a real playbook name.
- **push** (`needs: [build, test]`, `if: github.event_name != 'pull_request'`)
  — the only job with `packages: write` and `id-token: write`. Load the same
  artifact the test job validated, generate tags here via
  `docker/metadata-action` **with** a `tags:` input, push, then cosign-sign the
  pushed digest.

Tag generation must live in exactly one job. Time-based tag patterns
(`type=schedule`, timestamps) would diverge across job boundaries and must not
be used unless tags are piped between jobs as outputs.

Gate unqualified tags (`latest`, semver) on a single architecture — let amd64
own them and give arm64 only `latest-arm64` — otherwise both matrix legs race
to be the last writer.

Pin actions per constitution Principle IX: SHA pins with a trailing version
comment for every third-party action and for anything in the publish chain;
floating major tags only for `actions/`- and `github/`-org actions without
elevated permissions. Add each new action's `owner/repo@*` entry to the
allow-list documented in `CONTRIBUTING.md`.

## Constraints

- Single `docker run` UX — no docker-compose requirement.
- Secrets are never baked into the image; always host-mounted at runtime,
  read-only wherever the play does not write back.
- The runtime stage stays minimal: only what `ansible-playbook` and SSH-ing
  out to managed hosts need.
- Reuse existing top-level playbook entry points. Do not invent new ones for
  Docker.
- Playbook logic genuinely incompatible with a container target (systemd,
  snap, a display server) is out of scope. This harness runs the *control
  node* in a container; it does not make every play container-compatible.

## Done when

- `docker build -t <image-name> -f .docker/Dockerfile .` succeeds from the
  repository root.
- `docker run --rm <image-name>` without `PLAYBOOK` prints usage plus a real
  list of available playbooks.
- A full `docker run` with all real secret mounts executes the project's
  primary entry-point playbook successfully.
- `container-structure-test test --image <image-name> --config .docker/tests.yaml`
  passes locally.
- The CI workflow is syntactically valid and can be dispatched via
  `workflow_dispatch`.
