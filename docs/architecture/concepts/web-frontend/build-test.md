# Building and verifying the web frontend image

## Building the image locally

Run from the repository root (the build context is the whole repository,
filtered by [Dockerfile.web.dockerignore][web-dockerignore] — the
per-Dockerfile ignore-file convention, distinct from the root
[.dockerignore](../../../../.dockerignore) that filters the runner image's
build):

```shell
docker build -t ansible-web -f .docker/Dockerfile.web .
```

## Running the application tests

```shell
pip install -r requirements-web-dev.txt
python -m pytest tests/webui
```

`ansible-vault`-dependent tests (`tests/webui/test_vault.py`) and
`ssh-keygen`-dependent tests (`tests/webui/test_sshkeys.py`) skip themselves
when the respective binary is not on `PATH` — both run cleanly in CI (Linux
runners) and in the image's own build environment; a Windows development
machine without either binary on `PATH` sees those tests skipped rather than
failed.

## Running container structure tests locally

Follow the
[Container Structure Tests](https://github.com/GoogleContainerTools/container-structure-test)
documentation to install the `container-structure-test` command, then run
from the repository root:

```shell
container-structure-test test --image ansible-web:latest --config .docker/tests-web.yaml
```

The structure tests assert both what must be present (the pinned
`ansible-playbook` version, the kept collections, the pristine defaults
snapshot) and what must be absent — the dropped collections, `pip`, `gcc`,
`git`, the AWS/Windows-only task files and roles, and the vault secrets file.
The absence assertions are the regressions that matter: they are what keeps
the image minimal (spec.md SC-007) and keeps personal credentials out of it
(SC-006).

## Verify the published image signature

Images pushed by CI are signed with cosign (keyless, GitHub OIDC), the same
scheme as the runner image:

```shell
cosign verify \
  --certificate-identity "https://github.com/<owner>/ansible-all-my-things/.github/workflows/docker-web-publish.yml@refs/heads/main" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  ghcr.io/<owner>/ansible-web:latest
```

## More details in pipeline

[.github/workflows/docker-web-publish.yml](../../../../.github/workflows/docker-web-publish.yml)
mirrors the runner image's pipeline shape: a native `linux/amd64` and
`linux/arm64` build matrix, a test job running both the structure tests and a
smoke test (start the container, confirm the dashboard responds), and a push
job — gated on both, non-pull-request only — that assembles the
multi-architecture manifest, bakes OCI provenance labels and signs with
cosign. Only the push job holds `packages: write` and `id-token: write`.

[web-dockerignore]: ../../../../.docker/Dockerfile.web.dockerignore
