import logging
import os
import sys
import tomllib
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.config import settings
from app.db.database import engine
from app.utils import sandbox
from app.utils.process import run

logger = logging.getLogger(__name__)


def load_project_info() -> tuple[str, str]:
    # Read project metadata from pixi.toml
    with open("pixi.toml", "rb") as f:
        _pixi_config = tomllib.load(f)
        name = _pixi_config["workspace"]["name"]
        version = _pixi_config["workspace"]["version"]
    return name, version


async def run_checks():
    checks = {
        "Build isolation": check_build_isolation,
        "Toolchains": check_toolchains_root,
        "pixi": check_pixi,
        "Session cookie": check_session_cookie,
        "Database schema": check_database_schema,
    }
    # Missing tools only break builds, but a session misconfiguration exposes sessions in every environment,
    # and an outdated schema breaks every request. Outside development, every check is fatal.
    fatal_checks = {check_session_cookie, check_database_schema, check_toolchains_root}
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
        if fatal or settings.ENVIRONMENT != "development":
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


async def check_build_isolation():
    """Builds run content of the workspaces, so outside development they must be isolated."""
    try:
        await sandbox.setup()
    except Exception as e:
        if settings.ENVIRONMENT != "development":
            raise
        logger.warning(f"Builds aren't isolated, which is only acceptable in development: {e}")


async def check_toolchains_root():
    """Unused toolchains are removed from TOOLCHAINS_ROOT at startup, so it must be a folder of its own."""
    if os.environ.get("TOOLCHAINS_ROOT", None) == "":
        raise Exception("TOOLCHAINS_ROOT is empty; leave it out for the default or set a folder of its own")
    root = settings.TOOLCHAINS_ROOT
    for name, path in (("the server's folder", Path.cwd().resolve()), ("WORKSPACES_ROOT", settings.WORKSPACES_ROOT)):
        if path.is_relative_to(root):
            raise Exception(f"TOOLCHAINS_ROOT ({root}) must not be or contain {name} ({path})")
    if root.is_relative_to(settings.WORKSPACES_ROOT):
        raise Exception(f"TOOLCHAINS_ROOT ({root}) must not be inside WORKSPACES_ROOT ({settings.WORKSPACES_ROOT})")


async def check_pixi():
    """pixi installs the toolchains for the builds."""
    try:
        result = await run("pixi", "--version", timeout=10)
    except FileNotFoundError as e:
        raise Exception("Not installed or not available in the PATH") from e
    if result.returncode != 0:
        raise Exception(f"Returned exit code {result.returncode}")
