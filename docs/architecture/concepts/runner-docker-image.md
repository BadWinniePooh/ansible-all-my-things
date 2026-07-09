# Ansible runner as Docker image

The [.docker/Dockerfile](../../../.docker/Dockerfile) packages this
repository as a portable control node: any playbook runs with only
Docker and the existing secret files on the host — no local Ansible
installation required.

It differs from the
[toolchain Docker image](./toolchain-docker-image.md): the toolchain
image is an interactive development environment that clones the
repository at build time, while the runner image bakes the checkout it
was built from and executes exactly one playbook per `docker run`,
dispatched by
[.docker/entrypoint.sh](../../../.docker/entrypoint.sh).

- [Using the runner Docker image](./runner-docker-image/usage.md)
- [Building and verifying the image](./runner-docker-image/build-test.md)
