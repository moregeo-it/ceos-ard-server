"""Builds run workspace content, which anyone with a GitHub login controls: nothing may escape the sandbox."""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

from app.config import settings
from app.utils import sandbox
from app.utils.sandbox import BuildOutOfMemory, isolation, run_build

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Builds are only isolated on Linux")

# Runs in the sandbox and reports what it could reach
PROBE = """
import json, os, pathlib, socket, sys

def attempt(action):
    try:
        return action()
    except Exception as e:
        return type(e).__name__

def write(path):
    pathlib.Path(path).write_text("escaped")
    return "written"

def connect(host, port):
    socket.create_connection((host, port), timeout=2).close()
    return "connected"

outside, output = sys.argv[1], sys.argv[2]
print(json.dumps({
    "environment": sorted(os.environ),
    "secret": attempt(lambda: pathlib.Path(outside, "secret.env").read_text()),
    "git": attempt(lambda: sorted(os.listdir(".git"))),
    "passwd": attempt(lambda: pathlib.Path("/etc/passwd").read_text()),
    "hostname": attempt(lambda: pathlib.Path("/etc/hostname").read_text()),
    "symlink": attempt(lambda: pathlib.Path("leak").read_text()),
    "processes": len([entry for entry in os.listdir("/proc") if entry.isdigit()]),
    "write_workspace": attempt(lambda: write("written.txt")),
    "write_outside": attempt(lambda: write(pathlib.Path(outside, "written.txt"))),
    "write_output": attempt(lambda: write(pathlib.Path(output, "written.txt"))),
    "metadata_service": attempt(lambda: connect("169.254.169.254", 80)),
    "internet": attempt(lambda: connect("1.1.1.1", 53)),
    "dns": attempt(lambda: socket.getaddrinfo("github.com", 443)),
}))
"""

# Starts a process that leaves the build's session and writes a heartbeat until it is killed
ENDLESS = """
import subprocess, sys, time
beat = (
    "import pathlib, sys, time\\n"
    "while True:\\n"
    "    pathlib.Path(sys.argv[1]).write_text(str(time.time()))\\n"
    "    time.sleep(0.1)\\n"
)
subprocess.Popen([sys.executable, "-c", beat, sys.argv[1]], start_new_session=True)
time.sleep(600)
"""

GREEDY = """
memory = []
while True:
    memory.append(bytearray(10 * 1024 * 1024))
"""


@pytest.fixture(scope="module", autouse=True)
def isolated():
    try:
        asyncio.run(sandbox.setup())
    except Exception as e:
        if not isolation.sandbox and os.getenv("CI"):
            raise
        if not isolation.sandbox:
            pytest.skip(f"Builds can't be isolated here: {e}")
        # The sandbox works, but without cgroups; the tests that need them say so
    yield


@pytest.fixture
def workspace(tmp_path):
    """A workspace with git data, a symlink out of it, and secrets of the server next to it."""
    outside = tmp_path / "server"
    outside.mkdir()
    (outside / "secret.env").write_text("SECRET_KEY=must-not-leak")
    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True)
    (workspace / ".git" / "config").write_text("[remote]")
    (workspace / "leak").symlink_to(outside / "secret.env")
    (workspace / "build").mkdir()
    return workspace


def build(workspace: Path, script: str, *args: str, timeout: float = 30, env: dict[str, str] | None = None):
    (workspace / "script.py").write_text(script)
    python = Path(sys.executable)
    return asyncio.run(
        run_build(
            [python.name, "script.py", *args],
            path=[python.parent],
            read_only=[Path(sys.prefix), workspace],
            writable=[workspace / "build"],
            hidden=[workspace / ".git"],
            cwd=workspace,
            timeout=timeout,
            env=env,
        )
    )


def requires_cgroups():
    if isolation.cgroups is None:
        if os.getenv("CI"):
            pytest.fail("The server's cgroup isn't delegated")
        pytest.skip("The server's cgroup isn't delegated")


def test_a_build_reaches_nothing_but_its_files(workspace, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "must-not-leak")
    outside = workspace.parent / "server"
    result = build(workspace, PROBE, str(outside), str(workspace / "build"), env={"EXAMPLE": "1"})
    assert result.returncode == 0, result.stderr
    assert b"must-not-leak" not in result.stdout
    probe = json.loads(result.stdout)

    # The shell that enters the cgroup may add its own variables
    assert set(probe["environment"]) - {"PWD", "OLDPWD", "SHLVL", "_"} == {"EXAMPLE", "HOME", "LANG", "PATH", "PLAYWRIGHT_BROWSERS_PATH"}
    assert probe["secret"] == "FileNotFoundError"
    assert probe["git"] == []
    assert probe["passwd"] == "FileNotFoundError"
    assert probe["hostname"] == "FileNotFoundError"
    assert probe["symlink"] == "FileNotFoundError"
    assert probe["processes"] < 5

    assert probe["write_workspace"] == "OSError"
    assert probe["write_outside"] == "FileNotFoundError"
    assert probe["write_output"] == "written"
    assert not (outside / "written.txt").exists()
    assert (workspace / "build" / "written.txt").read_text() == "escaped"

    assert probe["metadata_service"] in ("OSError", "TimeoutError")
    assert probe["internet"] in ("OSError", "TimeoutError")
    assert probe["dns"] == "gaierror"


def test_an_endless_build_is_stopped_with_everything_it_started(workspace):
    beat = workspace / "build" / "beat"
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        build(workspace, ENDLESS, str(beat), timeout=2)
    assert time.monotonic() - start < 10

    assert beat.exists(), "the detached process never started"
    last = beat.read_text()
    time.sleep(1)
    assert beat.read_text() == last, "the detached process still runs"


def test_a_build_that_needs_too_much_memory_is_stopped(workspace, monkeypatch):
    requires_cgroups()
    monkeypatch.setattr(settings, "BUILD_MEMORY_MAX_PER_BUILD", "100M")
    with pytest.raises(BuildOutOfMemory):
        build(workspace, GREEDY)
    # The server lives on, and the next build works
    assert build(workspace, "print('fine')").stdout.strip() == b"fine"


def test_each_build_runs_in_a_cgroup_of_its_own(workspace, monkeypatch):
    requires_cgroups()
    created = []
    create = isolation.cgroups.create

    def record(memory_max):
        created.append(create(memory_max))
        return created[-1]

    monkeypatch.setattr(isolation.cgroups, "create", record)
    # The build's view of the cgroups starts at its own
    assert build(workspace, "print(open('/proc/self/cgroup').read())").stdout.strip() == b"0::/"
    build(workspace, "print('fine')")
    assert len(created) == 2 and created[0] != created[1]
    assert all(cgroup.parent == isolation.cgroups.builds for cgroup in created)
    # Removed once done
    assert [path for path in isolation.cgroups.builds.iterdir() if path.is_dir()] == []


FORK_BOMB = """
import os
while True:
    try:
        os.fork()
    except OSError:
        pass
"""


def test_a_fork_bomb_stays_in_its_build(workspace):
    requires_cgroups()
    start = time.monotonic()
    # pids.max stops it from taking the server's processes, the timeout or the memory limit ends it
    with pytest.raises((TimeoutError, BuildOutOfMemory)):
        build(workspace, FORK_BOMB, timeout=3)
    assert time.monotonic() - start < 20
    assert [path for path in isolation.cgroups.builds.iterdir() if path.is_dir()] == []
