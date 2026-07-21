<!-- SPDX-License-Identifier: MIT-0 -->
# obsidian

Ansible role that installs [Obsidian](https://obsidian.md) — a local-first
Markdown knowledge base app — from the official GitHub release `.deb`.

See [DESIGN.md](DESIGN.md) for non-obvious decisions, including the amd64-only
constraint and the idempotency model.

## Boundary

The role installs the `obsidian` package only. It does not configure vaults,
plugins, sync, or per-user application data — those are user-level operations
outside this role's scope.

## Requirements

- Ansible 2.19+
- Linux x86_64 (amd64) — see [DESIGN.md](DESIGN.md#architecture-support) for why
  arm64 is not supported
- Internet access to `github.com` from the target host

## Role Variables

All variables have safe defaults. None are required from the caller.

| Variable | Default | Description |
| --- | --- | --- |
| `obsidian_version` | `"v1.12.7"` | Pinned Obsidian release tag (`v`-prefixed). |
| `obsidian_sha256_amd64` | *(see defaults)* | SHA-256 of the amd64 `.deb` for `obsidian_version`. |

Both pinned values are updated together by
`playbooks/update-versions/perform-updates.yml`.

## Dependencies

None. See `meta/main.yml`.

## Example Playbook

```yaml
- hosts: desktop
  become: true
  roles:
    - role: obsidian
      tags: not-supported-on-arm64
```

## Molecule testing

```shell
cd roles/obsidian
molecule test
```

## License

MIT-0
