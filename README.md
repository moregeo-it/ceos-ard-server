# CEOS-ARD Server

A FastAPI-based server application for managing CEOS-ARD (Committee on Earth Observation Satellites - Analysis Ready Data) workspaces, enabling collaborative editing, preview generation, and Pull Request workflows for CEOS-ARD documentation.

## 🚀 Features

### Authentication & Authorization

- **OAuth Integration**: Support for GitHub and Google OAuth providers
- **Session Cookie**: The JWT lives in an HttpOnly cookie, never in the browser's storage or in URLs
- **Session Renewal**: GitHub sessions are renewed with GitHub's expiring tokens, without a new login
- **Logout**: Revokes the provider token and ends every session of the user, in all browsers
- **Idle Sessions**: The tokens of users idle for a week are revoked
- **User Management**: Automatic user creation and profile management
- **GitHub-Only Workspaces**: All workspace features exclusively available to GitHub users

### Workspace Management (GitHub Users Only)

- **Git-based Workspaces**: Create isolated workspaces with repository forking
- **CRUD Operations**: Full workspace lifecycle management (create, read, update, delete)
- **Sync with GitHub**: Changes on the fork's branch are brought into the workspace, with local commits
  replayed on top (no merge commits); a conflict is reported, never left half-done
- **Workspace Archival**: Archive workspaces with automatic cleanup after 1 month
- **Status Tracking**: Monitor workspace and Pull Request status
- **Owner-only Changes**: Only the owner changes a workspace; collaborators get read-only access
- **GitHub Authentication Required**: All workspace endpoints require GitHub OAuth

### Sharing & Realtime

- **Collaborators**: Invite GitHub users by username, read-only
- **Share Links**: Anyone with the link and a GitHub login joins as a read-only collaborator; links can expire
- **Live Updates**: Viewers get every save, commit, sync and status change over a WebSocket as it happens
- **Viewer Access**: Collaborators read and search the files, see the preview, and download PDF and Word

### File Operations

- **File Management**: Create, read, update, delete files and folders
- **Content Search**: Search through workspace files
- **Diff Tracking**: View changes and file differences
- **File Upload**: Support for file uploads and content storage

### PFS (Product Family Specification) Management

- **PFS Discovery**: List available PFS types from CEOS-ARD repository
- **PFS Creation**: Create and manage PFS documents within workspaces
- **PFS IDs**: 2 to 10 capital letters or digits (e.g. `NRB`), like every official PFS. No dashes or other
  characters: IDs become file names and CLI arguments, and `-` joins them in build outputs. This may be
  widened if CEOS needs it.
- **Template Integration**: Work with standardized PFS templates

### Preview & Build System

- **Document Preview**: Generate HTML previews of CEOS-ARD documents
- **Pandoc Integration**: Convert markdown to various formats
- **Cross-reference Support**: Handle document cross-references

### Pull Request Integration

- **Proposal Workflow**: Propose changes via GitHub Pull Requests
- **Status Monitoring**: Track PR status and updates
- **Collaborative Review**: Support for collaborative document review

## 🛠️ Technology Stack

- **Backend Framework**: FastAPI 0.138+ on Uvicorn (WebSockets via `websockets`)
- **Database**: SQLite in WAL mode (file-based, zero-config)
- **ORM**: SQLAlchemy 2.0+
- **Migrations**: Alembic
- **Authentication**: Authlib + OAuth2
- **Session Management**: JWT in an HttpOnly cookie; Starlette SessionMiddleware for the OAuth state
- **Package Management**: Pixi (conda-forge)
- **Document Processing**: Pandoc + Pandoc-crossref
- **Git Operations**: pygit2
- **Browser Automation**: Playwright (for preview generation)
- **Code Quality**: Ruff (linting & formatting), pre-commit

## 📋 Prerequisites

- **Pixi** (for dependency management; it also installs Python)
- **Git** (for repository operations)
- **sqlite3** command-line tool (for backups)
- **GitHub OAuth App** (for authentication; a Google OAuth App is optional)

## 🔧 Installation

### 1. Clone the Repository

```bash
git clone https://github.com/moregeo-it/ceos-ard-server.git
cd ceos-ard-server
```

