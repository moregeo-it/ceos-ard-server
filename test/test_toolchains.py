import asyncio
import os
import sys
import time
import tomllib
from pathlib import Path

import httpx
import pytest
from conftest import file_url

from app.config import settings
from app.db.database import SessionLocal
from app.models.workspace import GitWorkspace
from app.services.events_service import event_broker
from app.utils import limits
from app.utils.toolchains import Toolchain, ToolchainError, Toolchains, ToolchainSpec, _manifest, toolchains
from app.workspace_types.ceos_ard import cli_toolchain_spec, cli_version

CLI_PYPROJECT = """
[project]
dependencies = ["jinja2>=3.1.0,<4", "playwright>=1.50.0,<2", "bibtexparser==2.0.0b9"]

[tool.pixi.dependencies]
python = ">=3.11"
pandoc = "==3.8.2.1"
pandoc-crossref = "==0.3.22"
"""

# Writes the version of the CLI into the preview, like `ceos-ard generate ... -o <prefix>` writes `<prefix>.html`
FAKE_CLI_SH = """#!/bin/sh
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done
echo "<p>CLI {version}</p>" > "$out.html"
"""
FAKE_CLI_BAT = """@echo off
:loop
if "%~1"=="" goto done
if "%~1"=="-o" set "out=%~2"
shift
goto loop
:done
echo ^<p^>CLI {version}^</p^> > "%out%.html"
"""
# Leaves its documents as symlinks to a secret of the server, which the sandbox doesn't show it
FAKE_CLI_LINKS_SH = """#!/bin/sh
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done
ln -s "{secret}" "$out.html"
ln -s "{secret}" "$out.pdf"
"""
# Writes a preview without end
FAKE_CLI_FLOOD_SH = """#!/bin/sh
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done
yes "<p>{version}</p>" > "$out.html"
"""


def requirements(tmp_path: Path, text: str) -> Path:
    (tmp_path / "requirements.txt").write_text(text)
    return tmp_path


@pytest.mark.parametrize(
    ("text", "version"),
    [
        ("ceos-ard-cli~=0.5.9\n", "0.5.9"),
        ("ceos-ard-cli==0.5.9", "0.5.9"),
        ("# The CLI\nCEOS_ARD_CLI ~= 0.4.0  # pinned\n\n", "0.4.0"),
    ],
)
def test_the_cli_version_is_exactly_the_one_given_until_1_0(tmp_path, text, version):
    assert cli_version(requirements(tmp_path, text), installed=["ceos-ard-cli-0.5.10", "ceos-ard-cli-0.9.0"]) == version


def test_from_1_0_the_highest_installed_cli_version_within_the_range(tmp_path):
    workspace = requirements(tmp_path, "ceos-ard-cli~=1.2.0")
    assert cli_version(workspace) == "1.2.0"
    installed = ["ceos-ard-cli-1.2.3", "ceos-ard-cli-1.2.10", "ceos-ard-cli-1.3.0", "ceos-ard-cli-2.0.0", "other-1.2.5"]
    assert cli_version(workspace, installed) == "1.2.10"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "ceos-ard-cli~=0.5.9\nrequests==2.0.0",
        "requests==2.0.0",
        "-e git+https://example.com/evil.git#egg=ceos-ard-cli",
        "--index-url https://example.com/simple\nceos-ard-cli~=0.5.9",
        "ceos-ard-cli @ https://example.com/ceos_ard_cli-0.5.9.tar.gz",
        "ceos-ard-cli>=0.5.9",
        "ceos-ard-cli~=0.5",
        "ceos-ard-cli==0.5.*",
        "ceos-ard-cli>=0.5.9,<0.6",
        "ceos-ard-cli[dev]~=0.5.9",
        "ceos-ard-cli~=0.5.9; python_version > '3'",
        "ceos-ard-cli==0.5.9+local",
        "ceos-ard-cli==1!0.5.9",
    ],
)
def test_anything_else_in_requirements_txt_is_refused(tmp_path, text):
    with pytest.raises(ToolchainError, match="may only contain one line"):
        cli_version(requirements(tmp_path, text))


