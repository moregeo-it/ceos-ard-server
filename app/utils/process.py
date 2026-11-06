import asyncio
import contextlib
import os
import signal
import subprocess
import sys
from pathlib import Path

from app.utils.limits import KB, MB

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


# What a command may make the server hold in memory per stream, e.g. a build printing in a loop
MAX_OUTPUT = 16 * MB


class OutputTooLarge(Exception):
    pass


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


async def _read(stream: asyncio.StreamReader, limit: int) -> bytes:
    data = bytearray()
    while chunk := await stream.read(64 * KB):
        data += chunk
        if len(data) > limit:
            raise OutputTooLarge(f"The output exceeds {limit / MB:g} MB")
    return bytes(data)


async def _collect(process: asyncio.subprocess.Process, readers: list[asyncio.Task]) -> tuple[bytes, bytes]:
    stdout, stderr = await asyncio.gather(*readers)
    await process.wait()
    return stdout, stderr


async def _discard(process: asyncio.subprocess.Process) -> None:
    for stream in (process.stdout, process.stderr):
        while await stream.read(64 * KB):
            pass
    await process.wait()


async def run(
    *args, timeout: float = 60, env: dict[str, str] | None = None, cwd: Path | None = None, max_output: int = MAX_OUTPUT
) -> subprocess.CompletedProcess:
    """
    Run a command as a native async subprocess, with `command_env()` unless `env` is given.

    Occupies no worker thread while it waits; on timeout, cancellation or more than `max_output` bytes
    on stdout or stderr, the command and every process it started are killed instead of running on unattended.
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
    readers = [asyncio.ensure_future(_read(stream, max_output)) for stream in (process.stdout, process.stderr)]
    try:
        stdout, stderr = await asyncio.wait_for(_collect(process, readers), timeout)
    except (TimeoutError, asyncio.CancelledError, OutputTooLarge):
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        await _kill_tree(process.pid)
        # Without keeping what is left; a process that left the group can keep the pipes open, so don't wait for it
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_discard(process), 5)
        raise
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
