import asyncio
import contextlib
import os
import signal
import subprocess
import sys
from pathlib import Path

# The only environment variables commands get: .env is loaded into the server's environment, and
# its secrets must not reach builds, which run content from the workspaces
_ENV_KEYS = (
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TMPDIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "PIXI_HOME",
    "PIXI_CACHE_DIR",
    "RATTLER_CACHE_DIR",
    "PLAYWRIGHT_BROWSERS_PATH",
    # Windows
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "HOMEDRIVE",
    "HOMEPATH",
)


def command_env() -> dict[str, str]:
    return {key: value for key in _ENV_KEYS if (value := os.environ.get(key)) is not None}


async def _kill_tree(pid: int) -> None:
    """Kill the process and everything it started, e.g. pandoc and Chromium."""
    if sys.platform == "win32":
        taskkill = await asyncio.create_subprocess_exec(
            "taskkill", "/F", "/T", "/PID", str(pid), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await taskkill.wait()
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def run(*args, timeout: float = 60, env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """
    Run a command as a native async subprocess, with `command_env()` unless `env` is given.

    Occupies no worker thread while it waits; on timeout or cancellation, the command and every
    process it started are killed instead of running on unattended.
    """
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=command_env() if env is None else env,
        cwd=cwd,
        # Its own process group, so a timeout reaches its children too
        start_new_session=sys.platform != "win32",
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except (TimeoutError, asyncio.CancelledError):
        await _kill_tree(process.pid)
        # A process that left the group can keep the pipes open, so don't wait for it
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(process.communicate(), 5)
        raise
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
