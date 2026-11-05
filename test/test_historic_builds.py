"""
The content of ceos-org/ceos-ard still builds with the CLI version it pinned at the time: workspaces that
weren't updated since get exactly that version.

Needs Linux, the network and a clone of ceos-org/ceos-ard with its history in CEOS_ARD_HISTORY. Installs one
toolchain per version on first use; set TOOLCHAINS_ROOT to keep them between runs.
"""

import asyncio
import io
import os
import subprocess
import sys
import tarfile

import pytest

from app.services.build_service import BuildService
from app.utils import sandbox
from app.workspace_types import get_type
from app.workspace_types.ceos_ard import cli_version

HISTORY = os.getenv("CEOS_ARD_HISTORY")

pytestmark = [
    pytest.mark.skipif(sys.platform != "linux", reason="Builds are only isolated on Linux"),
    pytest.mark.skipif(not HISTORY, reason="Set CEOS_ARD_HISTORY to a clone of ceos-org/ceos-ard with its history"),
    pytest.mark.installs_toolchains,
]

# The commits of ceos-org/ceos-ard that changed requirements.txt, and the version each one gets
PINS = [
    ("0909206", "0.4.0"),
    ("d66f84f", "0.5.0"),
    ("dd71122", "0.5.2"),
    ("fb805e3", "0.5.4"),
    ("899901f", "0.5.9"),
]


@pytest.fixture(scope="module", autouse=True)
def isolated():
    try:
        asyncio.run(sandbox.setup())
    except Exception:
        if not sandbox.isolation.sandbox:
            raise


@pytest.mark.parametrize(("commit", "version"), PINS)
def test_historic_content_builds_with_its_cli_version(tmp_path, commit, version):
    workspace = tmp_path / "workspace"
    archive = subprocess.run(["git", "-C", HISTORY, "archive", commit], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(workspace, filter="data")
    (workspace / ".git").mkdir()
    assert cli_version(workspace) == version

    output = tmp_path / "output"
    result = asyncio.run(BuildService().build(workspace, "historic", get_type("ceos-ard"), ["NRB"], include_format="pdf", build_dir=output))
    assert result["status"] == "success", result["message"]
    assert (output / "NRB.html").stat().st_size > 0
    assert (output / "NRB.pdf").stat().st_size > 0
