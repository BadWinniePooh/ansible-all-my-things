# Quickstart: Hetzner Web Frontend

**Feature**: `017-hcloud-web-frontend` | **Date**: 2026-08-24

Two paths: the user path, from nothing to a provisioned machine, and the developer path,
build and test locally. Both are the target state once this feature is implemented.

## User path

### Prerequisites

- A container runtime.
- A Hetzner Cloud account and an API token with read and write access.
- No checkout of this repository, no Ansible installation.

### 1. Start it

Fetch `compose.yaml` and start it:

```shell
docker compose up -d
```

Then open <http://127.0.0.1:8080>.

The compose file publishes the port to the loopback interface only. This is deliberate: the
interface controls a cloud account and has no login of its own.

> **Security note.** Do not change the port binding to `0.0.0.0` on a machine reachable by
> others. Anyone who can open the page can watch your run output, and the interface is the
> control plane for your Hetzner account.

### 2. Unlock the session

Enter the Hetzner API token and choose a vault password. Neither is stored anywhere: both
live in memory for as long as the container runs, and both are discarded on restart or
after 30 minutes of inactivity. Re-entering them is normal, not a sign that something
broke.

On a fresh installation the vault password is asked for twice, because this first one
creates the encrypted configuration and every later one is checked against it — there is
nothing yet to check this one against, so a typo would silently become the real password.
If that ever happens, the Vault screen's **Discard configuration** action deletes the
unreadable file so setup can start over; everything it held is lost with it.

### 3. Fill in the configuration

Open **Vault** and complete the form. It is generated from the project's own configuration
template, so it always matches what the automation expects. At least one desktop user is
required.

The SSH key fields are shown read-only — the next step fills them in.

### 4. Generate and register an SSH key

Open **SSH key** and generate. This creates a keypair, registers the public half with your
Hetzner account under the configured name, and records it in the encrypted configuration.
The private key never leaves the container and is never shown.

> **Before rotating later.** Machines you have already provisioned trust the previous key.
> Rotating makes them unreachable to the automation. Rotate when no machines are live, or
> re-key existing hosts afterwards.

### 5. Provision a machine

Open **Create VM** and choose four things:

| Choice | Options | Notes |
|---|---|---|
| Profile | `basic`, `desktop` | `basic` is a headless development machine; `desktop` adds a graphical environment |
| Server size | `cx23`, `cx33`, `cx43` | Shown with vCPU, memory, disk and monthly cost |
| Location | `hel1`, `fsn1`, `nbg1`, `ash`, `hil`, `sin` | Helsinki, Falkenstein, Nuremberg, Ashburn, Hillsboro, Singapore |
| Image | Ubuntu long-term-support images | Free text accepted for anything else |

The machine's name is assigned automatically from the name pool — the first free entry in
order. Edit the pool under **Name pool** if you want different names or more of them.

The interface shows the exact command before running it. Start the run and watch the output
live.

### 6. Manage what you created

The dashboard lists your machines. Each row offers:

- **Configure** — re-apply the software profile. Running it twice reports no changes.
- **Destroy** — remove the machine. You must type its name to confirm; the name then
  returns to the pool.

Only one run happens at a time. A second request is refused with an explanation rather than
queued.

### Upgrading

```shell
docker compose pull && docker compose up -d
```

Your configuration, machine records, name pool, presets and SSH key live in a named volume
and survive the upgrade. Only the two session secrets need re-entering.

If the new image ships changed defaults, the dashboard shows a banner offering to refresh
them. Refreshing never touches your configuration, machine records, name pool or presets.

## Developer path

### Build locally

From a checkout, using the build compose file:

```shell
docker compose -f compose.build.yaml up -d --build
```

Or directly:

```shell
docker build -t ansible-web -f .docker/Dockerfile.web .
docker run --rm -p 127.0.0.1:8080:8080 -v aamt-inv:/ansible/inventories ansible-web
```

The image is self-contained. It does not depend on the `ansible-runner` image, so no other
image needs building or pulling first.

### Run the tests

Application tests:

```shell
python -m pytest tests/webui
```

Image structure tests:

```shell
container-structure-test test --image ansible-web --config .docker/tests-web.yaml
```

The structure tests assert both what must be present and what must be absent — the dropped
collections, the AWS SDK, the Molecule stack, any build tooling, and any configuration
file. The absence assertions are the regressions that matter: they are what keeps the image
minimal and keeps secrets out of it.

### Verify the two open mechanisms

Two design assumptions are unverified and must be settled before the layout is final. Both
are tracked; see [research.md](./research.md) sections 7 and 8.

Hidden directory inside the inventory directory:

```shell
mkdir -p inventories/.webui
echo 'not: [valid, inventory' > inventories/.webui/probe.yml
ansible-playbook playbooks/create-vm.yml --check
```

If the run starts without an inventory parse error, Ansible is skipping the hidden
directory as assumed. If it fails, add the directory to `inventory_ignore_patterns` in
`ansible.cfg`, which already carries `_known_hosts`. Remove the probe file afterwards.

Per-Dockerfile ignore support: build with `.docker/Dockerfile.web.dockerignore` present and
confirm the excluded paths are absent from the resulting image. If unsupported, extend the
shared root `.dockerignore` instead — but only with exclusions that are also safe for the
runner image build, since that file governs both.

## What is not supported

- The `tart` and `docker` providers. Both need a host-level tool or the host's own
  container daemon, neither of which exists inside this container.
- The AWS provider and the Windows profile. They need a second credential model and are out
  of scope.
- Backup and restore.
- Multiple simultaneous runs.
- Dry-run mode.
- Run history beyond the current session, which follows from run output never being
  persisted.
