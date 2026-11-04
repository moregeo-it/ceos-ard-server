import asyncio
import logging
import os
import signal
import subprocess
import sys
import tomllib

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.config import settings
from app.db.database import engine

logger = logging.getLogger(__name__)

# Bounds concurrent builds: a pandoc/Playwright document build takes tens of seconds, and an
# unbounded burst of preview requests would otherwise run one build per request.
_run_semaphore = asyncio.Semaphore(settings.BUILD_CONCURRENCY)

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


def _command_env() -> dict[str, str]:
    return {key: value for key in _ENV_KEYS if (value := os.environ.get(key)) is not None}


def _kill_tree(pid: int) -> None:
    """Kill the process and everything it started, e.g. pandoc and Chromium under `pixi run`."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def load_project_info() -> tuple[str, str]:
    # Read project metadata from pixi.toml
    with open("pixi.toml", "rb") as f:
        _pixi_config = tomllib.load(f)
        name = _pixi_config["workspace"]["name"]
        version = _pixi_config["workspace"]["version"]
    return name, version


async def run(*args, timeout=60) -> subprocess.CompletedProcess:
    """
    Run a command in the pixi environment as a native async subprocess.

    Occupies no worker thread while it waits; on timeout or cancellation, the command and every
    process it started are killed instead of running on unattended.
    """
    async with _run_semaphore:
        process = await asyncio.create_subprocess_exec(
            "pixi",
            "run",
            "--",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_command_env(),
            # Its own process group, so a timeout reaches its children too
            start_new_session=sys.platform != "win32",
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        except (TimeoutError, asyncio.CancelledError):
            _kill_tree(process.pid)
            await process.communicate()
            raise
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


async def run_checks():
    checks = {
        "CEOS-ARD CLI": check_ceos_ard_cli,
        "Playwright": check_playwright,
        "Session cookie": check_session_cookie,
        "Database schema": check_database_schema,
    }
    # Missing tools only break builds, but a session misconfiguration exposes sessions in every environment,
    # and an outdated schema breaks every request
    fatal_checks = {check_session_cookie, check_database_schema}
    failures = []
    fatal = False
    for check_name, check_func in checks.items():
        try:
            await check_func()
        except Exception as e:
            failures.append(f"{check_name}: {e}")
            fatal = fatal or check_func in fatal_checks

    if len(failures) > 0:
        logger.error("\n!!!! CHECKS FAILED !!!!\n - " + "\n - ".join(failures) + "\n")
        if fatal or settings.ENVIRONMENT == "production":
            sys.exit(1)
    else:
        logger.info("All prerequisite checks passed.")


async def check_database_schema():
    """The server never changes the schema itself: the database must be at the newest migration."""
    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    if current != head:
        raise Exception(f"Database at revision {current or 'none'}, the code needs {head}: back it up, then run `pixi run migrate`")


async def check_session_cookie():
    """Every request carries the session cookie, so only exact origins may read responses."""
    if any("*" in origin for origin in settings.CORS_ORIGINS):
        raise Exception("CORS_ORIGINS must list exact origins, no wildcards")
    if settings.ENVIRONMENT != "development" and not settings.SESSION_COOKIE_SECURE:
        raise Exception("SESSION_COOKIE_SECURE=false is for local development over HTTP only")


async def check_playwright():
    """Check if Playwright browsers are installed."""
    try:
        result = await run("playwright", "install", "--list", timeout=10)
        if result.returncode != 0:
            raise Exception(f"Failed with exit code {result.returncode}, likely needs to be reinstalled")
        if "chromium_headless_shell" not in result.stdout.decode():
            raise Exception("Chromium browser not installed, run 'pixi run install-browser'")
    except FileNotFoundError as e:
        raise Exception("Not installed or not available in the PATH") from e
    except Exception:
        raise


async def check_ceos_ard_cli():
    """Check if ceos-ard CLI is available."""
    try:
        result = await run("ceos-ard", "--version", timeout=10)
        if result.returncode != 0:
            raise Exception(f"Returned exit code {result.returncode}, likely needs to be reinstalled")
    except FileNotFoundError as e:
        raise Exception("Not installed or not available in the PATH") from e
    except Exception:
        raise
