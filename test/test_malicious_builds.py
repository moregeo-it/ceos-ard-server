"""
Real builds of malicious content with the CLI's toolchain, which is installed on first use (about a minute).

Needs Linux, the network and a ceos-org/ceos-ard checkout in CEOS_ARD_CHECKOUT.
"""

import asyncio
import os
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from app.services.build_service import BuildService
from app.utils import sandbox
from app.workspace_types import get_type

CHECKOUT = os.getenv("CEOS_ARD_CHECKOUT")

pytestmark = [
    pytest.mark.skipif(sys.platform != "linux", reason="Builds are only isolated on Linux"),
    pytest.mark.skipif(not CHECKOUT, reason="Set CEOS_ARD_CHECKOUT to a ceos-org/ceos-ard checkout"),
    pytest.mark.installs_toolchains,
]

LUA = """
local outside = "{outside}"
local found = {{}}
local function probe(name, action)
    local ok, result = pcall(action)
    table.insert(found, name .. "=" .. tostring(ok and result))
end
probe("env", function() return os.getenv("SECRET_KEY") end)
probe("passwd", function() return io.open("/etc/passwd"):read("a") end)
probe("secret", function() return io.open(outside .. "/secret.env"):read("a") end)
probe("git", function() return io.open(".git/config"):read("a") end)
probe("write", function() local file = io.open(outside .. "/written.txt", "w"); file:write("escaped"); file:close(); return "written" end)
probe("fetch", function() return select(2, pandoc.mediabag.fetch("http://169.254.169.254/latest/meta-data/")) end)
probe("shell", function() return io.popen("cat /etc/passwd " .. outside .. "/secret.env"):read("a") end)

function Pandoc(doc)
    table.insert(doc.blocks, pandoc.Para(pandoc.Str("PROBE[" .. table.concat(found, ";") .. "]")))
    return doc
end
"""

# The <img> and <iframe> only show that Chromium builds the PDF without network and files of the host:
# the PDF's text is compressed, so the leak check below can't see into it
TEMPLATE = """

JINJA[~{{ cycler.__init__.__globals__.os.popen('cat /etc/passwd {outside}/secret.env; env').read() }}~]

![](/etc/passwd)

![]({outside}/secret.env)

<img src="http://169.254.169.254/latest/meta-data/"><iframe src="file:///etc/passwd"></iframe>
"""

LEAKS = (b"root:x:0", b"must-not-leak", os.environ["SECRET_KEY"].encode())


@pytest.fixture(scope="module", autouse=True)
def isolated():
    try:
        asyncio.run(sandbox.setup())
    except Exception:
        if not sandbox.isolation.sandbox:
            raise


@pytest.fixture
def workspace(tmp_path):
    outside = tmp_path / "server"
    outside.mkdir()
    (outside / "secret.env").write_text("SECRET_KEY=must-not-leak")
    workspace = tmp_path / "workspace"
    shutil.copytree(CHECKOUT, workspace, ignore=shutil.ignore_patterns(".git", "build"))
    (workspace / ".git").mkdir()
    (workspace / ".git" / "config").write_text("token = must-not-leak")
    with open(workspace / "templates" / "pagebreak.lua", "a") as file:
        file.write(LUA.format(outside=outside))
    with open(workspace / "templates" / "template.md", "a") as file:
        file.write(TEMPLATE.format(outside=outside))
    return workspace


def contents(path: Path) -> bytes:
    if path.suffix == ".docx":
        with zipfile.ZipFile(path) as docx:
            return b"".join(docx.read(name) for name in docx.namelist())
    return path.read_bytes()


@pytest.mark.parametrize("format", ["docx", "pdf"])
def test_malicious_content_reaches_nothing(workspace, tmp_path, format):
    output = tmp_path / "output"
    result = asyncio.run(BuildService().build(workspace, "malicious", get_type("ceos-ard"), ["NRB"], include_format=format, build_dir=output))
    assert result["status"] == "success", result["message"]

    html = (output / "NRB.html").read_text()
    assert "PROBE[" in html, "the Lua filter didn't run"
    assert "JINJA[" in html, "the template didn't run"
    assert (output / f"NRB.{format}").exists()
    for path in output.rglob("*"):
        if path.is_file():
            assert not any(leak in contents(path) for leak in LEAKS), path
    assert not (tmp_path / "server" / "written.txt").exists()
