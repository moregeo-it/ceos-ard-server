"""API tests against local git repositories: bare repositories stand in for ceos-org/ceos-ard and the user's fork on GitHub."""

import json
import os
import shutil
import subprocess
import tempfile
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

DATA = Path(tempfile.mkdtemp(prefix="ceos-ard-server-test-"))
UPSTREAM = DATA / "upstream.git"
FORK = DATA / "fork.git"
OGC_UPSTREAM = DATA / "ogc-upstream.git"
OGC_FORK = DATA / "ogc-fork.git"
# Someone else's fork of opengeospatial/ogcapi-records, with the branch of an open pull request
OGC_CONTRIBUTION = DATA / "ogc-contribution.git"
# The user's own fork of opengeospatial/ogcapi-records, with the branch of their open pull request
OGC_OWN = DATA / "ogc-own.git"
TYPES = [
    {"id": "ceos-ard", "module": "ceos_ard", "upstream": {"owner": "ceos-org", "repo": "ceos-ard", "branch": "main"}},
    {"id": "ogc", "module": "ogc", "upstream": {"owner": "opengeospatial"}},
]
TYPES_CONFIG = DATA / "workspace-types.yaml"
# JSON is YAML too
TYPES_CONFIG.write_text(json.dumps({"types": TYPES}), encoding="utf-8")

os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{(DATA / 'test.db').as_posix()}",
        "WORKSPACES_ROOT": str(DATA / "workspaces"),
        # At least 32 bytes, as PyJWT recommends for HS256
        "SECRET_KEY": "test-secret-long-enough-for-hs256",
        "SESSION_COOKIE_SECURE": "false",
        "ENVIRONMENT": "development",
        "WORKSPACE_TYPES_CONFIG": str(TYPES_CONFIG),
    }
)
os.environ.setdefault("TOOLCHAINS_ROOT", str(DATA / "toolchains"))

import pygit2  # noqa: E402

# The server fetches upstream from GitHub; point that URL to the local upstream repository
(DATA / "home").mkdir()
# Git for Windows' system config converts line endings on checkout
(DATA / "home" / ".gitconfig").write_text(
    f'[core]\n\tautocrlf = false\n[url "{UPSTREAM.as_posix()}"]\n\tinsteadOf = https://github.com/ceos-org/ceos-ard\n'
    f'[url "{OGC_UPSTREAM.as_posix()}"]\n\tinsteadOf = https://github.com/opengeospatial/ogcapi-records\n'
    f'[url "{OGC_CONTRIBUTION.as_posix()}"]\n\tinsteadOf = https://github.com/octo/ogcapi-records\n'
)
pygit2.settings.search_path[pygit2.enums.ConfigLevel.GLOBAL] = str(DATA / "home")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from openapi_validation import validate_response  # noqa: E402

from app import dependencies  # noqa: E402
from app.db.database import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import IdentityProvider, User  # noqa: E402
from app.services import workspace_service  # noqa: E402
from app.services.jwt_service import JWTService  # noqa: E402
from app.utils.toolchains import ToolchainError, Toolchains  # noqa: E402

REPOSITORY = {
    "README.md": "# CEOS-ARD\n",
    ".github/workflows/build.yml": "name: Build\n",
    "templates/template.html": "<html>$body$</html>\n",
    "glossary/accuracy.yaml": "term: Accuracy\ndescription: Closeness of agreement.\n",
    "images/diagram.svg": "<svg><text>machine readable</text></svg>\n",
    "pfs/NRB/document.yaml": (
        "title: Normalised Radar Backscatter\nversion: 5.6.0\ntype: SAR\napplies_to: SAR data\nbackground: Background.\n"
        "glossary:\nreferences:\nintroduction:\nannexes:\nauthors:\nchanges:\nrequirements:\n"
        "  - category: general\n    requirements:\n      - general/metadata-machine-readable\n"
    ),
    "pfs/SR/document.yaml": (
        "title: Surface Reflectance\ntype: Optical\nrequirements:\n"
        "  - category: general\n    requirements:\n      - ref: general/metadata-machine-readable\n"
    ),
    "requirements/general/metadata-machine-readable.yaml": (
        "id: mmr\ntitle: Metadata Machine Readability\ndescription:\nrequirements:\n"
        "  threshold:\n    description: Metadata shall be machine readable.\n"
    ),
    "requirements/general/unused.yaml": "title: Unused\ndescription: Not used by any PFS.\n",
    "references/small2011.bib": "@article{small2011,\n  title = {Flattening Gamma: Radiometric Terrain Correction for SAR Imagery},\n}\n",
    "sections/introduction/what-are-ceos-ard-products.md": "## What are CEOS-ARD products?\n",
    "sections/requirement-categories/general.yaml": "id: meta\ntitle: General Metadata\ndescription: General requirements.\n",
}