### 2. Install Dependencies

```bash
# Install pixi if you haven't already
curl -fsSL https://pixi.sh/install.sh | bash

# Install project dependencies
pixi install

# Install Chromium for PDF rendering
pixi run install-browser
```

### 3. Environment Configuration

Create an `.env` file based on the [.env.example](./.env.example) file, e.g.
by copying it:

```bash
cp .env.example .env
```

Update the `.env` file according to your needs.
The following properties should be changed at least:

- `GITHUB_CLIENT_ID` (for OAuth login)
- `GITHUB_CLIENT_SECRET` (for OAuth login)
- `GITHUB_SERVICE_TOKEN` (for automated maintenance tasks - see setup below)
- `SECRET_KEY` (for JWT token signing)
- `ENVIRONMENT` (development/production)

The editor and the API must run on the **same site**, i.e. under the same registrable domain
(`editor.ceos-ard.moregeo.it` and `api.ceos-ard.moregeo.it`; in development both on `localhost`).
The session cookie is then first-party, so browsers don't block it as a third-party cookie.
`CORS_ORIGINS` must list the exact editor origin (e.g. `http://localhost:5173`), no wildcards.

For local development over plain HTTP in **Safari**, set `SESSION_COOKIE_SECURE=false`: Safari drops
`Secure` cookies on `http://localhost`. Chrome and Firefox accept them there, so the default works.
Only for `ENVIRONMENT=development`; the server refuses to start with it otherwise.

### 4. OAuth Setup

#### GitHub OAuth App (Required)

1. Go to GitHub Settings → Developer settings → OAuth Apps
2. Create a new OAuth App with:
  - **Application name**: CEOS-ARD
  - **Homepage URL**: `http://localhost:8000`
  - **Authorization callback URL**: `http://localhost:8000/auth/callback/github`
3. Copy the Client ID and Client Secret to your `.env` file
4. Turn on expiring tokens: in the app's settings, open **Optional features** and click **Opt-in** next to the token
  expiration feature.

In production, both URLs use `SERVER_URL`, e.g. `https://api.ceos-ard.moregeo.it/auth/callback/github`.

With expiring tokens, GitHub issues an 8-hour access token with a refresh token, and the server renews the session without
a new login. The server also requests the `offline_access` scope, which asks for the same on every login.

**Note**: GitHub authentication is mandatory for workspace features. All workspace operations require GitHub OAuth.

#### Google OAuth App (Optional - Future Use)

1. Go to Google Cloud Console → APIs & Services → Credentials
2. Create OAuth 2.0 Client ID with:
  - **Application type**: Web application
  - **Authorized redirect URIs**: `http://localhost:8000/auth/callback/google`
3. Copy the Client ID and Client Secret to your `.env` file

**Note**: Google authentication is currently not used for workspace features. Reserved for potential future functionality.

#### GitHub Service Token (Required for Automated Tasks)

The PR status checker needs a GitHub service token:

1. **Create a GitHub bot/service account** (recommended) or use your personal account:
  - Create a new GitHub account (e.g., `ceos-ard-bot`)

2. **Generate a Fine-Grained Personal Access Token**:
  - Login to the bot/service account
  - Go to Settings → Developer settings → Personal access tokens → **Fine-grained tokens**
  - Click "Generate new token"
  - Set token name: "CEOS ARD PR Status Checker"
  - Expiration: The desired expiration time. Make sure to set this time in the config to enable a reminder for renewal.
  - Repository access: Select the repository that is conifgured as target for PRs (e.g. ceos-org/ceos-ard).
  - Permissions needed:
    - **Pull requests**: Read-only access (to fetch PR status)
    - No other permissions required for public repositories
  - Click "Generate token"
  - **Copy the token immediately** - you won't see it again!
  - **Note the expiration date** - you'll need it for the next step

3. **Add to `.env` file**:
  ```bash
  GITHUB_SERVICE_TOKEN=xxxxxxxxxxxxxxxxxxxxx
  GITHUB_SERVICE_TOKEN_EXPIRES_AT=2027-03-15  # Use the expiry date from step 2
  ```

