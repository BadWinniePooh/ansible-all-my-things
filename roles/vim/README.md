<!-- SPDX-License-Identifier: MIT-0 -->

# vim

Ansible role that installs the Vim editor on Ubuntu Linux via apt.

## Requirements

- Ansible 2.19+
- Ubuntu 24.04 target host

## Role Variables

None.

## Dependencies

None. See `meta/main.yml` for details.

## Example Playbook

```yaml
- hosts: all
  roles:
    - role: vim
```

## What This Role Does

Installs the `vim` package via apt, unpinned (whatever version the
configured apt repositories currently serve).
