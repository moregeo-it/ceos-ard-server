# Deployment

The production host is configured with Ansible from this directory. Nothing here
is run automatically: you run it from your machine over SSH.

```
setup.yml   converge the host around the code (PostgreSQL, service, timers,
            Caddy, TLS, data dirs, backups, swap). It only clones the code on
            a new host, so it cannot change the deployed version.
deploy.yml  move the code to app_version, migrate, restart.
```

## A new host

Prepare by hand:

- Debian 13, whose PostgreSQL (17) the playbooks install; the server needs 16 or newer
- the user `server` with sudo, reachable over SSH with your key
- pixi for that user: `curl -fsSL https://pixi.sh/install.sh | bash`
- the domain pointing at the host, so Caddy can get its certificate. Until then, set
  `ansible_host` in `inventory.yml` to the host's address; Caddy keeps retrying

Then:

1. Install Ansible: `pipx install ansible`, then `ansible-galaxy collection install -r requirements.yml`
2. Copy `host_vars/<host>/vault.yml.example` to `vault.yml` and fill it as it describes
3. Encrypt it: `ansible-vault encrypt host_vars/<host>/vault.yml`
4. Point `ANSIBLE_VAULT_PASSWORD_FILE` at a file holding the vault password, kept out of git
5. Set `app_version` in `host_vars/<host>/vars.yml` to the commit to deploy
6. `ansible-playbook setup.yml -e app_service_started=false`: all but starting the server, without tables yet
7. `ansible-playbook deploy.yml`: installs the dependencies, creates the tables and starts the server

Later runs of `setup.yml` need no flag. Preview them with `--check --diff`, which only
works on a host that is already set up.

## Deploying a new version

```bash
# edit host_vars/<host>/vars.yml: app_version: <commit sha>
ansible-playbook deploy.yml
```

Rollback is the same thing with the previous commit. If the deploy migrated
the database, the old code can't use it: restore the backup the deploy took.
The deploy stops the service and timers, backs up, checks out, installs
dependencies, migrates, restarts and waits for a response.

## Where the data lives

- The database `ceos_ard_server` in the host's PostgreSQL, owned by the role
  `server`. The service connects over the local socket with peer
  authentication, so there is no database password.
- The workspaces, and the toolchains of the builds with their Chromium, in
  `/var/lib/ceos-ard-server`.
- The checkout in `/home/server/ceos-ard-server` holds only the code, `.env`
  (written by `setup.yml`) and `.pixi`.

## Backups

A nightly timer dumps the database to `/var/backups/ceos-ard-server`, plus a
workspace archive on Mondays; every deploy dumps it too. **These copies sit on
the same disk as the originals.** Copying them off the host is still manual,
and remains the single biggest gap in this setup.

To restore the database, as `server`:

```bash
sudo systemctl stop ceos-ard-server
pg_restore --clean --if-exists -d ceos_ard_server /var/backups/ceos-ard-server/db-<stamp>.dump
sudo systemctl start ceos-ard-server
```

## Known gaps

- Off-box backups are not automated.
- No alerting when a maintenance timer fails; failures only land in the journal.
- No firewall yet (`manage_firewall: false`).
- `app_hardening_strict` is off until previews and PDF downloads are verified
  with it.