**Alternative**: For development/testing, you can use a personal access token from your own GitHub account.

## 🚀 Running the Application

### Development Mode

```bash
# Create the database or bring it to the newest schema
pixi run migrate

# Start the development server with auto-reload
pixi run dev

# Or use uvicorn directly
pixi run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 --ws-max-size 4096
```

### Production Mode

```bash
# Start the production server
pixi run uvicorn app.main:app --host 0.0.0.0 --port 8000 --ws-max-size 4096
```

> **Run a single worker process.** The realtime event stream uses an in-memory broker that is not
> shared across processes, so do **not** pass `--workers N` (or run gunicorn with multiple workers /
> multiple replicas): extra workers silently drop cross-worker events and their viewers miss live
> updates. To scale horizontally, put a shared pub/sub (e.g. Redis) behind the `EventBroker`.

> **Realtime WebSocket.** Clients never send data on `/workspaces/{id}/events`, so `--ws-max-size 4096`
> caps inbound frames (larger frames are rejected with close code `1009`; any data frame at all closes
> the socket with `1008`). The handshake checks the `Origin` header against `CORS_ORIGINS`, and the
> server closes with `4001` when the JWT expires or the user logs out, `4003` when access is gone,
> and `4009` when a client must reconnect and resync. See the `/workspaces/{workspaceId}/events` entry in
> `openapi.yaml`.

The API will be available at:

- **API**: <http://localhost:8000>

The API contract is [`openapi.yaml`](openapi.yaml); open it in any OpenAPI viewer. The server serves no
docs pages of its own.

## 🧪 Development

### Code Quality

```bash
# Run pre-commit hooks
pixi run pre-commit
```

### Pre-commit Hooks

```bash
# Install pre-commit hooks
pixi run pre-commit-install
```

## 🐳 Database

The application uses SQLite as the database backend:

- **File Location**: `./ceos_ard_server.db` (in project root)
- **Creation**: `pixi run migrate` creates the database and its tables
- **No Installation Required**: SQLite is built into Python
- **Git Ignored**: Database files are automatically ignored by git
- **WAL Mode**: The server and the maintenance scripts write concurrently; this leaves `-wal`/`-shm` files next to
  the database. Back it up with `sqlite3 ceos_ard_server.db ".backup <dest>"`, never with `cp`
