# Work with a Virtual Machine

## Log in as the desktop user

The username of the desktop user is configured in
`/inventories/group_vars/all/vars.yml`. Here, we assume it is `galadriel`.

The section [Important Concepts](./important-concepts.md) provides more
information about the different users and their purposes.

Log in with (replace `tatooine` with the hostname shown by `ansible-inventory --graph`):

```shell
HOST=tatooine
inv=$(ansible-inventory --host "$HOST")
ssh -p "$(echo "$inv" | jq -r .ansible_port)" galadriel@"$(echo "$inv" | jq -r .ansible_host)"
```

The `-p` flag is only required for Docker, which maps SSH to a non-standard
port. For Hetzner Cloud, AWS, and Tart it can be omitted. The command above
works for all providers without modification.

To also forward the RDP port:

```shell
HOST=tatooine
inv=$(ansible-inventory --host "$HOST")
ssh -L 3389:localhost:3389 -p "$(echo "$inv" | jq -r .ansible_port)" galadriel@"$(echo "$inv" | jq -r .ansible_host)"
```

Now you can open an RDP client like Remmina, Windows App or Remote Desktop
to connect to the server at `localhost` with user `galadriel`.

## GNOME keyring password is login password

The GNOME keyring needs to be unlocked when you launch an application using
it, e.g. Visual Studio Code. The password is configured in
[/inventories/group_vars/all/vault.yml](../inventories/group_vars/all/vault.yml).

## Backup and restore

See [Backup and Restore](./backup-restore.md) for backing up the desktop
user's working directory and restoring it later.

---

Previous: [Create a Virtual Machine](./create-vm.md)
Next: [Backup and Restore](./backup-restore.md)
