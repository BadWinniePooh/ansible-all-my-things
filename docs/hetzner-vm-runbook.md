# Provisioning a Hetzner Cloud VM — Session Runbook

Personal runbook distilled from a live provisioning session (WSL on Windows,
repo checked out under `/mnt/c/...`). Steps are in the order they actually
had to happen, including the detours. Skip a step if you've already
confirmed it works.

## 0. One-time environment fixes (WSL + `/mnt/c` checkout)

These two issues only bite because the repo lives on the Windows-mounted
drive under WSL. Fix once, they stay fixed.

### A. `ansible.cfg` silently ignored ("world writable directory")

WSL mounts `/mnt/c` as world-writable by default; Ansible refuses to
auto-load a config file sitting in such a directory, so `./inventories`
never resolves and every `vault_*` variable ends up silently undefined.

```shell
echo 'export ANSIBLE_CONFIG="$(pwd)/ansible.cfg"' >> .envrc
```

(Rolled into the `.envrc` template in step 2 below — just confirming why
it's there.)

### B. Vault password script fails with "No such file or directory"

`scripts/echo-vault-password-environment-variable.sh` was checked out with
CRLF line endings, so its shebang becomes `#!/bin/sh\r` and the kernel
can't resolve the interpreter.

```shell
sed -i 's/\r$//' scripts/echo-vault-password-environment-variable.sh
chmod +x scripts/echo-vault-password-environment-variable.sh
```

Optional repo-wide fix so it doesn't recur on the next checkout:

```shell
git config core.autocrlf input
git add --renormalize .
git status   # review before committing anything
```

## 1. Install dependencies

```shell
pip3 install -r requirements.txt
ansible-galaxy collection install -r requirements.yml
```

Skipping this is what caused the later `couldn't resolve module/action
'win_shell'` failure — `configure-profile.yml` statically parses the whole
playbook set, including the Windows role, even when the target host is
Linux.

## 2. `.envrc` (repo root, gitignored)

No template ships in the repo. Create it yourself:

```shell
export ANSIBLE_VAULT_PASSWORD="YOUR_VAULT_PASSWORD_HERE"
export HCLOUD_TOKEN="YOUR_HCLOUD_API_TOKEN_HERE"
export ANSIBLE_CONFIG="$(pwd)/ansible.cfg"
```

```shell
chmod 600 .envrc
direnv allow          # if direnv is installed
# otherwise, in every new shell:
. .envrc
```

`ANSIBLE_VAULT_PASSWORD` feeds
`scripts/echo-vault-password-environment-variable.sh` (wired via
`ansible.cfg`'s `vault_password_file`) — no `--ask-vault-pass` or
`--vault-password-file` flag needed on the command line.

## 3. SSH key — generate, then register with Hetzner

```shell
ssh-keygen -t ed25519 -f ~/.ssh/id_ansible_ed25519 -C "$(whoami)@$(hostname)"
```

Register the public key in the Hetzner Cloud console:

1. <https://console.hetzner.cloud/> → your project
2. Security → SSH Keys → Add SSH Key
3. Paste the output of `cat ~/.ssh/id_ansible_ed25519.pub`
4. Name it exactly what you'll put in the vault in the next step
   (e.g. `id_ansible_ed25519`) — the playbook looks the key up by name,
   not by fingerprint. A mismatch here is what produced
   `resource (ssh_key) does not exist`.

## 4. Vault secrets

```shell
cp -v inventories/group_vars/all/vault-template.yml inventories/group_vars/all/vault.yml
ansible-vault encrypt inventories/group_vars/all/vault.yml
ansible-vault edit inventories/group_vars/all/vault.yml
```

Fields to fill:

| Var | What goes in it |
| --- | --- |
| `vault_my_ansible_user_name` / `vault_my_ansible_user_password` | Temporary sudo user `setup-users.yml` creates |
| `vault_my_ssh_key_name` | Must match the name you gave the key in the Hetzner console (step 3) |
| `vault_my_ssh_public_key` | Full contents of `~/.ssh/id_ansible_ed25519.pub` — public key, never the private one |
| `vault_my_additional_ssh_public_keys` | Optional list of further public keys authorized for every account; `[]` when the key above is yours |
| `vault_gnome_keyring_password` | Desktop keyring unlock password |
| `vault_windows_admin_password` | Only used for AWS Windows instances |
| `vault_desktop_users` | List of `{name, password, exa_api_key}` — login accounts for the desktop session, at least one required. `exa_api_key` just needs to be a non-empty placeholder if you don't use Exa web search. |

Sanity check without dumping secrets to a terminal you'll scroll past:

```shell
ansible-vault view inventories/group_vars/all/vault.yml
```

## 5. Create the VM

Load the key into the agent first — `wait_for_connection` during creation
needs it, not just later manual SSH:

```shell
ssh-add ~/.ssh/id_ansible_ed25519
ssh-add -l              # confirm it's listed

ansible-playbook playbooks/create-vm.yml --extra-vars "provider=hcloud"
```

If it fails with `resource_unavailable` / `error during placement`, that's
Hetzner-side capacity, not a config problem — retry with a different
location or type:

```shell
ansible-playbook playbooks/create-vm.yml --extra-vars "provider=hcloud" --extra-vars "hcloud_server_location=nbg1"
```

Valid locations: `hel1`, `fsn1`, `nbg1`, `ash`, `hil`, `sin`.

On success, note the hostname printed at the end (`edoras` in this
session) — you'll need it for every command below.

## 6. Apply the profile (separate, manual step)

`create-vm.yml` only creates the server — it does not chain into setup.
Run this yourself, right after creation:

```shell
ansible-playbook playbooks/configure-profile.yml
```

No `--extra-vars` needed; it targets whatever host the previous step just
registered in the dynamic inventory. Takes ~10–15 minutes. Default output
already streams task-by-task — that's your progress indicator; add `-v`
for more detail.

## 7. Connecting via SSH

Root, immediately after creation (before step 6 finishes):

```shell
ssh root@<ip>
```

Desktop user, once step 6 completes — use a name from
`vault_desktop_users`:

```shell
HOST=edoras
inv=$(ansible-inventory --host "$HOST")
ssh -p "$(echo "$inv" | jq -r .ansible_port)" YOUR_DESKTOP_USER@"$(echo "$inv" | jq -r .ansible_host)"
```

(The `-p` flag only matters for Docker; harmless to always include it.)

### "Permission denied (publickey)" in a brand-new terminal

`ssh-add` only loads the key into the agent for the current shell's agent
session. A fresh terminal either has no agent running, or has one that
never got the key — so a plain `ssh` there has nothing to offer the
server.

Fix per new terminal (quick):

```shell
ssh-add ~/.ssh/id_ansible_ed25519
```

Durable fix (do this once, works in every terminal from then on, no
`ssh-add` needed) — point SSH straight at the key file via
`~/.ssh/config`:

```text
Host edoras
    HostName <ip>
    User YOUR_DESKTOP_USER
    IdentityFile ~/.ssh/id_ansible_ed25519
    IdentitiesOnly yes
```

Then just:

```shell
ssh edoras
```

If the key itself has a passphrase, `ssh` will prompt for it directly
(no agent required) unless you use `ssh-agent` + keychain to persist one
agent across all WSL terminals.

RDP: any RDP client, host `<ip>`, log in as a desktop user.

## 8. Destroy when done

```shell
ansible-playbook playbooks/destroy-vm.yml --extra-vars provider=hcloud --extra-vars hostname=edoras
```

Monitor the Hetzner billing dashboard — nothing here auto-expires.

## Errors seen this session, at a glance

| Symptom | Cause | Fix |
| --- | --- | --- |
| `'vault_my_ssh_key_name' is undefined` | `ansible.cfg` ignored (`/mnt/c` world-writable) | §0-A |
| `No such file or directory` running the vault password script | CRLF line endings on checkout | §0-B |
| `resource (ssh_key) does not exist: <name>` | Key not registered in Hetzner console, or name mismatch | §3 |
| `error during placement (resource_unavailable, ...)` | Hetzner out of capacity for that type/location | §5, retry elsewhere |
| `timed out waiting for ping module test: ... Permission denied` | Key not loaded into `ssh-agent` before creation | §5, `ssh-add` first |
| `couldn't resolve module/action 'win_shell'` | `ansible-galaxy collection install -r requirements.yml` never run | §1 |
| `Permission denied (publickey)` in a new terminal | Agent not loaded in that shell | §7 |
