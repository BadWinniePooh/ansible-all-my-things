# syntax=docker/dockerfile:1

# Portable ansible-runner image: bakes this repository checkout so any
# playbook runs with only Docker + host-mounted secrets — no local Ansible
# install required. Distinct from the .devcontainer "ansible-toolchain"
# image, which is an interactive toolchain that clones the repo at build
# time; this image ships the checkout it was built from and dispatches
# playbooks via .docker/entrypoint.sh.
#
# Build (from repo root):
#   docker build -t ansible-runner -f .docker/Dockerfile .
#
# Two-stage build: builder installs pipx/git and produces /root/.local
# (ansible pipx venv) and /root/.ansible (galaxy collections); the runtime
# stage copies only those artifacts. BuildKit cache mounts keep apt lists
# outside image layers for fast rebuilds.

FROM ubuntu:26.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        openssh-client \
        pipx \
        python3 \
        python3-pip \
        python3-venv

ENV PATH="/root/.local/bin:$PATH"

# Pin ansible-core explicitly (never float). requirements.txt declares
# ansible-core>=2.19.0; the pin below must stay within that range.
RUN pipx install ansible-core==2.21.1

# Copy requirements files individually so each layer caches independently
# of code changes.
COPY requirements.txt /tmp/requirements.txt
RUN pipx inject ansible-core -r /tmp/requirements.txt

COPY requirements.yml /tmp/requirements.yml
RUN ansible-galaxy collection install -r /tmp/requirements.yml


FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive

# Runtime stays minimal: only what ansible-playbook and SSH-ing out to
# managed hosts need. No compilers, pip, pipx, or git.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        openssh-client \
        python3

# Copy ansible (pipx venv + wrappers) and galaxy collections
COPY --from=builder /root/.local /root/.local
COPY --from=builder /root/.ansible /root/.ansible

ENV PATH="/root/.local/bin:$PATH"
ENV ANSIBLE_CONFIG=/ansible/ansible.cfg

RUN mkdir -p /root/.ssh \
    && chmod 700 /root/.ssh

WORKDIR /ansible

# Whole repo, filtered by the root .dockerignore (no secrets, no docs,
# no VCS metadata).
COPY . /ansible

# Normalize line endings on executed scripts: a checkout on a Windows
# host (core.autocrlf) yields CRLF, and a CRLF shebang breaks the
# interpreter lookup inside the container.
RUN find /ansible -name '*.sh' -exec sed -i 's/\r$//' {} + \
    && chmod +x /ansible/.docker/entrypoint.sh /ansible/scripts/*.sh

# Declared after the repo COPY so changing the version does not bust the
# earlier cache layers.
ARG IMAGE_VERSION=dev
RUN echo "${IMAGE_VERSION}" > /ansible/VERSION

ENTRYPOINT ["/ansible/.docker/entrypoint.sh"]