def test_requirements_txt_that_is_no_text_is_refused(tmp_path):
    (tmp_path / "requirements.txt").write_bytes(b"ceos-ard-cli~=0.5.9\xff\xfe\n")
    with pytest.raises(ToolchainError, match="may only contain one line"):
        cli_version(tmp_path)


def test_requirements_txt_is_read_only_as_far_as_needed(tmp_path):
    (tmp_path / "requirements.txt").write_text("ceos-ard-cli~=0.5.9\n" + "#" * 10_000)
    with pytest.raises(ToolchainError, match="may only contain one line"):
        cli_version(tmp_path)


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks need extra rights on Windows")
def test_requirements_txt_that_is_a_symlink_is_refused(tmp_path):
    (tmp_path / "requirements.txt").symlink_to("/dev/zero")
    with pytest.raises(ToolchainError, match="may only contain one line"):
        cli_version(tmp_path)


def test_workspaces_without_requirements_txt_are_asked_to_update(tmp_path):
    with pytest.raises(ToolchainError, match="Update the workspace from upstream"):
        cli_version(tmp_path)


def test_the_toolchain_is_what_the_cli_release_pins(monkeypatch):
    urls = []

    async def get(self, url):
        urls.append(url)
        return httpx.Response(200, text=CLI_PYPROJECT, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    spec = asyncio.run(cli_toolchain_spec("0.5.9"))
    assert urls == ["https://raw.githubusercontent.com/ceos-org/ceos-ard-cli/v0.5.9/pyproject.toml"]
    assert spec == ToolchainSpec(
        conda={"python": ">=3.11", "pandoc": "==3.8.2.1", "pandoc-crossref": "==0.3.22"},
        pypi={"ceos-ard-cli": "==0.5.9", "bibtexparser": "==2.0.0b9"},
        chromium=True,
    )

    manifest = tomllib.loads(_manifest("ceos-ard-cli-0.5.9", spec))
    assert manifest["dependencies"] == spec.conda
    assert manifest["pypi-dependencies"] == spec.pypi


def test_unknown_cli_versions_are_refused(monkeypatch):
    async def get(self, url):
        return httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    with pytest.raises(ToolchainError, match="CEOS-ARD CLI 9.9.9 doesn't exist"):
        asyncio.run(cli_toolchain_spec("9.9.9"))


@pytest.fixture
def fake_toolchains(tmp_path, monkeypatch):
    """Installed toolchains whose CLI writes its version into the preview."""
    monkeypatch.setattr(toolchains, "root", tmp_path / "toolchains")
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET_KEY=must-not-leak")
    for version in ("0.5.9", "0.6.0", "0.6.1", "0.6.2"):
        name = f"ceos-ard-cli-{version}"
        if sys.platform == "win32":
            cli = toolchains.env(name) / "Scripts" / "ceos-ard.bat"
            content = FAKE_CLI_BAT
        else:
            cli = toolchains.env(name) / "bin" / "ceos-ard"
            content = {"0.6.1": FAKE_CLI_LINKS_SH, "0.6.2": FAKE_CLI_FLOOD_SH}.get(version, FAKE_CLI_SH)
        cli.parent.mkdir(parents=True)
        cli.write_text(content.format(version=version, secret=secret), newline="\n" if sys.platform != "win32" else "\r\n")
        cli.chmod(0o755)
        (toolchains.path(name) / ".ready").touch()


def save_requirements(client, workspace, text):
    # Upstream has it, but not the test repository
    with SessionLocal() as db:
        path = db.get(GitWorkspace, workspace["id"]).abs_path / "requirements.txt"
    if not path.exists():
        path.write_text("")
    response = client.put(file_url(workspace, "files", "/requirements.txt"), content=text)
    assert response.status_code in (200, 201), response.text


def test_each_workspace_builds_with_its_cli_version(client, workspace, fake_toolchains):
    save_requirements(client, workspace, "ceos-ard-cli~=0.5.9\n")
    response = client.post(f"/workspaces/{workspace['id']}/previews")
    assert response.status_code == 200, response.text
    assert "<p>CLI 0.5.9</p>" in response.text

    save_requirements(client, workspace, "ceos-ard-cli==0.6.0\n")
    response = client.post(f"/workspaces/{workspace['id']}/previews")
    assert response.status_code == 200, response.text
    assert "<p>CLI 0.6.0</p>" in response.text


def test_a_build_reports_problems_with_the_cli_version(client, workspace, fake_toolchains):
    response = client.post(f"/workspaces/{workspace['id']}/previews")
    assert response.status_code == 500, response.text
    assert "has no requirements.txt" in response.json()["detail"]

    save_requirements(client, workspace, "ceos-ard-cli @ https://example.com/evil.tar.gz\n")
    response = client.post(f"/workspaces/{workspace['id']}/previews")
    assert response.status_code == 500, response.text
    assert "may only contain one line" in response.json()["detail"]

    save_requirements(client, workspace, "ceos-ard-cli~=0.5.8\n")
    response = client.get(f"/workspaces/{workspace['id']}/download", params={"format": "pdf", "pfs": ["NRB"]})
    assert response.status_code == 500, response.text
    assert response.json()["detail"] == "The toolchain ceos-ard-cli-0.5.8 isn't installed"


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks need extra rights on Windows")
def test_documents_left_as_symlinks_are_not_read(client, workspace, fake_toolchains):
    save_requirements(client, workspace, "ceos-ard-cli==0.6.1\n")
    response = client.post(f"/workspaces/{workspace['id']}/previews")
    assert response.status_code == 500, response.text
    assert response.json()["detail"] == "The build produced no preview"

    response = client.get(f"/workspaces/{workspace['id']}/download", params={"format": "pdf", "pfs": ["NRB"]})
    assert response.status_code == 500, response.text
    assert "must-not-leak" not in response.text
    assert response.json()["detail"] == "The build produced no document"


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks need extra rights on Windows")
def test_builds_do_not_write_through_a_symlinked_build_folder(client, workspace, fake_toolchains, tmp_path):
    save_requirements(client, workspace, "ceos-ard-cli==0.5.9\n")
    with SessionLocal() as db:
        root = db.get(GitWorkspace, workspace["id"]).abs_path
    (tmp_path / "elsewhere").mkdir()
    (root / "build").symlink_to(tmp_path / "elsewhere", target_is_directory=True)

    response = client.post(f"/workspaces/{workspace['id']}/previews")
    assert response.status_code == 500, response.text
    assert response.json()["detail"] == "The build folder of the workspace is not a folder."
    assert list((tmp_path / "elsewhere").iterdir()) == []

    # Nor show what is there, e.g. the preview of another workspace
    (tmp_path / "elsewhere" / "NRB.html").write_text("<p>Another workspace</p>")
    response = client.get(f"/workspaces/{workspace['id']}/previews/current")
    assert response.status_code == 404, response.text
    assert "Another workspace" not in response.text


def test_previews_too_large_to_hold_in_memory_are_refused(client, workspace, fake_toolchains, monkeypatch):
    save_requirements(client, workspace, "ceos-ard-cli==0.5.9\n")
    monkeypatch.setattr(limits, "MAX_READ_SIZE", 5)
    response = client.post(f"/workspaces/{workspace['id']}/previews")
    assert response.status_code == 500, response.text
    assert response.json()["detail"] == "The build produced a preview that is too large"


@pytest.mark.skipif(sys.platform == "win32", reason="The fake CLI is a shell script")
def test_builds_that_write_too_much_are_stopped_and_removed(client, workspace, fake_toolchains, monkeypatch):
    monkeypatch.setattr(settings, "BUILD_OUTPUT_MAX", "5M")
    save_requirements(client, workspace, "ceos-ard-cli==0.6.2\n")
    response = client.post(f"/workspaces/{workspace['id']}/previews")
    assert response.status_code == 500, response.text
    assert response.json()["detail"] == "Generating the document wrote too much and was stopped."
    with SessionLocal() as db:
        assert not (db.get(GitWorkspace, workspace["id"]).abs_path / "build").exists()


def test_cli_versions_before_the_first_pin_are_refused(tmp_path):
    with pytest.raises(ToolchainError, match="too old, at least 0.4.0"):
        cli_version(requirements(tmp_path, "ceos-ard-cli==0.3.0"))


@pytest.mark.parametrize(
    "response",
    [httpx.ConnectError("unreachable"), httpx.Response(503), httpx.Response(200, text="[project]\ndependencies = []\n")],
    ids=["unreachable", "unavailable", "no toolchain"],
)
def test_cli_releases_that_can_not_be_read_are_reported(monkeypatch, response):
    async def get(self, url):
        if isinstance(response, Exception):
            raise response
        response.request = httpx.Request("GET", url)
        return response

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    with pytest.raises(ToolchainError, match="can't be found out right now"):
        asyncio.run(cli_toolchain_spec("0.5.9"))


def fake_toolchain(name):
    async def spec():
        return ToolchainSpec(conda={})

    return Toolchain(name=name, spec=spec)


def test_one_toolchain_is_installed_at_a_time(tmp_path):
    installer = Toolchains(tmp_path)
    running = []
    peak = 0

    async def install(toolchain):
        nonlocal peak
        running.append(toolchain.name)
        peak = max(peak, len(running))
        await asyncio.sleep(0.05)
        installer.path(toolchain.name).mkdir()
        (installer.path(toolchain.name) / ".ready").touch()
        running.remove(toolchain.name)

    installer._install = install

    async def install_two():
        await asyncio.gather(installer.ensure(fake_toolchain("first-1.0.0")), installer.ensure(fake_toolchain("second-1.0.0")))

    asyncio.run(install_two())
    assert peak == 1
    assert sorted(installer.installed()) == ["first-1.0.0", "second-1.0.0"]


def test_failed_installs_are_reported_for_a_while(tmp_path):
    installer = Toolchains(tmp_path)
    attempts = []

    async def install(toolchain):
        attempts.append(toolchain.name)
        raise ToolchainError("The network is gone")

    installer._install = install
    toolchain = fake_toolchain("broken-1.0.0")
    for _ in range(2):
        with pytest.raises(ToolchainError, match="The network is gone"):
            asyncio.run(installer.ensure(toolchain))
    assert attempts == ["broken-1.0.0"]


def test_toolchains_no_workspace_uses_are_removed(tmp_path):
    installer = Toolchains(tmp_path)
    for name in ("used-1.0.0", "unused-1.0.0", "recent-1.0.0", "half-1.0.0"):
        installer.path(name).mkdir()
        if name != "half-1.0.0":
            (installer.path(name) / ".ready").touch()
    long_ago = time.time() - 30 * 86400
    os.utime(installer.path("unused-1.0.0") / ".ready", (long_ago, long_ago))

    installer.remove_unused({"used-1.0.0"})
    assert sorted(path.name for path in tmp_path.iterdir()) == ["recent-1.0.0", "used-1.0.0"]


def test_installs_are_announced_before_the_build(client, workspace, fake_toolchains, monkeypatch):
    emitted = []
    monkeypatch.setattr(event_broker, "emit", lambda workspace_id, event_type, **fields: emitted.append((event_type.value, fields)))
    save_requirements(client, workspace, "ceos-ard-cli==0.6.3\n")
    response = client.post(f"/workspaces/{workspace['id']}/previews")
    assert response.status_code == 500, response.text
    assert [fields["toolchain"] for event, fields in emitted if event == "preview.preparing"] == ["ceos-ard-cli-0.6.3"]
