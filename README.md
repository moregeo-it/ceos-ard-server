# CEOS-ARD Server

A FastAPI-based server application for managing CEOS-ARD (Committee on Earth Observation Satellites - Analysis Ready Data) workspaces, enabling collaborative editing, preview generation, and Pull Request workflows for CEOS-ARD documentation.

## 🚀 Features

### Authentication & Authorization

- **OAuth Integration**: Support for GitHub and Google OAuth providers
- **JWT Token Management**: Secure token-based authentication
- **User Management**: Automatic user creation and profile management
- **GitHub-Only Workspaces**: All workspace features exclusively available to GitHub users

### Workspace Management (GitHub Users Only)

- **Git-based Workspaces**: Create isolated workspaces with repository forking
- **CRUD Operations**: Full workspace lifecycle management (create, read, update, delete)
- **Workspace Archival**: Archive workspaces with automatic cleanup after 1 month
- **Status Tracking**: Monitor workspace and Pull Request status
- **Multi-user Support**: User-specific workspace isolation
- **GitHub Authentication Required**: All workspace endpoints require GitHub OAuth

### File Operations

- **File Management**: Create, read, update, delete files and folders
- **Content Search**: Search through workspace files
- **Diff Tracking**: View changes and file differences
- **File Upload**: Support for file uploads and content storage

### PFS (Product Family Specification) Management

- **PFS Discovery**: List available PFS types from CEOS-ARD repository
- **PFS Creation**: Create and manage PFS documents within workspaces
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

- **Backend Framework**: FastAPI 0.116+
- **Database**: SQLite (file-based, zero-config)
- **ORM**: SQLAlchemy 2.0+
- **Authentication**: Authlib + OAuth2
- **Session Management**: Starlette SessionMiddleware
- **Package Management**: Pixi (conda-forge)
- **Document Processing**: Pandoc + Pandoc-crossref
- **Git Operations**: GitPython
- **Browser Automation**: Playwright (for preview generation)
- **Code Quality**: Ruff (linting & formatting)
- **Testing**: Pytest

## 📋 Prerequisites

- **Python 3.11+**
- **Pixi** (for dependency management)
- **Git** (for repository operations)
- **GitHub/Google OAuth Apps** (for authentication)

## 🔧 Installation

### 1. Clone the Repository

```bash
git clone https://github.com/ceos-org/ceos-ard-server.git
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

### 5. OAuth Setup

#### GitHub OAuth App (Required)

1. Go to GitHub Settings → Developer settings → OAuth Apps
2. Create a new OAuth App with:
  - **Application name**: CEOS-ARD
  - **Homepage URL**: `http://localhost:8000`
  - **Authorization callback URL**: `http://localhost:8000/auth/callback/github`
3. Copy the Client ID and Client Secret to your `.env` file

**Note**: GitHub authentication is mandatory for workspace features. All workspace operations require GitHub OAuth.

#### Google OAuth App (Optional - Future Use)

1. Go to Google Cloud Console → APIs & Services → Credentials
2. Create OAuth 2.0 Client ID with:
  - **Application type**: Web application
  - **Authorized redirect URIs**: `http://localhost:8000/auth/callback/google`
3. Copy the Client ID and Client Secret to your `.env` file

**Note**: Google authentication is currently not used for workspace features. Reserved for potential future functionality.

#### GitHub Service Token (Required for Automated Tasks)

For automated maintenance scripts (PR status checker and workspace cleanup), you need a GitHub service token:

1. **Create a GitHub bot/service account** (recommended) or use your personal account:
  - Create a new GitHub account (e.g., `ceos-ard-bot`)

2. **Generate a Personal Access Token**:
  - Login to the bot/service account
  - Go to Settings → Developer settings → Personal access tokens → **Tokens (classic)**
  - Click "Generate new token (classic)"
  - Set note: "CEOS ARD PR Status Checker"
  - Expiration: "No expiration" (for production) or custom duration
  - Scopes needed:
    - For **public repositories**: No scopes required (or select `public_repo` for clarity)
    - For **private repositories**: Select `repo` (full repository access)
  - Click "Generate token"
  - **Copy the token immediately** - you won't see it again!

3. **Add to `.env` file**:
  ```bash
  GITHUB_SERVICE_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
  ```

**Alternative**: For development/testing, you can use a personal access token from your own GitHub account.

## 🚀 Running the Application

### Development Mode

