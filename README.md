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

- `GITHUB_CLIENT_ID`
- `GITHUB_CLIENT_SECRET`
- `SECRET_KEY`
- `ENVIRONMENT`

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

## 📚 API Documentation

### Authentication Endpoints

- `GET /auth/login?identity_provider={github|google}` - Initiate OAuth login
- `GET /auth/callback/{github|google}` - OAuth callback handlers
- `GET /auth/logout` - Logout user
- `GET /auth/user` - Get current user profile
- `GET /auth/validate` - Validate authentication

### Workspace Endpoints

- `POST /workspaces` - Create a new workspace _(requires GitHub auth)_
- `GET /workspaces` - List user workspaces
- `GET /workspaces/{workspace_id}` - Get workspace details
- `PATCH /workspaces/{workspace_id}` - Update workspace (archive/reactivate) _(requires GitHub auth)_
- `DELETE /workspaces/{workspace_id}` - Delete workspace permanently _(requires GitHub auth)_
- `GET /workspaces/{workspace_id}/status` - Get workspace status
- `POST /workspaces/{workspace_id}/propose` - Propose changes (create PR) _(requires GitHub auth)_

### File Management Endpoints

- `GET /workspaces/{workspace_id}/files` - List files in workspace
- `POST /workspaces/{workspace_id}/files` - Create file or folder
- `GET /workspaces/{workspace_id}/files/{file_path}` - Read file content
- `PUT /workspaces/{workspace_id}/files/{file_path}` - Store file content
- `DELETE /workspaces/{workspace_id}/files/{file_path}` - Delete file or folder
- `PATCH /workspaces/{workspace_id}/files/{file_path}` - Update file metadata
- `GET /workspaces/{workspace_id}/search?query={query}` - Search files
- `GET /workspaces/{workspace_id}/diffs` - Get changed files
- `GET /workspaces/{workspace_id}/diffs/{file_path}` - Get file diff

### PFS Endpoints

- `GET /pfs` - List available PFS types
- `GET /workspaces/{workspace_id}/pfs` - List workspace PFS types
- `POST /workspaces/{workspace_id}/pfs` - Create PFS in workspace

### Preview Endpoints

- `GET /workspaces/{workspace_id}/previews?pfs={pfs_list}` - Generate document preview

## 🏗️ Project Structure

```text
app/
├── api/                    # API route handlers
│   ├── auth.py            # Authentication routes
│   ├── file.py            # File management routes
│   ├── pfs.py             # PFS-related routes
│   ├── preview.py         # Preview generation routes
│   └── workspace.py       # Workspace management routes
├── db/                     # Database configuration
│   └── database.py        # SQLAlchemy setup
├── models/                 # SQLAlchemy models
│   ├── user.py            # User model
│   └── workspace.py       # Workspace model
├── oauth/                  # OAuth configuration
│   └── handler.py         # OAuth client setup
├── schemas/                # Pydantic schemas
│   ├── auth.py            # Authentication schemas
│   ├── preview.py         # Preview schemas
│   └── workspace.py       # Workspace schemas
├── services/               # Business logic layer
│   ├── auth_service.py    # Authentication service
│   ├── build_service.py   # Document build service
│   ├── file_service.py    # File operations service
│   ├── git_service.py     # Git operations service
│   ├── github_service.py  # GitHub API service
│   ├── preview_service.py # Preview generation service
│   └── workspace_service.py # Workspace management service
├── utils/                  # Utility functions
│   ├── cli_checker.py     # CLI tool validation
│   ├── extraction.py      # Data extraction utilities
│   ├── handle_oauth_callback.py # OAuth callback handler
│   ├── handle_user_info_extractor.py # User info extraction
│   ├── sanitization.py    # Input sanitization
│   └── token_validator.py # Token validation
├── config.py              # Application configuration
├── dependencies.py        # FastAPI dependencies
└── main.py               # Application entry point
```

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

#### Cleanup Archived Workspaces

Archived workspaces are automatically cleaned up after the retention period (default: 30 days).

```bash
# Dry-run to see what would be deleted
pixi run python scripts/cleanup_archived_workspaces.py --dry-run

# Actually delete expired archived workspaces
pixi run python scripts/cleanup_archived_workspaces.py
```

**Recommended**: Set up a cron job or scheduled task to run cleanup daily:

```bash
# Add to crontab (run daily at 2 AM)
0 2 * * * cd /path/to/ceos-ard-server && pixi run python scripts/cleanup_archived_workspaces.py
```

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