def file_url(workspace: dict, endpoint: str, path: str) -> str:
    """A file endpoint with the path as one segment, as the editor sends it (encodeURIComponent)."""
    return f"/workspaces/{workspace['id']}/{endpoint}/{urllib.parse.quote(path, safe='')}"


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit_upstream(files: dict[str, str], message: str):
    """Add a commit to ceos-org/ceos-ard, as if merged there by someone else."""
    clone = Path(tempfile.mkdtemp(dir=DATA))
    git("clone", "-q", str(UPSTREAM), str(clone))
    for path, content in files.items():
        (clone / path).parent.mkdir(parents=True, exist_ok=True)
        (clone / path).write_text(content, encoding="utf-8", newline="\n")
    git("add", "-A", cwd=clone)
    git("-c", "user.name=Upstream", "-c", "user.email=upstream@example.org", "commit", "-q", "-m", message, cwd=clone)
    git("push", "-q", "origin", "main", cwd=clone)


def create_repository(files: dict[str, str], upstream: Path, fork: Path):
    """A bare upstream repository with `files`, and a bare clone of it as the user's fork."""
    source = Path(tempfile.mkdtemp(dir=DATA))
    for path, content in files.items():
        (source / path).parent.mkdir(parents=True, exist_ok=True)
        (source / path).write_text(content, encoding="utf-8", newline="\n")
    git("init", "-q", "-b", "main", cwd=source)
    git("add", "-A", cwd=source)
    git("-c", "user.name=Upstream", "-c", "user.email=upstream@example.org", "commit", "-q", "-m", "Initial", cwd=source)
    git("clone", "-q", "--bare", str(source), str(upstream))
    git("clone", "-q", "--bare", str(upstream), str(fork))


# Like opengeospatial/ogcapi-records: a core document and an extension, sharing examples
OGC_REPOSITORY = {
    "README.md": "# OGC API - Records\n",
    "core/standard/20-004.adoc": (
        "= OGC API - Records - Part 1: Core\n:doctype: standard\n:mn-document-class: ogc\n:mn-output-extensions: xml,html\n\n"
        "include::clause_1_scope.adoc[]\n"
    ),
    "core/standard/clause_1_scope.adoc": "== Scope\n\ninclude::../examples/record.json[]\n",
    "core/examples/record.json": "{}\n",
    "core/standard/README.adoc": "= How to build\n",
    "extensions/facets/document.adoc": "= OGC API - Records - Facets\n:mn-document-class: ogc\n\ninclude::/sections/clause_1.adoc[]\n",
    "extensions/facets/sections/clause_1.adoc": "== Scope\n",
}


@pytest.fixture(scope="session", autouse=True)
def repositories():
    create_repository(REPOSITORY, UPSTREAM, FORK)
    create_repository(OGC_REPOSITORY, OGC_UPSTREAM, OGC_FORK)
    git("clone", "-q", "--bare", str(OGC_UPSTREAM), str(OGC_CONTRIBUTION))
    contribution = Path(tempfile.mkdtemp(dir=DATA))
    git("clone", "-q", str(OGC_CONTRIBUTION), str(contribution))
    git("checkout", "-q", "-b", "facets-scope", cwd=contribution)
    (contribution / "extensions/facets/sections/clause_1.adoc").write_text("== Scope\n\nFacets of records.\n", encoding="utf-8", newline="\n")
    git("-c", "user.name=Octo", "-c", "user.email=octo@example.org", "commit", "-q", "-am", "Describe the scope", cwd=contribution)
    git("push", "-q", "origin", "facets-scope", cwd=contribution)
    git("clone", "-q", "--bare", str(OGC_CONTRIBUTION), str(OGC_OWN))

    command.upgrade(Config(str(Path(__file__).parent.parent / "alembic.ini")), "head")
    yield
    engine.dispose()
    # pygit2 may still hold pack files open on Windows
    shutil.rmtree(DATA, ignore_errors=True)


def pytest_configure(config):
    config.addinivalue_line("markers", "installs_toolchains: may install the toolchains of builds")


@pytest.fixture(autouse=True)
def no_toolchain_installs(request, monkeypatch):
    """Toolchains are only installed by tests marked `installs_toolchains`: installs take a minute and need the network."""
    if request.node.get_closest_marker("installs_toolchains"):
        return

    async def install(self, toolchain):
        raise ToolchainError(f"The toolchain {toolchain.name} isn't installed")

    monkeypatch.setattr(Toolchains, "_install", install)