```bash
# Start the development server with auto-reload
pixi run dev

# Or use uvicorn directly
pixi run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Production Mode

```bash
# Start the production server
pixi run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The API will be available at:

- **API**: <http://localhost:8000>
- **Interactive Docs**: <http://localhost:8000/docs>
- **ReDoc**: <http://localhost:8000/redoc>

## 🧪 Development

### Code Quality

```bash
# Run pre-commit hooks
pixi run pre-commit
```

### Testing

```bash
# Run tests
pixi run test

# Run tests with verbose output
pixi run pytest test/ -v
```

### Pre-commit Hooks

```bash
# Install pre-commit hooks
pixi run pre-commit-install
```

## 🐳 Database

The application uses SQLite as the database backend:

- **File Location**: `./ceos_ard_server.db` (in project root)
- **Automatic Creation**: Database and tables are created automatically on first run
- **No Installation Required**: SQLite is built into Python
- **Git Ignored**: Database files are automatically ignored by git

### Maintenance Tasks

The server includes two automated maintenance scripts designed to run as cron jobs:

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

#### Setting Up Cron Jobs

**Recommended**: Set up automated cron jobs for both maintenance tasks.

```bash
# Edit crontab
crontab -e

# Add these lines (replace /path/to/ceos-ard-server with actual path):
# Check PR status daily at midnight
0 0 * * * cd /path/to/ceos-ard-server && pixi run python scripts/check_pr_status.py >> logs/pr_status_check.log 2>&1

# Cleanup archived workspaces daily at 2 AM
0 2 * * * cd /path/to/ceos-ard-server && pixi run python scripts/cleanup_archived_workspaces.py >> logs/workspace_cleanup.log 2>&1
```

**How it works:**
- Each maintenance script explicitly loads `GITHUB_SERVICE_TOKEN` from `.env` file at startup
- Cron just needs to `cd` to project directory and run the script
- Ensure `.env` file exists in project root with `GITHUB_SERVICE_TOKEN` set.

### Database Models

#### User Model

- Stores user authentication information
- Supports multiple identity providers (GitHub, Google)
- Tracks creation and update timestamps

#### Workspace Model

- Manages git-based workspaces
- Links to forked repositories and branches
- Tracks Pull Request status and metadata
- Stores PFS associations (JSON format)
- Supports archival with automatic deletion after 1 month
- Computes deletion date dynamically from archived_at timestamp

## 🔐 Security Features

- **OAuth 2.0**: Secure authentication via GitHub/Google
- **JWT Tokens**: Stateless authentication tokens
- **Session Security**: Signed session cookies with `itsdangerous`
- **CORS Protection**: Configurable cross-origin resource sharing
- **Input Sanitization**: Protection against malicious input
- **User Isolation**: Workspaces are isolated per user
- **Provider-based Authorization**: Workspace access restricted to GitHub users only
- **Token Refresh**: Automatic token refresh for Google; re-authentication required for expired GitHub tokens

## 🔑 Authorization Model

### GitHub Users (Workspace Access)

Users authenticated with GitHub have full workspace access:

- ✅ Create workspaces (fork repositories)
- ✅ Delete workspaces
- ✅ Archive/reactivate workspaces
- ✅ Propose changes (create pull requests)
- ✅ View and manage files
- ✅ Generate previews
- ✅ Access all workspace-related endpoints

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

# Use absolute path for database in production
DATABASE_URL=sqlite:////app/data/ceos_ard_server.db
```

### Docker Deployment (Example)

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .

# Install pixi and dependencies
RUN curl -fsSL https://pixi.sh/install.sh | bash
RUN pixi install

EXPOSE 8000
CMD ["pixi", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Make your changes
4. Run tests and linting: `pixi run test && pixi run lint`
5. Commit your changes: `git commit -am 'Add your feature'`
6. Push to the branch: `git push origin feature/your-feature`
7. Submit a Pull Request

## 📝 License

This project is licensed under the terms specified in the LICENSE file.

## 🆘 Support

For issues and questions:

1. Check the [Issues](https://github.com/ceos-org/ceos-ard-server/issues) page
2. Create a new issue with detailed information
3. Include logs and error messages when reporting bugs

## 🔗 Related Projects

- [CEOS-ARD Repository](https://github.com/ceos-org/ceos-ard) - The main CEOS-ARD documentation repository
- [CEOS-ARD CLI](https://pypi.org/project/ceos-ard-cli/) - Command-line tools for CEOS-ARD
