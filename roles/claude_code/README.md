<!-- SPDX-License-Identifier: MIT-0 -->
# claude_code

Ansible role that installs [Anthropic's Claude Code](https://claude.ai/code) CLI
on Linux for each desktop user, verifies binary integrity, installs the
[oh-my-claudecode](https://github.com/Yeachan-Heo/oh-my-claudecode) and
[caveman](https://github.com/JuliusBrussee/caveman) Claude Code plugins,
symlinks skills from an [ai-agent-workspace](https://github.com/eudicy/ai-agent-workspace)
clone (provisioned by the `ai_agent_workspace` role) into `~/.claude/skills`,
and configures the [Exa](https://exa.ai) MCP server for web search.

`beads`, `specify_cli`, and the ai-agent-workspace clone are cross-harness
CLI tools installed by their own single-purpose roles, not by `claude_code`.
Those roles must run before this one (`ai_agent_workspace` specifically,
since the retained symlink task depends on its clone already existing). The
`rtk` role installs only the `rtk` binary; `rtk init -g` and the `omc` CLI +
oh-my-claudecode source clone are installed here instead — see `DESIGN.md`
for why.

## Requirements

- Ansible 2.19+
- `git` present on target hosts (used for Claude Code plugin marketplace and
  oh-my-claudecode source clones)
- The `ai_agent_workspace` role applied first, providing
  `~/Documents/Cline/ai-agent-workspace`
- The `rtk` role applied first, providing `/usr/local/bin/rtk`
- The `nodejs` role applied first, providing a system-wide Node.js LTS
  install (`/usr/local/bin/node`/`npm`)
- Internet access from target hosts (downloads Claude Code, plugins, and the
  omc CLI)

## Role Variables

| Variable | Default | Description |
| --- | --- | --- |
| `claude_code_manifest_base_url` | Google Storage URL | Base URL for the Claude Code release manifest |
| `claude_code_platform_map` | `{x86_64: linux-x64, aarch64: linux-arm64}` | Maps `ansible_architecture` to Claude Code platform string |
| `desktop_user_names` | *(required)* | List of local usernames to install Claude Code for |
| `desktop_users` | *(required)* | List of user objects with `name`, `password`, and `exa_api_key` |

## Dependencies

`rtk`, `nodejs`, `ai_agent_workspace`. See `meta/main.yml` for details.

## Example Playbook

```yaml
- hosts: desktops
  roles:
    - role: claude_code
      vars:
        desktop_user_names:
          - alice
          - bob
        desktop_users:
          - name: alice
            password: "{{ vault_alice_password }}"
            exa_api_key: "{{ vault_alice_exa_api_key }}"
          - name: bob
            password: "{{ vault_bob_password }}"
            exa_api_key: "{{ vault_bob_exa_api_key }}"
```

## What This Role Does

1. Verifies the target architecture is supported
2. Downloads the binary for the pinned `claude_code_version` directly from
   the manifest-derived URL for each user (skipped if already installed),
   verifying it against the pinned per-platform sha256 checksum BEFORE
   placement via Ansible-native SHA256 checking
3. Adds `~/.local/bin` to each user's `PATH` via `.bashrc`
4. Symlinks skills from the `ai_agent_workspace` role's clone into
   `~/.claude/skills/` (idempotent; re-provision re-links)
5. Registers and installs the oh-my-claudecode Claude Code plugin
6. Registers and installs the caveman Claude Code plugin
7. Registers the claude-plugins-official marketplace and installs the
   context7 plugin
8. Clones the oh-my-claudecode source repo and installs the `omc` CLI
   (`oh-my-claude-sisyphus`, skipped if already installed)
9. Initializes rtk globally (`rtk init -g`) for each desktop user
10. Merges required keys (agent-team env vars, auto-update-disable env vars,
    rtk/bd-guard hooks) into `settings.json` — see `DESIGN.md` for why
    auto-update is disabled
11. Configures the [Exa](https://exa.ai) MCP server (`exa`) with the user's API
    key (skipped if already registered)

## Post-install Manual Steps

After running the playbook, each user must complete initial setup interactively.
The role installs the plugins but does not configure them — that requires a
running Claude Code session. To simplify the process, the role copies a prompt
file to `~/Documents/Cline/setup-omc-prompt.md` to automate the setup steps.

1. `cd ~/Documents/Cline`
2. Run `claude` to open a Claude Code session.
3. Copy paste this prompt verbatim: `@setup-omc-prompt.md`
   The prompt instructs Claude to:
   - spawn a second Claude Code session in a new tmux pane
   - run `omc setup` in that pane (global install, suggested defaults, no MCP,
     caveman badge in the statusline)
   - restart the pane session and run `omc doctor` to verify the result

   The Exa MCP server is pre-configured by the role and does not need to be set
   up through `omc setup`. If the GitHub MCP is desired, a GitHub personal access
   token is required and must be provided when prompted.
4. Run `/caveman` to activate caveman mode.

## License

MIT-0