@pytest.fixture(scope="session")
def user(repositories):
    with SessionLocal() as db:
        user = User(
            full_name="Jane Doe",
            email="jane@example.org",
            username="jdoe",
            external_id="4242",
            identity_provider=IdentityProvider.github,
            access_token="not-a-token",
            token_expiry=datetime.now(UTC) + timedelta(days=30),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user


class FakeGitHub:
    """Records the GitHub API calls; anything not listed here fails the test."""

    def __init__(self):
        self.calls = []
        # GitHub accounts by login, for invitations
        self.users = {}
        self.repositories = {
            ("opengeospatial", "ogcapi-records"): {
                "name": "ogcapi-records",
                "full_name": "opengeospatial/ogcapi-records",
                "owner": {"login": "opengeospatial"},
                "description": "OGC API - Records",
                "default_branch": "main",
                "private": False,
                "archived": False,
            },
            ("opengeospatial", "WFS_FES"): {
                "name": "WFS_FES",
                "full_name": "opengeospatial/WFS_FES",
                "owner": {"login": "opengeospatial"},
                "description": None,
                "default_branch": "master",
                "private": False,
                "archived": True,
            },
        }

    # The local bare repository that stands in for the user's fork of an upstream repository
    forks = {"ceos-ard": FORK, "ogcapi-records": OGC_FORK}

    async def fork(self, user, upstream_owner, upstream_repo):
        self.calls.append(("fork", upstream_owner, upstream_repo))
        return {"owner": {"login": user.username}, "name": upstream_repo, "clone_url": str(self.forks[upstream_repo])}

    async def get_repository(self, owner, repo, token):
        self.calls.append(("get_repository", owner, repo))
        return self.repositories.get((owner, repo))

    async def branch_exists(self, owner, repo, branch, token):
        self.calls.append(("branch_exists", owner, repo, branch))
        return branch == "main"

    async def get_owner_repositories(self, owner, token):
        self.calls.append(("get_owner_repositories", owner))
        return [repository for (repository_owner, _), repository in self.repositories.items() if repository_owner == owner]

    # The pull requests of opengeospatial/ogcapi-records by number
    pull_requests = {
        304: {
            "number": 304,
            "title": "Describe the scope of facets",
            "state": "open",
            "user": {"login": "octo"},
            "head": {"label": "octo:facets-scope", "ref": "facets-scope", "repo": {"name": "ogcapi-records", "owner": {"login": "octo"}}},
            "base": {"ref": "main"},
        },
        306: {
            "number": 306,
            "html_url": "https://github.com/opengeospatial/ogcapi-records/pull/306",
            "title": "My facets",
            "body": "Mine",
            "state": "open",
            "draft": False,
            "merged_at": None,
            "user": {"login": "jdoe"},
            "head": {
                "label": "jdoe:facets-scope",
                "ref": "facets-scope",
                "repo": {"name": "ogcapi-records", "owner": {"login": "JDoe"}, "clone_url": str(OGC_OWN)},
            },
            "base": {"ref": "main"},
        },
        305: {
            "number": 305,
            "title": "Gone",
            "state": "open",
            "user": {"login": "ghost"},
            "head": {"label": "ghost:gone", "ref": "gone", "repo": None},
        },
        200: {
            "number": 200,
            "title": "Merged",
            "state": "closed",
            "user": {"login": "octo"},
            "head": {"label": "octo:old", "ref": "old", "repo": None},
        },
    }

    async def get_pull_request(self, owner, repo, number, access_token):
        self.calls.append(("get_pull_request", owner, repo, number))
        return self.pull_requests.get(int(number)) if (owner, repo) == ("opengeospatial", "ogcapi-records") else None

    async def get_repository_pull_requests(self, owner, repo, token, state="all", per_page=100):
        self.calls.append(("get_repository_pull_requests", owner, repo, state))
        return [pull_request for pull_request in self.pull_requests.values() if state == "all" or pull_request["state"] == state]

    async def get_folder_names(self, owner, repo, token, path, branch):
        self.calls.append(("get_folder_names", owner, repo, path, branch))
        return ["NRB", "SR"]

    async def get_github_user(self, username, access_token):
        self.calls.append(("get_github_user", username))
        return self.users.get(username)

    async def create_pull_request(self, access_token, owner, repo, pr_data):
        self.calls.append(("create_pull_request", owner, repo, pr_data["head"]))
        return {
            "number": 7,
            "html_url": f"https://github.com/{owner}/{repo}/pull/7",
            "title": pr_data["title"],
            "body": pr_data["body"],
            "state": "open",
            "draft": pr_data.get("draft", False),
            "merged_at": None,
            "head": {"repo": {"name": repo}},
        }


@pytest.fixture
def github(monkeypatch):
    # Each test sees its own GitHub, not the repositories another one listed
    monkeypatch.setattr(workspace_service, "_repository_lists", {})
    fake = FakeGitHub()
    service = dependencies.github_service
    for name in dir(service):
        if not name.startswith("_") and callable(getattr(service, name)) and name != "aclose":
            monkeypatch.setattr(service, name, getattr(fake, name, _unexpected(name)))
    return fake


def _unexpected(name):
    async def call(*args, **kwargs):
        raise AssertionError(f"Unexpected GitHub call: {name}")

    return call


@pytest.fixture
def client(user, github):
    token = JWTService.create_access_token(user)["access_token"]
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        # Every response is checked against openapi.yaml
        client.event_hooks = {"request": [], "response": [validate_response]}
        yield client


@pytest.fixture
def workspace(client):
    response = client.post("/workspaces", json={"title": "NRB review", "selection": ["NRB"]})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def ogc_workspace(client):
    response = client.post("/workspaces", json={"title": "Records review", "type": "ogc", "repository": "ogcapi-records"})
    assert response.status_code == 201, response.text
    return response.json()
