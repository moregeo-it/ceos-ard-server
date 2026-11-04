"""API tests against local git repositories: bare repositories stand in for ceos-org/ceos-ard and the user's fork on GitHub."""

import os
import shutil
import subprocess
import sys
import tempfile
import types
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

DATA = Path(tempfile.mkdtemp(prefix="ceos-ard-server-test-"))
UPSTREAM = DATA / "upstream.git"
FORK = DATA / "fork.git"

os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{(DATA / 'test.db').as_posix()}",
        "WORKSPACES_ROOT": str(DATA / "workspaces"),
        "SECRET_KEY": "test-secret",
        "SESSION_COOKIE_SECURE": "false",
        "ENVIRONMENT": "development",
        "CEOS_ARD_ORG": "ceos-org",
        "CEOS_ARD_REPO": "ceos-ard",
        "CEOS_ARD_BRANCH": "main",
    }
)

# POSIX file locks are not available on Windows (ceos-ard-server#139); the tests run in one process
if sys.platform == "win32":
    sys.modules.setdefault("fcntl", types.SimpleNamespace(flock=lambda *a: None, LOCK_EX=2, LOCK_NB=4, LOCK_UN=8))

import pygit2  # noqa: E402

# The server fetches upstream from GitHub; point that URL to the local upstream repository
(DATA / "home").mkdir()
(DATA / "home" / ".gitconfig").write_text(f'[url "{UPSTREAM.as_posix()}"]\n\tinsteadOf = https://github.com/ceos-org/ceos-ard\n')
pygit2.settings.search_path[pygit2.enums.ConfigLevel.GLOBAL] = str(DATA / "home")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from openapi_validation import validate_response  # noqa: E402

from app import dependencies  # noqa: E402
from app.db.database import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import IdentityProvider, User  # noqa: E402
from app.services.jwt_service import JWTService  # noqa: E402

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
        (clone / path).write_text(content, encoding="utf-8")
    git("add", "-A", cwd=clone)
    git("-c", "user.name=Upstream", "-c", "user.email=upstream@example.org", "commit", "-q", "-m", message, cwd=clone)
    git("push", "-q", "origin", "main", cwd=clone)


@pytest.fixture(scope="session", autouse=True)
def repositories():
    source = DATA / "source"
    for path, content in REPOSITORY.items():
        (source / path).parent.mkdir(parents=True, exist_ok=True)
        (source / path).write_text(content, encoding="utf-8")
    git("init", "-q", "-b", "main", cwd=source)
    git("add", "-A", cwd=source)
    git("-c", "user.name=Upstream", "-c", "user.email=upstream@example.org", "commit", "-q", "-m", "Initial", cwd=source)
    git("clone", "-q", "--bare", str(source), str(UPSTREAM))
    git("clone", "-q", "--bare", str(UPSTREAM), str(FORK))

    command.upgrade(Config(str(Path(__file__).parent.parent / "alembic.ini")), "head")
    yield
    engine.dispose()
    # pygit2 may still hold pack files open on Windows
    shutil.rmtree(DATA, ignore_errors=True)


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

    async def fork(self, user, upstream_owner, upstream_repo):
        self.calls.append(("fork", upstream_owner, upstream_repo))
        return {"owner": {"login": user.username}, "name": upstream_repo, "clone_url": str(FORK)}

    async def get_pfs_types(self, owner, repo, token, branch):
        self.calls.append(("get_pfs_types", owner, repo, branch))
        return ["NRB", "SR"]

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
    response = client.post("/workspaces", json={"title": "NRB review", "pfs": ["NRB"]})
    assert response.status_code == 201, response.text
    return response.json()
