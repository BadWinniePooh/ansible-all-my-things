# Hetzner web frontend

The [webui/](../../../webui/) application, packaged by
[.docker/Dockerfile.web](../../../.docker/Dockerfile.web), serves a
server-rendered web interface for this repository's Hetzner Cloud lifecycle —
provision, apply a software profile, destroy — by driving the existing
playbooks (`playbooks/create-vm.yml`, `playbooks/configure-profile.yml`,
`playbooks/destroy-vm.yml`) as a subprocess. It does not reimplement them.

It differs from both the
[toolchain Docker image](./toolchain-docker-image.md) and the
[runner Docker image](./runner-docker-image.md): those package this
repository's *command-line* automation for interactive development or
scripted `docker run` invocations. This image is fully self-contained and
independent of the runner image — see
[ADR-005](../decisions/005-web-frontend-for-hcloud-provisioning.md) for why —
and is driven entirely through a browser, with no Ansible knowledge or
checkout required on the operator's side.

The design and every functional requirement are specified in
[specs/017-hcloud-web-frontend/](../../../specs/017-hcloud-web-frontend/);
this page and its children are the durable, post-implementation summary.

- [Using the web frontend](./web-frontend/usage.md)
- [Building and verifying the image](./web-frontend/build-test.md)