- **Schema changes**: every change is an [Alembic](https://alembic.sqlalchemy.org) revision in `migrations/versions`, and the server refuses to start until the database is at the newest one. After pulling, run `pixi run migrate`. To change the schema, edit the model, run `pixi run alembic revision --autogenerate --rev-id 0004 -m "<short name>"` with the next number, review the generated file and commit it.

### Maintenance Tasks

The server includes four automated maintenance scripts designed to run as cron jobs:

#### 1. Pull Request Status Checker

Automatically monitors and updates the status of all workspace pull requests:

- Fetches PR status from GitHub (open/merged/closed)
- Updates workspace PR status in database
- Auto-archives workspaces with merged/closed PRs
- Reactivates workspaces if PRs are reopened

```bash
# Dry-run to see what would be updated
pixi run python scripts/check_pr_status.py --dry-run

# Actually update PR statuses
pixi run python scripts/check_pr_status.py

# Limit to specific number of workspaces for testing
pixi run python scripts/check_pr_status.py --dry-run --limit 5
```

**Requirements**:
- `GITHUB_SERVICE_TOKEN` must be set in `.env` file
- See [GitHub Service Token setup](#github-service-token-required-for-automated-tasks) for how to obtain this token

#### 2. Cleanup Archived Workspaces

Archived workspaces are automatically cleaned up after the retention period (default: 1 month).

```bash
# Dry-run to see what would be deleted
pixi run python scripts/cleanup_archived_workspaces.py --dry-run

# Actually delete expired archived workspaces
pixi run python scripts/cleanup_archived_workspaces.py
```

#### 3. Token Expiration Monitor

Simple script to check manual expiration date and send email alerts.

```bash
# Check token expiry status
pixi run python scripts/check_token_expiry.py

# Check without sending email (log only)
pixi run python scripts/check_token_expiry.py --no-email

# Test email configuration
pixi run python scripts/check_token_expiry.py --force-email
```

**Configuration**:

Set the token expiration date in `.env` (REQUIRED):

```bash
GITHUB_SERVICE_TOKEN_EXPIRES_AT=2027-03-15  # Format: YYYY-MM-DD
```

**Email alerts** (optional - configure SMTP in `.env`):
- 14 days before expiry - Early warning
- 7 days before expiry - Urgent warning
- When expired - Critical alert

```bash
ALERT_EMAIL_TO=admin@example.com
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_MODE=ssl
SMTP_USER=your-email@example.com
SMTP_PASSWORD=your-app-password
```

#### 4. Idle Session Cleanup

Revokes and clears the provider tokens of users without an authenticated request for a week (`users.last_seen_at`), so a leaked refresh token (valid six months unused) stops working. Affected users log in again with one click on their next visit.

```bash
# Preview
pixi run python scripts/revoke_idle_tokens.py --dry-run

# Run (default: 7 days idle)
pixi run python scripts/revoke_idle_tokens.py

# Other threshold
pixi run python scripts/revoke_idle_tokens.py --days 14
```

#### Setting Up Cron Jobs

**Recommended**: Set up automated cron jobs for all maintenance tasks.

```bash
# Edit crontab
crontab -e

# Add these lines (replace USER and paths with your actual values):
# IMPORTANT: Cron uses a minimal PATH - add pixi's location (check with: which pixi)
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/USER/.pixi/bin

# Check PR status daily at midnight
0 0 * * * cd /path/to/ceos-ard-server && pixi run python scripts/check_pr_status.py >> logs/pr_status_check.log 2>&1

# Cleanup archived workspaces daily at 2 AM
0 2 * * * cd /path/to/ceos-ard-server && pixi run python scripts/cleanup_archived_workspaces.py >> logs/workspace_cleanup.log 2>&1

# Check token expiration weekly on Monday at 9 AM
0 9 * * 1 cd /path/to/ceos-ard-server && pixi run python scripts/check_token_expiry.py >> logs/token_check.log 2>&1

# Revoke the tokens of users idle for a week (daily at 3 AM)
0 3 * * * cd /path/to/ceos-ard-server && pixi run python scripts/revoke_idle_tokens.py >> logs/idle_tokens.log 2>&1
```

**How it works:**
- Cron just needs to `cd` to project directory and run the script.
- Install the crontab for the user the server runs as (e.g. `server`), not root: the scripts create the
  database's `-wal`/`-shm` files and the `workspaces/.locks` files, and the server can't use them if root owns them.
- Ensure `.env` file exists in project root with `GITHUB_SERVICE_TOKEN` and `GITHUB_SERVICE_TOKEN_EXPIRES_AT` set.
- Token checker sends email alerts if configured (see above).

### Database Models

#### User (`users`)

- A GitHub or Google identity, created on the first login
- The provider's access token, refresh token and token expiry, kept server-side
- `last_seen_at`: the last authenticated request, to the hour; used by the idle session cleanup
- `session_version`: increased on logout; a JWT carrying another version is refused

#### Workspace (`git_workspaces`)

- A clone of the owner's fork, on the branch `workspace/<id>`
- The PFS selection (JSON list), which the preview builds
- Pull Request number and status
- `ACTIVE` or `ARCHIVED`; an archived workspace is deleted a month after `archived_at`

#### Collaborator (`workspace_collaborators`)

- Read access to another user's workspace, bound to the invitee's GitHub account id; the username is for display
- `PENDING` until the invitee first logs in, `ACCEPTED` right away if they already have an account
- Created by an invitation or by redeeming a share link (`share_id`)

#### Share Link (`workspace_shares`)

- A link `{CLIENT_URL}/share/<token>` with an access mode and an optional expiry
- Redeeming it with a GitHub login adds the user as a collaborator

## 🔐 Security Features

- **OAuth 2.0**: Secure authentication via GitHub/Google
- **Session Cookie**: The JWT in an `HttpOnly; Secure; SameSite=Strict` `__Host-session` cookie; scripts and
  tests can still send it as an `Authorization: Bearer` header
- **Cross-Site Request Protection**: POST, PUT, PATCH and DELETE requests authenticated by the cookie must send
  `X-Client-Id`, which a foreign page can't add without passing the CORS check. So must the three GET requests that
  change data (workspace and proposal refresh the pull request state, download builds the document)
- **User Content Sandbox**: Workspace files and previews are served with `Content-Security-Policy: sandbox`, so a
  script in them can't run on the API origin with the session cookie
- **OAuth State**: Signed `__Host-oauth_state` cookie with `itsdangerous` between login and callback; the prefix keeps
  sibling hosts of the same site from planting their own state
- **CORS Protection**: Credentialed requests from the exact origins in `CORS_ORIGINS` only; the realtime WebSocket
  checks its `Origin` header against the same list
- **Logout**: Revokes the provider token and refuses every JWT issued before it (`users.session_version`)
- **Input Sanitization**: Protection against malicious input
- **Workspace Access**: The owner and the collaborators they invited, read-only; every change is the owner's
- **Provider-based Authorization**: Workspace access restricted to GitHub users only
- **Token Refresh**: Provider tokens are renewed with their refresh token (Google, and GitHub with expiring tokens); a GitHub token without one requires a new login after 8 hours

## 🔑 Authorization Model

### GitHub Users (Workspace Access)

Users authenticated with GitHub have full access to their own workspaces:

- ✅ Create workspaces (fork repositories)
- ✅ Delete workspaces
- ✅ Archive/reactivate workspaces
- ✅ Propose changes (create pull requests)
- ✅ View and manage files
- ✅ Generate previews
- ✅ Share workspaces read-only, by invitation or share link
- ✅ Access all workspace-related endpoints

### Collaborators (Read-only)

GitHub users invited to a workspace, or who redeemed its share link:

- ✅ Read and search the files
- ✅ See the preview, and download PDF and Word
- ✅ Get live updates while the owner works
- ❌ No changes: files, PFS, commits, sync, proposals and sharing stay with the owner
- ❌ Access ends as soon as the owner removes them, also on open connections

### Google Users (No Workspace Access)

Users authenticated with Google **cannot access workspace features**:

- ❌ No access to any workspace endpoints
- ❌ Cannot view, create, delete, or manage workspaces
- ❌ Cannot access workspace files or content
- ❌ Cannot generate previews
- ❌ Cannot propose changes

**Why this restriction?**

- Workspaces are git repositories that require GitHub API integration
- All workspace operations (fork, clone, PR creation) require GitHub credentials
- Google OAuth provides no GitHub repository access
- To use workspace features, users must authenticate with GitHub

## 🚧 Deployment

### Environment Variables for Production

```bash
# Set production URL of the server / API
SERVER_URL=https://api.yourdomain.com

# Set production URLs of the client
CLIENT_URL=https://yourdomain.com

# Set environment
ENVIRONMENT=production

# Use a strong secret key
SECRET_KEY=your-production-secret-key

# The exact editor origin, on the same site as SERVER_URL (see Environment Configuration)
CORS_ORIGINS=https://yourdomain.com

# Use absolute path for database in production
DATABASE_URL=sqlite:////app/data/ceos_ard_server.db
```

### Running the Production Server Persistently

The application runs via `uvicorn`, managed through `pixi`.

For deployment, use **systemd**. It will:

- Keep the server running after you log out or close your terminal
- Automatically restart the server if it crashes
- Start the server automatically on system boot
- Centralize logs via `journalctl`

#### 1. Prerequisites

- `pixi` must be installed for the user the service will run as (not root). This is typically a dedicated service account, e.g. `server`.
- The project must already be checked out on the server, e.g. at `/home/server/ceos-ard-server`.
- Run `pixi install` once inside the project directory to make sure the environment resolves correctly before relying on the service to do it:

  ```bash
  cd /home/server/ceos-ard-server
  pixi install
  ```

#### 2. Create the service file

Create `/etc/systemd/system/ceos-ard-server.service`:

```ini
[Unit]
Description=CEOS-ARD FastAPI Server
After=network.target

[Service]
Type=simple
User=server
WorkingDirectory=/home/server/ceos-ard-server
Environment="PATH=/home/server/.pixi/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/home/server/.pixi/bin/pixi run uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1 --ws-max-size 4096
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal
SyslogIdentifier=ceos-ard-server

[Install]
WantedBy=multi-user.target
```

If your deployment differs from the defaults above, update:

| Field | Meaning | Adjust if... |
|-------|---------|--------------|
| `User` | System user the process runs as | You use a different service account than `server` |
| `WorkingDirectory` | Path to the checked-out project | Your install path differs |
| `Environment="PATH=..."` | Must include the pixi binary path for `User` | pixi is installed elsewhere, or for a different user |
| `ExecStart` | Full path to `pixi` + the run command | Same as above, or if host/port should differ |

#### 3. Enable and start the service

```bash
sudo systemctl daemon-reload            # re-read unit files after creating/editing one
sudo systemctl enable ceos-ard-server   # start automatically on boot
sudo systemctl start ceos-ard-server    # start it now
```

#### 4. Managing the running server

| Action | Command |
|--------|---------|
| Check status | `sudo systemctl status ceos-ard-server` |
| Stop the server | `sudo systemctl stop ceos-ard-server` |
| Restart the server | `sudo systemctl restart ceos-ard-server` |
| Disable auto-start on boot | `sudo systemctl disable ceos-ard-server` |
| Tail logs live | `journalctl -u ceos-ard-server -f` |
| View last 100 log lines | `journalctl -u ceos-ard-server -n 100` |

#### 5. Updating the server with new code

To deploy new changes to an already-running server:

```bash
cd /home/server/ceos-ard-server
sudo systemctl stop ceos-ard-server
mkdir -p ~/backups
sqlite3 ceos_ard_server.db ".backup $HOME/backups/ceos_ard_server-$(date +%F).db"
git pull
pixi install          # only strictly needed if pixi.toml/pixi.lock changed.
pixi run migrate      # brings the database to the newest schema; does nothing if it already is
sudo systemctl start ceos-ard-server
```

The server is stopped first, so nothing writes to the database while it is backed up and migrated.

**First deploy with migrations (once):** a database from before migrations has no revision yet. After the backup, mark it as the baseline with `pixi run alembic stamp 0001`, then run `pixi run migrate`.

After starting, confirm it came back up cleanly:

```bash
sudo systemctl status ceos-ard-server
journalctl -u ceos-ard-server -n 30
```

At startup the log shows `All prerequisite checks passed.` Otherwise it shows `CHECKS FAILED` with the reason, e.g. a
database that isn't migrated, and systemd keeps restarting the server until the cause is fixed.

### Configuring a Reverse Proxy (Caddy)

The service listens on `127.0.0.1:8000` only. [Caddy](https://caddyserver.com/docs/install) sits in front of it, handles
HTTPS (it issues and renews the certificate itself) and forwards all requests, the realtime WebSocket included, with no
extra configuration.

`/etc/caddy/Caddyfile`:

```caddyfile
api.yourdomain.com {
    reverse_proxy localhost:8000
    encode gzip

    log {
        output file /var/log/caddy/access.log
    }
}
```

The domain must match `SERVER_URL` and point at the server, with ports 80 and 443 open. Apply changes with
`sudo systemctl reload caddy`; a reload closes open WebSocket connections, which the editor reopens by itself.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Make your changes
4. Run the linting and checks: `pixi run pre-commit`
5. Commit your changes: `git commit -am 'Add your feature'`
6. Push to the branch: `git push origin feature/your-feature`
7. Submit a Pull Request

## 📝 License

This project is licensed under the terms specified in the LICENSE file.

## 🆘 Support

For issues and questions:

1. Check the [Issues](https://github.com/moregeo-it/ceos-ard-server/issues) page
2. Create a new issue with detailed information
3. Include logs and error messages when reporting bugs

## 🔗 Related Projects

- [CEOS-ARD Repository](https://github.com/ceos-org/ceos-ard) - The main CEOS-ARD documentation repository
- [CEOS-ARD CLI](https://pypi.org/project/ceos-ard-cli/) - Command-line tools for CEOS-ARD
