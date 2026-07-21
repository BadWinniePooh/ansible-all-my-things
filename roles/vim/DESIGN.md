<!-- SPDX-License-Identifier: MIT-0 -->

# vim role — Design Notes

## No pinned version

The role installs the `vim` apt package unpinned (`state: present`),
mirroring the `ruby` role's precedent. A pinned version was considered
and rejected: apt version strings for distro metapackages are
architecture- and release-specific (e.g. `2:9.1.0016-1ubuntu7`), making a
pin brittle across the amd64/arm64 targets this repository provisions.
The version-update-playbook mechanism under `playbooks/update-versions/`
targets tools pinned via `defaults/main.yml` fetched from upstream
releases (e.g. GitHub); it does not apply here because no version is
pinned. No requirement drives reproducible vim versions, so YAGNI
(Principle IV) favors the unpinned apt-latest install.
