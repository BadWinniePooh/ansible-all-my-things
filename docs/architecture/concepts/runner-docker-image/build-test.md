# Building and verifying the runner Docker image

[Container Structure Tests](https://github.com/GoogleContainerTools/container-structure-test)
verify correctness of the Dockerfile.

## Building the runner Docker image locally

Run from the repository root (the build context is the whole
repository, filtered by [.dockerignore](../../../../.dockerignore)):

```shell
docker build -t ansible-runner -f .docker/Dockerfile .
```

## Running container structure tests locally

Follow the
[Container Structure Tests](https://github.com/GoogleContainerTools/container-structure-test)
documentation to install the `container-structure-test` command.

Run from the repository root:

```shell
container-structure-test test --image ansible-runner:latest --config .docker/tests.yaml
```

## Verify the published image signature

Images pushed by CI are signed with cosign (keyless, GitHub OIDC):

```shell
cosign verify \
  --certificate-identity "https://github.com/<owner>/ansible-all-my-things/.github/workflows/docker-runner-publish.yml@refs/heads/main" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  ghcr.io/<owner>/ansible-runner:latest
```

## More details in pipeline

The GitHub Action building the Docker image shows details:
[.github/workflows/docker-runner-publish.yml](../../../../.github/workflows/docker-runner-publish.yml).
It follows the same build → test → push pipeline as the toolchain
image: no artefact reaches the registry without passing the structure
tests, and only the push job holds write-level permissions.
