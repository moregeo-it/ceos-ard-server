import asyncio
import os
import signal
import sys
import time

import pytest

from app.utils.process import run

# Starts a grandchild that writes the time into a file every 0.1 s for 30 s, then waits
CHILD = """
import subprocess, sys
subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])
import time
time.sleep(600)
"""
# The same, but the grandchild leaves the process group and writes its pid next to the heartbeat
DETACHING_CHILD = """
import subprocess, sys
subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]], start_new_session=True)
import time
time.sleep(600)
"""
GRANDCHILD = """
import os, pathlib, sys, time
pathlib.Path(sys.argv[1] + ".pid").write_text(str(os.getpid()))
for _ in range(300):
    pathlib.Path(sys.argv[1]).write_text(str(time.time()))
    time.sleep(0.1)
"""


def test_commands_do_not_see_the_server_settings(monkeypatch):
    # .env is loaded into the server's environment, with SECRET_KEY and the GitHub secrets
    monkeypatch.setenv("SECRET_KEY", "must-not-leak")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "must-not-leak")
    result = asyncio.run(run("python", "-c", "import os; print(sorted(os.environ))"))
    assert result.returncode == 0, result.stderr
    assert b"must-not-leak" not in result.stdout
    assert b"SECRET_KEY" not in result.stdout
    assert b"GITHUB_CLIENT_SECRET" not in result.stdout


def test_a_timeout_ends_everything_the_command_started(tmp_path):
    child, grandchild, beat = tmp_path / "child.py", tmp_path / "grandchild.py", tmp_path / "beat"
    child.write_text(CHILD)
    grandchild.write_text(GRANDCHILD)

    start = time.monotonic()
    with pytest.raises(TimeoutError):
        asyncio.run(run("python", str(child), str(grandchild), str(beat), timeout=5))
    # Returns right away instead of after the grandchild's 30 s: nothing left holds the output pipes open
    assert time.monotonic() - start < 20

    assert beat.exists(), "the grandchild never started"
    last = beat.read_text()
    time.sleep(1)
    assert beat.read_text() == last, "the grandchild still runs"


def test_a_timeout_returns_even_if_a_process_left_the_group(tmp_path):
    child, grandchild, beat = tmp_path / "child.py", tmp_path / "grandchild.py", tmp_path / "beat"
    child.write_text(DETACHING_CHILD)
    grandchild.write_text(GRANDCHILD)

    start = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            asyncio.run(run("python", str(child), str(grandchild), str(beat), timeout=2))
        # Doesn't wait for the grandchild's 30 s, although it still holds the output pipes
        assert time.monotonic() - start < 20
    finally:
        pid_file = tmp_path / "beat.pid"
        if pid_file.exists() and sys.platform != "win32":
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
