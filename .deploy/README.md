# Deployment

The production host is configured with Ansible from this directory. Nothing here
is run automatically: you run it from your machine over SSH.

```
setup.yml         converge the host around the code (service, timers, Caddy,
                  TLS, data dirs, backups, swap). Never touches the
                  git checkout, so it cannot change the deployed version.
deploy.yml        move the code to app_version, migrate, restart.
migrate-data.yml  ONE-TIME: move database and workspaces out of the checkout.
```

## First time

1. Install Ansible: `pipx install ansible`, then `ansible-galaxy collection install -r requirements.yml`
2. Copy `host_vars/<host>/vault.yml.example` to `vault.yml` and fill it from the server's existing `.env`
3. Encrypt it: `ansible-vault encrypt host_vars/<host>/vault.yml`
4. Point `ANSIBLE_VAULT_PASSWORD_FILE` at a file holding the vault password, kept out of git
5. Dry run: `ansible-playbook setup.yml --check --diff`
6. Apply: `ansible-playbook setup.yml`

## Deploying a new version

```bash
# edit host_vars/<host>/vars.yml: app_version: <commit sha>
ansible-playbook deploy.yml
```

Rollback is the same thing with the previous commit. The deploy stops the
service and timers, backs up, checks out, installs dependencies, migrates,
restarts and waits for a response.

## Where the data lives

Until `migrate-data.yml` has run, the database and every workspace sit inside
the git checkout. `app_data_relocated` records which state the host is in, and
everything else derives its paths from it. Nothing in `setup.yml` or
`deploy.yml` ever deletes the checkout or runs `git clean`, so the data is safe
either way, but it stays fragile until it is moved.

## Backups

A nightly timer writes a WAL-safe database copy to `/var/backups/ceos-ard-server`,
plus a workspace archive on Mondays. **These copies sit on the same disk as the
originals.** Copying them off the host is still manual, and remains the single
biggest gap in this setup.

## Known gaps

- Off-box backups are not automated.
- No alerting when a maintenance timer fails; failures only land in the journal.
- `app_hardening_strict` is off, because Playwright's Chromium has not been
  tested against the tighter sandbox.
